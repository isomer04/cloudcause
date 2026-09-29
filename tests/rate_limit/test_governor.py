"""Layer 3 outbound governor: shared provider/model buckets and fail-closed behavior."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from cloudcause_contracts import Settings
from cloudcause_rate_limit import AIRequestGovernor, MemoryRateLimiter, RateLimitExceeded


def _settings(**overrides: object) -> Settings:
    return Settings.from_env({}).with_overrides(**overrides)


async def test_two_callers_on_the_same_family_and_model_share_one_bucket() -> None:
    """Mirrors AWS and Azure both drawing from the openai bucket."""

    limiter = MemoryRateLimiter()
    governor = AIRequestGovernor(
        limiter, _settings(openai_max_concurrency=1, openai_requests_per_minute=60)
    )
    active = 0
    peak = 0

    async def call() -> None:
        nonlocal active, peak
        async with governor.permit("openai", "gpt-4.1-mini"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(call(), call(), call())
    assert peak == 1  # never more than the configured concurrency, shared across callers


async def test_different_families_do_not_share_capacity() -> None:
    limiter = MemoryRateLimiter()
    governor = AIRequestGovernor(
        limiter,
        _settings(
            openai_max_concurrency=1,
            openai_requests_per_minute=60,
            gemini_max_concurrency=1,
            gemini_requests_per_minute=60,
        ),
    )
    active: set[str] = set()
    overlapped = False

    async def call(family: str, model: str) -> None:
        nonlocal overlapped
        async with governor.permit(family, model):
            active.add(family)
            # Both families hold a permit at the same instant only if neither
            # had to wait on the other's bucket. Asserting each one merely ran
            # would also pass if they were serialized.
            await asyncio.sleep(0.02)
            overlapped = overlapped or len(active) == 2
            active.discard(family)

    await asyncio.gather(call("openai", "gpt-4.1-mini"), call("gemini", "gemini-2.5-flash"))
    assert overlapped


async def test_rpm_bucket_denies_within_the_bounded_wait_timeout() -> None:
    limiter = MemoryRateLimiter()
    governor = AIRequestGovernor(
        limiter,
        _settings(openai_max_concurrency=5, openai_requests_per_minute=1),
        permit_timeout_seconds=0.05,
    )

    async with governor.permit("openai", "gpt-4.1-mini"):
        pass

    with pytest.raises(RateLimitExceeded):
        async with governor.permit("openai", "gpt-4.1-mini"):
            pass


async def test_body_call_exception_is_not_relabeled_as_limiter_unavailable() -> None:
    """A model-call failure inside the permit block must propagate unchanged."""

    limiter = MemoryRateLimiter()
    governor = AIRequestGovernor(
        limiter, _settings(openai_max_concurrency=1, openai_requests_per_minute=60)
    )

    class FakeProviderError(RuntimeError):
        pass

    with pytest.raises(FakeProviderError):
        async with governor.permit("openai", "gpt-4.1-mini"):
            raise FakeProviderError("upstream 500")


async def test_backend_failure_fails_closed_as_rate_limit_exceeded() -> None:
    class BrokenLimiter:
        async def acquire_tokens(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise ConnectionError("redis unreachable")

        @asynccontextmanager
        async def concurrency_permit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise ConnectionError("redis unreachable")
            yield  # pragma: no cover - unreachable, makes this a generator

        async def evict_inactive(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return 0

    governor = AIRequestGovernor(
        BrokenLimiter(), _settings(openai_max_concurrency=1, openai_requests_per_minute=60)
    )

    with pytest.raises(RateLimitExceeded):
        async with governor.permit("openai", "gpt-4.1-mini"):
            pass  # pragma: no cover - never reached
