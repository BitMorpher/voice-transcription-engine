"""Offline recovery contracts; fixtures are invented PCM, never real testimony."""
import io
import json
import os
import signal
import shutil
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2
import openai
import pytest

from src.author_workflow import AuthorOptions
from src.model_config import EditingOptions
from src.batch.cli import main as batch_main
from src.batch.plan import load_plan
from src.batch.storage import save_snapshot, stage, summaries, write_summary
from src.cli import entrypoint
from src.interview_attribution import AttributedInterview, InterviewOptions, input_record
from src.model_config import TranscriptionOptions
from src.ordered_interview import OrderedInterview
from src.pipeline import Pipeline, PipelineError
from src.private_output import digest
from src.progress import Reporter, interruptions
from src.provider_control import CURRENT_CONTROL, ControlledClient, ProviderControl, ProviderStopped
from src.transcriber import Transcriber, TranscriptionError


def audio(path, seconds=3.25, seed=1):
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
        wav.writeframes(b''.join((seed + index // 100).to_bytes(2, 'little')
                                  for index in range(int(100 * seconds))))
    return path


def client():
    result = MagicMock()
    def respond(**parameters):
        with wave.open(parameters['file'][1], 'rb') as wav:
            duration = wav.getnframes() / wav.getframerate()
            label = int.from_bytes(wav.readframes(1), 'little')
        text = f'Synthetic words {label}.'
        if parameters['model'] == 'gpt-4o-transcribe-diarize':
            return {'text': text, 'segments': [{'text': text, 'speaker': 'A', 'start': 0,
                                                'end': duration}]}
        return SimpleNamespace(text=text)
    result.audio.transcriptions.create.side_effect = respond
    return result


@pytest.fixture
def pcm(monkeypatch):
    monkeypatch.setattr('src.pipeline.prepare_audio', lambda source, target, **kwargs:
                        shutil.copyfile(source, target))


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def test_asr_failure_after_n_chunks_and_restart_only_missing(tmp_path, pcm):
    source = audio(tmp_path / 'synthetic.wav')
    provider = client()
    options = TranscriptionOptions(chunk_seconds=1)
    transcriber = Transcriber(client=provider, options=options)
    original = provider.audio.transcriptions.create.side_effect
    def fail(**parameters):
        if provider.audio.transcriptions.create.call_count == 3:
            raise RuntimeError('PRIVATE_CONTENT')
        return original(**parameters)
    provider.audio.transcriptions.create.side_effect = fail
    output = tmp_path / 'out'
    with pytest.raises(PipelineError):
        Pipeline(output, options=options).process(source, transcriber=transcriber)
    job = next(path.parent for path in output.rglob('manifest.json') if path.parent.parent == output)
    assert not (job / 'transcription.txt').exists()
    assert json.loads((job / 'manifest.json').read_text())['stages']['transcription']['status'] == 'failed'
    saved = snapshot(job / 'asr-chunks')
    assert len(saved) == 5  # shared binding plus two atomic response/manifest pairs
    provider.audio.transcriptions.create.side_effect = original
    identity, _ = Pipeline(output, options=options, resume=True).process(source, transcriber=transcriber)
    assert identity == job.name
    assert provider.audio.transcriptions.create.call_count == 5  # two missing chunks only
    assert all(snapshot(job / 'asr-chunks')[key] == value for key, value in saved.items())
    assert (job / 'transcription.txt').read_text() == '\n\n'.join(f'Synthetic words {i}.' for i in range(1, 5))
    before = snapshot(output)
    Pipeline(output, options=options, resume=True).process(source, transcriber=transcriber)
    assert snapshot(output) == before
    assert provider.audio.transcriptions.create.call_count == 5


@pytest.mark.parametrize('tamper', ['response', 'manifest', 'missing', 'symlink', 'forged_shape',
                                    'offset', 'binding'])
def test_partial_asr_checkpoint_tamper_blocks_before_request(tmp_path, pcm, tamper):
    source = audio(tmp_path / 'synthetic.wav')
    provider, options = client(), TranscriptionOptions(chunk_seconds=1)
    transcriber = Transcriber(client=provider, options=options)
    original = provider.audio.transcriptions.create.side_effect
    provider.audio.transcriptions.create.side_effect = lambda **kwargs: (
        original(**kwargs) if provider.audio.transcriptions.create.call_count == 1
        else (_ for _ in ()).throw(RuntimeError('PRIVATE')))
    output = tmp_path / 'out'
    with pytest.raises(PipelineError):
        Pipeline(output, options=options).process(source, transcriber=transcriber)
    manifest = next(output.rglob('response.json')).with_name('manifest.json')
    response = manifest.with_name('response.json')
    if tamper == 'response':
        response.write_text('tampered')
    elif tamper == 'manifest':
        manifest.write_text('{}')
    elif tamper == 'missing':
        response.unlink()
    elif tamper == 'symlink':
        response.unlink()
        response.symlink_to(source)
    elif tamper == 'offset':
        state = json.loads(manifest.read_text())
        state['chunk']['offset_seconds'] += 1
        manifest.write_text(json.dumps(state))
    elif tamper == 'binding':
        binding = manifest.parent.parent / 'binding.json'
        state = json.loads(binding.read_text())
        state['binding']['source_sha256'] = 'b' * 64
        binding.write_text(json.dumps(state))
    else:
        response.write_text('{"text":42}')
        state = json.loads(manifest.read_text())
        state['response_sha256'] = digest(response)
        manifest.write_text(json.dumps(state))
    calls = provider.audio.transcriptions.create.call_count
    with pytest.raises(PipelineError):
        Pipeline(output, options=options, resume=True).process(source, transcriber=transcriber)
    assert provider.audio.transcriptions.create.call_count == calls


def family_fixture(tmp_path, pcm):
    source = audio(tmp_path / 'synthetic.wav')
    provider = client()
    transcriber = Transcriber(client=provider)
    output = tmp_path / 'out'
    identity, _ = Pipeline(output).process(source, transcriber=transcriber)
    job = output / identity
    inputs = [input_record(1, job, json.loads((job / 'manifest.json').read_text()))]
    return output, provider, transcriber, inputs


def test_diarization_partial_restart_and_complete_legacy_read_only(tmp_path, pcm):
    output, provider, transcriber, inputs = family_fixture(tmp_path, pcm)
    options = InterviewOptions(None, None, allow_unnamed=True, diarization_chunk_seconds=1)
    original = provider.audio.transcriptions.create.side_effect
    def fail(**parameters):
        if provider.audio.transcriptions.create.call_count == 4:  # original + two speaker chunks
            raise RuntimeError('PRIVATE')
        return original(**parameters)
    provider.audio.transcriptions.create.side_effect = fail
    family = AttributedInterview(output, inputs, options, resume=True, enhance=False, author_options=None)
    with pytest.raises(TranscriptionError):
        family.process(transcriber)
    assert not list((output / 'attributed').rglob('provider_responses.json'))
    assert not family.job.exists()
    saved = snapshot(output / 'attributed/diarization-chunks')
    assert len(saved) == 5
    provider.audio.transcriptions.create.side_effect = original
    family.process(transcriber)
    assert provider.audio.transcriptions.create.call_count == 6
    before = snapshot(output)
    family.process(transcriber)
    assert snapshot(output) == before
    # Completed cache version 1 remains consumable after dropping the new checkpoints.
    shutil.rmtree(output / 'attributed/diarization-chunks')
    before = snapshot(output)
    family.process(transcriber)
    assert snapshot(output) == before and provider.audio.transcriptions.create.call_count == 6
    assert json.loads(next(output.rglob('provider_responses.json')).read_text())['version'] == 1


@pytest.mark.parametrize('change', ['audio', 'source_binding', 'model_settings', 'model', 'byte_cap'])
def test_chunk_contract_binding_prevents_wrong_reuse(tmp_path, change):
    source, provider = audio(tmp_path / 'synthetic.wav'), client()
    options = TranscriptionOptions(chunk_seconds=1)
    transcriber = Transcriber(client=provider, options=options)
    keywords = {'checkpoint_root': tmp_path / 'cache', 'source_sha256': 'a' * 64,
                'audio_sha256': digest(source)}
    transcriber.checkpointed_transcribe(source, **keywords)
    old = snapshot(tmp_path / 'cache')
    if change == 'audio':
        audio(source, seed=9)
        keywords['audio_sha256'] = digest(source)
    elif change == 'source_binding':
        keywords['source_sha256'] = 'b' * 64
    elif change == 'model_settings':
        transcriber = Transcriber(client=provider, options=TranscriptionOptions(chunk_seconds=1, context='Synthetic context'))
    elif change == 'model':
        transcriber = Transcriber(client=provider, options=TranscriptionOptions(model='whisper-1', chunk_seconds=1))
    else:
        transcriber.max_bytes -= 100
    transcriber.checkpointed_transcribe(source, **keywords)
    assert provider.audio.transcriptions.create.call_count == 8
    assert all(snapshot(tmp_path / 'cache')[key] == value for key, value in old.items())


def test_independent_diarization_changes_no_asr_and_demands_mapping_confirmation(tmp_path, pcm):
    output, provider, transcriber, inputs = family_fixture(tmp_path, pcm)
    original = snapshot(output)
    old = AttributedInterview(output, inputs, InterviewOptions(None, None, allow_unnamed=True),
                              resume=True, enhance=False, author_options=None)
    old.process(transcriber)
    calls = provider.audio.transcriptions.create.call_count
    options = InterviewOptions(None, None, allow_unnamed=True, diarization_chunk_seconds=1)
    new = AttributedInterview(output, inputs, options, resume=True, enhance=False, author_options=None)
    new.process(transcriber)
    assert new.job != old.job
    assert provider.audio.transcriptions.create.call_count == calls + 4
    assert all(snapshot(output)[key] == value for key, value in original.items())
    with pytest.raises(ValueError, match='confirm-speaker'):
        InterviewOptions('Synthetic Host', 'Synthetic Guest', ('1:1:A=interviewer',), diarization_chunk_seconds=1)
    assert InterviewOptions('Synthetic Host', 'Synthetic Guest', ('1:1:A=interviewer',),
        diarization_chunk_seconds=1, mappings_reconfirmed=True).mappings()


def test_43_saved_recordings_old_contract_recovery_requests_only_four(tmp_path, pcm):
    sources = [audio(tmp_path / f'synthetic-{i}.wav', seconds=0.2, seed=i + 1) for i in range(47)]
    manifest = tmp_path / 'ordered.json'
    manifest.write_text(json.dumps({'version': 1, 'interview_id': 'synthetic-session',
        'parts': [{'id': f'part-{i}', 'path': path.name, 'media_type': 'audio'} for i, path in enumerate(sources)]}))
    output, provider = tmp_path / 'out', client()
    transcriber = Transcriber(client=provider)
    ordered = OrderedInterview(manifest, output, options=TranscriptionOptions(),
        editing_options=EditingOptions(), author_options=AuthorOptions(review=False))
    original = provider.audio.transcriptions.create.side_effect
    def fail(**parameters):
        if provider.audio.transcriptions.create.call_count == 44:
            raise RuntimeError('Synthetic failure')
        return original(**parameters)
    provider.audio.transcriptions.create.side_effect = fail
    with pytest.raises(PipelineError):
        ordered.process(transcriber=transcriber)
    completed = []
    for part, directory, identity in ordered.parts[:43]:
        job = directory / identity
        state = json.loads((job / 'manifest.json').read_text())
        assert state['version'] == 2 and state['stages']['transcription']['status'] == 'complete'
        # Emulate old completed recordings with no per-chunk checkpoints.
        shutil.rmtree(job / 'asr-chunks')
        completed.append((job, snapshot(job)))
    provider.audio.transcriptions.create.side_effect = original
    resumed = OrderedInterview(manifest, output, resume=True, options=TranscriptionOptions(),
        editing_options=EditingOptions(), author_options=AuthorOptions(review=False))
    resumed.process(transcriber=transcriber)
    assert provider.audio.transcriptions.create.call_count == 48  # 43 + failure + four remaining
    assert all(snapshot(job) == saved for job, saved in completed)


def test_request_cap_cached_chunks_dont_consume_budget(tmp_path):
    provider, source = client(), audio(tmp_path / 'synthetic.wav')
    transcriber = Transcriber(client=provider, options=TranscriptionOptions(chunk_seconds=1), provider_retries=0)
    kwargs = dict(checkpoint_root=tmp_path / 'cache', source_sha256='a' * 64, audio_sha256=digest(source))
    control = ProviderControl(max_requests=1, retries=0)
    token = CURRENT_CONTROL.set(control)
    try:
        with pytest.raises(ProviderStopped):
            transcriber.checkpointed_transcribe(source, **kwargs)
    finally:
        CURRENT_CONTROL.reset(token)
    assert control.requests == 1 and control.reason == 'request_limit'
    control = ProviderControl(max_requests=3, retries=0)
    token = CURRENT_CONTROL.set(control)
    try:
        text = transcriber.checkpointed_transcribe(source, **kwargs)
    finally:
        CURRENT_CONTROL.reset(token)
    assert control.requests == 3 and text.endswith('Synthetic words 4.')
    assert provider.audio.transcriptions.create.call_count == 4


def test_sdk_single_http_attempt_and_timeout_extensions_are_effective():
    seen = []
    def respond(request):
        seen.append(request.extensions['timeout'])
        raise httpx2.ReadTimeout('PRIVATE_PROVIDER_DETAIL', request=request)
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as transport:
        with openai.OpenAI(api_key='synthetic-placeholder', http_client=transport, max_retries=2) as provider:
            times = iter([10, 12, 12, 12])
            control = ProviderControl(max_requests=1, max_seconds=5, retries=0, clock=lambda: next(times))
            token = CURRENT_CONTROL.set(control)
            try:
                with pytest.raises(openai.APITimeoutError):
                    ControlledClient(provider, timeout=120, retries=0).audio.transcriptions.create(
                        model='gpt-transcribe', file=('audio.wav', io.BytesIO(b'synthetic'), 'audio/wav'))
            finally:
                CURRENT_CONTROL.reset(token)
    assert len(seen) == 1
    assert all(value == 3 for value in seen[0].values())


def test_start_deadline_does_not_claim_inflight_cancellation():
    now, provider = [0], client()
    control = ProviderControl(max_seconds=1, retries=0, clock=lambda: now[0])
    def finish(**kwargs):
        now[0] = 2
        return 'Synthetic completed response'
    provider.chat.completions.create.side_effect = finish
    assert control.call(provider, ('chat', 'completions', 'create'), 120, 0, {}) == 'Synthetic completed response'
    with pytest.raises(ProviderStopped):
        control.call(provider, ('chat', 'completions', 'create'), 120, 0, {})
    assert control.reason == 'start_deadline' and provider.chat.completions.create.call_count == 1


@pytest.mark.parametrize('flags', [['--max-provider-requests', '1'], ['--max-run-seconds', '5'],
                                   ['--provider-failure-limit', '0'], ['--max-provider-requests', '-1']])
def test_invalid_limits_before_provider(tmp_path, monkeypatch, flags):
    ctor = MagicMock(side_effect=AssertionError('Must not initialize provider'))
    monkeypatch.setattr('src.cli.Transcriber', ctor)
    assert entrypoint(['--workflow', '--input', str(tmp_path / 'PRIVATE.wav'), *flags]) == 1
    ctor.assert_not_called()


def test_consecutive_failures_reset_and_systemic_stops_immediately():
    provider = client()
    control = ProviderControl(failure_limit=2)
    request = httpx2.Request('POST', 'https://example.invalid')
    failure = openai.APITimeoutError(request=request)
    provider.chat.completions.create.side_effect = [failure, 'success', failure, failure]
    for _ in range(4):
        try:
            control.call(provider, ('chat', 'completions', 'create'), 120, 2, {})
        except openai.APITimeoutError:
            pass
    assert control.requests == 4 and control.reason == 'provider_failures'
    with pytest.raises(ProviderStopped):
        control.call(provider, ('chat', 'completions', 'create'), 120, 2, {})
    assert provider.chat.completions.create.call_count == 4
    control = ProviderControl(failure_limit=8)
    provider.chat.completions.create.side_effect = openai.AuthenticationError('PRIVATE',
        response=httpx2.Response(401, request=request), body={'PRIVATE': 'PRIVATE'})
    with pytest.raises(openai.AuthenticationError):
        control.call(provider, ('chat', 'completions', 'create'), 120, 2, {})
    assert control.reason == 'systemic_provider'


@pytest.fixture
def batch(tmp_path):
    entries = []
    for index in range(1, 4):
        source = audio(tmp_path / f'synthetic-{index}.wav', seconds=0.2)
        manifest = tmp_path / f'ordered-{index}.json'
        manifest.write_text(json.dumps({'version': 1, 'interview_id': f'synthetic-{index}',
            'parts': [{'id': 'one', 'path': source.name, 'media_type': 'audio'}]}))
        entries.append({'id': f'synthetic-entry-{index}', 'manifest': manifest.name})
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'version': 1, 'interviews': entries}))
    document = load_plan(plan)
    root = save_snapshot(tmp_path / 'batch', document, document['interviews'])
    for item in document['interviews']:
        stage(root, item, progress=lambda *a, **kw: None)
    return root


