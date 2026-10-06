"""Classify SDK failure metadata without messages, headers, bodies or request IDs."""

CATEGORIES = {'authentication', 'permission', 'model_access', 'invalid_request', 'quota',
              'rate_limit', 'timeout', 'connection', 'server', 'provider_unknown',
              'completion', 'validation', 'not_attempted'}
GUIDANCE = {
    'authentication': 'Check OPENAI_API_KEY locally before authorizing another provider run.',
    'permission': 'Check account/project permissions locally before retrying.',
    'model_access': 'Check the selected model and account access before retrying.',
    'invalid_request': 'Check model capabilities and request configuration before retrying.',
    'quota': 'Check account billing/quota locally before retrying; do not repeat the batch blindly.',
    'rate_limit': 'Provider rate limit reached; wait and check limits before retrying.',
    'timeout': 'Provider request timed out; check connectivity and request timeout/retry bounds.',
    'connection': 'Provider connection failed; check connectivity and endpoint configuration.',
    'server': 'Provider service failed; check service availability before retrying.',
    'provider_unknown': 'Provider failure type is unknown; inspect configuration locally before retrying.',
    'completion': 'Provider output was refused, truncated or malformed; no complete review is claimed.',
    'validation': 'Output failed schema, coverage or exact-source validation; preserve raw and failed reports.',
    'not_attempted': 'This provider operation was not started because admission stopped.',
}
SYSTEMIC = {'authentication', 'permission', 'model_access', 'invalid_request', 'quota'}


def classify(error):
    """Return only enums and an integer HTTP status from a recognized SDK error.

    Quota vs transient throttling is distinguished only when the SDK's scalar
    code exactly matches a known quota code. No arbitrary code value is emitted.
    Unknown/injected exceptions never have their fields or text serialized.
    """
    category, status = 'provider_unknown', None
    try:
        if __package__:
            from .provider_control import ProviderStopped
        else:
            from provider_control import ProviderStopped
        if isinstance(error, ProviderStopped):
            return {'error_category': 'not_attempted'}
        import openai
        if not isinstance(error, openai.APIError):
            return {'error_category': category}
        value = getattr(error, 'status_code', None)
        if type(value) is int and 100 <= value <= 599:
            status = value
        if isinstance(error, openai.APITimeoutError):
            category = 'timeout'
        elif isinstance(error, openai.APIConnectionError):
            category = 'connection'
        elif status in {401, 403, 404, 400, 422, 429}:
            category = {401: 'authentication', 403: 'permission', 404: 'model_access',
                        400: 'invalid_request', 422: 'invalid_request', 429: 'rate_limit'}[status]
            code = getattr(error, 'code', None)
            if status == 429 and isinstance(code, str) and code in {'insufficient_quota', 'billing_not_active'}:
                category = 'quota'
        elif status is not None and status >= 500:
            category = 'server'
    except Exception:
        return {'error_category': 'provider_unknown'}
    result = {'error_category': category}
    if status is not None:
        result['http_status'] = status
    return result
