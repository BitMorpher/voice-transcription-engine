"""Installed batch CLI. Plans and staging data remain private and external to Git."""

from collections import Counter
from dataclasses import replace
from pathlib import Path
import shutil
import sys

from ..ordered_interview import _local
from ..cli import PrivateArgumentParser, _positive_timeout
from ..provider_control import CURRENT_CONTROL, ProviderControl
from ..progress import CURRENT, Reporter, emit_progress, interruptions, LogError
from .plan import BatchError, load_plan, require, select
from .runner import run_one
from .speakers import configurations
from .storage import (lock, read_snapshot, save_snapshot, stage, summaries, verify, write_summary)


def parser():
    value = PrivateArgumentParser(prog='voice-batch', color=False, allow_abbrev=False,
                                  description=__doc__)
    value.add_argument('action', choices=('inventory', 'check', 'prepare', 'verify', 'run', 'status'))
    value.add_argument('--plan', type=Path, help='Private version 1 batch JSON; manifest paths relative to plan.')
    value.add_argument('--batch', type=Path, help='Fresh directory for prepare; existing staged directory otherwise.')
    value.add_argument('--select', action='append', default=[], help='Exact private entry ID; repeat as needed.')
    value.add_argument('--exclude', action='append', default=[], help='Exclude exact entry ID; repeat as needed.')
    value.add_argument('--copy-local-files', action='store_true')
    value.add_argument('--allow-hydration', action='store_true')
    value.add_argument('--send-to-openai', action='store_true')
    value.add_argument('--human-reviewed', action='store_true',
                       help='Confirm human review of every requested output family before selected chapters.')
    value.add_argument('--phase', choices=('raw', 'review', 'chapters'), default='raw')
    value.add_argument('--interview', action='store_true',
                       help='Run original and separate attributed families; missing names keep scoped unidentified speakers.')
    value.add_argument('--speaker-config', type=Path,
                       help='Optional private per-entry names and confirmed mappings; requires run --interview.')
    value.add_argument('--interview-model',
                       help='Additional diarization model, gpt-4o-transcribe-diarize; requires run --interview.')
    value.add_argument('--diarization-chunk-seconds', type=_positive_timeout,
                       help='Independent speaker-pass duration, 1–600 seconds; original ASR caches keep their settings.')
    value.add_argument('--confirm-speaker-mappings', action='store_true',
                       help='Confirm every supplied mapping against the explicit new diarization request scopes.')
    value.add_argument('--max-provider-requests', type=int,
                       help='Maximum new SDK operations across all selected entries/stages; requires --provider-retries 0. Use 1 for a bounded diagnostic.')
    value.add_argument('--max-run-seconds', type=_positive_timeout,
                       help='Elapsed deadline for new provider starts; requires zero retries. In-flight I/O is timeout-bounded, not cancelled at this deadline.')
    value.add_argument('--provider-failure-limit', type=int, default=2,
                       help='Stop after consecutive failures per endpoint/model (default: 2); account/configuration failures stop immediately.')
    value.add_argument('--model', default='gpt-transcribe')
    value.add_argument('--editing-model', default='gpt-6-astra')
    value.add_argument('--author-model', default='gpt-6-astra')
    value.add_argument('--audio-chunk-seconds', type=_positive_timeout, default=300)
    value.add_argument('--media-timeout', type=_positive_timeout, default=3600)
    value.add_argument('--provider-timeout', type=_positive_timeout, default=120)
    value.add_argument('--provider-retries', type=int, choices=range(6), default=2)
    value.add_argument('--heartbeat-seconds', type=_positive_timeout, default=30)
    value.add_argument('--log-directory', type=Path, help='Private execution logs; defaults to batch/execution-logs.')
    value.add_argument('--context-file')
    value.add_argument('--glossary-file')
    value.add_argument('--language', action='append', default=[])
    value.add_argument('--chapters', choices=('interview', 'narrative', 'both'), default='both')
    value.add_argument('--narrative-person', choices=('first', 'third'), default='first')
    return value


