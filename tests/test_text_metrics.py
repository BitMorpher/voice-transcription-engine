"""Offline usage accounting includes rejected completions without private values."""

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.progress import CURRENT, Reporter, safe_configuration, safe_text_metrics
from src.provider_control import CURRENT_CONTROL, ControlledClient, ProviderControl, ProviderStopped
from src.text_requests import (ResponseValidationError, TextMetrics, TextRequestCache,
                               collect_text_metrics, validated_chat)


def usage(prompt=30, completion=20, *, reasoning=12, cached=10):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=prompt + completion,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        private_field='SYNTHETIC_SECRET')


def response(content='Synthetic words.', *, finish='stop', refusal=None, tokens=None):
    return SimpleNamespace(usage=tokens, choices=[SimpleNamespace(finish_reason=finish,
        message=SimpleNamespace(content=content, refusal=refusal))],
        id='SYNTHETIC_SECRET_RESPONSE_ID')


def run(client, validate=lambda content: content, **kwargs):
    return validated_chat(client, {'model': 'synthetic', 'messages': [
        {'role': 'user', 'content': 'SYNTHETIC_SECRET_REQUEST'}]}, validate,
        stage='enhancement', **kwargs)


@pytest.mark.parametrize('controlled', [False, True])
@pytest.mark.parametrize('admission', [False, True])
def test_actual_usage_once_per_sdk_operation(controlled, admission):
    client = MagicMock()
    client.chat.completions.create.return_value = response(tokens=usage())
    active = ControlledClient(client, 600, 0) if controlled else client
    control = CURRENT_CONTROL.set(ProviderControl(retries=0)) if admission else None
    try:
        with collect_text_metrics() as metrics:
            assert run(active) == 'Synthetic words.'
            assert run(active) == 'Synthetic words.'
    finally:
        if control is not None:
            CURRENT_CONTROL.reset(control)
    totals = metrics.snapshot()
    assert totals['sdk_operations_started'] == totals['sdk_operations_completed'] == 2
    assert totals['sdk_operations_failed'] == totals['usage_missing'] == 0
    assert totals['usage_responses'] == totals['prompt_tokens_reported_operations'] == 2
    assert totals['prompt_tokens'] == 60 and totals['completion_tokens'] == 40
    assert totals['total_tokens'] == 100 and totals['reasoning_tokens'] == 24
    assert totals['cached_prompt_tokens'] == 20 and totals['usage_complete'] is True
    assert totals['sdk_internal_retries_observed'] is False
    assert 'SYNTHETIC_SECRET' not in json.dumps(totals)


@pytest.mark.parametrize('finish,refusal', [('length', None), ('stop', 'SYNTHETIC_SECRET')])
def test_usage_is_collected_before_completion_rejection(finish, refusal):
    client = MagicMock()
    client.chat.completions.create.return_value = response(finish=finish, refusal=refusal, tokens=usage())
    with collect_text_metrics() as metrics:
        with pytest.raises(ResponseValidationError):
            run(client)
    totals = metrics.snapshot()
    assert totals['sdk_operations_completed'] == 1
    assert totals['completion_tokens'] == 20 and totals['usage_responses'] == 1


def test_validation_recovery_counts_usage_of_both_responses():
    client = MagicMock()
    client.chat.completions.create.side_effect = [response('invalid', tokens=usage()),
                                                response('valid', tokens=usage(5, 3, reasoning=1, cached=0))]
    def validate(content):
        if content == 'invalid':
            raise ResponseValidationError('SYNTHETIC_SECRET', category='validation_schema')
        return content
    control = CURRENT_CONTROL.set(ProviderControl(retries=2))
    try:
        with collect_text_metrics() as metrics:
            assert run(ControlledClient(client, 600, 2), validate) == 'valid'
    finally:
        CURRENT_CONTROL.reset(control)
    totals = metrics.snapshot()
    assert totals['sdk_operations_started'] == totals['usage_responses'] == 2
    assert totals['validation_retries'] == 1
    assert totals['completion_tokens'] == 23 and totals['prompt_tokens'] == 35


@pytest.mark.parametrize('error', [RuntimeError('SYNTHETIC_SECRET'), KeyboardInterrupt()])
def test_failed_sdk_operation_keeps_usage_explicitly_missing(error):
    client = MagicMock()
    client.chat.completions.create.side_effect = error
    with collect_text_metrics() as metrics:
        with pytest.raises(type(error)):
            run(client)
    totals = metrics.snapshot()
    assert totals['sdk_operations_started'] == totals['sdk_operations_failed'] == 1
    assert totals['usage_missing'] == totals['latency_observations'] == 1
    assert totals['sdk_operations_completed'] == totals['completion_tokens_reported_operations'] == 0
    assert totals['usage_complete'] is False
    assert 'SYNTHETIC_SECRET' not in json.dumps(totals)


