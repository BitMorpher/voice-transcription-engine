"""Invented evidence cases, including real locked-SDK wire serialization."""
import copy
import json
from unittest.mock import MagicMock

import httpx2
from openai import OpenAI
import pytest

from src.author_review import (EVIDENCE_BYTES, ReviewError, ReviewOptions, _chunks,
    _evidence_pieces, _hash, _payload, _prompt, _validate, review_transcript, validate_review_report)
from tests.test_text_requests import completion


def ack(payload, findings=None):
    return {'contract_version': payload['contract_version'], 'chunk_index': payload['chunk_index'],
            'fully_reviewed': True, 'reviewed_piece_ids': payload['core_piece_ids'],
            'findings': findings or []}


def quote(ids, text):
    return {'category': 'wording_ambiguity', 'severity': 'low', 'reason_code': 'ambiguity_wording',
            'piece_ids': ids, 'excerpt': text}


def validate(raw, chunk, findings=None, **changes):
    return _validate(json.dumps(ack(_payload(raw, chunk), findings) | changes), chunk, raw)


def whole(raw):
    return next(_chunks(raw, 6000))


@pytest.mark.parametrize('raw', ['café\r\nदेवनागरी\u200d😀 e\u0301尾',
                                'prefix\r\nquote\r\nend', 'a' * 3100 + '😀tail',
                                'x\n' * 3000, 'begin' + ' ' * 500 + 'end'])
def test_piece_partition_preserves_every_character_and_bounds_ids(raw):
    chunks = list(_chunks(raw, 128 if len(raw) < 5000 else 6000))
    for chunk in chunks:
        pieces = _evidence_pieces(raw, chunk)
        assert ''.join(p['text'] for p in pieces) == raw[chunk['context_start']:chunk['context_end']]
        assert all(p['text'] == raw[p['start']:p['end']] for p in pieces)
        assert all(len(p['text'].encode()) <= EVIDENCE_BYTES for p in pieces)
        assert all(not p['start'] < boundary < p['end'] for p in pieces
                   for boundary in (chunk['start'], chunk['end']))
        assert len(pieces) < 40
        assert validate(raw, chunk) == []
    for first, second in zip(chunks, chunks[1:]):
        shared = {(p['start'], p['end'], p['piece_id']) for p in _evidence_pieces(raw, first)
                  if second['start'] <= p['start'] and p['end'] <= second['end']}
        core = {(p['start'], p['end'], p['piece_id']) for p in _evidence_pieces(raw, second)
                if second['start'] <= p['start'] and p['end'] <= second['end']}
        assert shared == core


@pytest.mark.parametrize('raw,excerpt', [
    ('😀prefix\r\nA café e\u0301 quote.\r\n尾', 'A café e\u0301 quote.\r\n'),
    ('left\r\nright', 't\r\nr'), ('one\u200dtwo', '\u200d'), ('😀😀word😀', 'word😀')])
def test_quote_offsets_are_python_slices_without_model_arithmetic(raw, excerpt):
    chunk = whole(raw)
    ids = _payload(raw, chunk)['core_piece_ids']
    row, = validate(raw, chunk, [quote(ids, excerpt)])
    assert row['start'] == raw.index(excerpt)
    assert row['end'] == row['start'] + len(excerpt)
    assert raw[row['start']:row['end']] == excerpt
    assert 'piece_ids' not in row  # Published exact-offset/provenance contract stays stable.


@pytest.mark.parametrize('excerpt', ['cafe', 'café\n', 'cafe\u0301\r\n', 'invented'])
def test_altered_missing_or_normalized_quote_is_rejected(excerpt):
    raw = 'prefix café\r\nend'
    chunk = whole(raw)
    with pytest.raises(ReviewError) as error:
        validate(raw, chunk, [quote(_payload(raw, chunk)['core_piece_ids'], excerpt)])
    assert error.value.category == 'validation_quote_missing'


@pytest.mark.parametrize('raw,excerpt', [('same same', 'same'), ('aaaa', 'aaa')])
def test_ambiguous_including_overlapping_occurrences_are_rejected(raw, excerpt):
    chunk = whole(raw)
    ids = _payload(raw, chunk)['core_piece_ids']
    with pytest.raises(ReviewError) as error:
        validate(raw, chunk, [quote(ids, excerpt)])
    assert error.value.category == 'validation_quote_ambiguous'
    # A longer exact quote explicitly disambiguates without fabricated offsets.
    row, = validate(raw, chunk, [quote(ids, raw)])
    assert (row['start'], row['end']) == (0, len(raw))


def test_repeated_quote_in_separate_pieces_resolves_selected_piece():
    raw = 'same ' + 'x' * 1100 + ' same'
    chunk = whole(raw)
    pieces = _evidence_pieces(raw, chunk)
    row, = validate(raw, chunk, [quote([pieces[-1]['piece_id']], 'same')])
    assert row['start'] == raw.rindex('same')


