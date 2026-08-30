"""Local, cancellation-safe limits for live-agent work.

These limits deliberately live below the HTTP surface. They are a bounded
single-process safeguard; distributed deployments still need a shared limiter.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


class AgentCallLimitExceeded(RuntimeError):
    """Raised before a live framework or native-tool call exceeds its budget."""


class LiveCapacityTimeoutError(RuntimeError):
    """Raised when a live investigation cannot obtain a local capacity slot."""


@dataclass
class AgentCallBudget:
    """Per-investigation budget shared by known live execution boundaries."""

    maximum: int
    used: int = 0

    def reserve(self, boundary: str) -> None:
        if self.used >= self.maximum:
            raise AgentCallLimitExceeded(
                f"live agent call budget exhausted at {boundary} ({self.used}/{self.maximum})"
            )
        self.used += 1


_current_budget: ContextVar[AgentCallBudget | None] = ContextVar("cloudcause_agent_call_budget", default=None)


def bind_agent_call_budget(budget: AgentCallBudget) -> Token[AgentCallBudget | None]:
    """Bind one budget to all child tasks of an investigation."""

    return _current_budget.set(budget)


def reset_agent_call_budget(token: Token[AgentCallBudget | None]) -> None:
    _current_budget.reset(token)


def current_agent_call_budget() -> AgentCallBudget | None:
    return _current_budget.get()


class LiveInvestigationCapacity:
    """Bound concurrent live investigations while retaining overflow jobs queued."""

    def __init__(self, maximum: int, queue_timeout_seconds: float) -> None:
        if maximum < 1:
            raise ValueError("maximum live investigations must be positive")
        if queue_timeout_seconds <= 0:
            raise ValueError("live investigation queue timeout must be positive")
        self.maximum = maximum
        self.queue_timeout_seconds = queue_timeout_seconds
        self._semaphore = asyncio.BoundedSemaphore(maximum)
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def reserve(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.queue_timeout_seconds)
        except TimeoutError as error:
            raise LiveCapacityTimeoutError(
                f"live AI capacity was unavailable for {self.queue_timeout_seconds:g}s"
            ) from error
        self._active += 1
        try:
            yield
        finally:
            self._active -= 1
            self._semaphore.release()
