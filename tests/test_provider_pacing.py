"""Shared final-429 pacing, cancellation and safe recovery with no network."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import json
import threading
import time
from unittest.mock import MagicMock

import httpx2
import openai
import pytest

from src.progress import CURRENT, Reporter
from src.provider_control import (CURRENT_CONTROL, ControlledClient, ProviderControl,
                                  ProviderStopped, rate_limit_delay)
from src.text_requests import ResponseValidationError, validated_chat
from tests.test_text_requests import completion


def throttled(headers=None, code=None):
    response = httpx2.Response(429, request=httpx2.Request('POST', 'https://example.invalid'),
                              headers=headers or {})
    return openai.RateLimitError('SYNTHETIC_SECRET', response=response,
                                body={'code': code} if code else None)


@pytest.mark.parametrize('headers,expected', [({}, 2), ({'retry-after': '1.5'}, 1.5),
    ({'retry-after-ms': '1500'}, 1.5), ({'retry-after': '500'}, 120),
    ({'retry-after': '-10'}, 1), ({'retry-after': 'nan'}, 2),
    ({'retry-after': 'SYNTHETIC_SECRET'}, 2)])
def test_retry_after_is_bounded_and_not_echoed(headers, expected):
    assert rate_limit_delay(throttled(headers)) == expected


def test_retry_after_date_is_supported():
    future = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert 28 <= rate_limit_delay(throttled({'retry-after': format_datetime(future)})) <= 30


def test_two_workers_share_cooldown_and_staggered_starts():
    client = MagicMock()
    seen = []
    def respond(**parameters):
        seen.append(time.monotonic())
        if len(seen) == 1:
            raise throttled({'retry-after-ms': '1'})  # bounded to a one-second minimum
        return 'ok'
    client.chat.completions.create.side_effect = respond
    control = ProviderControl(retries=0, failure_limit=3)
    token = CURRENT_CONTROL.set(control)
    try:
        wrapped = ControlledClient(client, 600, 0)
        with pytest.raises(openai.RateLimitError):
            wrapped.chat.completions.create(model='gpt-6-astra')
        contexts = [copy_context(), copy_context()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(context.run, wrapped.chat.completions.create, model='gpt-6-astra')
                       for context in contexts]
            assert [future.result(timeout=5) for future in futures] == ['ok', 'ok']
    finally:
        CURRENT_CONTROL.reset(token)
    assert control.requests == 3
    assert seen[1] - seen[0] >= 0.95
    assert seen[2] - seen[1] >= 0.20
    assert control.failure_streaks[(('chat', 'completions', 'create'), 'gpt-6-astra')] == 0


@pytest.mark.parametrize('stop', ['cancel', 'deadline', 'quota'])
def test_waiting_worker_stops_without_new_operation(stop):
    client = MagicMock()
    control = ProviderControl(retries=0, max_seconds=0.15 if stop == 'deadline' else None)
    control.cooldown_until = time.monotonic() + 120
    if stop == 'quota':
        # Hard quota classification never initiates transient cooldown/recovery.
        control.cooldown_until = 0
        client.chat.completions.create.side_effect = throttled(code='insufficient_quota')
        with pytest.raises(openai.RateLimitError):
            control.call(client, ('chat', 'completions', 'create'), 600, 0, {})
        assert control.reason == 'systemic_provider' and control.cooldown_until == 0
    timer = threading.Timer(0.05, control.cancel) if stop == 'cancel' else None
    if timer:
        timer.start()
    try:
        with pytest.raises(ProviderStopped):
            control.call(client, ('chat', 'completions', 'create'), 600, 0, {})
    finally:
        if timer:
            timer.join()
    assert client.chat.completions.create.call_count == (1 if stop == 'quota' else 0)


def test_cancellation_between_validation_and_recovery_prevents_new_request():
    raw = MagicMock()
    raw.chat.completions.create.return_value = completion('{}')
    control = ProviderControl(retries=2)
    token = CURRENT_CONTROL.set(control)
    def validate(content):
        control.cancel()
        raise ResponseValidationError('SYNTHETIC_SECRET')
    try:
        with pytest.raises(ProviderStopped):
            validated_chat(ControlledClient(raw, 600, 2), {}, validate, stage='author_review')
    finally:
        CURRENT_CONTROL.reset(token)
    assert control.requests == raw.chat.completions.create.call_count == 1


def test_local_validation_category_never_serializes_payloads():
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0)
    token = CURRENT.set(reporter)
    try:
        reporter.emit(error_category='validation_diarization', error='SYNTHETIC_SECRET',
                      headers={'retry-after': 'SYNTHETIC_SECRET'})
    finally:
        CURRENT.reset(token)
    record = json.loads(stream.getvalue())
    assert record['error_category'] == 'validation_diarization'
    assert 'SYNTHETIC_SECRET' not in stream.getvalue()
