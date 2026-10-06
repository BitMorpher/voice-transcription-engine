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
    assert classify(kind(request=httpx2.Request('POST', 'https://example.invalid'))) == {
        'error_category': category, **({'timeout_phase': 'unknown'} if category == 'timeout' else {})}


@pytest.mark.parametrize('transport_name', ['httpx', 'httpx2'])
@pytest.mark.parametrize('kind,phase', [('ConnectTimeout', 'connect'), ('WriteTimeout', 'write'),
                                      ('ReadTimeout', 'read'), ('PoolTimeout', 'pool')])
def test_real_sdk_wrapping_preserves_only_safe_timeout_phase(transport_name, kind, phase):
    transport = pytest.importorskip(transport_name)
    def fail(request):
        raise getattr(transport, kind)('SYNTHETIC_SECRET credentials and source', request=request)
    client = openai.OpenAI(api_key='SYNTHETIC_SECRET', max_retries=0,
        base_url='https://example.invalid/SYNTHETIC_SECRET',
        http_client=transport.Client(transport=transport.MockTransport(fail)))
    try:
        with pytest.raises(openai.APITimeoutError) as caught:
            client.audio.transcriptions.create(model='synthetic', file=('audio.wav', b'synthetic'))
        result = classify(caught.value)
    finally:
        client.close()
    assert result == {'error_category': 'timeout', 'timeout_phase': phase}
    stream = io.StringIO()
    Reporter(stream).emit(status='progress', stage='transcription', stage_status='failed', **result)
    event = json.loads(stream.getvalue())
    assert event['timeout_phase'] == phase
    assert 'SYNTHETIC_SECRET' not in stream.getvalue() and 'https://' not in stream.getvalue()


def test_timeout_phase_without_optional_legacy_transport(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'httpx', None)
    error = openai.APITimeoutError(request=httpx2.Request('POST', 'https://example.invalid'))
    error.__cause__ = httpx2.WriteTimeout('SYNTHETIC_SECRET')
    assert classify(error) == {'error_category': 'timeout', 'timeout_phase': 'write'}


def test_author_review_retains_safe_timeout_phase_in_report_and_progress():
    error = openai.APITimeoutError(request=httpx2.Request('POST', 'https://example.invalid'))
    error.__cause__ = httpx2.ReadTimeout('SYNTHETIC_SECRET')
    client = MagicMock()
    client.chat.completions.create.side_effect = error
    stream = io.StringIO()
    token = CURRENT.set(Reporter(stream))
    try:
        report = review_transcript('Invented short text.', client, ReviewOptions())
    finally:
        CURRENT.reset(token)
    assert report['coverage']['chunks'][0]['timeout_phase'] == 'read'
    failures = [json.loads(line) for line in stream.getvalue().splitlines()
                if json.loads(line).get('stage_status') == 'failed']
    assert len(failures) == 1 and failures[0]['timeout_phase'] == 'read'
    assert 'SYNTHETIC_SECRET' not in stream.getvalue() + json.dumps(report)


@pytest.mark.parametrize('chain', ['context', 'cause', 'suppressed', 'cycle', 'long', 'ambiguous', 'unknown'])
def test_timeout_phase_uses_only_reliable_active_exception_chain(chain):
    error = openai.APITimeoutError(request=httpx2.Request('POST', 'https://example.invalid'))
    transport = httpx2.ReadTimeout('SYNTHETIC_SECRET')
    expected = 'unknown'
    if chain == 'context':
        error.__context__ = transport
        expected = 'read'
    elif chain == 'cause':
        error.__context__ = httpx2.WriteTimeout('ignored context')
        error.__cause__ = transport
        expected = 'read'
    elif chain == 'suppressed':
        error.__context__ = transport
        error.__suppress_context__ = True
    elif chain == 'cycle':
        error.__cause__ = transport
        transport.__cause__ = error
    elif chain == 'long':
        current = error
        for _ in range(17):
            current.__cause__ = RuntimeError('SYNTHETIC_SECRET')
            current = current.__cause__
        current.__cause__ = transport
    elif chain == 'ambiguous':
        error.__cause__ = transport
        transport.__cause__ = httpx2.WriteTimeout('SYNTHETIC_SECRET')
    else:
        error.__cause__ = TimeoutError('ReadTimeout SYNTHETIC_SECRET')
    assert classify(error) == {'error_category': 'timeout', 'timeout_phase': expected}


def test_timeout_phase_never_reads_exception_text_or_overridden_chain_properties():
    class PrivateWrapper(RuntimeError):
        def __str__(self):
            pytest.fail('Do not inspect exception text.')
        @property
        def __cause__(self):
            pytest.fail('Do not call arbitrary chain properties.')
    wrapper = PrivateWrapper()
    BaseException.__cause__.__set__(wrapper, httpx2.PoolTimeout('SYNTHETIC_SECRET'))
    error = openai.APITimeoutError(request=httpx2.Request('POST', 'https://example.invalid'))
    error.__cause__ = wrapper
    assert classify(error)['timeout_phase'] == 'pool'
    assert classify(wrapper) == {'error_category': 'provider_unknown'}


def test_reporter_drops_injected_timeout_and_prerequisite_metadata():
    stream = io.StringIO()
    reporter = Reporter(stream)
    reporter.emit(status='failed', error_category='timeout', timeout_phase='SYNTHETIC_SECRET',
                  blocked_reason='SYNTHETIC_SECRET', family_blockers={'original': 'SYNTHETIC_SECRET'},
                  url='https://example.invalid/private', request_id='SYNTHETIC_SECRET')
    reporter.emit(status='progress', timeout_phase='read')
    assert 'SYNTHETIC_SECRET' not in stream.getvalue() and 'https://' not in stream.getvalue()
    assert all('timeout_phase' not in json.loads(line) for line in stream.getvalue().splitlines())


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
