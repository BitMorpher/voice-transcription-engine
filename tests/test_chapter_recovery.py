"""Synthetic chapter-only correction, privacy, admission and resume regressions."""
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.chapters import ChapterError, ChapterOptions, _validate_response, draft_chapters
from src.progress import CURRENT, Reporter, collect_text_metrics, safe_validation_diagnostics
from src.provider_control import CURRENT_CONTROL, ControlledClient, ProviderControl, ProviderStopped
from tests.test_chapters import narrative_client, report_for


@contextmanager
def controlled(client, *, retries=2, limit=3):
    control = ProviderControl(retries=retries, validation_failure_limit=limit)
    stream = io.StringIO()
    token = CURRENT_CONTROL.set(control)
    reporting = CURRENT.set(Reporter(stream, heartbeat=0))
    try:
        with collect_text_metrics() as metrics:
            yield ControlledClient(client, 120, retries), control, stream, metrics
    finally:
        CURRENT.reset(reporting)
        CURRENT_CONTROL.reset(token)


@pytest.mark.parametrize('mutation,reason', [
    (lambda b, s: b['passages'][0].update(text=''), 'empty_passage'),
    (lambda b, s: b['passages'][0].update(text='SYNTHETIC_REJECTED_BODY'), 'excerpt_mismatch'),
    (lambda b, s: b['passages'][0].update(kind='source_preserving', text='I know.'), 'word_sequence'),
    (lambda b, s: b['passages'][0].update(kind='source_preserving', text='I "am uncertain".'), 'quotation_anchors'),
])
def test_text_only_correction_is_validated_counted_and_private(tmp_path, mutation, reason):
    raw = 'I am uncertain.'
    bad, good = narrative_client(mutation), narrative_client()
    calls = []
    def keep_kind(body, supplied):
        if reason in {'word_sequence', 'quotation_anchors'}:
            body['passages'][0]['kind'] = 'source_preserving'
    valid = narrative_client(keep_kind)
    def respond(**request):
        calls.append(request)
        response = (bad if len(calls) == 1 else valid).chat.completions.create(**request)
        response.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        return response
    good.chat.completions.create.side_effect = respond
    with controlled(good) as (client, control, stream, metrics):
        result = draft_chapters(raw, report_for(raw), client, checkpoint_root=tmp_path)
        snapshot = metrics.snapshot()
    assert result['status'] == 'complete'
    assert control.requests == 2 and control.failures == control.validation_failures == 0
    assert snapshot['sdk_operations_started'] == snapshot['sdk_operations_completed'] == 2
    assert snapshot['validation_retries'] == 1 and snapshot['total_tokens'] == 60
    assert calls[0]['messages'][-1] == calls[1]['messages'][-1]
    assert calls[0]['response_format'] == calls[1]['response_format']
    assert 'coverage_omissions exactly unchanged' in calls[1]['messages'][0]['content']
    assert 'apostrophe' in calls[1]['messages'][0]['content']
    assert reason in stream.getvalue() and raw not in stream.getvalue()
    assert 'SYNTHETIC_REJECTED_BODY' not in stream.getvalue()
    saved = list(tmp_path.rglob('response.json'))
    assert len(saved) == 1 and 'SYNTHETIC_REJECTED_BODY' not in saved[0].read_text()


@pytest.mark.parametrize('retries,starts', [(0, 1), (2, 2), (5, 2)])
def test_failed_correction_counts_once_never_saves_or_falls_back(tmp_path, retries, starts):
    raw = 'SYNTHETIC_SOURCE_MARKER.'
    bad = narrative_client(lambda b, s: b['passages'][0].update(text='SYNTHETIC_REJECTED_BODY'))
    with controlled(bad, retries=retries, limit=1) as (client, control, stream, metrics):
        with pytest.raises(ChapterError) as captured:
            draft_chapters(raw, report_for(raw), client, checkpoint_root=tmp_path)
        assert captured.value.diagnostics['chapter_reason'] == 'excerpt_mismatch'
        assert control.requests == starts and control.validation_failures == 1
        assert control.failures == 0 and control.reason == 'validation_failures'
        assert not list(tmp_path.rglob('response.json'))
        assert raw not in stream.getvalue() and 'SYNTHETIC_REJECTED_BODY' not in stream.getvalue()
        with pytest.raises(ProviderStopped):
            draft_chapters(raw, report_for(raw), client, checkpoint_root=tmp_path)
        assert control.requests == starts


