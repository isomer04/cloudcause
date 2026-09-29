"""Provider-specific retry classification for live model calls.

Lives here, not in ``cloudcause_rate_limit``, because it inspects
provider-SDK exception shapes (OpenAI, httpx, google-genai) by duck typing
rather than importing those optional SDKs directly.
"""

from __future__ import annotations

from cloudcause_rate_limit import RateLimitExceeded, RetryDecision, parse_retry_after

from .live_limits import AgentCallLimitExceeded

#: Exception type-name fragments (lowercased) that signal a retryable failure
#: across providers whose SDKs are not installed in every deployment.
_RETRYABLE_NAME_TOKENS = ("ratelimit", "throttl", "serviceunavailable", "timeout", "connection")
#: Fragments that signal a failure retrying can never fix.
_NON_RETRYABLE_NAME_TOKENS = ("auth", "permission", "invalid", "safety", "badrequest")


def classify_live_agent_error(error: BaseException) -> RetryDecision:
    """Decide whether one live-agent-attempt failure should be retried."""

    if isinstance(error, AgentCallLimitExceeded):
        # A deterministic in-process budget, not a transient failure.
        return RetryDecision(retryable=False)
    if isinstance(error, RateLimitExceeded):
        # The governor already waited up to its own bounded timeout; retrying
        # immediately competes for the same exhausted capacity.
        return RetryDecision(retryable=False)

    status_code = _status_code(error)
    if status_code is not None:
        if status_code == 429:
            return RetryDecision(retryable=True, delay_seconds=_retry_after_seconds(error))
        if 500 <= status_code < 600:
            return RetryDecision(retryable=True)
        return RetryDecision(retryable=False)

    if isinstance(error, TimeoutError | ConnectionError | OSError):
        return RetryDecision(retryable=True)

    type_name = type(error).__name__.lower()
    if any(token in type_name for token in _RETRYABLE_NAME_TOKENS):
        return RetryDecision(retryable=True)
    if any(token in type_name for token in _NON_RETRYABLE_NAME_TOKENS):
        return RetryDecision(retryable=False)
    return RetryDecision(retryable=False)


def _status_code(error: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _retry_after_seconds(error: BaseException) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    return parse_retry_after(raw)
