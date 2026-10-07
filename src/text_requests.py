"""Validated private text checkpoints and bounded local response recovery."""

import hashlib

if __package__:
    from .chunk_cache import ChunkCache
    from .model_config import _fingerprint
    from .provider_control import CURRENT_CONTROL, ControlledClient
    from .progress import (TEXT_VALIDATION_RETRY, call_text_operation, current_text_metrics,
                           emit_progress, collect_text_metrics as collect_text_metrics,
                           TextMetrics as TextMetrics)
else:
    from chunk_cache import ChunkCache
    from model_config import _fingerprint
    from provider_control import CURRENT_CONTROL, ControlledClient
    from progress import (TEXT_VALIDATION_RETRY, call_text_operation, current_text_metrics,
                          emit_progress, collect_text_metrics as collect_text_metrics,
                          TextMetrics as TextMetrics)


VALIDATION_CATEGORIES = {'validation_schema', 'validation_coverage', 'validation_source',
                         'validation_quote_missing', 'validation_quote_ambiguous',
                         'validation_diarization', 'completion'}


class ResponseValidationError(ValueError, RuntimeError):
    """Only fixed local categories are exported; arbitrary messages stay private."""
    def __init__(self, message, *, category='validation_schema'):
        super().__init__(message)
        self.category = category if category in VALIDATION_CATEGORIES else 'validation_schema'


class TextRequestCache(ChunkCache):
    """Bind exact requests plus source, settings and local validator contract.

    The caller holds its existing job lock. Cached content is validated at
    construction, at reuse, and before publishing a completed stage. Hashes
    detect tampering relative to the manifest, not malicious replacement of both.
    """
    def __init__(self, root, binding, parameters, validators):
        self.validators = validators
        layout = [{'index': index, 'request_sha256': _fingerprint(request)}
                  for index, request in enumerate(parameters, 1)]
        super().__init__(root, {'text_contract': 1, **binding}, layout, None)

    def _validate(self, body, record):
        if not isinstance(body, dict) or set(body) != {'content'} or not isinstance(body['content'], str):
            raise ResponseValidationError('Text checkpoint has an invalid response schema.')
        self.validators[record['index'] - 1](body['content'])


def text_binding(stage, source, settings, **semantics):
    return {'stage': stage, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'settings_sha256': settings, **semantics}


def completion_content(response):
    try:
        choices = response.choices
        invalid_count = not isinstance(choices, list) or len(choices) != 1
        if not invalid_count:
            choice = choices[0]
            incomplete = choice.finish_reason != 'stop' or bool(getattr(choice.message, 'refusal', None))
            content = choice.message.content
    except Exception:
        raise ResponseValidationError('Text provider response could not be read.', category='completion') from None
    if invalid_count:
        raise ResponseValidationError('Text provider response has an invalid completion count.', category='completion')
    if incomplete:
        raise ResponseValidationError('Text output was incomplete or refused.', category='completion')
    if not isinstance(content, str) or not content.strip():
        raise ResponseValidationError('Text response was empty or malformed.')
    return content


def validated_chat(client, parameters, validate, *, stage, cache=None, index=1):
    record = cache.layout[index - 1] if cache else None
    if cache:
        saved = cache.get(record)
        if saved is not None:
            result = validate(saved['content'])
            metrics = current_text_metrics()
            if metrics is not None:
                metrics.cache_hit()
            emit_progress(stage, 'skipped', chunk=index)
            return result
    control = CURRENT_CONTROL.get()
    # At most one additional application operation; diagnostics with retries=0
    # never repeat. SDK retries still apply independently to each operation.
    recoveries = control.validation_retries if control else 0
    for attempt in range(recoveries + 1):
        if control:
            control.check()
        token = TEXT_VALIDATION_RETRY.set(attempt > 0)
        try:
            try:
                method = client.chat.completions.create
                response = (method(**parameters) if isinstance(client, ControlledClient)
                            else call_text_operation(method, parameters))
            except ResponseValidationError:
                # A provider/injected client cannot supply trusted local error prose.
                raise RuntimeError('Provider text request failed.') from None
        finally:
            TEXT_VALIDATION_RETRY.reset(token)
        try:
            content = completion_content(response)
            result = validate(content)
        except ResponseValidationError as error:
            if error.category not in {'validation_schema', 'validation_coverage'} or attempt >= recoveries:
                if control:
                    control.validation_failed(stage, parameters.get('model'))
                raise
            emit_progress(stage, 'failed', chunk=index, error_category=error.category)
            emit_progress(stage, 'running', chunk=index, validation_retries=attempt + 1)
            continue
        if cache:
            cache.put(record, {'content': content})
        return result
