"""Distributed rate limiter shared across every gateway and worker replica.

Required before running more than one live-capable process: ``MemoryRateLimiter``
only sees calls made in its own process. Token buckets and concurrency leases
are each updated atomically through a Lua script so two replicas racing on the
same key cannot both observe capacity and both proceed.

Bucket state uses wall-clock time (``time.time()``), not the monotonic clock
used by ``MemoryRateLimiter``: it must be comparable across process restarts
and separate machines, where a monotonic clock has no shared origin. Minor
clock skew between replicas is an accepted imprecision for a rate limiter,
not a security boundary.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from .errors import RateLimitExceeded
from .protocol import TokenBucketResult

_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1])
local last_refill = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  last_refill = now
end
local elapsed = now - last_refill
if elapsed < 0 then
  elapsed = 0
end
tokens = math.min(capacity, tokens + elapsed * refill_per_second)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
redis.call('EXPIRE', key, ttl)
return {allowed, tostring(tokens)}
"""

_ACQUIRE_LEASE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local maximum = tonumber(ARGV[2])
local lease_id = ARGV[3]
local lease_ttl = tonumber(ARGV[4])
local expire_seconds = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local count = redis.call('ZCARD', key)
if count < maximum then
  redis.call('ZADD', key, now + lease_ttl, lease_id)
  redis.call('EXPIRE', key, expire_seconds)
  return 1
end
return 0
"""

# Extends this process's own lease only: the ZADD re-scores an existing member,
# and the guard means an already-expired (swept) lease is never resurrected.
_RENEW_LEASE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local lease_id = ARGV[2]
local lease_ttl = tonumber(ARGV[3])
local expire_seconds = tonumber(ARGV[4])

if redis.call('ZSCORE', key, lease_id) == false then
  return 0
end
redis.call('ZADD', key, now + lease_ttl, lease_id)
redis.call('EXPIRE', key, expire_seconds)
return 1
"""

# A lease id is a uuid4 hex, so ZREM can only ever remove the exact lease this
# process was granted -- never another process's slot.
_RELEASE_LEASE_SCRIPT = """
redis.call('ZREM', KEYS[1], ARGV[1])
return 1
"""

_MIN_POLL_INTERVAL = 0.02
_MAX_POLL_INTERVAL = 0.25
#: Seconds added to a lease TTL for the key's own expiry, so the sorted set
#: outlives the last lease recorded in it.
_LEASE_KEY_TTL_BUFFER_SECONDS = 5
#: Fraction of the lease TTL between heartbeat renewals. A third leaves room
#: for two missed renewals before a live call's lease could be swept.
_RENEW_INTERVAL_RATIO = 1 / 3


class RedisRateLimiter:
    """Atomic, namespaced token buckets and concurrency leases on Redis."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "cloudcause",
        lease_ttl_seconds: float = 120.0,
        client: Redis | None = None,
    ) -> None:
        self._namespace = namespace
        self._lease_ttl_seconds = lease_ttl_seconds
        self._redis = client or Redis.from_url(redis_url, decode_responses=True)
        # EXPIRE takes whole seconds, so a fractional TTL has to round up here
        # rather than reach Redis as "5.5" and be rejected.
        self._lease_key_ttl_seconds = (
            math.ceil(lease_ttl_seconds) + _LEASE_KEY_TTL_BUFFER_SECONDS
        )
        self._token_bucket = self._redis.register_script(_TOKEN_BUCKET_SCRIPT)
        self._acquire_lease = self._redis.register_script(_ACQUIRE_LEASE_SCRIPT)
        self._renew_lease = self._redis.register_script(_RENEW_LEASE_SCRIPT)
        self._release_lease = self._redis.register_script(_RELEASE_LEASE_SCRIPT)

    def _namespaced(self, prefix: str, key: str) -> str:
        return f"{self._namespace}:{prefix}:{key}"

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
        now = time.time()
        # Generous enough that an idle bucket expires rather than lingering
        # forever, but never shorter than the time to refill from empty.
        ttl = max(60, int(capacity / refill_per_second) * 4)
        allowed, remaining_raw = await self._token_bucket(
            keys=[self._namespaced("tokens", key)],
            args=[capacity, refill_per_second, cost, now, ttl],
        )
        remaining = float(remaining_raw)
        if int(allowed):
            return TokenBucketResult(allowed=True, remaining=remaining, retry_after_seconds=0.0)
        deficit = cost - remaining
        return TokenBucketResult(
            allowed=False,
            remaining=remaining,
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
        lease_key = self._namespaced("concurrency", key)
        lease_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        poll_interval = _MIN_POLL_INTERVAL
        acquired = False
        while True:
            granted = await self._acquire_lease(
                keys=[lease_key],
                args=[
                    time.time(),
                    maximum,
                    lease_id,
                    self._lease_ttl_seconds,
                    self._lease_key_ttl_seconds,
                ],
            )
            if int(granted):
                acquired = True
                break
            if loop.time() >= deadline:
                break
            await asyncio.sleep(min(poll_interval, max(0.0, deadline - loop.time())))
            poll_interval = min(poll_interval * 2, _MAX_POLL_INTERVAL)
        if not acquired:
            raise RateLimitExceeded(
                scope=key,
                detail=f"no distributed concurrency lease for {key!r} became available within {timeout_seconds:g}s",
                retry_after_seconds=timeout_seconds,
            )
        # A call that outlives the lease TTL would otherwise have its lease
        # swept by the next acquirer's ZREMRANGEBYSCORE while it is still
        # running, letting the cluster exceed `maximum`. The heartbeat keeps
        # this process's own lease scored into the future for as long as the
        # body runs.
        heartbeat = asyncio.create_task(self._renew_until_cancelled(lease_key, lease_id))
        try:
            yield
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            try:
                await self._release_lease(keys=[lease_key], args=[lease_id])
            except Exception:
                # The lease expires on its own; failing to release it must not
                # replace whatever the body was already raising.
                pass

    async def _renew_until_cancelled(self, lease_key: str, lease_id: str) -> None:
        interval = max(_MIN_POLL_INTERVAL, self._lease_ttl_seconds * _RENEW_INTERVAL_RATIO)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._renew_lease(
                    keys=[lease_key],
                    args=[
                        time.time(),
                        lease_id,
                        self._lease_ttl_seconds,
                        self._lease_key_ttl_seconds,
                    ],
                )
            except Exception:
                # A transient Redis failure must not take down the call this
                # lease is protecting; the next tick tries again.
                continue

    async def evict_inactive(self, older_than_seconds: float) -> int:
        # Redis TTLs already expire idle buckets and leases server-side.
        return 0

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._redis.aclose()
