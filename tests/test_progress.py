"""Privacy, buffering, idle waits, transport bounds and signal cleanup."""

import io
import json
import signal
import threading
import time

import pytest

from src.progress import CURRENT, Reporter, emit_progress, interruptions
from src.transcriber import ConfigurationError, Transcriber


def events(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_allowlist_and_durable_log(tmp_path):
    stream = io.StringIO()
    reporter = Reporter(stream)
    reporter.start(tmp_path / 'logs')
    reporter.emit(status='failed', message='SYNTHETIC_SECRET /private/source transcript',
                  job='identifying-name', stage='author_review', chunk=2,
                  credential='SYNTHETIC_SECRET', stages={'author_review': 'failed', 'SYNTHETIC_SECRET': 'failed'})
    reporter.emit(status='progress', stage='transcription', stage_status='skipped', part=1)
    reporter.close()
    text = stream.getvalue()
    assert 'SYNTHETIC_SECRET' not in text and 'identifying-name' not in text
    assert '/private/source' not in text and 'message' not in events(stream)[1]
    assert events(stream)[-1]['cache_reused'] is True
    log = next((tmp_path / 'logs').glob('*.jsonl'))
    assert log.read_text() == text
    assert log.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / 'logs').stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('value', ['secret', ['secret'], {'secret': 1}, True, -1, 1.5, None])
def test_untrusted_fields_are_not_serialized(value):
    stream = io.StringIO()
    reporter = Reporter(stream)
    reporter.emit(status=value, stage=value, chunk=value, item=value)
    assert 'secret' not in stream.getvalue()
    assert 'chunk' not in events(stream)[0]


def test_heartbeat_context_elapsed_and_stop(tmp_path):
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0.01)
    reporter.start(tmp_path / 'logs')
    token = CURRENT.set(reporter)
    try:
        emit_progress('part_transcription', 'running', part=2, parts=3)
        emit_progress('author_review', 'running', chunk=4)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not any(row['status'] == 'heartbeat' for row in events(stream)):
            time.sleep(0.01)
    finally:
        CURRENT.reset(token)
        reporter.close()
    heartbeats = [row for row in events(stream) if row['status'] == 'heartbeat']
    assert heartbeats and heartbeats[0]['chunk'] == 4
    assert 'part' not in heartbeats[0] and 'parts' not in heartbeats[0]
    assert heartbeats[0]['idle_seconds'] >= 0.01
    assert [row['sequence'] for row in events(stream)] == list(range(1, len(events(stream)) + 1))
    assert all('percent' not in row for row in events(stream))
    assert not reporter.thread.is_alive()


def test_closed_console_keeps_logging(tmp_path):
    class Closed:
        def write(self, line):
            raise BrokenPipeError('SYNTHETIC_SECRET')
    reporter = Reporter(Closed())
    reporter.start(tmp_path / 'logs')
    reporter.emit(status='complete', chunk=1)
    reporter.close()
    assert 'complete' in next((tmp_path / 'logs').glob('*.jsonl')).read_text()


def test_parallel_events_are_complete_json():
    stream = io.StringIO()
    reporter = Reporter(stream)
    threads = [threading.Thread(target=lambda: [reporter.emit(status='progress', chunk=i) for i in range(20)])
               for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(events(stream)) == 60
    assert len({row['sequence'] for row in events(stream)}) == 60


def test_sigterm_handler_restored():
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt):
        with interruptions():
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    assert signal.getsignal(signal.SIGTERM) == before


@pytest.mark.parametrize('timeout,retries', [(True, 2), (False, 2), (0, 2), (float('inf'), 2), (float('nan'), 2), (120, -1), (120, 6)])
def test_transport_bounds(provider, timeout, retries):
    with pytest.raises(ConfigurationError):
        Transcriber(client=provider, provider_timeout=timeout, provider_retries=retries)


def test_sdk_timeout_and_retries(monkeypatch):
    import openai
    seen = {}
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: seen.update(kwargs) or object())
    Transcriber(provider_timeout=45, provider_retries=0)
    assert seen == {'timeout': 45, 'max_retries': 0}


def test_log_failure_has_no_traceback_or_private_values(tmp_path):
    from src.progress import LogError
    class Full:
        def write(self, line):
            raise OSError('SYNTHETIC_SECRET /private/log')
        def close(self):
            raise OSError('SYNTHETIC_SECRET')
    reporter = Reporter(io.StringIO())
    reporter.log = Full()
    with pytest.raises(LogError) as failure:
        reporter.emit(status='complete')
    assert 'SYNTHETIC_SECRET' not in str(failure.value)
    assert reporter.failed
    reporter.emit(status='failed', message=str(failure.value))
    reporter.close()
    assert 'SYNTHETIC_SECRET' not in reporter.stream.getvalue()


def test_review_per_chunk_events_and_privacy():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.author_review import ReviewOptions, review_transcript
    client = MagicMock()
    def respond(**kwargs):
        supplied = json.loads(kwargs['messages'][-1]['content'])
        body = {'chunk_index': supplied['chunk_index'], 'fully_reviewed': True,
                'reviewed_start': supplied['core_start'], 'reviewed_end': supplied['core_end'], 'findings': []}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content=json.dumps(body), refusal=None))])
    client.chat.completions.create.side_effect = respond
    stream = io.StringIO()
    reporter = Reporter(stream)
    token = CURRENT.set(reporter)
    try:
        report = review_transcript('Synthetic secret testimony. ' * 10, client, ReviewOptions(chunk_bytes=64))
    finally:
        CURRENT.reset(token)
    rows = events(stream)
    assert report['status'] == 'complete' and len(rows) >= 8
    assert [row['stage_status'] for row in rows] == ['running', 'complete'] * (len(rows) // 2)
    assert all(row['chunks'] == len(rows) // 2 for row in rows)
    assert 'testimony' not in stream.getvalue()


@pytest.mark.parametrize('failure,category', [
    ('properties', 'completion'), ('count', 'completion'), ('refused', 'completion'),
    ('truncated', 'completion'), ('malformed', 'validation_schema'), ('coverage', 'validation_coverage'),
])
def test_chapter_failures_emit_terminal_safe_chunk_event(failure, category):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.author_review import source_segments
    from src.chapters import ChapterError, ChapterOptions, _narrative
    raw = 'Synthetic private testimony.'
    if failure == 'properties':
        class Unreadable:
            @property
            def choices(self):
                raise RuntimeError('SYNTHETIC_SECRET response property')
        response = Unreadable()
    elif failure == 'count':
        response = SimpleNamespace(choices=[])
    else:
        body = {'chunk_index': 1, 'passages': [], 'coverage_omissions': []}
        content = 'SYNTHETIC_SECRET malformed payload' if failure == 'malformed' else json.dumps(body)
        response = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason='length' if failure == 'truncated' else 'stop',
            message=SimpleNamespace(content=content, refusal='SYNTHETIC_SECRET' if failure == 'refused' else None))])
    client = MagicMock()
    client.chat.completions.create.return_value = response
    stream = io.StringIO()
    reporter = Reporter(stream)
    token = CURRENT.set(reporter)
    try:
        with pytest.raises(ChapterError):
            _narrative(raw, source_segments(raw), [], client, ChapterOptions())
    finally:
        CURRENT.reset(token)
    rows = events(stream)
    assert [row['stage_status'] for row in rows] == ['running', 'failed']
    assert rows[-1]['chunk'] == 1 and rows[-1]['error_category'] == category
    assert 'SYNTHETIC_SECRET' not in stream.getvalue() and raw not in stream.getvalue()
