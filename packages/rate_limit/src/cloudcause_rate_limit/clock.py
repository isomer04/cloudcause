"""Injectable time source so token-bucket tests never sleep for real."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...


class MonotonicClock:
    """The real clock, used everywhere outside tests."""

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """A settable clock for deterministic token-bucket and eviction tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot move a clock backwards")
        self._now += seconds