def test_reporter_records_missing_usage_without_new_progress_events():
    client = MagicMock()
    client.chat.completions.create.return_value = response()
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0)
    token = CURRENT.set(reporter)
    try:
        assert run(client) == 'Synthetic words.'
        reporter.emit(status='complete', stage='enhancement')
    finally:
        CURRENT.reset(token)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(rows) == 1
    assert rows[0]['text_metrics'] == reporter.text_metrics.snapshot()
    assert rows[0]['text_metrics']['usage_missing'] == 1
    assert rows[0]['text_metrics']['prompt_tokens_reported_operations'] == 0
    assert 'SYNTHETIC_SECRET' not in stream.getvalue()


def test_cache_reuse_does_not_add_tokens_or_sdk_operation(tmp_path):
    client = MagicMock()
    client.chat.completions.create.return_value = response(tokens=usage())
    parameters = {'model': 'synthetic', 'messages': [
        {'role': 'user', 'content': 'SYNTHETIC_SECRET_REQUEST'}]}
    def validate(content):
        return content
    cache = TextRequestCache(tmp_path, {'contract': 1}, [parameters], [validate])
    with collect_text_metrics() as metrics:
        assert run(client, cache=cache) == 'Synthetic words.'
        assert run(client, cache=cache) == 'Synthetic words.'
    totals = metrics.snapshot()
    assert totals['sdk_operations_started'] == totals['cache_hits'] == 1
    assert totals['prompt_tokens'] == 30 and totals['completion_tokens'] == 20
    assert client.chat.completions.create.call_count == 1


def test_cancelled_validation_retry_is_not_an_sdk_start():
    raw = MagicMock()
    raw.chat.completions.create.return_value = response(tokens=usage())
    control = ProviderControl(retries=2)
    token = CURRENT_CONTROL.set(control)
    def cancel(content):
        control.cancel()
        raise ResponseValidationError('SYNTHETIC_SECRET')
    try:
        with collect_text_metrics() as metrics:
            with pytest.raises(ProviderStopped):
                run(ControlledClient(raw, 600, 2), cancel)
    finally:
        CURRENT_CONTROL.reset(token)
    totals = metrics.snapshot()
    assert totals['sdk_operations_started'] == 1
    assert totals['validation_retries'] == totals['sdk_operations_failed'] == 0


def test_admission_race_does_not_count_rejected_operation(monkeypatch):
    raw = MagicMock()
    control = ProviderControl(retries=0)
    original = control.check
    def cancel_after_check():
        original()
        control.cancel()
    monkeypatch.setattr(control, 'check', cancel_after_check)
    token = CURRENT_CONTROL.set(control)
    try:
        with collect_text_metrics() as metrics:
            with pytest.raises(ProviderStopped):
                run(ControlledClient(raw, 600, 0))
    finally:
        CURRENT_CONTROL.reset(token)
    assert metrics.snapshot()['sdk_operations_started'] == 0
    assert metrics.snapshot()['latency_observations'] == 0
    raw.chat.completions.create.assert_not_called()


