"""Build the configured ``RateLimiter`` backend from ``Settings``."""

from __future__ import annotations

from cloudcause_contracts import Settings

from .memory import MemoryRateLimiter
from .protocol import RateLimiter
from .redis_backend import RedisRateLimiter


def build_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.rate_limit_backend == "redis":
        if not settings.rate_limit_redis_url:
            raise ValueError(
                "CLOUDCAUSE_RATE_LIMIT_BACKEND=redis requires CLOUDCAUSE_RATE_LIMIT_REDIS_URL"
            )
        return RedisRateLimiter(settings.rate_limit_redis_url)
    return MemoryRateLimiter()