@pytest.mark.parametrize('repair', ['omit', 'split', 'kind', 'references', 'omission_reason'])
def test_correction_cannot_hide_failure_by_changing_accepted_layout(tmp_path, repair):
    raw = 'First testimony.\nSecond testimony.\nThird testimony.'
    calls = 0
    def mutate(body, supplied):
        nonlocal calls
        calls += 1
        units = supplied['source_units']
        body['passages'] = [{'unit_ids': [u['unit_id'] for u in units[:2]],
                             'kind': 'verbatim_excerpt',
                             'text': ''.join(u['text'] for u in units[:2]).strip()}]
        body['coverage_omissions'] = [{'unit_ids': [units[2]['unit_id']], 'reason': 'fragment'}]
        if calls == 1:
            body['passages'][0]['text'] = 'SYNTHETIC_REJECTED_BODY'
        elif repair == 'omit':
            body['passages'] = []
            body['coverage_omissions'] = [{'unit_ids': [u['unit_id'] for u in units], 'reason': 'fragment'}]
        elif repair == 'split':
            body['passages'] = [{'unit_ids': [u['unit_id']], 'kind': 'verbatim_excerpt',
                                 'text': u['text'].strip()} for u in units[:2]]
        elif repair == 'kind':
            body['passages'][0]['kind'] = 'source_preserving'
        elif repair == 'references':
            body['passages'][0].update(unit_ids=[units[0]['unit_id']], text=units[0]['text'].strip())
            body['coverage_omissions'][0]['unit_ids'] = [u['unit_id'] for u in units[1:]]
        else:
            body['coverage_omissions'][0]['reason'] = 'repetition'
    with controlled(narrative_client(mutate)) as (client, control, stream, metrics):
        with pytest.raises(ChapterError) as captured:
            draft_chapters(raw, report_for(raw), client, checkpoint_root=tmp_path)
        assert captured.value.diagnostics['chapter_reason'] == 'correction_layout'
        assert control.requests == 2 and control.validation_failures == 1
    assert not list(tmp_path.rglob('response.json'))


@pytest.mark.parametrize('action', ['cancel', 'interrupt', 'fail'])
def test_late_chunk_resume_keeps_good_checkpoint(tmp_path, action):
    raw = 'First testimony.\nSecond testimony.\nThird testimony.\n' * 3
    options = ChapterOptions(chunk_bytes=64)
    bad = narrative_client(lambda b, s: b['passages'][0].update(text='SYNTHETIC_REJECTED_BODY'))
    client = narrative_client()
    normal = client.chat.completions.create.side_effect
    with controlled(client) as (wrapped, control, stream, metrics):
        def respond(**request):
            supplied = json.loads(request['messages'][-1]['content'])
            if supplied['chunk_index'] == 2:
                if action == 'interrupt':
                    raise KeyboardInterrupt()
                if action == 'cancel':
                    control.cancel()
                return bad.chat.completions.create(**request)
            return normal(**request)
        client.chat.completions.create.side_effect = respond
        expected = {'cancel': ProviderStopped, 'interrupt': KeyboardInterrupt, 'fail': ChapterError}[action]
        with pytest.raises(expected):
            draft_chapters(raw, report_for(raw), wrapped, options, checkpoint_root=tmp_path)
        assert control.validation_failures == (1 if action == 'fail' else 0)
        assert control.requests == (3 if action == 'fail' else 2)
        if action == 'cancel':
            assert 'not_attempted' in stream.getvalue() and 'incomplete' in stream.getvalue()
    saved = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert len(list(tmp_path.rglob('response.json'))) == 1
    client.chat.completions.create.side_effect = normal
    with controlled(client) as (wrapped, control, stream, metrics):
        result = draft_chapters(raw, report_for(raw), wrapped, options, checkpoint_root=tmp_path)
        assert metrics.snapshot()['cache_hits'] == 1
        assert set(result['chapters']) == {'interview', 'narrative'} and result['status'] == 'complete'
    assert all(p.read_bytes() == body for p, body in saved.items())
    calls = client.chat.completions.create.call_count
    with controlled(client) as (wrapped, control, stream, metrics):
        draft_chapters(raw, report_for(raw), wrapped, options, checkpoint_root=tmp_path)
        assert control.requests == 0
    assert client.chat.completions.create.call_count == calls


