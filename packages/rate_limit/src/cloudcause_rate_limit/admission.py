"""Layer 1: gateway admission control for POST /investigations.

Applies only to requests whose resolved ``agent_mode`` is ``live``. Two token
buckets are checked in order -- per-client, then deployment-global -- so a
single noisy client cannot exhaust capacity other clients would otherwise get,
while the global bucket still caps aggregate live-investigation starts.
"""

from __future__ import annotations

from .errors import RateLimitExceeded, fail_closed
from .keys import ADMISSION_GLOBAL_KEY, hash_client_key
from .protocol import RateLimiter

_BACKEND_UNAVAILABLE_DETAIL = "The rate limiter backend is unavailable. Try again shortly."


class AdmissionGuard:
    def __init__(
        self,
        limiter: RateLimiter,
        *,
        enabled: bool,
        client_per_hour: int,
        client_burst: int,
        global_per_minute: int,
        id_hash_salt: str,
    ) -> None:
        self._limiter = limiter
        self.enabled = enabled
        # Burst is allowance *above* the steady rate, not a cap below it: a
        # bucket sized to exactly the per-hour rate could never actually
        # deliver a burst, since refilling to capacity would take the full hour.
        self._client_capacity = client_per_hour + client_burst
        self._client_refill = client_per_hour / 3600.0
        self._global_capacity = global_per_minute
        self._global_refill = global_per_minute / 60.0
        self._salt = id_hash_salt

    async def check(self, peer_ip: str) -> None:
        """Raise ``RateLimitExceeded`` if either bucket is exhausted.

        A backend failure (e.g. Redis unreachable) also raises
        ``RateLimitExceeded`` rather than propagating as a 500: deployment-wide
        admission must fail closed, not fall open to an unbounded path.
        """

        if not self.enabled:
            return
        client_key = hash_client_key(peer_ip, self._salt)
        client_result = await fail_closed(
            "rate_limiter_unavailable",
            _BACKEND_UNAVAILABLE_DETAIL,
            5.0,
            lambda: self._limiter.acquire_tokens(
                client_key,
                capacity=self._client_capacity,
                refill_per_second=self._client_refill,
            ),
        )
        if not client_result.allowed:
            raise RateLimitExceeded(
                scope="client_live_investigation",
                detail=(
                    "Too many live investigations were started. "
                    f"Try again in {client_result.retry_after_seconds:.0f} seconds."
                ),
                retry_after_seconds=client_result.retry_after_seconds,
            )
        global_result = await fail_closed(
            "rate_limiter_unavailable",
            _BACKEND_UNAVAILABLE_DETAIL,
            5.0,
            lambda: self._limiter.acquire_tokens(
                ADMISSION_GLOBAL_KEY,
                capacity=self._global_capacity,
                refill_per_second=self._global_refill,
            ),
        )
        if not global_result.allowed:
            raise RateLimitExceeded(
                scope="global_live_investigation",
                detail=(
                    "This deployment is at its live-investigation capacity. "
                    f"Try again in {global_result.retry_after_seconds:.0f} seconds."
                ),
                retry_after_seconds=global_result.retry_after_seconds,
            )