def execute(args, reporter):
    require((args.action == 'run' or not args.interview)
            and (args.interview or (args.speaker_config is None and args.interview_model is None
                and args.diarization_chunk_seconds is None and not args.confirm_speaker_mappings)),
            'Speaker options require run --interview.')
    require(args.action == 'run' or (args.max_provider_requests is None and args.max_run_seconds is None
            and args.provider_failure_limit == 2), 'Provider controls require run.')
    root = args.batch.absolute() if args.batch else None
    existing = args.action in {'verify', 'run', 'status'}
    require((existing and root is not None and args.plan is None)
            or (not existing and args.plan is not None), 'Use --plan for metadata/staging or --batch for staged operations.')
    if existing:
        root = _local(root)
        frozen = read_snapshot(root)
        plan = frozen['plan']
        include = args.select or frozen['selected_ids']
    else:
        plan = load_plan(args.plan)
        include = args.select
    args.interview_options_by_id = (configurations(args.speaker_config, plan,
        args.interview_model or 'gpt-4o-transcribe-diarize') if args.interview else {})
    if args.diarization_chunk_seconds is not None:
        args.interview_options_by_id = {key: replace(value,
            diarization_chunk_seconds=args.diarization_chunk_seconds,
            mappings_reconfirmed=args.confirm_speaker_mappings) if value else None
            for key, value in args.interview_options_by_id.items()}
    require(not args.confirm_speaker_mappings or args.diarization_chunk_seconds is not None,
            'Speaker mapping confirmation requires explicit diarization chunks.')
    items = select(plan, include, args.exclude)
    phase = args.phase if args.action == 'run' else args.action
    reporter.context = {'scope': 'batch', 'phase': phase, 'selected': len(items)}
    if args.action in {'check', 'prepare', 'run'}:
        require(shutil.which('ffmpeg') and shutil.which('ffprobe'),
                'Install FFmpeg and ffprobe on PATH before staging/processing.')
    if args.action == 'prepare':
        require(args.copy_local_files and root is not None,
                'Staging reads/copies sources; supply --copy-local-files and a fresh --batch.')
        root = save_snapshot(root, plan, items)
    if args.action == 'run':
        require(args.send_to_openai, 'Provider calls require --send-to-openai.')
        require(args.phase != 'chapters' or (args.human_reviewed and args.select),
                'Chapters require --human-reviewed and explicit --select IDs.')
    if args.log_directory or args.action in {'prepare', 'verify', 'run'}:
        reporter.heartbeat = args.heartbeat_seconds
        reporter.start(args.log_directory or root / 'execution-logs')
    if args.action == 'status':
        for summary in summaries(root):
            reporter.emit(**({'status': 'summary'} | summary))
        return 0
    control = None
    if args.action == 'run':
        try:
            control = ProviderControl(max_requests=args.max_provider_requests,
                max_seconds=args.max_run_seconds, failure_limit=args.provider_failure_limit,
                retries=args.provider_retries)
        except ValueError:
            raise BatchError('Provider request/time limits require --provider-retries 0 and positive limits.') from None
        CURRENT_CONTROL.set(control)
        reporter.emit(status='configuration', configuration={
            'asr_model': args.model, 'editing_model': args.editing_model, 'author_model': args.author_model,
            'diarization_model': args.interview_model or 'gpt-4o-transcribe-diarize',
            'audio_chunk_seconds': args.audio_chunk_seconds,
            'diarization_chunk_seconds': args.diarization_chunk_seconds or args.audio_chunk_seconds,
            'provider_timeout': args.provider_timeout, 'provider_retries': args.provider_retries,
            'max_provider_requests': args.max_provider_requests, 'max_run_seconds': args.max_run_seconds,
            'provider_failure_limit': args.provider_failure_limit, 'interview': args.interview,
            'context_supplied': bool(args.context_file), 'glossary_supplied': bool(args.glossary_file),
            'language_hint_count': len(args.language)})
    rows = []
    def process():
        for item in items:
            reporter.context = {'scope': 'batch', 'phase': phase, 'item': item['position'], 'selected': len(items)}
            reporter.active = {}
            emit_progress('preflight', 'running')
            if control and control.reason:
                status = 'not_attempted'
                requested_families = ['original']
                if args.interview_options_by_id.get(item['id']) is not None:
                    requested_families.append('attributed')
                reporter.item_stages[item['position']] = {
                    family: {'preflight': 'not_attempted'} for family in requested_families}
            elif item['status'] == 'blocked':
                status = 'blocked'
            else:
                try:
                    if args.action == 'prepare':
                        stage(root, item, hydration=args.allow_hydration, progress=emit_progress, timeout=args.media_timeout)
                        status = 'staged'
                    elif args.action == 'verify':
                        verify(root, item)
                        status = 'verified'
                    elif args.action == 'run':
                        run_one(root, item, args)
                        status = 'complete'
                    else:
                        status = 'complete'
                        reporter.emit(status='progress', stage='preflight', stage_status='complete',
                                      parts=len(item['manifest']['parts']),
                                      blocked=sum(part['dataless'] for part in item['manifest']['parts']))
                except (LogError, KeyboardInterrupt):
                    raise
                except Exception as error:
                    if type(error) is BatchError:
                        reporter.emit(status='failed', message=str(error))
                    status = ('incomplete' if control and control.reason in {'request_limit', 'start_deadline'} else 'failed')
            row = {'item': item['position'], 'status': status,
                   'families': reporter.item_stages.get(item['position'], {})}
            if control and control.reason:
                row['stop_reason'] = control.reason
            rows.append(row)
            counts = Counter(row['status'] for row in rows)
            reporter.emit(**row, processed=sum(row['status'] != 'not_attempted' for row in rows), failed=counts['failed'], completed=counts['complete'],
                          staged=counts['staged'], verified=counts['verified'], blocked=counts['blocked'])
    if root and args.action in {'prepare', 'verify', 'run'}:
        with lock(root):
            try:
                process()
            except KeyboardInterrupt:
                active = reporter.context.get('item', 0)
                rows.append({'item': active, 'status': 'interrupted',
                             'families': reporter.item_stages.get(active, {})})
                attempted = {row['item'] for row in rows}
                rows.extend({'item': item['position'], 'status': 'not_attempted',
                    'families': {family: {'preflight': 'not_attempted'} for family in
                        (['original', 'attributed'] if args.interview_options_by_id.get(item['id']) else ['original'])}}
                    for item in items if item['position'] not in attempted)
                write_summary(root, reporter, rows, phase)
                raise
            write_summary(root, reporter, rows, phase)
    else:
        process()
    reporter.context = {'scope': 'batch', 'phase': phase}
    reporter.emit(status='summary', selected=len(items), processed=sum(row['status'] != 'not_attempted' for row in rows),
                  failed=sum(row['status'] == 'failed' for row in rows),
                  blocked=sum(row['status'] == 'blocked' for row in rows),
                  not_attempted=sum(row['status'] == 'not_attempted' for row in rows),
                  incomplete=sum(row['status'] == 'incomplete' for row in rows),
                  provider_requests=control.requests if control else 0,
                  stop_reason=control.reason if control else None)
    return int(any(row['status'] in {'failed', 'blocked', 'not_attempted', 'interrupted', 'incomplete'} for row in rows))


def main(argv=None):
    args = parser().parse_args(argv)
    reporter = Reporter(sys.stdout, heartbeat=args.heartbeat_seconds)
    token = CURRENT.set(reporter)
    control_token = CURRENT_CONTROL.set(None)
    try:
        with interruptions():
            code = execute(args, reporter)
    except KeyboardInterrupt:
        reporter.emit(status='interrupted')
        code = 130
    except Exception as error:
        reporter.emit(status='failed', message=str(error) if type(error) in (BatchError, LogError) else None)
        code = 1
    finally:
        reporter.close()
        CURRENT.reset(token)
        CURRENT_CONTROL.reset(control_token)
    return code or int(reporter.failed)


if __name__ == '__main__':
    sys.exit(main())
