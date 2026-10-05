"""Allowlisted execution events; never serialize arbitrary errors or user values."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import signal
import sys
from pathlib import Path
import threading
import time
import uuid

if __package__:
    from .provider_errors import CATEGORIES, GUIDANCE as ERROR_GUIDANCE
else:
    from provider_errors import CATEGORIES, GUIDANCE as ERROR_GUIDANCE

CURRENT = ContextVar('execution_reporter', default=None)
STAGES = {'conversion', 'transcription', 'enhancement', 'author_review', 'chapters',
          'part_transcription', 'staging', 'preflight', 'verification', 'combined_raw'}
STAGES |= {'diarization', 'attribution', 'attributed_attribution', 'attributed_enhancement',
           'attributed_author_review', 'attributed_chapters'}
STATUSES = {'started', 'progress', 'running', 'complete', 'failed', 'summary', 'heartbeat',
            'skipped', 'interrupted', 'blocked', 'staged', 'verified', 'incomplete', 'pending'}
COUNTERS = {'item', 'part', 'parts', 'chunk', 'chunks', 'processed', 'failed', 'selected',
            'completed', 'blocked', 'staged', 'verified', 'interrupted'}
GUIDANCE = ('Check local input permissions, media validity, output space and cache integrity; '
            'for provider stages check OPENAI_API_KEY, model access, quota and connectivity. '
            'Completed caches are retained; retry with matching inputs and --resume. '
            'Chapter runs require complete review and explicit human approval.')

SAFE_GUIDANCE = {
    'Speaker options require run --interview.',
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


def emit_progress(stage, status, **counters):
    reporter = CURRENT.get()
    if reporter is not None:
        reporter.emit(status='progress', stage=stage, stage_status=status, **counters)


class LogError(RuntimeError):
    """Fixed local logging failure, without paths or original exception text."""


class Reporter:
    """Thread-safe JSONL console/file reporting with an honest idle heartbeat."""
    def __init__(self, stream, *, heartbeat=30):
        self.stream, self.heartbeat = stream, heartbeat
        self.started = time.monotonic()
        self.last = self.started
        self.sequence = 0
        self.context = {}
        self.active = {}
        self.log = None
        self.failed = False
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.run = uuid.uuid4().hex

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
                elif key == 'stages' and isinstance(value, dict):
                    event[key] = {k: v for k, v in value.items()
                                  if k in STAGES and isinstance(v, str) and v in STATUSES}
            if event.get('stage_status') == 'skipped':
                event['cache_reused'] = True
            if event.get('status') in {'failed', 'blocked'} or event.get('stage_status') == 'failed':
                event.setdefault('guidance', GUIDANCE)
            if event.get('status') == 'heartbeat':
                event['idle_seconds'] = round(now - self.last, 3)
            else:
                self.last = now
                if event.get('stage') != self.active.get('stage'):
                    self.active.pop('chunk', None)
                self.active.update({k: event[k] for k in ('stage', 'stage_status', 'item', 'part', 'parts', 'chunk', 'chunks') if k in event})
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
                    raise LogError('Local execution logging failed; check permissions and free space.') from None
            if self.stream is not None:
                try:
                    self.stream.write(line)
                    self.stream.flush()
                except (OSError, ValueError):
                    if self.stream is sys.stdout:
                        sys.stdout = open(os.devnull, 'w')
                    self.stream = None
            return event

    def _heartbeats(self):
        while not self.stop.wait(self.heartbeat):
            with self.lock:
                if time.monotonic() - self.last >= self.heartbeat:
                    try:
                        self.emit(status='heartbeat', **self.active)
                    except LogError:
                        self.stop.set()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
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