@pytest.mark.parametrize('kind', ['empty', 'unknown', 'duplicate', 'reverse', 'gap', 'irrelevant'])
def test_reference_set_must_be_known_contiguous_ordered_and_intersected(kind):
    raw = 'unique ' + 'A' * 1100 + 'B' * 1100 + 'C' * 1100
    chunk = whole(raw)
    ids = _payload(raw, chunk)['core_piece_ids']
    refs = {'empty': [], 'unknown': ['PRIVATE_UNKNOWN'], 'duplicate': [ids[0], ids[0]],
            'reverse': ids[::-1], 'gap': [ids[0], ids[2]], 'irrelevant': ids}[kind]
    with pytest.raises(ReviewError) as error:
        validate(raw, chunk, [quote(refs, 'unique')])
    assert error.value.category == 'validation_source'
    assert 'PRIVATE_UNKNOWN' not in str(error.value)


def test_context_only_rejected_and_cross_core_long_sentence_resolves_deduplicated():
    raw = 'A' * 60 + ' begin café😀 boundary quote end ' + 'B' * 180
    chunks = list(_chunks(raw, 64))
    excerpt = 'A' * 10 + ' begin café😀 boundary quote end'
    found = []
    for chunk in chunks:
        pieces = _evidence_pieces(raw, chunk)
        start, end = raw.index(excerpt), raw.index(excerpt) + len(excerpt)
        ids = [p['piece_id'] for p in pieces if p['start'] < end and p['end'] > start]
        if not ids or ''.join(p['text'] for p in pieces).find(excerpt) < 0:
            continue
        if start < chunk['end'] and end > chunk['start']:
            found.extend(validate(raw, chunk, [quote(ids, excerpt)]))
        else:
            with pytest.raises(ReviewError) as error:
                validate(raw, chunk, [quote(ids, excerpt)])
            assert error.value.category == 'validation_source'
    assert len(found) >= 2 and len({(r['start'], r['end']) for r in found}) == 1
    client = MagicMock()
    def respond(**parameters):
        payload = json.loads(parameters['messages'][-1]['content'])
        chunk = chunks[payload['chunk_index'] - 1]
        pieces = _evidence_pieces(raw, chunk)
        ids = [p['piece_id'] for p in pieces if p['start'] < end and p['end'] > start]
        rows = [quote(ids, excerpt)] if start < chunk['end'] and end > chunk['start'] else []
        return completion(json.dumps(ack(payload, rows)))
    client.chat.completions.create.side_effect = respond
    report = review_transcript(raw, client, ReviewOptions(chunk_bytes=64))
    validate_review_report(raw, report, ReviewOptions(chunk_bytes=64))
    assert len(report['findings']) == 1


@pytest.mark.parametrize('kind', ['incomplete', 'missing', 'extra_context', 'reordered', 'duplicate',
                                 'unknown', 'wrong_version', 'bool_version', 'legacy_offsets'])
def test_exact_versioned_coverage_cannot_silently_acknowledge_less_or_more(kind):
    raw = 'A' * 2200 + 'B' * 2200 + 'C' * 2200
    chunk = list(_chunks(raw, 2500))[1]
    payload = _payload(raw, chunk)
    ids = payload['core_piece_ids']
    changes = {'incomplete': {'fully_reviewed': False}, 'missing': {'reviewed_piece_ids': ids[:-1]},
        'extra_context': {'reviewed_piece_ids': [p['piece_id'] for p in payload['evidence_pieces']]},
        'reordered': {'reviewed_piece_ids': ids[::-1]}, 'duplicate': {'reviewed_piece_ids': ids + ids},
        'unknown': {'reviewed_piece_ids': ['PRIVATE_UNKNOWN']}, 'wrong_version': {'contract_version': 1},
        'bool_version': {'contract_version': True}, 'legacy_offsets': {'reviewed_start': 0}}
    with pytest.raises(ReviewError):
        validate(raw, chunk, **changes[kind])
    assert validate(raw, chunk) == []  # Empty findings + exact acknowledgement is structurally complete.


def test_legacy_exact_offset_reports_remain_valid_but_not_new_checkpoint_identity():
    raw = 'Legacy synthetic quote.'
    client = MagicMock()
    client.chat.completions.create.side_effect = lambda **kw: completion(json.dumps(ack(
        json.loads(kw['messages'][-1]['content']), [quote(_payload(raw, whole(raw))['core_piece_ids'], raw)])))
    report = review_transcript(raw, client)
    legacy = copy.deepcopy(report)
    legacy.update(schema_version=1, prompt_version='author-review-v1', prompt_sha256=_hash(_prompt(1)),
                  settings_fingerprint='a' * 64)
    for chunk in legacy['coverage']['chunks']:
        chunk.pop('reviewed_piece_ids')
    validate_review_report(raw, legacy)
    with pytest.raises(ReviewError):
        validate_review_report(raw, legacy, ReviewOptions())
    legacy['findings'][0]['excerpt'] = 'changed'
    with pytest.raises(ReviewError):
        validate_review_report(raw, legacy)
    report['coverage']['chunks'][0]['reviewed_piece_ids'] = []
    with pytest.raises(ReviewError):
        validate_review_report(raw, report)


