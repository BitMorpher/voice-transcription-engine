"""Offline text resumption, strict identity and bounded recovery regressions."""
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.author_review import ReviewOptions, _chunks, review_transcript, validate_review_report
from src.chapters import ChapterOptions, draft_chapters
from src.chunk_cache import ChunkCacheError
from src.model_config import EditingOptions
from src.private_output import digest
from src.progress import CURRENT, Reporter
from src.provider_control import CURRENT_CONTROL, ControlledClient, ProviderControl
from src.text_requests import TextRequestCache, ResponseValidationError, text_binding, validated_chat
from src.transcriber import Transcriber, TranscriptionError

RAW = 'Synthetic testimony stays exact.\n' * 12


def completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=content, refusal=None))])


def reviewer():
    client = MagicMock()
    def respond(**parameters):
        payload = json.loads(parameters['messages'][-1]['content'])
        return completion(json.dumps({'chunk_index': payload['chunk_index'], 'fully_reviewed': True,
            'reviewed_start': payload['core_start'], 'reviewed_end': payload['core_end'], 'findings': []}))
    client.chat.completions.create.side_effect = respond
    return client


def narrator():
    client = MagicMock()
    def respond(**parameters):
        payload = json.loads(parameters['messages'][-1]['content'])
        units = payload['source_units']
        return completion(json.dumps({'chunk_index': payload['chunk_index'], 'passages': [
            {'unit_ids': [u['unit_id'] for u in units], 'kind': 'verbatim_excerpt',
             'text': ''.join(u['text'] for u in units).strip()}], 'coverage_omissions': []}))
    client.chat.completions.create.side_effect = respond
    return client


@pytest.mark.parametrize('interrupt', [False, True])
def test_review_restart_reuses_every_successful_chunk(tmp_path, interrupt):
    options = ReviewOptions(chunk_bytes=64)
    client = reviewer()
    respond = client.chat.completions.create.side_effect
    def fail_late(**parameters):
        if json.loads(parameters['messages'][-1]['content'])['chunk_index'] == 3:
            raise KeyboardInterrupt() if interrupt else RuntimeError('SYNTHETIC_SECRET')
        return respond(**parameters)
    client.chat.completions.create.side_effect = fail_late
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            review_transcript(RAW, client, options, checkpoint_root=tmp_path)
    else:
        report = review_transcript(RAW, client, options, checkpoint_root=tmp_path)
        assert report['status'] == 'incomplete'
        assert 'SYNTHETIC_SECRET' not in json.dumps(report)
    saved = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    calls = client.chat.completions.create.call_count
    client.chat.completions.create.side_effect = respond
    report = review_transcript(RAW, client, options, checkpoint_root=tmp_path)
    validate_review_report(RAW, report, options)
    expected = 1 if not interrupt else len(list(_chunks(RAW, 64))) - 2
    assert client.chat.completions.create.call_count - calls == expected
    assert all(p.read_bytes() == body for p, body in saved.items())
    review_transcript(RAW, client, options, checkpoint_root=tmp_path)
    assert client.chat.completions.create.call_count == calls + expected


@pytest.mark.parametrize('stage', ['polish', 'narrative'])
def test_adjacent_stages_keep_requests_on_restart(tmp_path, provider, stage):
    if stage == 'polish':
        client = provider
        transcriber = Transcriber(client=client, editing_options=EditingOptions(chunk_bytes=64))
        def run():
            return transcriber.enhance_transcription(RAW, checkpoint_root=tmp_path)
        error = TranscriptionError
    else:
        client = narrator()
        report = review_transcript(RAW, reviewer())
        def run():
            return draft_chapters(RAW, report, client,
                ChapterOptions(styles=('narrative',), chunk_bytes=64), checkpoint_root=tmp_path)
        error = ResponseValidationError
    respond = client.chat.completions.create.side_effect
    def fail_late(**parameters):
        if client.chat.completions.create.call_count == 3:
            raise RuntimeError('SYNTHETIC_SECRET')
        return respond(**parameters)
    client.chat.completions.create.side_effect = fail_late
    with pytest.raises(error):
        run()
    saved = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    client.chat.completions.create.side_effect = respond
    run()
    assert all(p.read_bytes() == body for p, body in saved.items())
    indexes = [json.loads(c.kwargs['messages'][-1]['content'])['chunk_index']
               for c in client.chat.completions.create.call_args_list]
    assert indexes[:4] == [1, 2, 3, 3]
    calls = len(indexes)
    run()
    assert client.chat.completions.create.call_count == calls


