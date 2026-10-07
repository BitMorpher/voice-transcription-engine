"""Installed batch CLI. Plans and staging data remain private and external to Git."""

from collections import Counter
from dataclasses import replace
from pathlib import Path
import argparse
import shutil
import sys

from ..ordered_interview import _local
from ..cli import PrivateArgumentParser, _positive_timeout
from ..model_config import REASONING_EFFORTS, TEXT_PROFILES
from ..provider_control import CURRENT_CONTROL, ProviderControl
from ..progress import CURRENT, Reporter, emit_progress, interruptions, LogError
from .plan import BatchError, load_plan, require, select
from .runner import run_one, blocked
from .concurrency import run_sessions
from .speakers import configurations
from .storage import (lock, read_snapshot, save_snapshot, stage, summaries, verify, write_summary)


def parser(*, studio=False):
    command = 'interview batch' if studio else 'voice-batch'
    value = PrivateArgumentParser(prog=command, color=False, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Interview Studio: prepare and process a selected group of independent interviews. Recordings within each interview stay ordered.',
        epilog='Actions:\n'
               '  inventory  List entries and inspect file metadata without reading recordings.\n'
               '  check      Check the plan and required local tools without reading recordings.\n'
               '  prepare    Copy selected recordings into a fresh private batch folder.\n'
               '  verify     Check saved copies and file checksums without reading original recordings.\n'
               '  run        Process the saved batch: raw (transcribe), review, or chapters.\n'
               '  status     Show results recorded by previous batch commands.\n\n'
               'Examples:\n'
               f'  {command} inventory --batch-plan private/config/batch.json\n'
               f'  {command} prepare --batch-plan private/config/batch.json --batch-folder private/batches/demo --copy-local-files\n'
               f'  {command} run --batch-folder private/batches/demo --step raw --send-to-openai\n\n'
               'Previous option spellings remain supported. See docs/cli-reference.md for the full pipeline guide.')
    value.add_argument('action', choices=('inventory', 'check', 'prepare', 'verify', 'run', 'status'),
                       metavar='ACTION', help='Choose an action; see the descriptions and examples below.')
    files = value.add_argument_group('Batch files and selection')
    files.add_argument('--batch-plan', '--plan', dest='plan', type=Path,
                       metavar='FILE', help='Private version 1 JSON list of independent interviews; recording-list paths are relative to this file.')
    files.add_argument('--batch-folder', '--batch', dest='batch', type=Path,
                       metavar='FOLDER', help='Fresh folder for prepare; existing saved batch folder for verify, run, or status.')
    files.add_argument('--select', action='append', default=[], metavar='ENTRY_ID', help='Process only this exact entry ID from the plan; repeat to choose several.')
    files.add_argument('--exclude', action='append', default=[], metavar='ENTRY_ID', help='Leave out this exact entry ID; repeat as needed.')
    prepare = value.add_argument_group('Local preparation')
    prepare.add_argument('--copy-local-files', action='store_true',
                         help='Explicitly allow prepare to read and copy recordings into the fresh batch folder.')
    prepare.add_argument('--download-cloud-files', '--allow-hydration', dest='allow_hydration', action='store_true',
                         help='Allow prepare to download cloud-placeholder recordings before copying; may use network and disk space.')
    steps = value.add_argument_group('Processing steps (run)')
    steps.add_argument('--send-to-openai', action='store_true',
                       help='Explicitly allow run to send audio/text to OpenAI; requests can incur charges.')
    steps.add_argument('--step', '--phase', dest='phase', choices=('raw', 'review', 'chapters'), default='raw',
                       metavar='STEP', help='raw: transcribe; review: polish and flag passages; chapters: draft after human review (default: raw). Each transcript version needs its own completed prerequisites.')
    steps.add_argument('--human-reviewed', action='store_true',
                       help='Confirm human review of every requested transcript version before chapters; also requires explicit --select IDs.')
    steps.add_argument('--parallel-interviews', type=int, default=1,
                       metavar='COUNT', help='Maximum interviews running together (default: 1; start with 2 for overlap). Recordings within each stay ordered; shared request limits, no requests-per-minute or cost cap.')
    chapters = value.add_argument_group('Chapter drafts (run --step chapters)')
    chapters.add_argument('--chapter-style', '--chapters', dest='chapters', choices=('interview', 'narrative', 'both'), default='both',
                          metavar='STYLE', help='Draft interview excerpts, narrative arrangement, or both (default: both).')
    chapters.add_argument('--narrative-person', choices=('first', 'third'), default='first',
                          metavar='PERSON', help='Keep the source voice (first) or frame exact testimony in third person (default: first).')
    transcription = value.add_argument_group('Transcription models and hints (run)')
    transcription.add_argument('--transcription-model', '--model', dest='model', default='gpt-transcribe',
                               metavar='MODEL', help='Speech-to-text model for the original transcript (default: gpt-transcribe).')
    transcription.add_argument('--editing-model', default='gpt-6-astra',
                               metavar='MODEL', help='Punctuation and layout polish model (default: gpt-6-astra); also accepts gpt-6.1-sol.')
    transcription.add_argument('--review-model', '--author-model', dest='author_model', default='gpt-6-astra',
                               metavar='MODEL', help='Review and chapter arrangement model (default: gpt-6-astra); also accepts gpt-6.1-sol.')
    transcription.add_argument('--editing-reasoning-effort', choices=REASONING_EFFORTS, default='high',
                               metavar='EFFORT', help='Polish reasoning: low, medium, or high (default: high; balanced profile: low).')
    transcription.add_argument('--review-reasoning-effort', '--author-reasoning-effort', dest='review_reasoning_effort',
                               choices=REASONING_EFFORTS, default='high', metavar='EFFORT',
                               help='Review and narrative chapter reasoning: low, medium, or high (default: high; balanced profile: medium).')
    transcription.add_argument('--text-profile', choices=TEXT_PROFILES, default='legacy', metavar='PROFILE',
                               help='legacy: keep Astra/high defaults; balanced: Sol 6.1/low polish and Sol 6.1/medium review candidate. Explicit model/effort flags override each setting.')
    transcription.add_argument('--audio-chunk-seconds', type=_positive_timeout, default=300,
                               metavar='SECONDS', help='Maximum audio seconds per transcription request, 1–600 (default: 300); keep unchanged to reuse completed transcription.')
    transcription.add_argument('--context-file', metavar='FILE', help='Private UTF-8 text file explaining the recording; sent with supported transcription requests.')
    transcription.add_argument('--glossary-file', metavar='FILE', help='Private UTF-8 list of expected terms, one per line; gpt-transcribe only.')
    transcription.add_argument('--language', action='append', default=[],
                               metavar='CODE', help='Expected lowercase language code, such as en; repeat for multilingual gpt-transcribe. Voice separation accepts one two-letter code.')
    speakers = value.add_argument_group('Speaker labels (run)')
    speakers.add_argument('--separate-speakers', '--interview', dest='interview', action='store_true',
                          help='Add a separate transcript with voice labels alongside the original. Unconfirmed voices remain unidentified.')
    speakers.add_argument('--speaker-config', type=Path,
                          metavar='FILE', help='Private per-entry display names and confirmed voice mappings; requires --separate-speakers.')
    speakers.add_argument('--speaker-model', '--interview-model', dest='interview_model',
                          metavar='MODEL', help='Voice-separation model, gpt-4o-transcribe-diarize; does not identify people. Requires --separate-speakers.')
    speakers.add_argument('--speaker-chunk-seconds', '--diarization-chunk-seconds', dest='diarization_chunk_seconds', type=_positive_timeout,
                          metavar='SECONDS', help='Audio seconds per voice-separation request, 1–600; original transcription keeps its saved settings.')
    speakers.add_argument('--confirm-speaker-mappings', action='store_true',
                          help='Confirm every supplied voice mapping against the explicitly selected speaker chunks.')
    execution = value.add_argument_group('Progress, logs, and request limits')
    display = execution.add_mutually_exclusive_group()
    display.add_argument('--progress', choices=('auto', 'plain', 'json'), default='auto',
                           metavar='MODE', help='auto: live terminal status, JSON when redirected; plain: readable scrolling lines; json: structured events (default: auto).')
    display.add_argument('--plain', dest='progress', action='store_const', const='plain',
                         help='Readable scrolling progress without colors or terminal controls; alias for --progress plain.')
    execution.add_argument('--quiet', action='store_true', help='Suppress console progress; retain private execution logs and exit status.')
    execution.add_argument('--no-color', action='store_true', help='Disable colors; also honors NO_COLOR. Live terminal updates remain available.')
    execution.add_argument('--logs-folder', '--log-directory', dest='log_directory', type=Path,
                           metavar='FOLDER', help='Private structured execution logs (default for prepare/verify/run: <batch-folder>/execution-logs).')
    execution.add_argument('--status-interval', '--heartbeat-seconds', dest='heartbeat_seconds', type=_positive_timeout, default=30,
                           metavar='SECONDS', help='Seconds between idle status updates while a step is waiting (default: 30).')
    execution.add_argument('--media-timeout', type=_positive_timeout, default=3600,
                           metavar='SECONDS', help='Maximum seconds per local FFmpeg operation (default: 3600); prepare/run.')
    execution.add_argument('--request-timeout', '--provider-timeout', dest='provider_timeout', type=_positive_timeout, default=120,
                           metavar='SECONDS', help='Seconds allowed per OpenAI network wait (default: 120); run, not a total deadline.')
    execution.add_argument('--request-retries', '--provider-retries', dest='provider_retries', type=int, choices=range(6), default=2,
                           metavar='COUNT', help='OpenAI retries per operation, 0–5 (default: 2); nonzero also allows one eligible text-validation recovery. Use 0 with request/time limits.')
    execution.add_argument('--max-requests', '--max-provider-requests', dest='max_provider_requests', type=int,
                           metavar='COUNT', help='Maximum new OpenAI operations across selected entries and steps; requires --request-retries 0. Use 1 for a limited diagnostic run.')
    execution.add_argument('--max-run-seconds', type=_positive_timeout,
                           metavar='SECONDS', help='Stop starting new requests after this many seconds; requires zero retries. Requests already running are not cancelled; omit for full runs.')
    execution.add_argument('--failure-limit', '--provider-failure-limit', dest='provider_failure_limit', type=int, default=2,
                           metavar='COUNT', help='Stop new requests after consecutive failures for one service/model (default: 2); account/configuration failures stop immediately.')
    execution.add_argument('--validation-failure-limit', type=int, default=3,
                           metavar='COUNT', help='Stop new requests after this many terminal text-validation failures in one stage/model across workers (default: 3); counts after recovery, never resets on SDK success.')
    return value