@pytest.mark.parametrize('mutation,reason,category', [
    (lambda b: b.update(chunk_index=2), 'json_shape', 'validation_schema'),
    (lambda b: b['passages'][0].update(unit_ids=['PRIVATE_BAD_ID']), 'unit_reference', 'validation_source'),
    (lambda b: b['passages'][0].update(unit_ids=['unit-1', 'unit-1']), 'unit_order', 'validation_source'),
    (lambda b: b['passages'][0].update(kind='PRIVATE_BAD_KIND'), 'passage_kind', 'validation_source'),
    (lambda b: b.update(passages=[]), 'coverage', 'validation_coverage'),
])
def test_structural_diagnostics_are_safe_and_source_repair_is_ineligible(mutation, reason, category):
    chunk = [{'unit_id': 'unit-1', 'segment_id': 'segment-1', 'start': 0, 'end': 7, 'text': 'Source.'}]
    body = {'chunk_index': 1, 'passages': [{'unit_ids': ['unit-1'], 'text': 'Source.',
                                         'kind': 'verbatim_excerpt'}], 'coverage_omissions': []}
    mutation(body)
    with pytest.raises(ChapterError) as captured:
        _validate_response(json.dumps(body), chunk, 1, 'first', [])
    error = captured.value
    assert error.category == category and error.diagnostics['chapter_reason'] == reason
    assert error.recovery_validator is None
    assert 'PRIVATE' not in str(error) and 'PRIVATE' not in json.dumps(error.diagnostics)


def test_diagnostics_boundary_rejects_injected_text_and_invalid_indexes():
    assert safe_validation_diagnostics({'chapter_reason': 'PRIVATE', 'passage_index': -1,
        'unit_index': True, 'chunk_index': 'PRIVATE', 'text': 'PRIVATE'}) == {}
    assert safe_validation_diagnostics({'chapter_reason': 'word_sequence', 'passage_index': 0,
                                      'unit_index': 1, 'chunk_index': 2}) == {
        'chapter_reason': 'word_sequence', 'passage_index': 0, 'unit_index': 1, 'chunk_index': 2}


def test_provider_failure_during_correction_is_not_a_validation_failure(tmp_path):
    raw = 'Synthetic uncertainty.'
    client = narrative_client(lambda b, s: b['passages'][0].update(text='SYNTHETIC_REJECTED_BODY'))
    initial = client.chat.completions.create.side_effect
    def respond(**request):
        if client.chat.completions.create.call_count == 2:
            raise RuntimeError('SYNTHETIC_PROVIDER_SECRET')
        return initial(**request)
    client.chat.completions.create.side_effect = respond
    with controlled(client) as (wrapped, control, stream, metrics):
        with pytest.raises(ChapterError) as captured:
            draft_chapters(raw, report_for(raw), wrapped, checkpoint_root=tmp_path)
        assert 'SYNTHETIC_PROVIDER_SECRET' not in str(captured.value)
        assert control.requests == 2 and control.failures == 1 and control.validation_failures == 0
        assert metrics.snapshot()['sdk_operations_failed'] == 1
        assert metrics.snapshot()['validation_retries'] == 1
    assert not list(tmp_path.rglob('response.json'))


@pytest.mark.parametrize('person', ['first', 'third'])
def test_correction_preserves_third_person_contract_and_explicit_omissions(tmp_path, person):
    raw = 'I am uncertain.\nRepeated uncertainty.\n'
    calls = 0
    def mutate(body, supplied):
        nonlocal calls
        calls += 1
        first, second = supplied['source_units']
        body['passages'] = [{'unit_ids': [first['unit_id']], 'kind': 'verbatim_excerpt',
                             'text': 'Invalid testimony.' if calls == 1 else first['text'].strip()}]
        body['coverage_omissions'] = [{'unit_ids': [second['unit_id']], 'reason': 'repetition'}]
    with controlled(narrative_client(mutate)) as (client, control, stream, metrics):
        result = draft_chapters(raw, report_for(raw), client,
            ChapterOptions(styles=('narrative',), person=person), checkpoint_root=tmp_path)
    chapter = result['chapters']['narrative']
    assert control.requests == 2 and chapter['coverage']['complete'] is False
    assert chapter['coverage_omissions'][0]['reason'] == 'repetition'
    assert chapter['passages'][0]['text'] == 'I am uncertain.'
    assert chapter['passages'][0]['kind'] == 'verbatim_excerpt'



def test_mixed_wording_and_missing_coverage_cannot_retry_into_new_omissions(tmp_path):
    raw = 'First uncertain testimony.\nSecond uncertain testimony.\n'
    def bad(body, supplied):
        body['passages'][0].update(unit_ids=[supplied['source_units'][0]['unit_id']],
                                   text='Invented testimony.')
    with controlled(narrative_client(bad)) as (client, control, stream, metrics):
        with pytest.raises(ChapterError) as captured:
            draft_chapters(raw, report_for(raw), client, checkpoint_root=tmp_path)
        assert captured.value.category == 'validation_source'
        assert captured.value.diagnostics['chapter_reason'] == 'excerpt_mismatch'
        assert control.requests == 1 and control.validation_failures == 1
        assert metrics.snapshot()['validation_retries'] == 0
    assert not list(tmp_path.rglob('response.json'))
