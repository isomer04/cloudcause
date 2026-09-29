"""Layer 4: bounded, jittered retries for provider throttling and transient errors.

Generic on purpose -- it knows nothing about OpenAI or Gemini. Provider-specific
error classification (which exceptions are retryable, and whether one carries a
``Retry-After``) lives in ``worker_core`` next to the SDK calls, since it needs
lazy SDK imports this package has no reason to depend on.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryDecision:
    """What ``classify`` decided about one failed attempt."""

    retryable: bool
    #: An explicit provider-supplied delay (e.g. Retry-After), when available.
    delay_seconds: float | None = None


async def run_with_retries(
    factory: Callable[[], Awaitable[T]],
    *,
    classify: Callable[[BaseException], RetryDecision],
    attempts: int,
    base_seconds: float,
    max_seconds: float,
    deadline_seconds: float,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
    rng: random.Random | None = None,
) -> T:
    """Call ``factory()`` up to ``attempts`` times within ``deadline_seconds`` total.

    ``attempts`` is total attempts, not retries: a value of 3 means at most
    two retries after the first try. ``factory`` is invoked fresh every
    attempt -- for CloudCause's live agents that means a brand-new agent,
    ``NativeToolset``, and MCP session each time, since each
    ``run_*_investigation`` function already builds all of that internally.
    A non-retryable error, or an attempt with no time left in the deadline,
    propagates immediately.
    """

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    rng = rng or random.Random()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    for attempt in range(1, attempts + 1):
        try:
            return await factory()
        except Exception as error:  # noqa: BLE001 - classified immediately below
            decision = classify(error)
            if not decision.retryable or attempt >= attempts:
                raise
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise
            if decision.delay_seconds is not None:
                delay = min(decision.delay_seconds, max_seconds, remaining)
            else:
                exponential = base_seconds * (2 ** (attempt - 1))
                delay = min(rng.uniform(0, exponential), max_seconds, remaining)
            if on_retry is not None:
                on_retry(attempt, delay, error)
            await asyncio.sleep(delay)
            # The delay can consume the whole remaining budget, and starting a
            # fresh attempt then would run well past the deadline the caller
            # asked us to stay inside.
            if loop.time() >= deadline:
                raise
    raise AssertionError("unreachable: loop always returns or raises")
