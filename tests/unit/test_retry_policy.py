"""Provider-error classification for live-agent retries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
from cloudcause_rate_limit import RateLimitExceeded
from cloudcause_worker_core import AgentCallLimitExceeded
from cloudcause_worker_core.retry_policy import classify_live_agent_error


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        # Real provider SDKs expose case-insensitive headers; a plain dict
        # would let a casing regression pass here but fail in production.
        self.headers = httpx.Headers(headers or {})


class _FakeAPIError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        super().__init__("provider error")
        self.response = _FakeResponse(status_code, headers)


def test_429_is_retryable_and_honors_retry_after() -> None:
    decision = classify_live_agent_error(_FakeAPIError(429, {"retry-after": "12"}))
    assert decision.retryable is True
    assert decision.delay_seconds == 12.0
    # Header casing is the provider's choice, not ours.
    assert classify_live_agent_error(_FakeAPIError(429, {"Retry-After": "12"})).delay_seconds == 12.0


def test_retry_after_accepts_an_http_date() -> None:
    """RFC 9110 allows Retry-After to be a date rather than a delay."""

    when = datetime.now(UTC) + timedelta(seconds=30)
    decision = classify_live_agent_error(_FakeAPIError(429, {"retry-after": format_datetime(when)}))
    assert decision.retryable is True
    assert decision.delay_seconds is not None
    assert 25 <= decision.delay_seconds <= 31


def test_a_retry_after_date_in_the_past_means_retry_now() -> None:
    past = datetime.now(UTC) - timedelta(seconds=30)
    decision = classify_live_agent_error(_FakeAPIError(429, {"retry-after": format_datetime(past)}))
    assert decision.delay_seconds == 0.0


def test_an_unusable_retry_after_falls_back_to_backoff() -> None:
    for raw in ("not-a-delay", "-5"):
        decision = classify_live_agent_error(_FakeAPIError(429, {"retry-after": raw}))
        assert decision.retryable is True
        assert decision.delay_seconds is None


def test_5xx_is_retryable() -> None:
    for status in (500, 502, 503, 504):
        assert classify_live_agent_error(_FakeAPIError(status)).retryable is True


def test_4xx_other_than_429_is_not_retryable() -> None:
    assert classify_live_agent_error(_FakeAPIError(401)).retryable is False
    assert classify_live_agent_error(_FakeAPIError(400)).retryable is False


def test_network_errors_are_retryable() -> None:
    assert classify_live_agent_error(TimeoutError("timed out")).retryable is True
    assert classify_live_agent_error(ConnectionError("reset")).retryable is True
    assert classify_live_agent_error(OSError("network unreachable")).retryable is True


def test_agent_call_budget_exhaustion_is_not_retryable() -> None:
    error = AgentCallLimitExceeded("live agent call budget exhausted at x (1/1)")
    assert classify_live_agent_error(error).retryable is False


def test_governor_rate_limit_exceeded_is_not_retried_at_this_layer() -> None:
    error = RateLimitExceeded("openai:gpt-4.1-mini", "no permit", retry_after_seconds=5.0)
    assert classify_live_agent_error(error).retryable is False


def test_unknown_error_defaults_to_non_retryable() -> None:
    assert classify_live_agent_error(RuntimeError("something odd")).retryable is False
