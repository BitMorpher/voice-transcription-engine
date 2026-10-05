"""Sanitized SDK metadata and systemic review circuit breaks; no network."""

import io
import json
from unittest.mock import MagicMock

import httpx2
import openai
import pytest

from src.author_review import ReviewOptions, review_transcript
from src.progress import CURRENT, Reporter
from src.provider_errors import classify


def status_error(status, *, code='synthetic-private-code'):
    response = httpx2.Response(status, request=httpx2.Request('POST', 'https://example.invalid/private-source'),
                               headers={'x-request-id': 'SYNTHETIC_SECRET'})
    return openai.APIStatusError('SYNTHETIC_SECRET raw transcript', response=response,
                                body={'code': code, 'transcript': 'SYNTHETIC_SECRET'})


@pytest.mark.parametrize('status,code,category', [
    (401, 'private', 'authentication'), (403, 'private', 'permission'), (404, 'private', 'model_access'),
    (400, 'private', 'invalid_request'), (422, 'private', 'invalid_request'),
    (429, 'insufficient_quota', 'quota'), (429, 'billing_not_active', 'quota'),
    (429, 'private', 'rate_limit'), (500, 'private', 'server'), (503, 'private', 'server'),
])
def test_known_status_classification(status, code, category):
    result = classify(status_error(status, code=code))
    assert result == {'error_category': category, 'http_status': status}
    assert 'SYNTHETIC_SECRET' not in json.dumps(result)


@pytest.mark.parametrize('kind,category', [(openai.APITimeoutError, 'timeout'), (openai.APIConnectionError, 'connection')])
def test_transport_classification(kind, category):
    assert classify(kind(request=httpx2.Request('POST', 'https://example.invalid'))) == {'error_category': category}


def test_unknown_exception_fields_are_not_accessed():
    class Unknown(RuntimeError):
        @property
        def status_code(self):
            pytest.fail('Unknown fields must not be accessed.')
    assert classify(Unknown('SYNTHETIC_SECRET')) == {'error_category': 'provider_unknown'}


@pytest.mark.parametrize('status,code', [(401, 'private'), (403, 'private'), (404, 'private'),
                                       (400, 'private'), (429, 'insufficient_quota')])
def test_systemic_review_stops_after_one_call_with_full_failed_coverage(status, code):
    client = MagicMock()
    client.chat.completions.create.side_effect = status_error(status, code=code)
    stream = io.StringIO()
    reporter = Reporter(stream)
    token = CURRENT.set(reporter)
    try:
        report = review_transcript('Synthetic secret source. ' * 20, client, ReviewOptions(chunk_bytes=64))
    finally:
        CURRENT.reset(token)
    assert client.chat.completions.create.call_count == 1
    assert report['status'] == 'failed' and report['coverage']['reviewed_characters'] == 0
    chunks = report['coverage']['chunks']
    assert len(chunks) > 1 and chunks[-1]['end'] == len('Synthetic secret source. ' * 20)
    assert all(chunk['error_category'] == 'not_attempted' and chunk['attempted'] is False for chunk in chunks[1:])
    assert 'SYNTHETIC_SECRET' not in stream.getvalue() and 'secret source' not in stream.getvalue()
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert rows[1]['http_status'] == status
    assert all(row['stage_status'] == 'blocked' for row in rows[2:])


@pytest.mark.parametrize('failure', ['timeout', 'server', 'rate_limit'])
def test_transient_review_failures_preserve_later_chunk_attempts(failure):
    client = MagicMock()
    error = openai.APITimeoutError(request=httpx2.Request('POST', 'https://example.invalid')) if failure == 'timeout' else status_error(500 if failure == 'server' else 429)
    client.chat.completions.create.side_effect = error
    report = review_transcript('Synthetic source. ' * 10, client, ReviewOptions(chunk_bytes=64))
    assert client.chat.completions.create.call_count == len(report['coverage']['chunks']) > 1
    assert all(chunk['error_category'] == failure for chunk in report['coverage']['chunks'])