@pytest.mark.parametrize('mutation', ['content', 'schema_with_checksum', 'missing', 'symlink',
                                     'binding', 'duplicate_json'])
def test_review_checkpoint_tamper_preflights_before_calls(tmp_path, mutation):
    client = reviewer()
    review_transcript(RAW, client, ReviewOptions(chunk_bytes=64), checkpoint_root=tmp_path)
    response = next(tmp_path.rglob('response.json'))
    manifest = response.with_name('manifest.json')
    if mutation == 'content':
        response.write_text('changed')
    elif mutation == 'missing':
        response.unlink()
    elif mutation == 'symlink':
        response.unlink()
        response.symlink_to(manifest)
    elif mutation == 'binding':
        response.parent.parent.joinpath('binding.json').write_text('{}')
    else:
        response.write_text('{"content":"{}"}' if mutation == 'schema_with_checksum'
                            else '{"content":"{}","content":"{}"}')
        state = json.loads(manifest.read_text())
        state['response_sha256'] = digest(response)
        manifest.write_text(json.dumps(state))
    calls = client.chat.completions.create.call_count
    with pytest.raises(ChunkCacheError):
        review_transcript(RAW, client, ReviewOptions(chunk_bytes=64), checkpoint_root=tmp_path)
    assert client.chat.completions.create.call_count == calls


@pytest.mark.parametrize('changed', ['source', 'model', 'reasoning', 'chunk', 'prompt', 'contract'])
def test_identity_binds_every_request_semantic(tmp_path, changed):
    binding = text_binding('review', RAW, 'settings', validator_contract=1)
    parameters = [{'model': 'gpt-6-astra', 'reasoning_effort': 'high', 'chunk': 1,
                   'messages': [{'role': 'system', 'content': 'Synthetic prompt'}]}]
    cache = TextRequestCache(tmp_path, binding, parameters, [lambda content: None])
    cache.put(cache.layout[0], {'content': 'validated'})
    if changed == 'source':
        binding['source_sha256'] = 'a' * 64
    elif changed == 'contract':
        binding['validator_contract'] = 2
    elif changed == 'prompt':
        parameters[0]['messages'][0]['content'] += ' changed'
    else:
        key = {'model': 'model', 'reasoning': 'reasoning_effort', 'chunk': 'chunk'}[changed]
        parameters[0][key] = 'changed'
    new = TextRequestCache(tmp_path, binding, parameters, [lambda content: None])
    assert new.identity != cache.identity
    assert new.get(new.layout[0]) is None


@pytest.mark.parametrize('retries,category,expected', [(2, 'validation_schema', 2),
    (2, 'validation_coverage', 2), (0, 'validation_schema', 1),
    (2, 'validation_source', 1), (2, 'completion', 1)])
def test_recovery_is_bounded_counted_and_cannot_promote_invalid(tmp_path, retries, category, expected):
    raw = MagicMock()
    raw.chat.completions.create.return_value = completion('invalid')
    control = ProviderControl(retries=retries)
    token = CURRENT_CONTROL.set(control)
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0)
    reporting = CURRENT.set(reporter)
    def validate(content):
        raise ResponseValidationError('SYNTHETIC_SECRET', category=category)
    cache = TextRequestCache(tmp_path, {'contract': 1}, [{'model': 'gpt-6-astra'}], [validate])
    try:
        with pytest.raises(ResponseValidationError):
            validated_chat(ControlledClient(raw, 600, retries), {'model': 'gpt-6-astra'}, validate,
                           stage='author_review', cache=cache)
    finally:
        CURRENT_CONTROL.reset(token)
        CURRENT.reset(reporting)
    assert raw.chat.completions.create.call_count == control.requests == expected
    assert control.failures == 0  # Local validation is not a provider breaker failure.
    assert not list(tmp_path.rglob('response.json'))
    assert 'SYNTHETIC_SECRET' not in stream.getvalue()


def test_recovery_success_is_saved_once(tmp_path):
    client = reviewer()
    normal = client.chat.completions.create.side_effect
    def respond(**parameters):
        return completion('{bad') if client.chat.completions.create.call_count == 1 else normal(**parameters)
    client.chat.completions.create.side_effect = respond
    control = ProviderControl(retries=2)
    token = CURRENT_CONTROL.set(control)
    try:
        report = review_transcript('Synthetic statement.', ControlledClient(client, 600, 2),
                                   checkpoint_root=tmp_path)
    finally:
        CURRENT_CONTROL.reset(token)
    assert report['status'] == 'complete'
    assert control.requests == 2
    assert len(list(tmp_path.rglob('response.json'))) == 1


