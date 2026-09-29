"""Single-process rate limiter: correct for one gateway or worker replica.

Distributed deployments need ``RedisRateLimiter`` instead (see
``redis_backend.py``); this backend has no way to see another process's
buckets or leases.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .clock import Clock, MonotonicClock
from .errors import RateLimitExceeded
from .protocol import TokenBucketResult


@dataclass
class _TokenBucket:
    tokens: float
    last_refill: float
    last_access: float


class MemoryRateLimiter:
    """Per-bucket ``asyncio.Lock`` and monotonic-clock token bucket math."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or MonotonicClock()
        self._buckets: dict[str, _TokenBucket] = {}
        self._bucket_locks: dict[str, asyncio.Lock] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._maximums: dict[str, int] = {}
        self._active: dict[str, int] = {}
        self._creation_lock = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._bucket_locks.get(key)
        if lock is not None:
            return lock
        async with self._creation_lock:
            return self._bucket_locks.setdefault(key, asyncio.Lock())

    def _check_maximum(self, key: str, maximum: int) -> None:
        """A cached semaphore keeps its original size, so a changed limit is a lie.

        Silently honouring the first caller's value would leave the configured
        limit unenforced for the rest of the process's life -- for a rate
        limiter, failing loudly is the safer of the two.
        """

        recorded = self._maximums.get(key)
        if recorded is not None and recorded != maximum:
            raise ValueError(
                f"concurrency maximum for {key!r} changed from {recorded} to {maximum}; "
                "one key must keep a single limit for the life of the limiter"
            )

    async def _semaphore_for(self, key: str, maximum: int) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(key)
        if semaphore is not None:
            self._check_maximum(key, maximum)
            return semaphore
        async with self._creation_lock:
            semaphore = self._semaphores.get(key)
            if semaphore is None:
                semaphore = asyncio.Semaphore(maximum)
                self._semaphores[key] = semaphore
                self._maximums[key] = maximum
                self._active[key] = 0
            else:
                self._check_maximum(key, maximum)
            return semaphore

    async def acquire_tokens(
        self,
        key: str,
        *,
        capacity: float,
        refill_per_second: float,
        cost: float = 1.0,
    ) -> TokenBucketResult:
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("capacity and refill_per_second must be positive")
        lock = await self._lock_for(key)
        async with lock:
            now = self._clock.monotonic()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _TokenBucket(tokens=capacity, last_refill=now, last_access=now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.last_refill)
                bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_per_second)
                bucket.last_refill = now
            bucket.last_access = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return TokenBucketResult(allowed=True, remaining=bucket.tokens, retry_after_seconds=0.0)
            deficit = cost - bucket.tokens
            return TokenBucketResult(
                allowed=False,
                remaining=bucket.tokens,
                retry_after_seconds=deficit / refill_per_second,
            )

    @asynccontextmanager
    async def concurrency_permit(
        self,
        key: str,
        *,
        maximum: int,
        timeout_seconds: float,
    ) -> AsyncIterator[None]:
        if maximum < 1:
            raise ValueError("maximum concurrency must be positive")
        semaphore = await self._semaphore_for(key, maximum)
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=timeout_seconds)
        except TimeoutError as error:
            raise RateLimitExceeded(
                scope=key,
                detail=f"no concurrency permit for {key!r} became available within {timeout_seconds:g}s",
                retry_after_seconds=timeout_seconds,
            ) from error
        self._active[key] = self._active.get(key, 0) + 1
        try:
            yield
        finally:
            self._active[key] = max(0, self._active.get(key, 1) - 1)
            semaphore.release()

    async def evict_inactive(self, older_than_seconds: float) -> int:
        now = self._clock.monotonic()
        evicted = 0
        for key in list(self._buckets):
            lock = self._bucket_locks.get(key)
            if lock is not None and lock.locked():
                continue
            bucket = self._buckets.get(key)
            if bucket is not None and now - bucket.last_access >= older_than_seconds:
                del self._buckets[key]
                self._bucket_locks.pop(key, None)
                evicted += 1
        # Concurrency keys are namespaced separately from bucket keys, so they
        # have no bucket to age out and would otherwise be retained for the
        # life of the process. A fully-released semaphore holds no state worth
        # keeping -- the next caller just builds a fresh one the same size --
        # while one that is held, or has waiters, is left alone. Not counted in
        # `evicted`, which reports token buckets.
        for semaphore_key, semaphore in list(self._semaphores.items()):
            if self._active.get(semaphore_key, 0) == 0 and not semaphore.locked():
                del self._semaphores[semaphore_key]
                self._maximums.pop(semaphore_key, None)
                self._active.pop(semaphore_key, None)
        return evicted

    async def ping(self) -> bool:
        return True  # in-process: reachable by definition
