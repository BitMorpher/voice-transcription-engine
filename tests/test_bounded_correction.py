"""Offline regressions for strict correction without losing evidence or checkpoints."""
import io
import json
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import httpx2
from openai import OpenAI
import pytest

from src.author_review import ReviewOptions, review_transcript, validate_review_report
from src.model_config import EditingOptions
from src.progress import CURRENT, Reporter, safe_validation_diagnostics
from src.provider_control import CURRENT_CONTROL, ControlledClient, ProviderControl, ProviderStopped
from src.transcriber import Transcriber, TranscriptionError
from tests.test_attributed_editing import fixture, respond
from tests.test_review_evidence import ack, quote
from tests.test_text_requests import completion


@contextmanager
def execution(retries=2, limit=3):
    control = ProviderControl(retries=retries, validation_failure_limit=limit)
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0)
    token = CURRENT_CONTROL.set(control)
    reporting = CURRENT.set(reporter)
    try:
        yield control, reporter, stream
    finally:
        CURRENT.reset(reporting)
        CURRENT_CONTROL.reset(token)


@pytest.mark.parametrize('mutation', ['repetition', 'name', 'sanskrit', 'symbol', 'move'])
@pytest.mark.parametrize('recover', [False, True])
def test_group_correction_preserves_strict_fidelity_and_reuses_earlier_groups(tmp_path, mutation, recover):
    raw, turns = fixture(['Synthetic statement.'] * 32 + ['Mira राम yes yes ₹5.', 'A reply.'])
    client = MagicMock()
    attempts = []
    def answer(**kw):
        body = json.loads(kw['messages'][-1]['content'])
        attempts.append(body['group_index'])
        result = respond(kw)
        if body['group_index'] == 2 and (attempts.count(2) == 1 or not recover):
            value = json.loads(result.choices[0].message.content)
            replacements = {'repetition': ('yes yes', 'yes'), 'name': ('Mira', 'Myra'),
                            'sanskrit': ('राम', 'रम'), 'symbol': ('₹', '$'), 'move': ('Mira ', '')}
            a, b = replacements[mutation]
            value['edits'][0]['text'] = value['edits'][0]['text'].replace(a, b)
            if mutation == 'move':
                value['edits'][1]['text'] = 'Mira ' + value['edits'][1]['text']
            result = completion(json.dumps(value))
        return result
    client.chat.completions.create.side_effect = answer
    editor = Transcriber(client=client, editing_options=EditingOptions(model='gpt-6.1-sol'))
    with execution(limit=1) as (control, reporter, stream):
        if recover:
            assert editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path) == raw
        else:
            with pytest.raises(TranscriptionError):
                editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path)
        assert control.requests == 3 and attempts == [1, 2, 2]
        assert control.validation_failures == int(not recover)
        assert control.reason == (None if recover else 'validation_failures')
        assert reporter.text_metrics.snapshot()['validation_retries'] == 1
    saved = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.name == 'response.json'}
    assert len(saved) == (2 if recover else 1)
    client.chat.completions.create.side_effect = lambda **kw: respond(kw)
    before = client.chat.completions.create.call_count
    assert editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path) == raw
    assert client.chat.completions.create.call_count - before == int(not recover)
    assert all(p.read_bytes() == data for p, data in saved.items())
    rows = list(map(json.loads, stream.getvalue().splitlines()))
    diagnostics = [r['validation_diagnostics'] for r in rows if r.get('validation_diagnostics')]
    assert diagnostics and diagnostics[0]['group_index'] == 2
    assert diagnostics[0]['turn_index'] == diagnostics[0]['piece_index'] == 1
    assert 'Mira' not in stream.getvalue() and 'राम' not in stream.getvalue()