def execute(args, reporter):
    require(args.parallel_interviews >= 1 and (args.action == 'run' or args.parallel_interviews == 1),
            'Parallel interviews require run and a positive integer.')
    require((args.action == 'run' or not args.interview)
            and (args.interview or (args.speaker_config is None and args.interview_model is None
                and args.diarization_chunk_seconds is None and not args.confirm_speaker_mappings)),
            'Speaker options require run --interview.')
    require(args.action == 'run' or (args.max_provider_requests is None and args.max_run_seconds is None
            and args.provider_failure_limit == 2 and args.validation_failure_limit == 3), 'Provider controls require run.')
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
    positions = {item['position']: index for index, item in enumerate(items, 1)}
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
                validation_failure_limit=args.validation_failure_limit,
                retries=args.provider_retries)
        except ValueError:
            raise BatchError('Provider request/time limits require --provider-retries 0 and positive limits.') from None
        CURRENT_CONTROL.set(control)
        reporter.emit(status='configuration', configuration={
            'asr_model': args.model, 'editing_model': args.editing_model, 'author_model': args.author_model,
            'text_profile': args.text_profile, 'editing_reasoning_effort': args.editing_reasoning_effort,
            'review_reasoning_effort': args.review_reasoning_effort,
            'diarization_model': args.interview_model or 'gpt-4o-transcribe-diarize',
            'audio_chunk_seconds': args.audio_chunk_seconds,
            'diarization_chunk_seconds': args.diarization_chunk_seconds or args.audio_chunk_seconds,
            'provider_timeout': args.provider_timeout, 'provider_retries': args.provider_retries,
            'max_provider_requests': args.max_provider_requests, 'max_run_seconds': args.max_run_seconds,
            'provider_failure_limit': args.provider_failure_limit,
            'validation_failure_limit': args.validation_failure_limit, 'interview': args.interview,
            'parallel_interviews': args.parallel_interviews,
            'context_supplied': bool(args.context_file), 'glossary_supplied': bool(args.glossary_file),
            'language_hint_count': len(args.language)})
    rows = []
    started = set()
    def process_item(item):
        previous = reporter.context
        reporter.context = {'scope': 'batch', 'phase': phase, 'item': item['position'],
                            'batch_position': positions[item['position']], 'selected': len(items)}
        reporter.begin_session(item['position'])
        with reporter.lock:
            started.add(item['position'])
        try:
            emit_progress('preflight', 'running')
            if control and control.reason:
                status = 'not_attempted'
                requested_families = ['original']
                if args.interview_options_by_id.get(item['id']) is not None:
                    requested_families.append('attributed')
                for family in requested_families:
                    reporter.emit(status='not_attempted', family=family,
                                  stages={'preflight': 'not_attempted'})
            elif item['status'] == 'blocked':
                status = 'blocked'
            elif args.action == 'run' and args.phase == 'chapters' and not (args.human_reviewed and args.select):
                for family in (['original', 'attributed'] if args.interview_options_by_id.get(item['id']) else ['original']):
                    blocked(reporter, family, args.phase, 'human_review_required')
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
                        status = run_one(root, item, args) or 'complete'
                    else:
                        status = 'complete'
                        reporter.emit(status='progress', stage='preflight', stage_status='complete',
                                      parts=len(item['manifest']['parts']),
                                      blocked=sum(part['dataless'] for part in item['manifest']['parts']))
                except KeyboardInterrupt:
                    if control:
                        control.cancel()
                    status = 'interrupted'
                except LogError:
                    raise
                except Exception as error:
                    if type(error) is BatchError:
                        reporter.emit(status='failed', message=str(error))
                    status = ('incomplete' if control and control.reason in {'request_limit', 'start_deadline'} else 'failed')
                    if control and control.cancelled.is_set():
                        status = 'interrupted'
            row = {'item': item['position'], 'status': status,
                   'families': reporter.families(item['position']),
                   'family_blockers': reporter.blockers(item['position'])}
            if control and control.reason:
                row['stop_reason'] = control.reason
            return row
        finally:
            reporter.end_session(item['position'])
            reporter.context = previous

    def record(row):
        if reporter.failed and row['status'] not in {'interrupted', 'not_attempted'}:
            row = {**row, 'status': 'failed'}
        rows.append(row)
        counts = Counter(row['status'] for row in rows)
        if reporter.failed:
            return
        reporter.emit(**row, processed=sum(row['status'] != 'not_attempted' for row in rows), failed=counts['failed'], completed=counts['complete'],
                      finished=len(rows),
                      staged=counts['staged'], verified=counts['verified'], blocked=counts['blocked'],
                      message='One or more requested families are blocked; inspect their prerequisite events. Eligible family results are retained.' if row['status'] == 'blocked' else None)

    def unattempted(active_status='interrupted'):
        attempted = {row['item'] for row in rows}
        for item in items:
            if item['position'] not in attempted:
                active = item['position'] in started
                row = {'item': item['position'], 'status': active_status if active else 'not_attempted',
                    'family_blockers': reporter.blockers(item['position']),
                    'families': reporter.families(item['position']) if active else
                        {family: {'preflight': 'not_attempted'} for family in
                        (['original', 'attributed'] if args.interview_options_by_id.get(item['id']) else ['original'])}}
                if control and control.reason:
                    row['stop_reason'] = control.reason
                record(row)

    def process():
        if args.action == 'run' and args.parallel_interviews > 1:
            run_sessions(items, min(args.parallel_interviews, len(items)), process_item, record, control)
            unattempted()
        else:
            for item in items:
                row = process_item(item)
                record(row)
                if row['status'] == 'interrupted' or control and control.cancelled.is_set():
                    raise KeyboardInterrupt()
    if root and args.action in {'prepare', 'verify', 'run'}:
        with lock(root):
            try:
                process()
            except KeyboardInterrupt:
                unattempted()
                rows.sort(key=lambda row: row['item'])
                write_summary(root, reporter, rows, phase)
                raise
            except LogError:
                if control:
                    control.cancel()
                unattempted(active_status='failed')
                rows.sort(key=lambda row: row['item'])
                write_summary(root, reporter, rows, phase)
                raise
            rows.sort(key=lambda row: row['item'])
            write_summary(root, reporter, rows, phase)
    else:
        process()
    reporter.context = {'scope': 'batch', 'phase': phase}
    reporter.emit(status='summary', selected=len(items), processed=sum(row['status'] != 'not_attempted' for row in rows),
                  finished=len(rows),
                  completed=sum(row['status'] == 'complete' for row in rows),
                  failed=sum(row['status'] == 'failed' for row in rows),
                  blocked=sum(row['status'] == 'blocked' for row in rows),
                  not_attempted=sum(row['status'] == 'not_attempted' for row in rows),
                  incomplete=sum(row['status'] == 'incomplete' for row in rows),
                  provider_requests=control.requests if control else 0,
                  stop_reason=control.reason if control else None)
    return int(any(row['status'] in {'failed', 'blocked', 'not_attempted', 'interrupted', 'incomplete'} for row in rows))


def main(argv=None, *, studio=False):
    args = parser(studio=studio).parse_args(argv)
    reporter = Reporter(sys.stdout, heartbeat=args.heartbeat_seconds, output=args.progress,
                        quiet=args.quiet, no_color=args.no_color)
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
