"""Layer 4 retry helper: jittered backoff, deadline, and classification."""

from __future__ import annotations

import random

import pytest
from cloudcause_rate_limit import RetryDecision, run_with_retries


async def test_retries_up_to_the_configured_attempts_then_succeeds() -> None:
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("transient")
        return "ok"

    result = await run_with_retries(
        flaky,
        classify=lambda _error: RetryDecision(retryable=True),
        attempts=3,
        base_seconds=0.001,
        max_seconds=0.01,
        deadline_seconds=5.0,
        rng=random.Random(0),
    )
    assert result == "ok"
    assert calls == 3


async def test_non_retryable_error_is_attempted_once() -> None:
    calls = 0

    async def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("auth failure")

    with pytest.raises(ValueError):
        await run_with_retries(
            always_fails,
            classify=lambda _error: RetryDecision(retryable=False),
            attempts=3,
            base_seconds=0.001,
            max_seconds=0.01,
            deadline_seconds=5.0,
        )
    assert calls == 1


async def test_exhausted_attempts_raise_the_final_error() -> None:
    calls = 0

    async def always_throttled() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError(f"attempt {calls}")

    with pytest.raises(TimeoutError, match="attempt 3"):
        await run_with_retries(
            always_throttled,
            classify=lambda _error: RetryDecision(retryable=True),
            attempts=3,
            base_seconds=0.001,
            max_seconds=0.01,
            deadline_seconds=5.0,
        )
    assert calls == 3


async def test_deadline_stops_retries_even_with_attempts_remaining() -> None:
    import asyncio

    calls = 0

    async def slow_then_throttled() -> None:
        nonlocal calls
        calls += 1
        # Consumes the whole deadline itself, so no time is left for a retry
        # even though attempts remain and the error is retryable.
        await asyncio.sleep(0.03)
        raise TimeoutError("throttled")

    with pytest.raises(TimeoutError):
        await run_with_retries(
            slow_then_throttled,
            classify=lambda _error: RetryDecision(retryable=True),
            attempts=5,
            base_seconds=0.001,
            max_seconds=0.01,
            deadline_seconds=0.01,
        )
    assert calls == 1


async def test_on_retry_callback_receives_attempt_delay_and_error() -> None:
    calls = 0
    observed: list[tuple[int, float, str]] = []

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise TimeoutError("transient")
        return "ok"

    def on_retry(attempt: int, delay: float, error: BaseException) -> None:
        observed.append((attempt, delay, type(error).__name__))

    await run_with_retries(
        flaky,
        classify=lambda _error: RetryDecision(retryable=True),
        attempts=3,
        base_seconds=0.001,
        max_seconds=0.01,
        deadline_seconds=5.0,
        on_retry=on_retry,
    )
    assert observed == [(1, observed[0][1], "TimeoutError")]
    assert observed[0][1] >= 0


async def test_explicit_retry_after_delay_is_honored_over_exponential_backoff() -> None:
    calls = 0
    delays: list[float] = []

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise TimeoutError("throttled")
        return "ok"

    await run_with_retries(
        flaky,
        classify=lambda _error: RetryDecision(retryable=True, delay_seconds=0.002),
        attempts=3,
        base_seconds=10.0,  # would dominate if delay_seconds were ignored
        max_seconds=20.0,
        deadline_seconds=5.0,
        on_retry=lambda _attempt, delay, _error: delays.append(delay),
    )
    assert delays == [0.002]


async def test_a_delay_that_consumes_the_deadline_does_not_start_another_attempt() -> None:
    """The sleep can use the whole remaining budget; the next try would overrun it."""

    calls = 0

    async def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("transient")

    with pytest.raises(TimeoutError):
        await run_with_retries(
            always_fails,
            # A provider-supplied delay longer than what is left, so it clamps
            # to exactly the remaining deadline.
            classify=lambda _error: RetryDecision(retryable=True, delay_seconds=5.0),
            attempts=5,
            base_seconds=0.001,
            max_seconds=5.0,
            deadline_seconds=0.05,
            rng=random.Random(0),
        )
    assert calls == 1