@pytest.mark.parametrize('correction', ['correct', 'drop_invalid', 'drop_valid', 'move_valid', 'change_reason', 'still_invalid'])
def test_review_correction_cannot_silently_remove_or_move_findings(tmp_path, correction):
    raw = 'First exact criticism. Second exact criticism. Third exact sentence.'
    client = MagicMock()
    def answer(**kw):
        payload = json.loads(kw['messages'][-1]['content'])
        ids = payload['core_piece_ids']
        findings = [quote(ids, 'First exact criticism.'), quote(ids, 'Second altered criticism.')]
        if client.chat.completions.create.call_count == 2:
            findings[1]['excerpt'] = 'Second exact criticism.'
            if correction == 'drop_invalid':
                findings.pop()
            elif correction == 'drop_valid':
                findings.pop(0)
            elif correction == 'move_valid':
                findings[0]['excerpt'] = 'Third exact sentence.'
            elif correction == 'change_reason':
                findings[1].update(category='allegation', severity='high', reason_code='serious_allegation')
            elif correction == 'still_invalid':
                findings[1]['excerpt'] = 'Second altered criticism.'
        return completion(json.dumps(ack(payload, findings)))
    client.chat.completions.create.side_effect = answer
    with execution() as (control, reporter, stream):
        report = review_transcript(raw, ControlledClient(client, 120, 2), checkpoint_root=tmp_path)
        assert control.requests == 2
        assert control.validation_failures == int(correction != 'correct')
    if correction == 'correct':
        validate_review_report(raw, report)
        assert len(report['findings']) == 2
    else:
        assert report['status'] == 'failed'
        assert not list(tmp_path.rglob('response.json'))
    assert 'criticism' not in stream.getvalue()


