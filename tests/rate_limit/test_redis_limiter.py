"""RedisRateLimiter against fakeredis by default, or a real Redis in CI.

Set CLOUDCAUSE_TEST_REDIS_URL to run these against a real server (the
redis-rate-limit CI job does this against a service container); otherwise
they run hermetically against fakeredis, exercising the same Lua scripts.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from cloudcause_rate_limit import RateLimitExceeded, RedisRateLimiter


@pytest.fixture
async def limiter():
    real_url = os.environ.get("CLOUDCAUSE_TEST_REDIS_URL")
    if real_url:
        instance = RedisRateLimiter(real_url, namespace=f"cctest-{os.getpid()}")
    else:
        import fakeredis.aioredis

        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        instance = RedisRateLimiter("redis://ignored", client=client)
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_ping_reports_reachability(limiter: RedisRateLimiter) -> None:
    assert await limiter.ping() is True


async def test_token_bucket_allows_up_to_capacity_then_denies(limiter: RedisRateLimiter) -> None:
    key = "test:tokens"
    for _ in range(3):
        result = await limiter.acquire_tokens(key, capacity=3, refill_per_second=3 / 60)
        assert result.allowed
    denied = await limiter.acquire_tokens(key, capacity=3, refill_per_second=3 / 60)
    assert not denied.allowed
    assert denied.retry_after_seconds > 0


async def test_concurrency_lease_is_exclusive_and_reusable(limiter: RedisRateLimiter) -> None:
    key = "test:concurrency"
    async with limiter.concurrency_permit(key, maximum=1, timeout_seconds=1.0):
        with pytest.raises(RateLimitExceeded):
            async with limiter.concurrency_permit(key, maximum=1, timeout_seconds=0.1):
                pass  # pragma: no cover - never reached

    # Released after the first block exits, so it can be acquired again.
    async with limiter.concurrency_permit(key, maximum=1, timeout_seconds=1.0):
        pass


async def test_concurrency_lease_release_cannot_remove_another_leaseholder(
    limiter: RedisRateLimiter,
) -> None:
    key = "test:concurrency-two"
    first_acquired = asyncio.Event()
    release_first = asyncio.Event()

    async def hold_first() -> None:
        async with limiter.concurrency_permit(key, maximum=2, timeout_seconds=1.0):
            first_acquired.set()
            await release_first.wait()

    task = asyncio.create_task(hold_first())
    await asyncio.wait_for(first_acquired.wait(), timeout=1.0)
    try:
        # A second, independent lease must not be disturbed by the first
        # holder's eventual release -- each lease id is unique.
        async with limiter.concurrency_permit(key, maximum=2, timeout_seconds=1.0):
            pass
    finally:
        release_first.set()
        await task


async def _short_lease_limiter(lease_ttl_seconds: float) -> RedisRateLimiter:
    real_url = os.environ.get("CLOUDCAUSE_TEST_REDIS_URL")
    if real_url:
        return RedisRateLimiter(
            real_url, namespace=f"cctest-lease-{os.getpid()}", lease_ttl_seconds=lease_ttl_seconds
        )
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return RedisRateLimiter("redis://ignored", client=client, lease_ttl_seconds=lease_ttl_seconds)


async def test_a_held_lease_is_renewed_past_its_ttl() -> None:
    """A call outliving the lease TTL must not have its slot swept out from under it."""

    # Fractional on purpose: EXPIRE takes whole seconds, so this also covers
    # the TTL rounding.
    limiter = await _short_lease_limiter(0.3)
    try:
        async with limiter.concurrency_permit("test:renew", maximum=1, timeout_seconds=1.0):
            # Well past the lease TTL: without the heartbeat the lease would
            # have aged out of the sorted set and a second caller could claim
            # the same slot.
            await asyncio.sleep(0.9)
            with pytest.raises(RateLimitExceeded):
                async with limiter.concurrency_permit("test:renew", maximum=1, timeout_seconds=0.05):
                    pass
        # Released for real once the body exits.
        async with limiter.concurrency_permit("test:renew", maximum=1, timeout_seconds=0.5):
            pass
    finally:
        await limiter.aclose()
