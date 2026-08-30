"""The structured error every rate-limit boundary raises.

One exception type covers gateway admission, outbound provider/model permits,
and Redis concurrency leases so callers never special-case which layer denied
a request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class RateLimitExceeded(RuntimeError):
    """A bucket, concurrency permit, or lease could not be acquired in time."""

    def __init__(
        self,
        scope: str,
        detail: str,
        *,
        retry_after_seconds: float,
        retryable: bool = True,
    ) -> None:
        super().__init__(detail)
        self.scope = scope
        self.detail = detail
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        self.retryable = retryable

    def to_response_body(self) -> dict[str, object]:
        return {
            "error": "rate_limit_exceeded",
            "detail": self.detail,
            "retryable": self.retryable,
            "retry_after_seconds": round(self.retry_after_seconds, 3),
            "scope": self.scope,
        }


async def fail_closed(scope: str, detail: str, retry_after_seconds: float, call: Callable[[], Awaitable[T]]) -> T:
    """Run ``call()``, translating any backend failure into ``RateLimitExceeded``.

    Shared by admission and governor checks: a limiter backend error (e.g.
    Redis unreachable) must deny the request, not surface as a bare 500 or
    silently proceed unbounded.
    """

    try:
        return await call()
    except RateLimitExceeded:
        raise
    except Exception as error:
        raise RateLimitExceeded(scope=scope, detail=detail, retry_after_seconds=retry_after_seconds) from error