@pytest.mark.parametrize('retries,expected', [(0, 1), (2, 2)])
def test_actual_sdk_correction_keeps_sol_effort_source_schema_and_no_store(tmp_path, retries, expected):
    raw = 'Synthetic café e\u0301 राम yes yes 😀.'
    requests = []
    def answer(request):
        body = json.loads(request.content)
        requests.append(body)
        payload = json.loads(body['messages'][-1]['content'])
        text = raw.replace('yes yes', 'yes') if len(requests) == 1 else raw
        content = json.dumps(ack(payload, [quote(payload['core_piece_ids'], text)]), ensure_ascii=False)
        return httpx2.Response(200, json={'id': 'synthetic', 'object': 'chat.completion',
            'created': 0, 'model': 'gpt-6.1-sol', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': content}}]})
    with httpx2.Client(transport=httpx2.MockTransport(answer)) as transport:
        with OpenAI(api_key='synthetic-placeholder', http_client=transport, max_retries=0) as client:
            with execution(retries=retries) as (control, reporter, stream):
                report = review_transcript(raw, ControlledClient(client, 120, retries),
                    ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium'), checkpoint_root=tmp_path)
    assert len(requests) == control.requests == expected
    assert report['status'] == ('complete' if retries else 'failed')
    for body in requests:
        assert body['model'] == 'gpt-6.1-sol' and body['reasoning_effort'] == 'medium'
        assert body['store'] is False
        assert body['response_format'] == requests[0]['response_format']
        assert body['messages'][-1] == requests[0]['messages'][-1]
    if retries:
        assert requests[1]['messages'][-2]['role'] == 'assistant'
    rows = list(map(json.loads, stream.getvalue().splitlines()))
    operations = [r for r in rows if 'sdk_operation' in r]
    assert len(operations) == expected * 2
    for index in range(expected):
        start, end = operations[index * 2:index * 2 + 2]
        assert start['sdk_operation'] == end['sdk_operation'] == index + 1
        assert start['sdk_status'] == 'started' and end['sdk_status'] == 'returned'
        assert end['sdk_seconds'] >= 0
        assert datetime.fromisoformat(start['timestamp_utc']) <= datetime.fromisoformat(end['timestamp_utc'])
    assert raw not in stream.getvalue()


def test_quote_wrong_piece_diagnostic_is_boolean_and_correction_is_exact(tmp_path):
    raw = 'First unique quote. ' + 'padding ' * 160 + 'Last unique quote.'
    client = MagicMock()
    def answer(**kw):
        payload = json.loads(kw['messages'][-1]['content'])
        first, last = payload['core_piece_ids'][0], payload['core_piece_ids'][-1]
        selected = first if client.chat.completions.create.call_count == 1 else last
        return completion(json.dumps(ack(payload, [quote([selected], 'Last unique quote.')])))
    client.chat.completions.create.side_effect = answer
    with execution() as (_, _, stream):
        report = review_transcript(raw, ControlledClient(client, 120, 2), checkpoint_root=tmp_path)
    validate_review_report(raw, report)
    rows = list(map(json.loads, stream.getvalue().splitlines()))
    assert any(r.get('validation_diagnostics', {}).get('quote_found_elsewhere') is True for r in rows)


def test_diagnostics_allowlist_rejects_private_strings_boolean_counts_and_arbitrary_fields():
    supplied = dict(source_tokens=True, response_tokens='PRIVATE', group_index=2,
        quote_found_elsewhere=True, excerpt='PRIVATE', turn_id='PRIVATE',
        mismatch_type='PRIVATE', finding_index=-1)
    assert safe_validation_diagnostics(supplied) == {'group_index': 2, 'quote_found_elsewhere': True}


@pytest.mark.parametrize('attributed', [False, True])
def test_stopped_correction_retains_valid_checkpoints_and_never_counts_as_another_failure(tmp_path, attributed):
    raw, turns = fixture(['Synthetic words.'])
    client = MagicMock()
    with execution() as (control, _, stream):
        def answer(**kw):
            control.cancel()
            if attributed:
                result = respond(kw)
                value = json.loads(result.choices[0].message.content)
                value['edits'][0]['text'] = 'Changed source.'
            else:
                value = {'chunk_index': 1, 'text': 'Changed source.', 'speaker_uncertain': False}
            return completion(json.dumps(value))
        client.chat.completions.create.side_effect = answer
        editor = Transcriber(client=client)
        with pytest.raises(ProviderStopped):
            if attributed:
                editor.enhance_attributed(raw, turns, checkpoint_root=tmp_path)
            else:
                editor.enhance_transcription('Synthetic words.', checkpoint_root=tmp_path)
        assert control.requests == 1 and control.validation_failures == 0
    assert not list(tmp_path.rglob('response.json'))
    assert not any(r.get('stage_status') == 'failed' for r in map(json.loads, stream.getvalue().splitlines()))


@pytest.mark.parametrize('prior_success', [False, True])
def test_stopped_review_exports_incomplete_coverage_and_retains_cached_holes(tmp_path, prior_success):
    from src.author_review import _chunks
    from src.review_export import export_review
    from tests.test_text_requests import reviewer
    raw = 'Synthetic evidence sentence.\n' * 10
    options = ReviewOptions(chunk_bytes=64)
    client = reviewer()
    answer = client.chat.completions.create.side_effect
    def fail_first(**kw):
        if json.loads(kw['messages'][-1]['content'])['chunk_index'] == 1:
            raise RuntimeError('Synthetic failure')
        return answer(**kw)
    if prior_success:
        client.chat.completions.create.side_effect = fail_first
        report = review_transcript(raw, client, options, checkpoint_root=tmp_path / 'cache')
        assert report['status'] == 'incomplete'
    before = {p: p.read_bytes() for p in tmp_path.rglob('response.json')}
    calls = client.chat.completions.create.call_count
    with execution() as (control, _, _):
        control.cancel()
        report = review_transcript(raw, ControlledClient(client, 120, 2), options,
                                   checkpoint_root=tmp_path / 'cache')
    assert client.chat.completions.create.call_count == calls
    assert report['status'] == 'incomplete'
    coverage = report['coverage']['chunks']
    assert coverage[0]['status'] == 'not_attempted'
    expected = 'complete' if prior_success else 'not_attempted'
    assert all(c['status'] == expected for c in coverage[1:])
    assert len(coverage) == len(list(_chunks(raw, 64)))
    assert all(p.read_bytes() == data for p, data in before.items())
    export_review(report, tmp_path / 'review.xlsx')
    assert (tmp_path / 'review.xlsx').is_file()