def test_batch_circuit_breaker_not_attempted_and_sanitized_config(batch, monkeypatch, capsys, pcm):
    provider = client()
    request = httpx2.Request('POST', 'https://example.invalid')
    provider.audio.transcriptions.create.side_effect = openai.APITimeoutError(request=request)
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))
    assert batch_main(['run', '--batch', str(batch), '--send-to-openai', '--provider-retries', '0']) == 1
    assert provider.audio.transcriptions.create.call_count == 2
    report = json.loads(next(batch.glob('summary-*.json')).read_text())
    assert [row['status'] for row in report['items']] == ['failed', 'failed', 'not_attempted']
    assert report['processed'] == 2 and report['not_attempted'] == 1
    assert report['configuration']['provider_retries'] == 0
    assert '--resume.' not in capsys.readouterr().out
    assert not (batch / '.batch.lock').exists()


def test_batch_limit_mid_item_and_signal_remaining_not_attempted(batch, monkeypatch, pcm):
    provider = client()
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))
    assert batch_main(['run', '--batch', str(batch), '--send-to-openai', '--interview',
                      '--provider-retries', '0', '--max-provider-requests', '1']) == 1
    report = json.loads(next(batch.glob('summary-*.json')).read_text())
    assert [row['status'] for row in report['items']] == ['incomplete', 'not_attempted', 'not_attempted']
    assert provider.audio.transcriptions.create.call_count == 1
    def stop(*args):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    monkeypatch.setattr('src.batch.cli.run_one', stop)
    assert batch_main(['run', '--batch', str(batch), '--send-to-openai']) == 130
    latest = max(batch.glob('summary-*.json'), key=lambda path: path.stat().st_mtime)
    assert [row['status'] for row in json.loads(latest.read_text())['items']] == ['interrupted', 'not_attempted', 'not_attempted']
    assert not (batch / '.batch.lock').exists()


