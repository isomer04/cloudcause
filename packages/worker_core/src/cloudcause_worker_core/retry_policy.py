"""Provider-specific retry classification for live model calls.

Lives here, not in ``cloudcause_rate_limit``, because it inspects
provider-SDK exception shapes (OpenAI, httpx, google-genai) by duck typing
rather than importing those optional SDKs directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from cloudcause_rate_limit import RateLimitExceeded, RetryDecision

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
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _retry_after_http_date(raw)
    # A negative delay is not a delay; fall back to the caller's own backoff.
    return seconds if seconds >= 0 else None


def _retry_after_http_date(raw: str) -> float | None:
    """RFC 9110 allows Retry-After to be an HTTP-date instead of a delay."""

    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    # A date already in the past means "retry now", not "retry in the past".
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
