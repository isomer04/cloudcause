"""The storage-agnostic contract every rate-limit backend implements.

``MemoryRateLimiter`` and ``RedisRateLimiter`` both satisfy this protocol, so
the gateway admission guard and the outbound ``AIRequestGovernor`` are written
once against ``RateLimiter`` and work unmodified against either backend.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TokenBucketResult:
    """The outcome of one token-bucket acquisition attempt."""

    allowed: bool
    remaining: float
    retry_after_seconds: float


@runtime_checkable
class RateLimiter(Protocol):
    async def acquire_tokens(
        self,
        key: str,
        *,
        capacity: float,
        refill_per_second: float,
        cost: float = 1.0,
    ) -> TokenBucketResult:
        """Spend ``cost`` tokens from ``key``'s bucket, refilling it first.

        Never raises for a denied request; ``TokenBucketResult.allowed`` is
        the signal. Capacity and refill rate are supplied per call so one
        limiter instance can back buckets with different limits.
        """

    def concurrency_permit(
        self,
        key: str,
        *,
        maximum: int,
        timeout_seconds: float,
    ) -> AbstractAsyncContextManager[None]:
        """Hold one of ``maximum`` concurrent slots for ``key``.

        Raises ``cloudcause_rate_limit.errors.RateLimitExceeded`` if no slot
        opens within ``timeout_seconds``. Always releases the slot on normal
        exit, exception, or cancellation.
        """

    async def evict_inactive(self, older_than_seconds: float) -> int:
        """Drop buckets untouched for longer than ``older_than_seconds``.

        Returns the number of buckets evicted. Backends with server-side
        expiry (Redis) may implement this as a no-op returning 0.
        """

    async def ping(self) -> bool:
        """Whether the backend is currently reachable, for ``/health``.

        Never raises: a connectivity failure is reported as ``False``, not
        propagated, since a broken health check must not itself 500.
        """


__all__ = ["RateLimiter", "TokenBucketResult"]
