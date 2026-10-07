"""Terminal validation accounting, parallel stop/drain and private resume."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading
from unittest.mock import MagicMock

import pytest

from src.author_review import ReviewOptions, review_transcript, validate_review_report
from src.batch.concurrency import run_sessions
from src.progress import CURRENT, Reporter
from src.provider_control import CURRENT_CONTROL, ControlledClient, ProviderControl, ProviderStopped
from src.text_requests import ResponseValidationError, TextRequestCache, validated_chat
from tests.test_review_evidence import ack
from tests.test_text_requests import completion


def reject(content, category='validation_source'):
    raise ResponseValidationError('PRIVATE_SOURCE_BODY_KEY', category=category)


def client():
    raw = MagicMock()
    raw.chat.completions.create.return_value = completion('Synthetic response')
    return raw


def run(control, raw, validate=reject, *, stage='author_review', model='gpt-6.1-sol', cache=None):
    token = CURRENT_CONTROL.set(control)
    try:
        return validated_chat(ControlledClient(raw, 120, 0), {'model': model}, validate,
                              stage=stage, cache=cache)
    finally:
        CURRENT_CONTROL.reset(token)


@pytest.mark.parametrize('category,retries,starts', [
    ('validation_schema', 2, 2), ('validation_coverage', 2, 2),
    ('validation_schema', 0, 1), ('validation_source', 2, 1),
    ('validation_quote_missing', 2, 1), ('validation_quote_ambiguous', 2, 1), ('completion', 2, 1)])
def test_terminal_failure_counts_once_after_recovery_and_stops_new_admission(category, retries, starts):
    control = ProviderControl(retries=retries, validation_failure_limit=1)
    raw = client()
    with pytest.raises(ResponseValidationError):
        run(control, raw, lambda content: reject(content, category))
    assert control.validation_failures == 1 and control.requests == starts
    assert control.failures == 0 and control.reason == 'validation_failures'
    with pytest.raises(ProviderStopped):
        run(control, raw, lambda content: content)
    assert control.requests == starts


def test_intermediate_recovery_and_valid_cache_hits_are_not_failures(tmp_path):
    control = ProviderControl(retries=2, validation_failure_limit=1)
    raw = client()
    responses = iter(['bad', 'good'])
    raw.chat.completions.create.side_effect = lambda **kw: completion(next(responses))
    def validate(content):
        if content == 'bad':
            reject(content, 'validation_schema')
        return content
    cache = TextRequestCache(tmp_path, {'synthetic': 1}, [{'model': 'gpt-6.1-sol'}], [validate])
    assert run(control, raw, validate, cache=cache) == 'good'
    assert control.requests == 2 and control.validation_failures == 0
    control.cancel()
    assert run(control, raw, validate, cache=cache) == 'good'
    assert control.requests == 2 and control.reason == 'interrupted'


def test_scope_counts_do_not_mix_stages_models_or_reset_on_valid_sdk_response():
    control = ProviderControl(retries=0, validation_failure_limit=2)
    raw = client()
    for stage, model in [('author_review', 'gpt-6.1-sol'), ('enhancement', 'gpt-6.1-sol'),
                         ('author_review', 'gpt-6-astra')]:
        with pytest.raises(ResponseValidationError):
            run(control, raw, stage=stage, model=model)
    assert control.validation_failures == 3 and control.reason is None
    assert run(control, raw, lambda content: content) == 'Synthetic response'
    assert control.validation_failures_by_scope[('author_review', 'gpt-6.1-sol')] == 1
    with pytest.raises(ResponseValidationError):
        run(control, raw)
    assert control.reason == 'validation_failures' and control.requests == 5


def test_sdk_provider_failures_do_not_count_as_local_validation():
    control = ProviderControl(retries=0, failure_limit=3, validation_failure_limit=1)
    raw = client()
    raw.chat.completions.create.side_effect = RuntimeError('PRIVATE_PROVIDER_BODY')
    with pytest.raises(RuntimeError):
        run(control, raw)
    assert control.validation_failures == 0 and control.failures == 1 and control.reason is None


def test_parallel_admitted_success_drains_checkpoints_stop_is_irreversible_and_resume_reuses(tmp_path):
    control = ProviderControl(retries=0, validation_failure_limit=1)
    raw = client()
    both_admitted = threading.Barrier(2)
    stopped = threading.Event()
    entered = []
    lock = threading.Lock()
    def respond(**parameters):
        worker = parameters['worker']
        with lock:
            entered.append(worker)
        both_admitted.wait(timeout=5)
        if worker == 2:
            assert stopped.wait(timeout=5)
        return completion('invalid' if worker == 1 else 'verified')
    raw.chat.completions.create.side_effect = respond
    def process(item):
        worker = item['position']
        parameters = {'model': 'gpt-6.1-sol', 'worker': worker}
        def validate(content):
            if content == 'invalid':
                reject(content)
            return content
        cache = TextRequestCache(tmp_path / str(worker), {'worker': worker}, [parameters], [validate])
        try:
            value = validated_chat(ControlledClient(raw, 120, 0), parameters, validate,
                                   stage='author_review', cache=cache)
        except ResponseValidationError:
            stopped.set()
            value = 'failed'
        return {'position': worker, 'value': value}
    token = CURRENT_CONTROL.set(control)
    recorded = []
    try:
        run_sessions([{'position': i} for i in range(1, 6)], 2, process, recorded.append, control)
    finally:
        CURRENT_CONTROL.reset(token)
    assert sorted(entered) == [1, 2]
    assert sorted(recorded, key=lambda row: row['position']) == [
        {'position': 1, 'value': 'failed'}, {'position': 2, 'value': 'verified'}]
    assert control.requests == 2 and control.validation_failures == 1
    assert control.reason == 'validation_failures' and not control.cancelled.is_set()
    assert len(list(tmp_path.rglob('response.json'))) == 1
    saved = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    # A fresh invocation resets admission observations, retaining exact successes.
    resumed = ProviderControl(retries=0, validation_failure_limit=1)
    raw.chat.completions.create.side_effect = lambda **kw: completion('verified')
    token = CURRENT_CONTROL.set(resumed)
    try:
        assert process({'position': 1})['value'] == 'verified'
        assert process({'position': 2})['value'] == 'verified'
    finally:
        CURRENT_CONTROL.reset(token)
    assert resumed.requests == 1 and resumed.validation_failures == 0
    assert all(p.read_bytes() == content for p, content in saved.items())


def test_parallel_terminal_failure_race_counts_each_operation_once_and_cancellation_wins():
    control = ProviderControl(retries=0, validation_failure_limit=2)
    raw = client()
    barrier = threading.Barrier(4)
    raw.chat.completions.create.side_effect = lambda **kw: (barrier.wait(timeout=5), completion('bad'))[1]
    def worker():
        with pytest.raises(ResponseValidationError):
            run(control, raw)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker) for _ in range(4)]
        for future in futures:
            future.result(timeout=10)
    assert control.validation_failures == control.requests == 4
    assert control.validation_failures_by_scope == {('author_review', 'gpt-6.1-sol'): 4}
    assert control.reason == 'validation_failures'
    cancelled = ProviderControl(validation_failure_limit=1)
    cancelled.cancel()
    cancelled.validation_failed('PRIVATE_STAGE', 'PRIVATE_MODEL')
    assert cancelled.reason == 'interrupted'


def test_failed_review_marks_remaining_unattempted_counts_and_privacy(tmp_path):
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0)
    reporting = CURRENT.set(reporter)
    control = ProviderControl(retries=2, validation_failure_limit=2)
    token = CURRENT_CONTROL.set(control)
    raw = client()
    raw.chat.completions.create.return_value = completion('PRIVATE_INVALID_RESPONSE_KEY')
    source = 'Invented synthetic words.\n' * 20
    options = ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium', chunk_bytes=64)
    try:
        report = review_transcript(source, ControlledClient(raw, 120, 2), options, checkpoint_root=tmp_path)
    finally:
        CURRENT_CONTROL.reset(token)
        CURRENT.reset(reporting)
    assert report['status'] == 'failed' and not report['coverage']['complete']
    assert control.requests == 4 and control.validation_failures == 2
    assert [c['error_category'] for c in report['coverage']['chunks'][:2]] == ['validation_schema'] * 2
    assert all(c['attempted'] is False and c['error_category'] == 'not_attempted'
               for c in report['coverage']['chunks'][2:])
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert any(e.get('stop_reason') == 'validation_failures' for e in events)
    assert max(e.get('validation_failures', 0) for e in events) == 2
    assert reporter.text_metrics.snapshot()['validation_retries'] == 2
    assert reporter.text_metrics.snapshot()['sdk_operations_completed'] == 4
    assert reporter.text_metrics.snapshot()['sdk_operations_failed'] == 0
    assert not list(tmp_path.rglob('response.json'))
    assert 'PRIVATE_' not in json.dumps(report) + stream.getvalue()
    assert all(b'PRIVATE_' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())
    raw.chat.completions.create.side_effect = lambda **kw: completion(json.dumps(ack(
        json.loads(kw['messages'][-1]['content']))))
    renewed = ProviderControl(retries=0)
    token = CURRENT_CONTROL.set(renewed)
    try:
        report = review_transcript(source, ControlledClient(raw, 120, 0), options, checkpoint_root=tmp_path)
    finally:
        CURRENT_CONTROL.reset(token)
    validate_review_report(source, report, options)


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, 'PRIVATE'])
def test_limit_is_a_positive_integer_without_echoing_input(limit):
    with pytest.raises(ValueError) as error:
        ProviderControl(validation_failure_limit=limit)
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('excerpt', ['', ' \r\n'])
def test_empty_quote_remains_invalid_after_bounded_correction(excerpt):
    from tests.test_review_evidence import quote
    control = ProviderControl(retries=2)
    raw = client()
    def respond(**kw):
        payload = json.loads(kw['messages'][-1]['content'])
        return completion(json.dumps(ack(payload, [quote(payload['core_piece_ids'], excerpt)])))
    raw.chat.completions.create.side_effect = respond
    token = CURRENT_CONTROL.set(control)
    try:
        report = review_transcript('Synthetic source.', ControlledClient(raw, 120, 2))
    finally:
        CURRENT_CONTROL.reset(token)
    assert report['coverage']['chunks'][0]['error_category'] == 'validation_quote_missing'
    assert control.requests == 2 and control.validation_failures == 1


def test_denied_recovery_and_cache_integrity_do_not_count_terminal_validation(tmp_path):
    from src.chunk_cache import ChunkCacheError
    control = ProviderControl(retries=2)
    raw = client()
    def stop_during_validation(content):
        control.cancel()
        reject(content, 'validation_schema')
    with pytest.raises(ProviderStopped):
        run(control, raw, stop_during_validation)
    assert control.requests == 1 and control.validation_failures == 0
    raw.chat.completions.create.return_value = completion('verified')
    cache = TextRequestCache(tmp_path, {'synthetic': 1}, [{'model': 'gpt-6.1-sol'}], [lambda c: c])
    cache.put(cache.layout[0], {'content': 'verified'})
    next(tmp_path.rglob('response.json')).write_text('PRIVATE_CORRUPT')
    fresh = ProviderControl(retries=0)
    with pytest.raises(ChunkCacheError):
        run(fresh, raw, lambda c: c, cache=cache)
    assert fresh.requests == fresh.validation_failures == 0 and fresh.reason is None
