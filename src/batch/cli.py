"""Installed batch CLI. Plans and staging data remain private and external to Git."""

from collections import Counter
from pathlib import Path
import shutil
import sys

from ..ordered_interview import _local
from ..cli import PrivateArgumentParser, _positive_timeout
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
    value.add_argument('--model', default='gpt-transcribe')
    value.add_argument('--editing-model', default='gpt-6-astra')
    value.add_argument('--author-model', default='gpt-6-astra')
    value.add_argument('--audio-chunk-seconds', type=_positive_timeout, default=300)
    value.add_argument('--media-timeout', type=_positive_timeout, default=3600)
    value.add_argument('--provider-timeout', type=_positive_timeout, default=120)
    value.add_argument('--provider-retries', type=int, choices=range(6), default=2)
    display = value.add_mutually_exclusive_group()
    display.add_argument('--progress', choices=('auto', 'plain', 'json'), default='auto',
                         help='Progress output: live on terminals, JSON when redirected (default: auto).')
    display.add_argument('--plain', dest='progress', action='store_const', const='plain',
                         help='Append-only readable progress; no colors or terminal controls.')
    value.add_argument('--quiet', action='store_true', help='Suppress console progress; retain private JSONL logs.')
    value.add_argument('--no-color', action='store_true', help='Disable colors (also honors NO_COLOR).')
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
            and (args.interview or (args.speaker_config is None and args.interview_model is None)),
            'Speaker options require run --interview.')
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
            reporter.emit(status='summary', **summary)
        return 0
    rows = []
    def process():
        for item in items:
            reporter.context = {'scope': 'batch', 'phase': phase, 'item': item['position'], 'selected': len(items)}
            reporter.active = {}
            emit_progress('preflight', 'running')
            if item['status'] == 'blocked':
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
                except LogError:
                    raise
                except Exception as error:
                    if type(error) is BatchError:
                        reporter.emit(status='failed', message=str(error))
                    status = 'failed'
            row = {'item': item['position'], 'status': status}
            rows.append(row)
            counts = Counter(row['status'] for row in rows)
            reporter.emit(**row, processed=len(rows), failed=counts['failed'], completed=counts['complete'],
                          staged=counts['staged'], verified=counts['verified'], blocked=counts['blocked'])
    if root and args.action in {'prepare', 'verify', 'run'}:
        with lock(root):
            try:
                process()
            except KeyboardInterrupt:
                rows.append({'item': reporter.context.get('item', 0), 'status': 'interrupted'})
                write_summary(root, reporter, rows, phase)
                raise
            write_summary(root, reporter, rows, phase)
    else:
        process()
    reporter.context = {'scope': 'batch', 'phase': phase}
    reporter.emit(status='summary', selected=len(items), processed=len(rows),
                  failed=sum(row['status'] == 'failed' for row in rows),
                  blocked=sum(row['status'] == 'blocked' for row in rows))
    return int(any(row['status'] in {'failed', 'blocked'} for row in rows))


def main(argv=None):
    args = parser().parse_args(argv)
    reporter = Reporter(sys.stdout, heartbeat=args.heartbeat_seconds)
    reporter.context = {'scope': 'batch'}
    reporter.configure_console(mode=args.progress, no_color=args.no_color, quiet=args.quiet)
    token = CURRENT.set(reporter)
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
    return code or int(reporter.failed)


if __name__ == '__main__':
    sys.exit(main())
