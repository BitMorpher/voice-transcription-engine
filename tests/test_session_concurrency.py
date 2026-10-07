"""Deterministic concurrency contracts using invented PCM and fake providers."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import io
import json
import shutil
import signal
import threading
from types import SimpleNamespace
import wave

import httpx2
import openai
import pytest

from src.batch.cli import main, parser
from src.batch.plan import load_plan
from src.batch.storage import save_snapshot, stage, summaries
from src.progress import CURRENT, LogError, Reporter, emit_progress
from src.provider_control import CURRENT_CONTROL, ProviderControl, ProviderStopped
from src.transcriber import Transcriber


@pytest.fixture
def batch(tmp_path, monkeypatch):
    entries = []
    for item in range(1, 6):
        parts = []
        for part in (1, 2):
            path = tmp_path / f'synthetic-{item}-{part}.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
                wav.writeframes((item * 10 + part).to_bytes(2, 'little') * 20)
            parts.append({'id': f'part-{part}', 'path': path.name, 'media_type': 'audio'})
        path = tmp_path / f'ordered-{item}.json'
        path.write_text(json.dumps({'version': 1, 'interview_id': f'synthetic-{item}', 'parts': parts}))
        entries.append({'id': f'entry-{item}', 'manifest': path.name})
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'version': 1, 'interviews': entries}))
    document = load_plan(plan)
    root = save_snapshot(tmp_path / 'batch', document, document['interviews'])
    for item in document['interviews']:
        stage(root, item, progress=lambda *args, **kwargs: None)
    monkeypatch.setattr('src.pipeline.prepare_audio', lambda source, target, **kwargs:
                        shutil.copyfile(source, target))
    monkeypatch.setattr('src.cli.require_ffmpeg', lambda: None)
    monkeypatch.setattr('src.batch.cli.shutil.which', lambda _: 'synthetic-tool')
    return root


def run(root, workers=2, *flags):
    return main(['run', '--batch', str(root), '--send-to-openai',
                 '--parallel-interviews', str(workers), '--provider-retries', '0', *flags])


def latest(root):
    return json.loads(max(root.glob('summary-*.json'), key=lambda path: path.stat().st_mtime_ns).read_text())


def artifacts(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*')
            if path.is_file() and 'output' in path.parts}


class FakeProvider:
    def __init__(self, action=None):
        self.action = action
        self.calls = []
        self.active = self.peak = 0
        self.lock = threading.Lock()
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.respond))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.edit))

    def respond(self, **parameters):
        item = CURRENT.get().context['item']
        with wave.open(parameters['file'][1], 'rb') as wav:
            seed = int.from_bytes(wav.readframes(1), 'little')
            duration = wav.getnframes() / wav.getframerate()
        model = parameters['model']
        with self.lock:
            self.calls.append((item, seed, model))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.action:
                self.action(item, seed, model)
            text = f'Synthetic words {seed}.'
            if model == 'gpt-4o-transcribe-diarize':
                return {'text': text, 'segments': [dict(text=text, speaker='A', start=0, end=duration)]}
            return SimpleNamespace(text=text)
        finally:
            with self.lock:
                self.active -= 1

    def edit(self, **parameters):
        body = json.loads(parameters['messages'][-1]['content'])
        name = parameters['response_format']['json_schema']['name']
        if name == 'faithful_transcript_edit':
            result = dict(chunk_index=body['chunk_index'], text=body['text'], speaker_uncertain=False)
        elif name == 'faithful_turn_group_edit':
            result = dict(group_index=body['group_index'], edits=[{**turn, 'speaker_uncertain': False} for turn in body['turns']])
        elif name == 'source_grounded_author_review':
            body['text'] = ''.join(p['text'] for p in body['evidence_pieces'])
            result = dict(chunk_index=body['chunk_index'], fully_reviewed=True,
                          contract_version=body['contract_version'], reviewed_piece_ids=body['core_piece_ids'], findings=[])
        else:
            result = dict(chunk_index=body['chunk_index'], passages=[dict(unit_ids=[unit['unit_id']],
                text=unit['text'].strip(), kind='verbatim_excerpt') for unit in body['source_units']], coverage_omissions=[])
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps(result), refusal=None))])


def install(monkeypatch, provider):
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))


def test_overlap_ceiling_order_isolation_and_byte_identical_resume(batch, monkeypatch, capsys):
    barrier = threading.Barrier(2)
    def overlap(item, seed, model):
        if item <= 2 and seed % 10 == 1 and model != 'gpt-4o-transcribe-diarize':
            barrier.wait(timeout=5)
    provider = FakeProvider(overlap)
    install(monkeypatch, provider)
    assert run(batch, 2, '--interview') == 0
    assert provider.peak == 2
    assert len(provider.calls) == 20
    for item in range(1, 6):
        calls = [(seed, model) for position, seed, model in provider.calls if position == item]
        assert calls == [(item * 10 + part, model) for model in ('gpt-transcribe', 'gpt-4o-transcribe-diarize') for part in (1, 2)]
        output = batch / f'item-{item:04d}/output'
        original = next((output / 'interviews').rglob('transcription.txt')).read_text()
        assert original == f'Synthetic words {item * 10 + 1}.\n\nSynthetic words {item * 10 + 2}.'
        attributed = next((output / 'attributed').rglob('transcription.txt')).read_text()
        assert f'words {item * 10 + 1}' in attributed and f'words {item * 10 + 2}' in attributed
        assert not list(output.rglob('.lock'))
    before = artifacts(batch)
    assert run(batch, 3, '--interview') == 0
    assert artifacts(batch) == before and len(provider.calls) == 20
    report = latest(batch)
    assert [row['item'] for row in report['items']] == [1, 2, 3, 4, 5]
    assert report['configuration']['parallel_interviews'] == 3
    assert all(set(row['families']) == {'original', 'attributed'} for row in report['items'])
    output = capsys.readouterr().out
    assert str(batch) not in output and 'Synthetic words' not in output and 'entry-' not in output
    for log in (batch / 'execution-logs').glob('*.jsonl'):
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert [row['sequence'] for row in rows] == list(range(1, len(rows) + 1))
        assert log.stat().st_mode & 0o777 == 0o600
        assert all(row.get('item') in range(1, 6) for row in rows if 'item' in row)
    assert not list(batch.rglob('*.lock'))


def test_batch_request_allowance_is_not_multiplied_and_resume_only_missing(batch, monkeypatch):
    barrier = threading.Barrier(3)
    provider = FakeProvider(lambda item, seed, model: barrier.wait(timeout=5))
    install(monkeypatch, provider)
    assert run(batch, 3, '--max-provider-requests', '3') == 1
    assert len(provider.calls) == provider.peak == 3
    assert [row['status'] for row in latest(batch)['items']] == ['incomplete'] * 3 + ['not_attempted'] * 2
    saved = artifacts(batch)
    provider.action = None
    assert run(batch, 2) == 0
    assert len(provider.calls) == 10  # ten parts total, including three retained checkpoints
    after = artifacts(batch)
    assert all(after[key] == value for key, value in saved.items() if not key.endswith('manifest.json'))


@pytest.mark.parametrize('systemic', [False, True])
def test_shared_failure_stop_allows_only_already_admitted_calls(batch, monkeypatch, systemic):
    barrier = threading.Barrier(2)
    request = httpx2.Request('POST', 'https://example.invalid')
    def failure(item, seed, model):
        barrier.wait(timeout=5)
        if systemic:
            raise openai.AuthenticationError('SYNTHETIC_PRIVATE',
                response=httpx2.Response(401, request=request), body=None)
        raise openai.APITimeoutError(request=request)
    provider = FakeProvider(failure)
    install(monkeypatch, provider)
    assert run(batch, 2) == 1
    assert len(provider.calls) == 2
    report = latest(batch)
    assert [row['status'] for row in report['items']] == ['failed'] * 2 + ['not_attempted'] * 3
    assert all(row['stop_reason'] == ('systemic_provider' if systemic else 'provider_failures') for row in report['items'][2:])
    assert all(row['families']['original'] == {'preflight': 'not_attempted'} for row in report['items'][2:])


def test_local_failure_isolated_without_provider_charge_for_tampered_item(batch, monkeypatch):
    provider = FakeProvider()
    install(monkeypatch, provider)
    (batch / 'item-0002/input/part-0001.wav').write_bytes(b'SYNTHETIC_TAMPERING')
    assert run(batch, 3) == 1
    assert [row['status'] for row in latest(batch)['items']] == ['complete', 'failed', 'complete', 'complete', 'complete']
    assert len(provider.calls) == 8 and all(item != 2 for item, _, _ in provider.calls)


def test_parallel_review_and_chapter_gates_keep_approved_bytes(batch, monkeypatch):
    provider = FakeProvider()
    install(monkeypatch, provider)
    assert run(batch, 2, '--interview', '--phase', 'review') == 1
    assert provider.calls == []
    assert run(batch, 2, '--interview') == 0
    assert run(batch, 2, '--interview', '--phase', 'review') == 0
    before = {path: path.read_bytes() for path in batch.rglob('review_report.*')}
    # No chapter requests are allowed without explicit human selection/approval.
    assert run(batch, 2, '--interview', '--phase', 'chapters', '--human-reviewed') == 1
    assert not list(batch.rglob('chapter_drafts.json'))
    assert run(batch, 2, '--interview', '--phase', 'chapters', '--human-reviewed',
               '--select', 'entry-1', '--select', 'entry-2', '--chapters', 'interview') == 0
    assert len(list(batch.rglob('chapter_drafts.json'))) == 4
    assert all(path.read_bytes() == data for path, data in before.items())
    assert len(provider.calls) == 20


@pytest.mark.parametrize('signals', [1, 2])
def test_main_signal_drains_active_checkpoint_before_unlock_and_no_new_requests(batch, monkeypatch, signals):
    entered = threading.Barrier(3)
    release = threading.Event()
    first_entered = threading.Event()
    control_holder = []
    def pending(item, seed, model):
        control = CURRENT_CONTROL.get()
        control_holder.append(control)
        first_entered.set()
        entered.wait(timeout=5)
        assert release.wait(5)
        assert (batch / '.batch.lock').exists()
    provider = FakeProvider(pending)
    install(monkeypatch, provider)
    from src.batch import concurrency
    real_wait = concurrency.wait
    waits = 0
    def interrupt(futures, **kwargs):
        nonlocal waits
        waits += 1
        if waits == 1:
            entered.wait(timeout=5)
        if waits <= signals:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return real_wait(futures, **kwargs)
    monkeypatch.setattr(concurrency, 'wait', interrupt)
    # A helper releases fake I/O only after coordinator cancellation is visible.
    def drain():
        assert first_entered.wait(5)
        entered_control = control_holder[0]
        assert entered_control.cancelled.wait(5)
        assert (batch / '.batch.lock').exists()
        release.set()
    helper = threading.Thread(target=drain)
    helper.start()
    try:
        assert run(batch, 2) == 130
    finally:
        release.set()
        helper.join(timeout=5)
    assert not helper.is_alive() and provider.active == 0 and len(provider.calls) == 2
    assert len(list(batch.rglob('response.json'))) == 2
    assert [row['status'] for row in latest(batch)['items']] == ['interrupted'] * 2 + ['not_attempted'] * 3
    assert not list(batch.rglob('*.lock'))
    monkeypatch.setattr(concurrency, 'wait', real_wait)
    provider.action = None
    assert run(batch, 2) == 0 and len(provider.calls) == 10


@pytest.mark.parametrize('workers', ['0', '-1', 'SYNTHETIC_SECRET', '1.5'])
def test_invalid_workers_before_staging_or_provider(batch, monkeypatch, capsys, workers):
    monkeypatch.setattr('src.batch.cli.run_one', lambda *args: pytest.fail('Must not run'))
    if workers in {'0', '-1'}:
        assert main(['run', '--batch', str(batch), '--send-to-openai', '--parallel-interviews', workers]) == 1
    else:
        with pytest.raises(SystemExit) as failure:
            main(['run', '--batch', str(batch), '--send-to-openai', '--parallel-interviews', workers])
        assert failure.value.code == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC_SECRET' not in captured.out + captured.err


def test_workers_require_run_and_single_selected_session_caps_threads(batch, monkeypatch):
    assert main(['status', '--batch', str(batch), '--parallel-interviews', '2']) == 1
    provider = FakeProvider()
    install(monkeypatch, provider)
    assert run(batch, 100, '--select', 'entry-4') == 0
    assert provider.peak == 1 and [item for item, _, _ in provider.calls] == [4, 4]


def test_serial_local_preparation_interrupt_keeps_exit_and_unattempted_summary(batch, monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr('src.batch.cli.stage', interrupt)
    root = batch.parent / 'interrupted-staging'
    assert main(['prepare', '--plan', str(batch.parent / 'plan.json'), '--batch', str(root),
                 '--copy-local-files']) == 130
    assert [row['status'] for row in latest(root)['items']] == ['interrupted'] + ['not_attempted'] * 4
    assert not (root / '.batch.lock').exists()


def test_atomic_request_cap_many_simultaneous_contenders():
    barrier = threading.Barrier(8)
    entered = threading.Barrier(3)
    provider = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:
        entered.wait(timeout=5))))
    control = ProviderControl(max_requests=3, retries=0)
    def contender():
        barrier.wait(timeout=5)
        try:
            control.call(provider, ('chat', 'completions', 'create'), 120, 0, {})
            return True
        except ProviderStopped:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: contender(), range(8)))
    assert sum(results) == control.requests == 3 and control.reason == 'request_limit'


def test_deadline_and_other_scope_success_cannot_reopen_stopped_admission():
    now = [0]
    entered = threading.Event()
    release = threading.Event()
    control = ProviderControl(max_seconds=1, retries=0, clock=lambda: now[0])
    def pending(**kwargs):
        entered.set()
        assert release.wait(5)
        return 'Synthetic completed response'
    provider = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=pending)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(control.call, provider, ('chat', 'completions', 'create'), 120, 0, {})
        assert entered.wait(5)
        now[0] = 2
        with pytest.raises(ProviderStopped):
            control.call(provider, ('audio', 'transcriptions', 'create'), 120, 0, {})
        release.set()
        assert future.result(timeout=5) == 'Synthetic completed response'
    assert control.requests == 1 and control.reason == 'start_deadline'


def test_late_success_cannot_reopen_shared_endpoint_model_breaker():
    entered, release = threading.Event(), threading.Event()
    control = ProviderControl(failure_limit=1, retries=0)
    request = httpx2.Request('POST', 'https://example.invalid')
    def response(**parameters):
        if parameters['model'] == 'slow':
            entered.set()
            assert release.wait(5)
            return 'Synthetic success'
        raise openai.APITimeoutError(request=request)
    provider = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=response)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(control.call, provider, ('chat', 'completions', 'create'), 120, 0, {'model': 'slow'})
        assert entered.wait(5)
        with pytest.raises(openai.APITimeoutError):
            control.call(provider, ('chat', 'completions', 'create'), 120, 0, {'model': 'fast'})
        release.set()
        assert future.result(timeout=5) == 'Synthetic success'
    assert control.reason == 'provider_failures' and control.requests == 2
    with pytest.raises(ProviderStopped):
        control.call(provider, ('chat', 'completions', 'create'), 120, 0, {'model': 'slow'})


def test_thread_local_context_and_per_session_heartbeat(tmp_path):
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0.01)
    reporter.context = {'scope': 'batch', 'phase': 'raw'}
    reporter.start(tmp_path / 'logs')
    release = threading.Event()
    both = threading.Barrier(3)
    token = CURRENT.set(reporter)
    def session(item):
        reporter.context = {'scope': 'interview', 'phase': 'raw', 'item': item}
        reporter.begin_session(item)
        try:
            emit_progress('transcription', 'running', chunk=item)
            both.wait(timeout=5)
            assert release.wait(5)
            assert reporter.context['item'] == item
        finally:
            reporter.end_session(item)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(copy_context().run, session, item) for item in (1, 2)]
            both.wait(timeout=5)
            # Synchronize on actual heartbeat events rather than elapsed sleeps.
            observed = threading.Event()
            real_emit = reporter.emit
            seen = set()
            def emit(**details):
                event = real_emit(**details)
                if event['status'] == 'heartbeat':
                    seen.add(event['item'])
                    if seen == {1, 2}:
                        observed.set()
                return event
            reporter.emit = emit
            assert observed.wait(5)
            release.set()
            for future in futures:
                future.result(timeout=5)
        assert reporter.context == {'scope': 'batch', 'phase': 'raw'}
    finally:
        release.set()
        CURRENT.reset(token)
        reporter.close()
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    beats = [event for event in events if event['status'] == 'heartbeat']
    assert {event['item'] for event in beats} == {1, 2}
    assert all(event['chunk'] == event['item'] and event['active_sessions'] == 2 for event in beats)


def test_aliases_match_namespaces_and_diarization_alias_requires_opt_in(batch, capsys):
    preferred = parser().parse_args(['run', '--transcription-model', 'whisper-1', '--speaker-model', 'gpt-4o-transcribe-diarize'])
    old = parser().parse_args(['run', '--model', 'whisper-1', '--interview-model', 'gpt-4o-transcribe-diarize'])
    assert vars(preferred) == vars(old)
    assert run(batch, 2, '--speaker-model', 'gpt-4o-transcribe-diarize') == 1
    from src.cli import entrypoint
    with pytest.raises(SystemExit) as failure:
        entrypoint(['--workflow', '--input', str(batch / 'synthetic.wav'),
                    '--speaker-model', 'gpt-4o-transcribe-diarize'])
    assert failure.value.code == 2
    assert 'SYNTHETIC_SECRET' not in capsys.readouterr().out


def test_parallel_logging_failure_stops_new_calls_and_releases_lock(batch, monkeypatch, capsys):
    provider = FakeProvider()
    install(monkeypatch, provider)
    real_emit = Reporter.emit
    def fail(reporter, **details):
        if details.get('stage') == 'preflight' and details.get('stage_status') == 'running':
            reporter.failed = True
            raise LogError('Local execution logging failed; check permissions and free space.')
        return real_emit(reporter, **details)
    monkeypatch.setattr(Reporter, 'emit', fail)
    assert run(batch, 2) == 1
    assert provider.calls == [] and not list(batch.rglob('*.lock'))
    assert any(row['status'] == 'failed' for row in latest(batch)['items'])
    assert 'Traceback' not in capsys.readouterr().out


def test_status_records_completed_original_when_parallel_attribution_fails(batch, monkeypatch):
    def fail(item, seed, model):
        if model == 'gpt-4o-transcribe-diarize' and item == 1:
            raise RuntimeError('SYNTHETIC_PRIVATE payload')
    provider = FakeProvider(fail)
    install(monkeypatch, provider)
    assert run(batch, 2, '--interview') == 1
    rows = [row for row in summaries(batch) if 'latest_run' in row and row.get('item') == 1]
    assert any(row['family'] == 'original' and row['recorded_status'] == 'complete' for row in rows)
    assert any(row['family'] == 'attributed' and row['recorded_status'] == 'failed' for row in rows)
