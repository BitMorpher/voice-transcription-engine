"""Offline grouped-edit fidelity, request reduction, recovery and SDK contracts."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2
from openai import OpenAI
import pytest

from src.attributed_editing import grouped_turns
from src.model_config import EditingOptions
from src.provider_control import CURRENT_CONTROL, ProviderControl, ProviderStopped
from src.transcriber import Transcriber, TranscriptionError


def fixture(speech):
    raw, turns = '', []
    for index, text in enumerate(speech, 1):
        # Include recording separator and private metadata that never enters requests.
        gap = '\r\n' if index == 3 else ''
        raw += gap
        start = len(raw)
        header = f'[speaker {index % 2} | SYNTHETIC_PRIVATE_NAME | part 1]\r\n'
        raw += header
        a = len(raw)
        raw += text
        b = len(raw)
        raw += '\n\n'
        turns.append({'turn_id': f'turn-{index:06d}', 'start': start, 'end': len(raw),
                      'speech_start': a, 'speech_end': b,
                      'speech_sha256': hashlib.sha256(text.encode()).hexdigest()})
    return raw, turns


def respond(parameters):
    body = json.loads(parameters['messages'][-1]['content'])
    content = json.dumps({'group_index': body['group_index'], 'edits': [
        {**turn, 'speaker_uncertain': True} for turn in body['turns']]}, ensure_ascii=False)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=content, refusal=None))])


def client():
    result = MagicMock()
    result.chat.completions.create.side_effect = lambda **kw: respond(kw)
    return result


def test_short_turns_reduce_requests_while_preserving_every_boundary(tmp_path):
    raw, turns = fixture(['Yes.', 'I remember that day.', '', ' \r\n', 'कृष्ण café e\u0301 ₹5 😀.'] * 64)
    provider = client()
    transcriber = Transcriber(client=provider, editing_options=EditingOptions(
        model='gpt-6.1-sol', reasoning_effort='low'))
    assert transcriber.enhance_attributed(raw, turns, checkpoint_root=tmp_path) == raw
    # 320 turns, 192 nonempty: old per-turn path needed 192 SDK operations.
    assert provider.chat.completions.create.call_count == 10
    for call in provider.chat.completions.create.call_args_list:
        payload = json.loads(call.kwargs['messages'][-1]['content'])
        assert len(payload['turns']) <= 32
        assert sum(len(t['text'].encode()) for t in payload['turns']) <= 6000
        assert 'SYNTHETIC_PRIVATE_NAME' not in json.dumps(call.kwargs)
    transcriber.enhance_attributed(raw, turns, checkpoint_root=tmp_path)
    assert provider.chat.completions.create.call_count == 10


def test_long_unicode_turn_piece_fidelity(tmp_path):
    raw, turns = fixture(['कृष्ण e\u0301 ₹5 👩\u200d💻. ' * 80, '', 'A short reply.'])
    provider = client()
    editor = Transcriber(client=provider, editing_options=EditingOptions(chunk_bytes=64))
    groups = grouped_turns(raw, turns, 64)
    assert ''.join(t['text'] for group in groups for t in group) == ''.join(
        raw[t['speech_start']:t['speech_end']] for t in turns)
    assert editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path) == raw


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'reorder', 'identity', 'piece',
                                 'words', 'sanskrit', 'name', 'empty', 'duplicate_key', 'length'])
def test_group_rejects_bad_output_and_retains_no_checkpoint(tmp_path, bad):
    raw, turns = fixture(['राम Sam remembers.', 'Yes.', ''])
    provider = client()
    def broken(**kw):
        response = respond(kw)
        body = json.loads(response.choices[0].message.content)
        if bad == 'missing':
            body['edits'].pop()
        elif bad == 'duplicate':
            body['edits'][1] = body['edits'][0]
        elif bad == 'reorder':
            body['edits'].reverse()
        elif bad == 'identity':
            body['edits'][0]['turn_id'] = 'turn-999999'
        elif bad == 'piece':
            body['edits'][0]['piece_index'] = True
        elif bad in ('words', 'sanskrit', 'name'):
            body['edits'][0]['text'] = {'words': 'Sam remembers राम.',
                'sanskrit': 'रामा Sam remembers.', 'name': 'राम Samuel remembers.'}[bad]
        elif bad == 'empty':
            body['edits'][2]['text'] = 'Invented memory.'
        elif bad == 'length':
            response.choices[0].finish_reason = 'length'
        response.choices[0].message.content = json.dumps(body)
        if bad == 'duplicate_key':
            response.choices[0].message.content = '{"group_index":1,"group_index":1,"edits":[]}'
        return response
    provider.chat.completions.create.side_effect = broken
    with pytest.raises(TranscriptionError):
        Transcriber(client=provider).enhance_attributed(raw, turns, checkpoint_root=tmp_path)
    assert not list(tmp_path.rglob('response.json'))
    assert provider.chat.completions.create.call_count == 1


@pytest.mark.parametrize('failure', ['provider', 'cancel', 'interrupt'])
def test_failed_group_resume_reuses_prior_success(tmp_path, failure):
    raw, turns = fixture(['A short reply.'] * 96)
    provider = client()
    control = ProviderControl()
    token = CURRENT_CONTROL.set(control)
    def fail(**kw):
        if json.loads(kw['messages'][-1]['content'])['group_index'] == 2:
            if failure == 'interrupt':
                raise KeyboardInterrupt()
            if failure == 'cancel':
                control.cancel()
                return respond(kw)
            raise RuntimeError('SYNTHETIC_PRIVATE_PAYLOAD')
        return respond(kw)
    provider.chat.completions.create.side_effect = fail
    editor = Transcriber(client=provider)
    try:
        with pytest.raises((TranscriptionError, ProviderStopped, KeyboardInterrupt)):
            editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path)
    finally:
        CURRENT_CONTROL.reset(token)
    saved = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    before = provider.chat.completions.create.call_count
    provider.chat.completions.create.side_effect = lambda **kw: respond(kw)
    assert editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path) == raw
    assert provider.chat.completions.create.call_count - before == (1 if failure == 'cancel' else 2)
    assert all(p.read_bytes() == data for p, data in saved.items())


def test_tamper_preflights_all_groups_before_calls(tmp_path):
    raw, turns = fixture(['A short reply.'] * 96)
    provider = client()
    editor = Transcriber(client=provider)
    editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path)
    list(tmp_path.rglob('response.json'))[-1].write_text('tampered')
    calls = provider.chat.completions.create.call_count
    with pytest.raises(TranscriptionError):
        editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path)
    assert provider.chat.completions.create.call_count == calls


def test_invalid_source_metadata_and_empty_groups_never_call(tmp_path):
    raw, turns = fixture(['', ' \r\n'])
    provider = client()
    editor = Transcriber(client=provider)
    assert editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path) == raw
    turns[0]['speech_sha256'] = 'a' * 64
    with pytest.raises(TranscriptionError):
        editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path)
    provider.chat.completions.create.assert_not_called()


def test_independent_checkpoint_concurrency(tmp_path):
    def run(index):
        raw, turns = fixture(['A short reply.'] * 64)
        provider = client()
        editor = Transcriber(client=provider)
        assert editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path / str(index)) == raw
        return provider.chat.completions.create.call_count
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(run, range(4))) == [2] * 4


def test_locked_sdk_group_schema_and_low_effort(tmp_path):
    requests = []
    def handle(request):
        parameters = json.loads(request.content)
        requests.append(parameters)
        body = respond(parameters).choices[0].message.content
        return httpx2.Response(200, json={'id': 'synthetic', 'object': 'chat.completion',
            'created': 0, 'model': 'gpt-6.1-sol', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': body}}]})
    raw, turns = fixture(['Yes.', 'राम stays as supplied.'])
    with httpx2.Client(transport=httpx2.MockTransport(handle)) as transport:
        with OpenAI(api_key='synthetic-placeholder', http_client=transport, max_retries=0) as provider:
            editor = Transcriber(client=provider, editing_options=EditingOptions(
                model='gpt-6.1-sol', reasoning_effort='low'))
            assert editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path) == raw
    assert requests[0]['reasoning_effort'] == 'low'
    assert requests[0]['max_completion_tokens'] >= 8192
    assert requests[0]['store'] is False
    assert requests[0]['response_format']['json_schema']['strict'] is True


def test_group_fingerprint_binds_prompt_schema_and_bounds(monkeypatch):
    import src.attributed_editing as grouped
    editor = Transcriber(client=client())
    initial = editor.attributed_editing_fingerprint
    monkeypatch.setattr(grouped, 'GROUP_INSTRUCTION', grouped.GROUP_INSTRUCTION + ' Changed contract.')
    assert editor.attributed_editing_fingerprint != initial
    changed = editor.attributed_editing_fingerprint
    monkeypatch.setattr(grouped, 'MAX_GROUP_TURNS', 16)
    assert editor.attributed_editing_fingerprint != changed


def test_comparison_fixture_measures_baseline_and_grouped_operations():
    from src.text_comparison import SyntheticClient, synthetic_fixture
    from src.text_requests import collect_text_metrics
    raw, turns = synthetic_fixture()
    editor = Transcriber(client=SyntheticClient(), editing_options=EditingOptions(
        model='gpt-6.1-sol', reasoning_effort='low'))
    # Execute the former per-turn behavior against the same local provider and
    # source, rather than deriving its request count from a fixture's length.
    with collect_text_metrics() as baseline:
        for turn in turns:
            speech = raw[turn['speech_start']:turn['speech_end']]
            if speech.strip():
                assert editor.enhance_transcription(speech) == speech
    with collect_text_metrics() as grouped:
        assert editor.enhance_attributed(raw, turns) == raw
    assert baseline.snapshot()['sdk_operations_started'] == 103
    assert grouped.snapshot()['sdk_operations_started'] == 4