def test_actual_sdk_review_serializes_unicode_schema_effort_and_no_store():
    raw = '😀 Synthetic café\r\nदेवनागरी\u200d e\u0301 quote.'
    requests = []
    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][-1]['content'])
        requests.append((body, payload))
        return httpx2.Response(200, json={'id': 'synthetic', 'object': 'chat.completion',
            'created': 0, 'model': 'gpt-6.1-sol', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': json.dumps(ack(payload, [
                quote(payload['core_piece_ids'], raw)]), ensure_ascii=False)}}]})
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as transport:
        with OpenAI(api_key='synthetic-placeholder', http_client=transport, max_retries=0) as client:
            report = review_transcript(raw, client, ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium'))
    assert report['status'] == 'complete' and report['findings'][0]['excerpt'] == raw
    body, payload = requests[0]
    assert ''.join(p['text'] for p in payload['evidence_pieces']) == raw
    assert 'text' not in payload and 'core_start' not in payload
    assert body['store'] is False and body['reasoning_effort'] == 'medium'
    schema = body['response_format']['json_schema']['schema']
    assert schema['properties']['contract_version']['enum'] == [2]
    assert schema['properties']['reviewed_piece_ids']['items']['enum'] == payload['core_piece_ids']
    assert 'start' not in schema['properties']['findings']['items']['properties']


@pytest.mark.parametrize('failure', ['missing_quote', 'ambiguous_quote', 'coverage', 'malformed_json'])
def test_actual_sdk_malformed_review_is_rejected_without_private_body_cache(tmp_path, failure):
    raw = 'Synthetic duplicate duplicate.'
    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][-1]['content'])
        body = ack(payload)
        if failure in {'missing_quote', 'ambiguous_quote'}:
            text = 'PRIVATE_INVENTED' if failure == 'missing_quote' else 'duplicate'
            body['findings'] = [quote(payload['core_piece_ids'], text)]
        elif failure == 'coverage':
            body['reviewed_piece_ids'] = []
        content = 'PRIVATE_MALFORMED_JSON' if failure == 'malformed_json' else json.dumps(body)
        return httpx2.Response(200, json={'id': 'synthetic', 'object': 'chat.completion',
            'created': 0, 'model': 'gpt-6.1-sol', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': content}}]})
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as transport:
        with OpenAI(api_key='synthetic-placeholder', http_client=transport, max_retries=0) as client:
            report = review_transcript(raw, client, checkpoint_root=tmp_path)
    assert report['status'] == 'failed' and report['findings'] == []
    expected = {'missing_quote': 'validation_quote_missing', 'ambiguous_quote': 'validation_quote_ambiguous',
                'coverage': 'validation_coverage', 'malformed_json': 'validation_schema'}[failure]
    assert report['coverage']['chunks'][0]['error_category'] == expected
    assert 'PRIVATE_' not in json.dumps(report)
    assert not list(tmp_path.rglob('response.json'))
    assert all(b'PRIVATE_' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())


@pytest.mark.parametrize('version', [1, 2])
def test_cached_reports_reject_exact_whitespace_only_evidence_even_with_matching_id(version):
    raw = 'Synthetic words. '
    client = MagicMock()
    client.chat.completions.create.side_effect = lambda **kw: completion(json.dumps(ack(
        json.loads(kw['messages'][-1]['content']), [quote(_payload(raw, whole(raw))['core_piece_ids'], raw)])))
    report = review_transcript(raw, client)
    if version == 1:
        report.update(schema_version=1, prompt_version='author-review-v1', prompt_sha256=_hash(_prompt(1)))
        for chunk in report['coverage']['chunks']:
            chunk.pop('reviewed_piece_ids')
    row = report['findings'][0]
    row.update(start=len(raw) - 1, end=len(raw), excerpt=' ')
    key = [report['raw_sha256'], row['start'], row['end'], row['category']]
    row['finding_id'] = 'finding-' + _hash(json.dumps(key))[:24]
    with pytest.raises(ReviewError):
        validate_review_report(raw, report)


def test_long_sentence_quote_crosses_bounded_piece_boundary_with_exact_unicode_offsets():
    raw = 'a' * 1010 + ' café😀unique boundary marker ' + 'b' * 2400
    chunk = whole(raw)
    pieces = _evidence_pieces(raw, chunk)
    boundary = pieces[0]['end']
    start, end = boundary - 20, boundary + 60
    ids = [p['piece_id'] for p in pieces if p['start'] < end and p['end'] > start]
    row, = validate(raw, chunk, [quote(ids, raw[start:end])])
    assert len(ids) >= 2 and (row['start'], row['end']) == (start, end)