def test_checkpoint_publication_interruption_never_promotes(tmp_path, monkeypatch):
    cache = TextRequestCache(tmp_path, {'contract': 1}, [{'model': 'synthetic'}], [lambda content: None])
    def interrupted(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr('src.chunk_cache.os.rename', interrupted)
    with pytest.raises(KeyboardInterrupt):
        cache.put(cache.layout[0], {'content': 'Synthetic validated response'})
    resumed = TextRequestCache(tmp_path, {'contract': 1}, [{'model': 'synthetic'}], [lambda content: None])
    assert resumed.get(resumed.layout[0]) is None
    assert not list(tmp_path.rglob('response.json'))


def test_cache_changed_during_review_prevents_completion_and_next_run_calls(tmp_path):
    client = reviewer()
    options = ReviewOptions(chunk_bytes=64)
    normal = client.chat.completions.create.side_effect
    def tamper(**parameters):
        payload = json.loads(parameters['messages'][-1]['content'])
        if payload['chunk_index'] == 2:
            next(tmp_path.rglob('response.json')).write_text('{}')
        return normal(**parameters)
    client.chat.completions.create.side_effect = tamper
    with pytest.raises(ChunkCacheError):
        review_transcript(RAW, client, options, checkpoint_root=tmp_path)
    # A final verify rejects mutations after use; no complete report is promoted.
    assert client.chat.completions.create.call_count > 1
    calls = client.chat.completions.create.call_count
    with pytest.raises(ChunkCacheError):
        review_transcript(RAW, client, options, checkpoint_root=tmp_path)
    assert client.chat.completions.create.call_count == calls


@pytest.mark.parametrize('duration,expected', [(60, 6), (120, 3)])
def test_shorter_speaker_requests_leave_asr_and_text_chunks_independent(tmp_path, duration, expected):
    from tests.test_recovery import audio, client
    from src.interview_attribution import diarization_configuration, diarize, InterviewOptions
    from src.text_editing import split_text
    provider = client()
    transcriber = Transcriber(client=provider)
    source = audio(tmp_path / 'synthetic.wav', seconds=301.25)
    original_chunks = list(transcriber._wave_chunks(source))
    assert [c.duration_seconds for c in original_chunks] == [300, 1.25]
    for chunk in original_chunks:
        chunk.close()
    before = list(split_text(RAW, 64))
    payload = diarize(transcriber, source, diarization_configuration(transcriber, chunk_seconds=duration))
    assert len(payload['requests']) == expected
    assert sum(r['duration_seconds'] for r in payload['requests']) == 301.25
    assert payload['requests'][-1]['duration_seconds'] == (1.25 if duration == 60 else 61.25)
    assert transcriber.options.chunk_seconds == 300
    assert before == list(split_text(RAW, 64))
    assert InterviewOptions(None, None, allow_unnamed=True, diarization_chunk_seconds=60).fingerprint != (
        InterviewOptions(None, None, allow_unnamed=True, diarization_chunk_seconds=120).fingerprint)


def test_invalid_utf8_editing_input_stays_private(provider):
    with pytest.raises(TranscriptionError) as error:
        Transcriber(client=provider).enhance_transcription('SYNTHETIC_SECRET\ud800')
    assert 'SYNTHETIC_SECRET' not in str(error.value)
    provider.chat.completions.create.assert_not_called()


def test_local_diarization_validation_is_distinct_from_provider_failure(tmp_path):
    from tests.test_recovery import audio
    from src.interview_attribution import diarization_configuration, diarize
    client = MagicMock()
    client.audio.transcriptions.create.return_value = {'text': 'SYNTHETIC_SECRET', 'segments': []}
    transcriber = Transcriber(client=client)
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0)
    token = CURRENT.set(reporter)
    try:
        with pytest.raises(TranscriptionError):
            diarize(transcriber, audio(tmp_path / 'synthetic.wav'), diarization_configuration(transcriber))
    finally:
        CURRENT.reset(token)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert rows[-1]['error_category'] == 'validation_diarization'
    assert 'SYNTHETIC_SECRET' not in stream.getvalue()