def test_latency_excludes_admission_wait(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('src.progress.time.monotonic', lambda: clock[0])
    raw = MagicMock()
    def respond(**kwargs):
        clock[0] += 3.0
        return response(tokens=usage())
    raw.chat.completions.create.side_effect = respond
    control = ProviderControl(retries=0, clock=lambda: clock[0])
    def wait():
        clock[0] += 7.0
    monkeypatch.setattr(control, '_wait_for_slot', wait)
    token = CURRENT_CONTROL.set(control)
    try:
        with collect_text_metrics() as metrics:
            run(ControlledClient(raw, 600, 0))
    finally:
        CURRENT_CONTROL.reset(token)
    assert metrics.snapshot()['latency_seconds'] == 3.0
    assert clock[0] == 110.0


def test_usage_properties_are_untrusted_and_never_expose_errors():
    class UnreadableUsage:
        @property
        def usage(self):
            raise RuntimeError('SYNTHETIC_SECRET')
        choices = response().choices
    client = MagicMock()
    client.chat.completions.create.return_value = UnreadableUsage()
    with collect_text_metrics() as metrics:
        assert run(client) == 'Synthetic words.'
    assert metrics.snapshot()['usage_missing'] == 1
    assert 'SYNTHETIC_SECRET' not in json.dumps(metrics.snapshot())


def test_partial_usage_preserves_available_fields_and_observations():
    client = MagicMock()
    client.chat.completions.create.return_value = response(tokens={
        'prompt_tokens': 30, 'completion_tokens': True, 'total_tokens': -1,
        'completion_tokens_details': {'reasoning_tokens': 2},
        'prompt_tokens_details': {'cached_tokens': 'SYNTHETIC_SECRET'}})
    with collect_text_metrics() as metrics:
        run(client)
    totals = metrics.snapshot()
    assert totals['usage_missing'] == 1 and totals['usage_complete'] is False
    assert totals['prompt_tokens'] == 30 and totals['prompt_tokens_reported_operations'] == 1
    assert totals['completion_tokens'] == totals['completion_tokens_reported_operations'] == 0
    assert totals['reasoning_tokens'] == 2 and totals['reasoning_tokens_reported_operations'] == 1
    assert totals['cached_prompt_tokens_reported_operations'] == 0


def test_collectors_are_context_isolated_and_snapshots_independent():
    client = MagicMock()
    client.chat.completions.create.return_value = response(tokens=usage())
    with collect_text_metrics() as first:
        run(client)
        with collect_text_metrics() as second:
            run(client)
        run(client)
    assert first.snapshot()['sdk_operations_started'] == 2
    assert second.snapshot()['sdk_operations_started'] == 1
    snapshot = first.snapshot()
    snapshot['completion_tokens'] = -1
    assert first.snapshot()['completion_tokens'] == 40


def test_shared_run_collector_is_thread_safe():
    reporter = Reporter(None, heartbeat=0)
    failures = []
    def worker():
        token = CURRENT.set(reporter)
        try:
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kwargs: response(tokens=usage()))))
            for _ in range(25):
                run(client)
        except BaseException as error:
            failures.append(error)
        finally:
            CURRENT.reset(token)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    totals = reporter.text_metrics.snapshot()
    assert not failures
    assert totals['sdk_operations_started'] == totals['usage_responses'] == 100
    assert totals['completion_tokens'] == 2000 and totals['latency_observations'] == 100


def test_metrics_event_and_configuration_allowlists():
    assert safe_text_metrics({'completion_tokens': 4, 'usage_missing': 1,
        'latency_seconds': 0.2, 'sdk_internal_retries_observed': False,
        'api_key': 'SYNTHETIC_SECRET', 'model': 'SYNTHETIC_SECRET',
        'text': 'SYNTHETIC_SECRET', 'total_tokens': True,
        'prompt_tokens': -1, 'reasoning_tokens': None}) == {
        'completion_tokens': 4, 'usage_missing': 1, 'latency_seconds': 0.2,
        'sdk_internal_retries_observed': False}
    assert safe_configuration({'text_profile': 'balanced', 'editing_reasoning_effort': 'low',
        'review_reasoning_effort': 'medium', 'api_key': 'SYNTHETIC_SECRET'}) == {
        'text_profile': 'balanced', 'editing_reasoning_effort': 'low',
        'review_reasoning_effort': 'medium'}
    assert safe_configuration({'text_profile': ['SYNTHETIC_SECRET'],
        'editing_reasoning_effort': {'SYNTHETIC_SECRET': 1},
        'review_reasoning_effort': 'SYNTHETIC_SECRET'}) == {}


def test_explicit_collector_can_be_shared_without_global_leakage():
    collector = TextMetrics()
    client = MagicMock()
    client.chat.completions.create.return_value = response(tokens=usage())
    with collect_text_metrics(collector):
        run(client)
    run(client)
    assert collector.snapshot()['sdk_operations_started'] == 1


def test_real_sdk_response_usage_shape_is_supported():
    from openai.types.chat import ChatCompletion
    completion = ChatCompletion.model_validate({'id': 'synthetic', 'object': 'chat.completion',
        'created': 0, 'model': 'synthetic', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': 'Synthetic words.'}}],
        'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50,
            'prompt_tokens_details': {'cached_tokens': 10},
            'completion_tokens_details': {'reasoning_tokens': 12}}})
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    with collect_text_metrics() as metrics:
        run(client)
    assert metrics.snapshot()['reasoning_tokens'] == 12
    assert metrics.snapshot()['cached_prompt_tokens'] == 10
    assert metrics.snapshot()['usage_responses'] == 1


def test_audio_sdk_operations_do_not_enter_text_metrics():
    raw = MagicMock()
    raw.audio.transcriptions.create.return_value = SimpleNamespace(text='Synthetic words.', usage=usage())
    client = ControlledClient(raw, 600, 0)
    control = CURRENT_CONTROL.set(ProviderControl(retries=0))
    try:
        with collect_text_metrics() as metrics:
            client.audio.transcriptions.create(model='synthetic', file='synthetic')
    finally:
        CURRENT_CONTROL.reset(control)
    assert metrics.snapshot()['sdk_operations_started'] == metrics.snapshot()['usage_responses'] == 0
