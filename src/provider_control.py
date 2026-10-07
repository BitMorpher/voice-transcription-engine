"""Batch-wide, thread-safe provider admission; no content or credentials."""

from contextvars import ContextVar
import math
import time
import threading
from types import SimpleNamespace

if __package__:
    from .provider_errors import classify, SYSTEMIC
    from .progress import CURRENT, call_text_operation
else:
    from provider_errors import classify, SYSTEMIC
    from progress import CURRENT, call_text_operation

CURRENT_CONTROL = ContextVar('provider_control', default=None)
STOP_REASONS = {'request_limit', 'start_deadline', 'provider_failures', 'validation_failures',
                'systemic_provider', 'interrupted'}


class ProviderStopped(RuntimeError):
    """Admission denied before any additional SDK operation."""


class ProviderControl:
    def __init__(self, *, max_requests=None, max_seconds=None, failure_limit=2,
                 validation_failure_limit=3, retries=2,
                 clock=time.monotonic):
        if (max_requests is not None and (type(max_requests) is not int or max_requests < 1)
                or max_seconds is not None and (type(max_seconds) not in (int, float)
                    or not math.isfinite(max_seconds) or max_seconds <= 0)
                or type(failure_limit) is not int or failure_limit < 1
                or type(validation_failure_limit) is not int or validation_failure_limit < 1
                or type(retries) is not int or not 0 <= retries <= 5
                or (max_requests is not None or max_seconds is not None) and retries != 0):
            raise ValueError('Provider request/time limits require --provider-retries 0 and positive limits.')
        self.max_requests, self.max_seconds = max_requests, max_seconds
        self.failure_limit, self.clock = failure_limit, clock
        self.validation_failure_limit = validation_failure_limit
        self.validation_retries = min(1, retries)
        self.cooldown_until = self.next_slot = 0.0
        self.started = clock()
        self.requests = self.failures = 0
        self.failure_streaks = {}
        self.validation_failures = 0
        self.validation_failures_by_scope = {}
        self.reason = None
        self.lock = threading.RLock()
        self.cancelled = threading.Event()

    def validation_failed(self, stage, model):
        """Count terminal text failures once, after allowed local recovery.

        The run stops when one stage/model reaches its cumulative threshold.
        Neither SDK success nor another scope erases these observations. Already
        admitted I/O drains and can save verified checkpoints. No response body
        or user-controlled scope value is emitted.
        """
        scope = (stage, model if isinstance(model, str) else None)
        with self.lock:
            self.validation_failures += 1
            count = self.validation_failures_by_scope.get(scope, 0) + 1
            self.validation_failures_by_scope[scope] = count
            if self.reason is None and count >= self.validation_failure_limit:
                self.reason = 'validation_failures'
            reporter = CURRENT.get()
            if reporter:
                reporter.emit(status='progress', validation_failures=self.validation_failures,
                              scope_validation_failures=count,
                              stop_reason=self.reason)

    def cancel(self):
        """Deny subsequent calls while already admitted I/O drains normally."""
        with self.lock:
            self.cancelled.set()
            if self.reason is None:
                self.reason = 'interrupted'

    def remaining(self):
        return None if self.max_seconds is None else self.max_seconds - (self.clock() - self.started)

    def check(self):
        with self.lock:
            self._check()

    def _check(self):
        if self.reason is None:
            if self.max_requests is not None and self.requests >= self.max_requests:
                self.reason = 'request_limit'
            else:
                remaining = self.remaining()
                if remaining is not None and remaining <= 0:
                    self.reason = 'start_deadline'
        if self.reason:
            reporter = CURRENT.get()
            if reporter:
                reporter.emit(status='not_attempted', stop_reason=self.reason, provider_requests=self.requests)
            raise ProviderStopped('Provider admission stopped; completed checkpoints are retained. No additional request was started.')

    def call(self, client, path, timeout, retries, parameters):
        while True:
            self._wait_for_slot()
            # Admission and paced slot reservation are atomic with cooldown updates.
            with self.lock:
                self._check()
                now = self.clock()
                if now < max(self.cooldown_until, self.next_slot):
                    continue
                if self.cooldown_until:
                    self.next_slot = now + 0.25
                remaining = self.remaining()
                if remaining is not None and remaining <= 0:
                    self.reason = 'start_deadline'
                    self._check()
                effective_timeout = timeout if remaining is None else min(timeout, remaining)
                if (self.max_requests is not None or self.max_seconds is not None) and retries != 0:
                    raise ProviderStopped('Bounded provider calls require zero SDK retries.')
                self.requests += 1
                reporter = CURRENT.get()
                if reporter:
                    reporter.emit(status='progress', provider_requests=self.requests,
                                  effective_provider_timeout=effective_timeout)
                break
        # Count SDK operation starts. Zero retries prevents SDK retry attempts;
        # redirects/custom transports are not monetary or server-work guarantees.
        import openai
        if isinstance(client, openai.OpenAI):
            client = client.with_options(timeout=effective_timeout, max_retries=retries)
        method = client
        for name in path:
            method = getattr(method, name)
        model = parameters.get('model')
        scope = (path, model if isinstance(model, str) else None)
        try:
            response = (call_text_operation(method, parameters)
                        if path == ('chat', 'completions', 'create') else method(**parameters))
        except Exception as error:
            category = classify(error)['error_category']
            with self.lock:
                if category == 'rate_limit':
                    delay = rate_limit_delay(error)
                    self.cooldown_until = max(self.cooldown_until, self.clock() + delay)
                self.failures = self.failure_streaks.get(scope, 0) + 1
                self.failure_streaks[scope] = self.failures
                if self.reason is None:
                    if category in SYSTEMIC:
                        self.reason = 'systemic_provider'
                    elif self.failures >= self.failure_limit:
                        self.reason = 'provider_failures'
            raise
        else:
            # Observe streaks in response-completion order. A success cannot
            # reopen a stopped run, including cancellation or another scope.
            with self.lock:
                self.failures = self.failure_streaks[scope] = 0
            return response

    def _wait_for_slot(self):
        """Interruptible shared cooldown, then stagger subsequent worker starts.

        Already admitted I/O and SDK-internal retries cannot be recalled.
        Reservation and admission share the lock to avoid a cooldown race.
        """
        while True:
            with self.lock:
                self._check()
                now = self.clock()
                delay = max(self.cooldown_until, self.next_slot) - now
                if delay <= 0:
                    return
                remaining = self.remaining()
                if remaining is not None:
                    delay = min(delay, remaining)
            self.cancelled.wait(min(delay, 0.25))


