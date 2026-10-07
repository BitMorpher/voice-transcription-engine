"""Allowlisted execution events; never serialize arbitrary errors or user values."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import re
from datetime import datetime, timezone
import os
import signal
import sys
from pathlib import Path
import threading
import time
import uuid

if __package__:
    from .provider_errors import CATEGORIES, GUIDANCE as ERROR_GUIDANCE, TIMEOUT_PHASES, TIMEOUT_GUIDANCE
    from .batch.prerequisites import GUIDANCE as BLOCK_GUIDANCE
    from .terminal_progress import TerminalProgress, live_capable
else:
    from provider_errors import CATEGORIES, GUIDANCE as ERROR_GUIDANCE, TIMEOUT_PHASES, TIMEOUT_GUIDANCE
    from batch.prerequisites import GUIDANCE as BLOCK_GUIDANCE
    from terminal_progress import TerminalProgress, live_capable

CURRENT = ContextVar('execution_reporter', default=None)
STAGES = {'prerequisite', 'phase_result', 'conversion', 'transcription', 'enhancement', 'author_review', 'chapters',
          'part_transcription', 'staging', 'preflight', 'verification', 'combined_raw'}
STAGES |= {'diarization', 'attribution', 'attributed_attribution', 'attributed_enhancement',
           'attributed_author_review', 'attributed_chapters'}
STATUSES = {'started', 'progress', 'running', 'complete', 'failed', 'summary', 'heartbeat',
            'skipped', 'interrupted', 'blocked', 'staged', 'verified', 'incomplete', 'pending',
            'not_attempted', 'configuration', 'latest'}
COUNTERS = {'item', 'part', 'parts', 'chunk', 'chunks', 'processed', 'failed', 'selected',
            'completed', 'blocked', 'staged', 'verified', 'interrupted', 'not_attempted', 'provider_requests', 'incomplete', 'active_sessions', 'validation_retries', 'finished', 'batch_position'}
GUIDANCE = ('Check local input permissions, media validity, output space and cache integrity; '
            'for provider stages check OPENAI_API_KEY, model access, quota and connectivity. '
            'Completed caches are retained; repeat interview batch run with matching settings, or use --resume with interview transcribe. '
            'Chapter runs require complete review and explicit human approval.')

SAFE_GUIDANCE = {
    'One or more requested families are blocked; inspect their prerequisite events. Eligible family results are retained.',
    'Speaker options require run --interview.',
    'Provider controls require run.',
    'Parallel interviews require run and a positive integer.',
    'Provider request/time limits require --provider-retries 0 and positive limits.',
    'Speaker mapping confirmation requires explicit diarization chunks.',
    'Invalid private speaker configuration; use version 1, known entry IDs, optional display names and scoped confirmed mappings.',
    'Staged video could not be validated; retain partial staging and use a fresh batch after correcting the input.',
    'Attributed stages require matching complete raw and chapters require an intact reviewed bundle without high findings.',
    'Provider calls require --send-to-openai.',
    'Chapters require --human-reviewed and explicit --select IDs.',
    'Staging reads/copies sources; supply --copy-local-files and a fresh --batch.',
    'Staging requires a fresh batch directory.',
    'Install FFmpeg and ffprobe on PATH before staging/processing.',
    'Use --plan for metadata/staging or --batch for staged operations.',
    'Unknown batch selection.', 'Selection is empty.',
    'Offline sources require --allow-hydration before staging.',
    'Source changed after metadata checks; refresh the private plan.',
    'Batch is locked; check for an active run before inspecting a stale lock.',
    'Review needs completed raw; chapters need intact complete review without high findings.',
    'Staged media changed; retain artifacts and use fresh staging.',
    'Batch snapshot changed; use a fresh staging directory.',
    'Cannot read batch plan metadata; check JSON schema and local source access.',
    'Local execution logging failed; check permissions and free space.',
    'Set OPENAI_API_KEY in your environment before transcribing.',
    'Approved review changed, conflicts, or does not match this generation; '
    'retain outputs and review the exact bundle again before drafting.',
}


def safe_configuration(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    models = {'asr_model': {'gpt-transcribe', 'whisper-1', 'gpt-4o-transcribe', 'gpt-4o-mini-transcribe'},
              'diarization_model': {'gpt-4o-transcribe-diarize'},
              'editing_model': {'gpt-6-astra', 'gpt-6.1-sol'}, 'author_model': {'gpt-6-astra', 'gpt-6.1-sol'}}
    durations = {'audio_chunk_seconds', 'diarization_chunk_seconds', 'provider_timeout', 'max_run_seconds'}
    counts = {'provider_retries', 'max_provider_requests', 'provider_failure_limit', 'language_hint_count', 'parallel_interviews'}
    flags = {'interview', 'context_supplied', 'glossary_supplied'}
    for key, data in value.items():
        if key in models and isinstance(data, str) and data in models[key]:
            result[key] = data
        elif key in durations and type(data) in (int, float) and math.isfinite(data) and data > 0:
            result[key] = data
        elif key in counts and type(data) is int and data >= 0:
            result[key] = data
        elif key in flags and type(data) is bool:
            result[key] = data
        elif key in {'max_run_seconds', 'max_provider_requests'} and data is None:
            result[key] = None
    return result


def safe_families(value):
    if not isinstance(value, dict):
        return {}
    return {family: {stage: status for stage, status in stages.items()
                     if stage in STAGES and isinstance(status, str) and status in STATUSES}
            for family, stages in value.items()
            if family in {'original', 'attributed'} and isinstance(stages, dict)}


def safe_blockers(value):
    if not isinstance(value, dict):
        return {}
    return {family: reason for family, reason in value.items()
            if family in {'original', 'attributed'} and isinstance(reason, str) and reason in BLOCK_GUIDANCE}


def emit_progress(stage, status, **counters):
    reporter = CURRENT.get()
    if reporter is not None:
        reporter.emit(status='progress', stage=stage, stage_status=status, **counters)


class LogError(RuntimeError):
    """Fixed local logging failure, without paths or original exception text."""


class Reporter:
    """Thread-safe private JSON logs and selectable sanitized console reporting."""
    def __init__(self, stream, *, heartbeat=30, output='json'):
        self.stream, self.heartbeat = stream, heartbeat
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started = time.monotonic()
        self.last = self.started
        self.sequence = 0
        self._context = threading.local()
        self.item_stages = {}
        self.item_blockers = {}
        self.configuration = {}
        self.active = {}
        self.sessions = {}
        self.log = None
        self.failed = False
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.run = uuid.uuid4().hex
        self.output = None
        self._renderer = None
        self.set_output(output)

    def set_output(self, mode):
        """Use JSON by default; CLI auto mode opts capable terminals into live status."""
        if mode not in {'auto', 'plain', 'json'}:
            raise ValueError('Progress output must be auto, plain or json.')
        with self.lock:
            if mode == self.output:
                return
            if self._renderer is not None:
                try:
                    self._renderer.close()
                except (OSError, ValueError):
                    pass
            self.output = mode
            live = mode == 'auto' and live_capable(self.stream)
            self._renderer = (TerminalProgress(self.stream, live=live)
                              if self.stream is not None and (mode == 'plain' or live) else None)

    @property
    def context(self):
        """Immutable-by-convention context local to each session worker."""
        return getattr(self._context, 'value', {})

    @context.setter
    def context(self, value):
        self._context.value = dict(value)

    def begin_session(self, item):
        with self.lock:
            self.sessions[item] = (time.monotonic(), {**self.context, 'item': item})

    def end_session(self, item):
        with self.lock:
            self.sessions.pop(item, None)
            if self.active.get('item') == item:
                self.active = {}

    def families(self, item):
        """Snapshot stage state without exposing mutable shared dictionaries."""
        with self.lock:
            return {family: dict(stages) for family, stages in self.item_stages.get(item, {}).items()}

    def blockers(self, item):
        with self.lock:
            return dict(self.item_blockers.get(item, {}))

    def start(self, directory):
        if self.log is not None:
            return
        # Reuse the engine's repository/symlink restrictions and private permissions.
        if __package__:
            from .private_output import output_directory
        else:
            from private_output import output_directory
        root = output_directory(Path(directory))
        fd = os.open(root / ('execution-' + self.run + '.jsonl'),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.log = os.fdopen(fd, 'w', encoding='utf-8')
        self.emit(status='started')
        self.thread = threading.Thread(target=self._heartbeats, daemon=True)
        self.thread.start()

    def emit(self, **details):
        with self.lock:
            if self.failed and details.get('status') not in {'failed', 'interrupted'}:
                raise LogError('Local execution logging failed; check permissions and free space.')
            now = time.monotonic()
            event = {'run': self.run, 'sequence': self.sequence + 1,
                     'elapsed_seconds': round(now - self.started, 3)}
            for key, value in (self.context | details).items():
                if key in COUNTERS and type(value) is int and value >= 0:
                    event[key] = value
                elif key in {'status', 'stage_status'} and isinstance(value, str) and value in STATUSES:
                    event[key] = value
                elif key == 'stage' and isinstance(value, str) and value in STAGES:
                    event[key] = value
                elif key == 'error_category' and isinstance(value, str) and value in CATEGORIES:
                    event[key] = value
                    event['guidance'] = ERROR_GUIDANCE[value]
                elif key == 'timeout_phase' and isinstance(value, str) and value in TIMEOUT_PHASES:
                    event[key] = value
                elif key == 'blocked_reason' and isinstance(value, str) and value in BLOCK_GUIDANCE:
                    event[key] = value
                    event['guidance'] = BLOCK_GUIDANCE[value]
                elif key == 'family_blockers':
                    event[key] = safe_blockers(value)
                elif key == 'http_status' and type(value) is int and 100 <= value <= 599:
                    event[key] = value
                elif key == 'scope' and isinstance(value, str) and value in {'batch', 'interview'}:
                    event[key] = value
                elif key == 'family' and isinstance(value, str) and value in {'original', 'attributed'}:
                    event[key] = value
                elif key == 'phase' and isinstance(value, str) and value in {'raw', 'review', 'chapters', 'inventory', 'check', 'prepare', 'verify', 'status'}:
                    event[key] = value
                elif key == 'message' and isinstance(value, str) and value in SAFE_GUIDANCE:
                    event['guidance'] = value
                elif key in {'historical_run', 'latest_run'} and isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value):
                    event[key] = value
                elif key == 'started_at' and isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+[+]00:00', value):
                    event[key] = value
                elif key == 'stop_reason' and isinstance(value, str) and value in {'request_limit', 'start_deadline', 'provider_failures', 'systemic_provider', 'interrupted'}:
                    event[key] = value
                elif key == 'effective_provider_timeout' and type(value) in (int, float) and math.isfinite(value) and value > 0:
                    event[key] = value
                elif key == 'configuration':
                    event[key] = safe_configuration(value)
                    if details.get('status') == 'configuration':
                        self.configuration = event[key]
                elif key == 'families':
                    event[key] = safe_families(value)
                elif key == 'recorded_status' and isinstance(value, str) and value in STATUSES:
                    event[key] = value
                elif key == 'stages' and isinstance(value, dict):
                    event[key] = {k: v for k, v in value.items()
                                  if k in STAGES and isinstance(v, str) and v in STATUSES}
            item = event.get('item')
            if type(item) is int and item > 0:
                families = self.item_stages.setdefault(item, {})
                def remember(stage, status):
                    if stage.startswith('attributed_'):
                        family, stage = 'attributed', stage.removeprefix('attributed_')
                    else:
                        family = event.get('family', 'original')
                    if stage in {'prerequisite', 'phase_result', 'conversion', 'transcription', 'enhancement', 'author_review', 'chapters', 'combined_raw', 'diarization', 'attribution'} or stage == 'preflight' and status == 'not_attempted':
                        families.setdefault(family, {})[stage] = status
                if 'stage' in event and 'stage_status' in event:
                    remember(event['stage'], event['stage_status'])
                for stage, status in event.get('stages', {}).items():
                    remember(stage, status)
                if 'blocked_reason' in event and event.get('family') in {'original', 'attributed'}:
                    self.item_blockers.setdefault(item, {})[event['family']] = event['blocked_reason']
            if event.get('error_category') == 'timeout' and 'timeout_phase' in event:
                event['guidance'] = TIMEOUT_GUIDANCE[event['timeout_phase']]
            else:
                event.pop('timeout_phase', None)
            if event.get('stage_status') == 'skipped':
                event['cache_reused'] = True
            if event.get('status') in {'failed', 'blocked'} or event.get('stage_status') == 'failed':
                event.setdefault('guidance', GUIDANCE)
            if event.get('status') == 'heartbeat':
                last = self.sessions[item][0] if item in self.sessions else self.last
                event['idle_seconds'] = round(now - last, 3)
            else:
                self.last = now
                if 'stage' in event and event['stage'] != self.active.get('stage'):
                    self.active.pop('chunk', None)
                    self.active.pop('chunks', None)
                    if event.get('stage') not in {'conversion', 'part_transcription', 'transcription', 'diarization', 'staging'}:
                        self.active.pop('part', None)
                        self.active.pop('parts', None)
                self.active.update({k: event[k] for k in ('scope', 'phase', 'family', 'selected',
                    'batch_position', 'stage', 'stage_status', 'item', 'part', 'parts', 'chunk', 'chunks') if k in event})
                if 'stage' in event:
                    self.active['family'] = event.get('family',
                        'attributed' if event['stage'].startswith('attributed_') else 'original')
                if item in self.sessions:
                    _, active = self.sessions[item]
                    if 'stage' in event and event['stage'] != active.get('stage'):
                        active.pop('chunk', None)
                        active.pop('chunks', None)
                        if event['stage'] not in {'conversion', 'part_transcription', 'transcription', 'diarization', 'staging'}:
                            active.pop('part', None)
                            active.pop('parts', None)
                    active.update({k: event[k] for k in ('scope', 'phase', 'stage', 'stage_status',
                        'family', 'item', 'part', 'parts', 'chunk', 'chunks') if k in event})
                    active['family'] = event.get('family', 'original')
                    self.sessions[item] = (now, active)
            self.sequence += 1
            line = json.dumps(event, sort_keys=True) + '\n'
            # Persist before console delivery. A closed pipe must not lose the local log.
            if self.log is not None:
                try:
                    self.log.write(line)
                    self.log.flush()
                except (OSError, ValueError):
                    self.failed = True
                    try:
                        self.log.close()
                    except (OSError, ValueError):
                        pass
                    self.log = None
                    if self._renderer is not None:
                        try:
                            self._renderer.suspend()
                        except (OSError, ValueError):
                            pass
                    raise LogError('Local execution logging failed; check permissions and free space.') from None
            if self.stream is not None:
                try:
                    if self._renderer is not None:
                        self._renderer.emit(event)
                    else:
                        self.stream.write(line)
                        self.stream.flush()
                except (OSError, ValueError):
                    if self._renderer is not None:
                        try:
                            self._renderer.close()
                        except (OSError, ValueError):
                            pass
                    if self.stream is sys.stdout:
                        sys.stdout = open(os.devnull, 'w')
                    self.stream = None
            return event

    def _heartbeats(self):
        while not self.stop.wait(self.heartbeat):
            with self.lock:
                try:
                    if self.sessions:
                        for last, active in self.sessions.values():
                            if time.monotonic() - last >= self.heartbeat:
                                self.emit(status='heartbeat', active_sessions=len(self.sessions), **active)
                    elif time.monotonic() - self.last >= self.heartbeat:
                        self.emit(status='heartbeat', **self.active)
                except LogError:
                    self.stop.set()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        if self._renderer is not None:
            try:
                self._renderer.close()
            except (OSError, ValueError):
                pass
        if self.log is not None:
            try:
                self.log.close()
            except (OSError, ValueError):
                self.failed = True
            self.log = None


@contextmanager
def interruptions():
    """Clean up locks/logs on SIGTERM; never install handlers in worker threads."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