def test_signal_retains_atomic_prior_chunk_and_releases_job_lock(tmp_path, pcm):
    source, provider = audio(tmp_path / 'synthetic.wav'), client()
    options = TranscriptionOptions(chunk_seconds=1)
    original = provider.audio.transcriptions.create.side_effect
    def stop(**kwargs):
        if provider.audio.transcriptions.create.call_count == 2:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return original(**kwargs)
    provider.audio.transcriptions.create.side_effect = stop
    output = tmp_path / 'out'
    with pytest.raises(KeyboardInterrupt), interruptions():
        Pipeline(output, options=options).process(source, transcriber=Transcriber(client=provider, options=options))
    assert len(list(output.rglob('response.json'))) == 1
    assert not list(output.rglob('.lock'))
    provider.audio.transcriptions.create.side_effect = original
    Pipeline(output, options=options, resume=True).process(source, transcriber=Transcriber(client=provider, options=options))
    assert provider.audio.transcriptions.create.call_count == 5


def test_status_chronological_identities_and_mixed_family_latest(tmp_path):
    root = tmp_path / 'batch'
    root.mkdir()
    old = {'run': 'f' * 32, 'phase': 'prepare', 'items': [{'item': 1, 'status': 'staged'}], 'processed': 1}
    path = root / ('summary-' + old['run'] + '.json')
    path.write_text(json.dumps(old))
    os.utime(path, (0, 0))
    reporter = Reporter(None)
    reporter.run, reporter.started_at = 'e' * 32, '2026-01-01T00:00:00+00:00'
    write_summary(root, reporter, [{'item': 1, 'status': 'failed', 'families': {
        'original': {'combined_raw': 'complete'}, 'attributed': {'attribution': 'failed'}}}], 'raw')
    reporter.run, reporter.started_at = 'a' * 32, '2026-01-02T00:00:00+00:00'
    write_summary(root, reporter, [{'item': 1, 'status': 'complete', 'families': {
        'original': {'combined_raw': 'skipped', 'author_review': 'complete'}}}], 'review')
    before = snapshot(root)
    rows = summaries(root)
    assert [row['historical_run'] for row in rows if 'historical_run' in row] == ['f' * 32, 'e' * 32, 'a' * 32]
    latest = [row for row in rows if row.get('status') == 'latest']
    assert len(latest) == 3
    assert next(row for row in latest if row['family'] == 'attributed')['latest_run'] == 'e' * 32
    assert next(row for row in latest if row['family'] == 'original' and row['phase'] == 'raw')['recorded_status'] == 'complete'
    assert snapshot(root) == before
    stream = io.StringIO()
    reporter = Reporter(stream)
    reporter.emit(status='configuration', configuration={'asr_model': 'PRIVATE', 'context': 'PRIVATE',
        'provider_timeout': 'PRIVATE', 'provider_retries': 0, 'context_supplied': True}, historical_run='PRIVATE')
    assert 'PRIVATE' not in stream.getvalue()


