"""In-memory investigation job store with progress streaming.

This is the interface the gateway codes against. ``history.SqlJobStore`` subclasses
it to add PostgreSQL or SQLite persistence without changing a single endpoint:
live jobs keep their queue here, durable copies are written through listeners.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime

from cloudcause_contracts import (
    InvestigationRequest,
    InvestigationState,
    ProgressEvent,
    Provider,
    ProviderStatus,
    utcnow,
)

#: Called after every state change. ``None`` means "state only, no new event".
JobListener = Callable[["InvestigationJob", "ProgressEvent | None"], None]


class InvestigationJob:
    def __init__(self, investigation_id: str, request: InvestigationRequest) -> None:
        self.state = InvestigationState(
            investigation_id=investigation_id,
            status="queued",
            question=request.question,
            request=request,
        )
        self.events: list[ProgressEvent] = []
        self.listeners: list[JobListener] = []
        self.is_restored = False
        self._queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        self._sequence = 0

    @classmethod
    def restored(cls, state: InvestigationState, events: list[ProgressEvent]) -> InvestigationJob:
        """Rebuild a finished job from storage so history reads like a live one.

        Nothing more will arrive, so the stream replays the stored events and
        closes instead of waiting on a queue no producer owns.
        """

        job = cls(state.investigation_id, state.request)
        job.state = state
        job.events = list(events)
        job.is_restored = True
        job._sequence = max((event.sequence for event in events), default=0)
        job._queue.put_nowait(None)
        return job

    @property
    def investigation_id(self) -> str:
        return self.state.investigation_id

    def notify(self, event: ProgressEvent | None = None) -> None:
        for listener in list(self.listeners):
            listener(self, event)

    def emit(
        self,
        stage: str,
        message: str = "",
        *,
        status: str = "progress",
        provider: Provider | None = None,
        **data: object,
    ) -> ProgressEvent:
        self._sequence += 1
        event = ProgressEvent(
            investigation_id=self.investigation_id,
            sequence=self._sequence,
            stage=stage,
            status=status,  # type: ignore[arg-type]
            provider=provider,
            message=message,
            data=dict(data),
        )
        self.events.append(event)
        self.state.stage = stage
        self.state.message = message
        self.state.updated_at = utcnow()
        self._queue.put_nowait(event)
        self.notify(event)
        return event

    def set_provider_statuses(self, statuses: list[ProviderStatus]) -> None:
        self.state.provider_statuses = statuses
        self.state.updated_at = utcnow()
        self.notify()

    def mark_running(self) -> None:
        self.state.status = "running"
        self.state.updated_at = utcnow()
        self.notify()

    def finish(self, *, error: str | None = None) -> None:
        self.state.status = "failed" if error else "completed"
        self.state.error = error
        self.state.updated_at = utcnow()
        self._queue.put_nowait(None)
        self.notify()

    async def stream(self, replay: bool = True) -> AsyncIterator[ProgressEvent]:
        if replay:
            for event in list(self.events):
                yield event
        if self.state.status in ("completed", "failed"):
            return
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, InvestigationJob] = {}

    def create(self, investigation_id: str, request: InvestigationRequest) -> InvestigationJob:
        job = InvestigationJob(investigation_id, request)
        self._jobs[investigation_id] = job
        return job

    def get(self, investigation_id: str) -> InvestigationJob | None:
        return self._jobs.get(investigation_id)

    def list(self) -> list[InvestigationState]:
        return sorted(
            (job.state for job in self._jobs.values()),
            key=lambda state: state.created_at,
            reverse=True,
        )

    def prune(self, keep: int | None = 50) -> None:
        limit = 50 if keep is None else keep
        if len(self._jobs) <= limit:
            return
        ordered: list[tuple[datetime, str]] = sorted(
            ((job.state.created_at, key) for key, job in self._jobs.items())
        )
        for _, key in ordered[: len(self._jobs) - limit]:
            self._jobs.pop(key, None)

    def describe(self) -> dict[str, object]:
        """What ``/health`` reports about where investigation history lives."""

        return {"backend": "memory", "durable": False, "retention": 50}
