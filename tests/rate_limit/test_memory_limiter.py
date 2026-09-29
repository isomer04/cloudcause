"""Unit tests for MemoryRateLimiter: token bucket math, concurrency, eviction."""

from __future__ import annotations

import asyncio

import pytest
from cloudcause_rate_limit import FakeClock, MemoryRateLimiter, RateLimitExceeded


async def test_token_bucket_refills_over_time_and_denies_when_empty() -> None:
    clock = FakeClock()
    limiter = MemoryRateLimiter(clock=clock)

    first = await limiter.acquire_tokens("k", capacity=2, refill_per_second=1.0)
    second = await limiter.acquire_tokens("k", capacity=2, refill_per_second=1.0)
    third = await limiter.acquire_tokens("k", capacity=2, refill_per_second=1.0)

    assert first.allowed and second.allowed
    assert not third.allowed
    assert third.retry_after_seconds == pytest.approx(1.0, abs=0.01)

    clock.advance(1.0)
    fourth = await limiter.acquire_tokens("k", capacity=2, refill_per_second=1.0)
    assert fourth.allowed


async def test_token_bucket_never_exceeds_capacity_after_a_long_idle_period() -> None:
    clock = FakeClock()
    limiter = MemoryRateLimiter(clock=clock)

    await limiter.acquire_tokens("k", capacity=3, refill_per_second=1.0)
    clock.advance(1000.0)  # far more than enough to refill past capacity

    result = await limiter.acquire_tokens("k", capacity=3, refill_per_second=1.0, cost=3.0)
    assert result.allowed
    assert result.remaining == pytest.approx(0.0, abs=1e-9)


async def test_separate_bucket_keys_are_isolated() -> None:
    clock = FakeClock()
    limiter = MemoryRateLimiter(clock=clock)

    await limiter.acquire_tokens("client:a", capacity=1, refill_per_second=1.0)
    denied_a = await limiter.acquire_tokens("client:a", capacity=1, refill_per_second=1.0)
    allowed_b = await limiter.acquire_tokens("client:b", capacity=1, refill_per_second=1.0)

    assert not denied_a.allowed
    assert allowed_b.allowed


async def test_concurrency_permit_never_exceeds_maximum() -> None:
    limiter = MemoryRateLimiter()
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with limiter.concurrency_permit("provider", maximum=2, timeout_seconds=1.0):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak <= 2
    assert active == 0


async def test_concurrency_permit_times_out_when_exhausted() -> None:
    limiter = MemoryRateLimiter()
    hold_released = asyncio.Event()

    async def holder() -> None:
        async with limiter.concurrency_permit("provider", maximum=1, timeout_seconds=1.0):
            await hold_released.wait()

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0.01)  # let the holder actually acquire first
    try:
        with pytest.raises(RateLimitExceeded, match="provider"):
            async with limiter.concurrency_permit("provider", maximum=1, timeout_seconds=0.05):
                pass
    finally:
        hold_released.set()
        await holder_task


async def test_concurrency_permit_releases_on_exception_and_cancellation() -> None:
    limiter = MemoryRateLimiter()

    with pytest.raises(ValueError):
        async with limiter.concurrency_permit("k", maximum=1, timeout_seconds=1.0):
            raise ValueError("boom")

    async def cancel_me() -> None:
        async with limiter.concurrency_permit("k", maximum=1, timeout_seconds=1.0):
            await asyncio.sleep(10)

    task = asyncio.create_task(cancel_me())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The slot must be free again after both the exception and the cancellation.
    async with limiter.concurrency_permit("k", maximum=1, timeout_seconds=0.1):
        pass


async def test_ping_is_always_true_for_the_in_process_backend() -> None:
    assert await MemoryRateLimiter().ping() is True


async def test_evict_inactive_drops_only_buckets_past_the_threshold() -> None:
    clock = FakeClock()
    limiter = MemoryRateLimiter(clock=clock)

    await limiter.acquire_tokens("stale", capacity=1, refill_per_second=1.0)
    clock.advance(100.0)
    await limiter.acquire_tokens("fresh", capacity=1, refill_per_second=1.0)

    evicted = await limiter.evict_inactive(older_than_seconds=50.0)
    assert evicted == 1
    # The surviving bucket keeps its token state: "fresh" spent its only token
    # above and the clock has not advanced since, so it must still be empty.
    # Had it been evicted, it would come back refilled and allow this.
    assert (await limiter.acquire_tokens("fresh", capacity=1, refill_per_second=1.0)).allowed is False
    # The evicted one is rebuilt full.
    assert (await limiter.acquire_tokens("stale", capacity=1, refill_per_second=1.0)).allowed is True


async def test_a_changed_concurrency_maximum_for_one_key_is_rejected() -> None:
    """A cached semaphore keeps its first size, so the new limit would not apply."""

    limiter = MemoryRateLimiter()
    async with limiter.concurrency_permit("k", maximum=1, timeout_seconds=0.1):
        pass
    with pytest.raises(ValueError, match="changed from 1 to 5"):
        async with limiter.concurrency_permit("k", maximum=5, timeout_seconds=0.1):
            pass


async def test_evict_inactive_drops_idle_semaphores_but_keeps_held_ones() -> None:
    limiter = MemoryRateLimiter()
    async with limiter.concurrency_permit("idle", maximum=1, timeout_seconds=0.1):
        pass

    async with limiter.concurrency_permit("held", maximum=1, timeout_seconds=0.1):
        await limiter.evict_inactive(older_than_seconds=0.0)
        # Dropping a semaphore that is still held would hand the next caller a
        # fresh one and let concurrency exceed the maximum.
        assert "held" in limiter._semaphores
        assert "idle" not in limiter._semaphores