@pytest.mark.parametrize('tamper', ['response', 'rebound', 'incomplete', 'forged_segment'])
def test_partial_diarization_tamper_before_any_new_call(tmp_path, pcm, tamper):
    output, provider, transcriber, inputs = family_fixture(tmp_path, pcm)
    options = InterviewOptions(None, None, allow_unnamed=True, diarization_chunk_seconds=1)
    original = provider.audio.transcriptions.create.side_effect
    def fail(**parameters):
        if provider.audio.transcriptions.create.call_count == 4:
            raise RuntimeError('PRIVATE')
        return original(**parameters)
    provider.audio.transcriptions.create.side_effect = fail
    family = AttributedInterview(output, inputs, options, resume=True, enhance=False, author_options=None)
    with pytest.raises(TranscriptionError):
        family.process(transcriber)
    manifest = sorted((output / 'attributed/diarization-chunks').rglob('manifest.json'))[-1]
    response = manifest.with_name('response.json')
    if tamper == 'response':
        response.write_text('{}')
    elif tamper == 'incomplete':
        manifest.unlink()
    elif tamper == 'rebound':
        body = json.loads(manifest.read_text())
        body['binding_sha256'] = 'b' * 64
        manifest.write_text(json.dumps(body))
    else:
        body = json.loads(response.read_text())
        body['segments'][0]['end'] = 10000
        response.write_text(json.dumps(body))
        state = json.loads(manifest.read_text())
        state['response_sha256'] = digest(response)
        manifest.write_text(json.dumps(state))
    calls = provider.audio.transcriptions.create.call_count
    with pytest.raises(TranscriptionError):
        family.process(transcriber)
    assert provider.audio.transcriptions.create.call_count == calls
    assert not family.job.exists()


