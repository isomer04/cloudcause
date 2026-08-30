"""Layer 3: outbound per-provider/model quota around every AI model call.

One permit covers both axes a provider can throttle on: concurrent in-flight
requests and requests per minute. Call sites acquire it around the single
model-call boundary in each live agent (never around a whole agent run, which
can issue many underlying SDK requests internally).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cloudcause_contracts import Settings

from .errors import RateLimitExceeded, fail_closed
from .keys import provider_model_key
from .protocol import RateLimiter

_BACKEND_UNAVAILABLE_DETAIL = "The outbound rate limiter backend is unavailable."

#: Floor for one requests-per-minute wait. A near-full bucket reports a tiny
#: deficit (a fraction of a token / refill rate), and sleeping that literally
#: would spin the loop against the backend until the deadline.
_MIN_RETRY_SLEEP_SECONDS = 0.01

#: Bucket family -> (Settings attribute for max concurrency, for requests/minute).
_FAMILY_LIMITS = {
    "openai": ("openai_max_concurrency", "openai_requests_per_minute"),
    "gemini": ("gemini_max_concurrency", "gemini_requests_per_minute"),
    "gemini-summary": ("gemini_summary_max_concurrency", "gemini_summary_requests_per_minute"),
}


class AIRequestGovernor:
    def __init__(
        self,
        limiter: RateLimiter,
        settings: Settings,
        *,
        permit_timeout_seconds: float | None = None,
    ) -> None:
        self._limiter = limiter
        self._settings = settings
        self._permit_timeout_seconds = permit_timeout_seconds or settings.max_agent_seconds

    @asynccontextmanager
    async def permit(self, family: str, model: str) -> AsyncIterator[None]:
        """Acquire a concurrency slot and a requests-per-minute token for one call.

        Raises ``RateLimitExceeded`` if either is unavailable within the
        configured timeout. Cancellation-safe: both are released on any exit.
        """

        concurrency_attr, rpm_attr = _FAMILY_LIMITS[family]
        maximum = getattr(self._settings, concurrency_attr)
        requests_per_minute = getattr(self._settings, rpm_attr)
        key = provider_model_key(family, model)
        # The requests-per-minute wait happens *before* a concurrency slot is
        # taken: a call sleeping out its token deficit holds no slot, so it
        # cannot block a call that already has budget from making progress.
        await self._acquire_rate(key, requests_per_minute)
        async with self._acquire_concurrency(key, maximum):
            yield

    @asynccontextmanager
    async def _acquire_concurrency(self, key: str, maximum: int) -> AsyncIterator[None]:
        """Acquire the concurrency slot, failing closed on a backend error.

        Only the acquisition itself is guarded: an exception raised by the
        caller's model call, inside the ``yield``, must propagate unchanged
        for the retry classifier to see -- not get relabeled as a limiter
        failure.
        """

        permit_cm = self._limiter.concurrency_permit(
            key, maximum=maximum, timeout_seconds=self._permit_timeout_seconds
        )
        # Paid outbound calls fail closed on a limiter-backend failure (e.g.
        # Redis unreachable): the caller falls back to the deterministic
        # playbooks for this provider rather than making an ungoverned call.
        await fail_closed(f"{key}:unavailable", _BACKEND_UNAVAILABLE_DETAIL, 5.0, permit_cm.__aenter__)
        try:
            yield
        finally:
            await permit_cm.__aexit__(None, None, None)

    async def _acquire_rate(self, key: str, requests_per_minute: int) -> None:
        rate_key = f"{key}:rpm"
        refill_per_second = requests_per_minute / 60.0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._permit_timeout_seconds
        while True:
            result = await fail_closed(
                f"{rate_key}:unavailable",
                _BACKEND_UNAVAILABLE_DETAIL,
                5.0,
                lambda: self._limiter.acquire_tokens(
                    rate_key, capacity=requests_per_minute, refill_per_second=refill_per_second
                ),
            )
            if result.allowed:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RateLimitExceeded(
                    scope=rate_key,
                    detail=(
                        f"no request-per-minute budget for {rate_key!r} became available "
                        f"within {self._permit_timeout_seconds:g}s"
                    ),
                    retry_after_seconds=result.retry_after_seconds,
                )
            delay = max(result.retry_after_seconds, _MIN_RETRY_SLEEP_SECONDS)
            await asyncio.sleep(min(delay, remaining))