def rate_limit_delay(error):
    """Parse bounded Retry-After metadata locally, never publish headers."""
    from email.utils import parsedate_to_datetime
    from datetime import datetime, timezone
    try:
        import openai
        if not isinstance(error, openai.APIStatusError):
            return 2.0
        headers = error.response.headers
        if 'retry-after-ms' in headers:
            delay = float(headers['retry-after-ms']) / 1000
        else:
            value = headers.get('retry-after', '')
            try:
                delay = float(value)
            except ValueError:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        return min(120.0, max(1.0, delay)) if math.isfinite(delay) else 2.0
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 2.0


class ControlledClient:
    """Cover ASR, diarization, editing, review and chapters with one run budget."""
    def __init__(self, client, timeout, retries):
        self.raw, self.timeout, self.retries = client, timeout, retries
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._audio))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _call(self, path, parameters):
        control = CURRENT_CONTROL.get()
        if control:
            return control.call(self.raw, path, self.timeout, self.retries, parameters)
        method = self.raw
        for name in path:
            method = getattr(method, name)
        return (call_text_operation(method, parameters)
                if path == ('chat', 'completions', 'create') else method(**parameters))

    def _audio(self, **parameters):
        return self._call(('audio', 'transcriptions', 'create'), parameters)

    def _chat(self, **parameters):
        return self._call(('chat', 'completions', 'create'), parameters)
