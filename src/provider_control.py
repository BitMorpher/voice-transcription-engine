"""Shared serial provider admission limits; no content, credentials, or cost estimates."""

from contextvars import ContextVar
import math
import time
from types import SimpleNamespace

if __package__:
    from .provider_errors import classify, SYSTEMIC
    from .progress import CURRENT
else:
    from provider_errors import classify, SYSTEMIC
    from progress import CURRENT

CURRENT_CONTROL = ContextVar('provider_control', default=None)
STOP_REASONS = {'request_limit', 'start_deadline', 'provider_failures', 'systemic_provider'}


class ProviderStopped(RuntimeError):
    """Admission denied before any additional SDK operation."""


class ProviderControl:
    def __init__(self, *, max_requests=None, max_seconds=None, failure_limit=2, retries=2,
                 clock=time.monotonic):
        if (max_requests is not None and (type(max_requests) is not int or max_requests < 1)
                or max_seconds is not None and (type(max_seconds) not in (int, float)
                    or not math.isfinite(max_seconds) or max_seconds <= 0)
                or type(failure_limit) is not int or failure_limit < 1
                or (max_requests is not None or max_seconds is not None) and retries != 0):
            raise ValueError('Provider request/time limits require --provider-retries 0 and positive limits.')
        self.max_requests, self.max_seconds = max_requests, max_seconds
        self.failure_limit, self.clock = failure_limit, clock
        self.started = clock()
        self.requests = self.failures = 0
        self.failure_streaks = {}
        self.reason = None

    def remaining(self):
        return None if self.max_seconds is None else self.max_seconds - (self.clock() - self.started)

    def check(self):
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
        self.check()
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            self.reason = 'start_deadline'
            self.check()
        effective_timeout = timeout if remaining is None else min(timeout, remaining)
        # Count SDK operation starts. Zero retries prevents SDK retry attempts;
        # redirects/custom transports are not monetary or server-work guarantees.
        if (self.max_requests is not None or self.max_seconds is not None) and retries != 0:
            raise ProviderStopped('Bounded provider calls require zero SDK retries.')
        import openai
        if isinstance(client, openai.OpenAI):
            client = client.with_options(timeout=effective_timeout, max_retries=retries)
        self.requests += 1
        reporter = CURRENT.get()
        if reporter:
            reporter.emit(status='progress', provider_requests=self.requests,
                          effective_provider_timeout=effective_timeout)
        method = client
        for name in path:
            method = getattr(method, name)
        model = parameters.get('model')
        scope = (path, model if isinstance(model, str) else None)
        try:
            response = method(**parameters)
        except Exception as error:
            self.failures = self.failure_streaks.get(scope, 0) + 1
            self.failure_streaks[scope] = self.failures
            category = classify(error)['error_category']
            if category in SYSTEMIC:
                self.reason = 'systemic_provider'
            elif self.failures >= self.failure_limit:
                self.reason = 'provider_failures'
            raise
        else:
            self.failures = self.failure_streaks[scope] = 0
            return response


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
        return method(**parameters)

    def _audio(self, **parameters):
        return self._call(('audio', 'transcriptions', 'create'), parameters)

    def _chat(self, **parameters):
        return self._call(('chat', 'completions', 'create'), parameters)