def test_deadline_expires_during_validation_no_provider_start():
    times = iter([0, 0.9, 1.1])
    control = ProviderControl(max_seconds=1, retries=0, clock=lambda: next(times))
    provider = client()
    with pytest.raises(ProviderStopped):
        control.call(provider, ('chat', 'completions', 'create'), 120, 0, {})
    assert control.requests == 0 and control.reason == 'start_deadline'
    provider.chat.completions.create.assert_not_called()


def test_empty_asr_is_retryable_not_a_successful_checkpoint(tmp_path, pcm):
    source, provider, output = audio(tmp_path / 'synthetic.wav', seconds=0.2), client(), tmp_path / 'out'
    original = provider.audio.transcriptions.create.side_effect
    provider.audio.transcriptions.create.side_effect = None
    provider.audio.transcriptions.create.return_value = SimpleNamespace(text='')
    with pytest.raises(PipelineError):
        Pipeline(output).process(source, transcriber=Transcriber(client=provider), require_nonempty=True)
    assert not list(output.rglob('response.json'))
    provider.audio.transcriptions.create.side_effect = original
    Pipeline(output, resume=True).process(source, transcriber=Transcriber(client=provider), require_nonempty=True)
    assert provider.audio.transcriptions.create.call_count == 2



def test_batch_asr_success_does_not_erase_repeated_speaker_timeouts(batch, monkeypatch, pcm):
    provider = client()
    original = provider.audio.transcriptions.create.side_effect
    request = httpx2.Request('POST', 'https://example.invalid')
    def respond(**parameters):
        if parameters['model'] == 'gpt-4o-transcribe-diarize':
            raise openai.APITimeoutError(request=request)
        return original(**parameters)
    provider.audio.transcriptions.create.side_effect = respond
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))
    assert batch_main(['run', '--batch', str(batch), '--send-to-openai', '--interview',
                      '--provider-retries', '0']) == 1
    assert provider.audio.transcriptions.create.call_count == 4
    report = json.loads(next(batch.glob('summary-*.json')).read_text())
    assert [row['status'] for row in report['items']] == ['failed', 'failed', 'not_attempted']
    assert all(row['families']['original']['combined_raw'] == 'complete' for row in report['items'][:2])
    assert all(row['families']['attributed']['attribution'] == 'failed' for row in report['items'][:2])
