"""Layer 1 gateway admission control: client/global isolation and the 429 contract."""

from __future__ import annotations

import pytest
from cloudcause_rate_limit import AdmissionGuard, FakeClock, MemoryRateLimiter, RateLimitExceeded


def _guard(
    limiter: MemoryRateLimiter,
    *,
    client_per_hour: int = 3,
    client_burst: int = 2,
    global_per_minute: int = 20,
    enabled: bool = True,
) -> AdmissionGuard:
    return AdmissionGuard(
        limiter,
        enabled=enabled,
        client_per_hour=client_per_hour,
        client_burst=client_burst,
        global_per_minute=global_per_minute,
        id_hash_salt="test-salt",
    )


async def test_first_allowed_requests_succeed_then_the_next_is_denied() -> None:
    limiter = MemoryRateLimiter(clock=FakeClock())
    guard = _guard(limiter, client_per_hour=3, client_burst=0)

    for _ in range(3):
        await guard.check("203.0.113.10")

    with pytest.raises(RateLimitExceeded) as excinfo:
        await guard.check("203.0.113.10")
    assert excinfo.value.scope == "client_live_investigation"
    assert excinfo.value.retryable is True
    assert excinfo.value.retry_after_seconds > 0


async def test_different_clients_have_isolated_buckets() -> None:
    limiter = MemoryRateLimiter(clock=FakeClock())
    guard = _guard(limiter, client_per_hour=1, client_burst=0, global_per_minute=100)

    await guard.check("203.0.113.10")
    with pytest.raises(RateLimitExceeded):
        await guard.check("203.0.113.10")
    # A second client is unaffected by the first client's exhausted bucket.
    await guard.check("203.0.113.20")


async def test_global_bucket_caps_aggregate_starts_across_clients() -> None:
    limiter = MemoryRateLimiter(clock=FakeClock())
    guard = _guard(limiter, client_per_hour=100, client_burst=0, global_per_minute=2)

    await guard.check("203.0.113.10")
    await guard.check("203.0.113.11")
    with pytest.raises(RateLimitExceeded) as excinfo:
        await guard.check("203.0.113.12")
    assert excinfo.value.scope == "global_live_investigation"


async def test_disabled_guard_never_raises() -> None:
    limiter = MemoryRateLimiter(clock=FakeClock())
    guard = _guard(limiter, client_per_hour=1, client_burst=0, enabled=False)

    for _ in range(5):
        await guard.check("203.0.113.10")


async def test_error_body_matches_the_documented_contract() -> None:
    limiter = MemoryRateLimiter(clock=FakeClock())
    guard = _guard(limiter, client_per_hour=1, client_burst=0)

    await guard.check("203.0.113.10")
    with pytest.raises(RateLimitExceeded) as excinfo:
        await guard.check("203.0.113.10")

    body = excinfo.value.to_response_body()
    assert body["error"] == "rate_limit_exceeded"
    assert body["retryable"] is True
    assert body["scope"] == "client_live_investigation"
    assert isinstance(body["retry_after_seconds"], float)
    assert isinstance(body["detail"], str) and body["detail"]
