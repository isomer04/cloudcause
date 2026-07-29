"""Durable investigation history behind the in-memory ``JobStore`` interface.

Live jobs stay in memory because SSE streaming needs a queue, and every state
change is written to SQL so history, reports, and progress survive a gateway
restart.

Two backends, one schema:

* ``sqlite``   - stdlib :mod:`sqlite3`, no extra dependency, used by the offline
  suite and by anyone running the gateway without Docker.
* ``postgres`` - :mod:`psycopg` against the ``postgres`` service in
  ``infra/docker/docker-compose.yml``. The driver is imported lazily, so nothing
  offline depends on it.

Account and subscription identifiers are hashed on the way in, per the security
posture in ``docs/architecture.md``: the durable copy is deliberately not the raw
request. Without ``CLOUDCAUSE_ID_HASH_SALT`` the digest is a stable pseudonym,
not a secret.

Persistence never fails an investigation. A write error degrades the store to
memory-only and is reported by :meth:`SqlJobStore.describe`, which the gateway
exposes on ``/health``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from cloudcause_contracts import (
    InvestigationRequest,
    InvestigationState,
    ProgressEvent,
    Settings,
)
from cloudcause_datasets import (
    Database,
    DatabaseTarget,
    DatabaseUnavailableError,
    apply_migrations,
    hash_identifier,
    parse_database_url,
)
from cloudcause_datasets.sql import SCHEMA_TABLE

from .jobs import InvestigationJob, JobStore

logger = logging.getLogger("cloudcause.history")

HistoryBackend = Literal["sqlite", "postgres"]

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

__all__ = [
    "MIGRATIONS_DIR",
    "SCHEMA_TABLE",
    "DatabaseTarget",
    "DatabaseUnavailableError",
    "InvestigationHistory",
    "SqlJobStore",
    "apply_migrations",
    "build_job_store",
    "hash_identifier",
    "parse_database_url",
    "redact_request",
    "redact_state",
]


def redact_request(request: InvestigationRequest, salt: str = "") -> InvestigationRequest:
    if not request.account_ids:
        return request
    return request.model_copy(
        update={"account_ids": [hash_identifier(value, salt) for value in request.account_ids]}
    )


def redact_state(state: InvestigationState, salt: str = "") -> InvestigationState:
    """Copy ``state`` with every stored account identifier hashed."""

    stored = state.model_copy(deep=True)
    stored.request = redact_request(stored.request, salt)
    if stored.report is not None:
        stored.report.request = redact_request(stored.report.request, salt)
    return stored


class InvestigationHistory:
    """Read/write investigation history. The only SQL in CloudCause lives here."""

    def __init__(
        self, target: DatabaseTarget, *, hash_salt: str = "", connect_timeout: float = 5.0
    ) -> None:
        self.target = target
        self.hash_salt = hash_salt
        self._database = Database(target, connect_timeout)
        self.applied_migrations = apply_migrations(self._database, MIGRATIONS_DIR)

    @property
    def backend(self) -> HistoryBackend:
        return self.target.backend

    def save(self, state: InvestigationState) -> None:
        stored = redact_state(state, self.hash_salt)
        self._database.execute(
            "INSERT INTO investigations "
            "(investigation_id, status, stage, question, scenario_id, providers, data_mode, "
            " agent_mode, has_report, error, created_at, updated_at, state_json, dataset_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (investigation_id) DO UPDATE SET "
            "status = excluded.status, stage = excluded.stage, has_report = excluded.has_report, "
            "error = excluded.error, updated_at = excluded.updated_at, state_json = excluded.state_json",
            (
                stored.investigation_id,
                stored.status,
                stored.stage,
                stored.question,
                stored.request.scenario_id,
                ",".join(stored.request.providers),
                stored.report.data_mode if stored.report else "fixtures",
                stored.report.agent_mode if stored.report else "stub",
                1 if stored.report else 0,
                stored.error,
                stored.created_at.isoformat(),
                stored.updated_at.isoformat(),
                stored.model_dump_json(),
                stored.request.dataset_id,
            ),
        )

    def save_event(self, event: ProgressEvent) -> None:
        self._database.execute(
            "INSERT INTO investigation_events "
            "(investigation_id, sequence, at, stage, status, provider, message, event_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (investigation_id, sequence) DO NOTHING",
            (
                event.investigation_id,
                event.sequence,
                event.at.isoformat(),
                event.stage,
                event.status,
                event.provider,
                event.message,
                event.model_dump_json(),
            ),
        )

    def load(self, investigation_id: str) -> InvestigationState | None:
        rows = self._database.query(
            "SELECT state_json FROM investigations WHERE investigation_id = ?",
            (investigation_id,),
        )
        if not rows:
            return None
        return InvestigationState.model_validate_json(rows[0][0])

    def events(self, investigation_id: str) -> list[ProgressEvent]:
        rows = self._database.query(
            "SELECT event_json FROM investigation_events WHERE investigation_id = ? ORDER BY sequence",
            (investigation_id,),
        )
        return [ProgressEvent.model_validate_json(row[0]) for row in rows]

    def recent(self, limit: int = 50) -> list[InvestigationState]:
        rows = self._database.query(
            "SELECT state_json FROM investigations ORDER BY created_at DESC, investigation_id DESC LIMIT ?",
            (limit,),
        )
        return [InvestigationState.model_validate_json(row[0]) for row in rows]

    def prune(self, keep: int = 50) -> list[str]:
        """Drop the oldest investigations beyond ``keep``. Returns the ids removed."""

        rows = self._database.query(
            "SELECT investigation_id FROM investigations ORDER BY created_at DESC, investigation_id DESC"
        )
        doomed = [str(row[0]) for row in rows[max(keep, 0) :]]
        for investigation_id in doomed:
            self._database.execute(
                "DELETE FROM investigation_events WHERE investigation_id = ?", (investigation_id,)
            )
            self._database.execute(
                "DELETE FROM investigations WHERE investigation_id = ?", (investigation_id,)
            )
        return doomed

    def close(self) -> None:
        self._database.close()


class SqlJobStore(JobStore):
    """``JobStore`` with a SQL write-behind and read-through.

    Same four methods the gateway already calls. Live jobs keep their in-memory
    queue for streaming; anything pruned from memory or created before a restart
    is rehydrated from the database as a finished, replayable job.
    """

    def __init__(self, history: InvestigationHistory, *, keep: int = 50) -> None:
        super().__init__()
        self.history = history
        self.keep = keep
        self.degraded_reason: str | None = None

    def create(self, investigation_id: str, request: InvestigationRequest) -> InvestigationJob:
        job = super().create(investigation_id, request)
        job.listeners.append(self._persist)
        self._persist(job, None)
        return job

    def get(self, investigation_id: str) -> InvestigationJob | None:
        job = super().get(investigation_id)
        if job is not None:
            return job
        state = self._safely(lambda: self.history.load(investigation_id))
        if state is None:
            return None
        events = self._safely(lambda: self.history.events(investigation_id)) or []
        return InvestigationJob.restored(state, events)

    def list(self) -> list[InvestigationState]:
        states = self._safely(lambda: self.history.recent(self.keep))
        return states if states is not None else super().list()

    def prune(self, keep: int | None = None) -> None:
        limit = self.keep if keep is None else keep
        super().prune(limit)
        self._safely(lambda: self.history.prune(limit))

    def describe(self) -> dict[str, object]:
        return {
            "backend": self.history.backend,
            "target": self.history.target.label,
            "durable": self.degraded_reason is None,
            "retention": self.keep,
            "schema_versions": list(self.history.applied_migrations),
            "identifiers_hashed": True,
            "degraded_reason": self.degraded_reason,
        }

    def _persist(self, job: InvestigationJob, event: ProgressEvent | None) -> None:
        def write() -> None:
            self.history.save(job.state)
            if event is not None:
                self.history.save_event(event)

        self._safely(write)

    def _safely(self, action):  # type: ignore[no-untyped-def]
        try:
            result = action()
        except Exception as error:  # noqa: BLE001 - history must never fail an investigation
            reason = f"{type(error).__name__}: {error}"
            if reason != self.degraded_reason:
                logger.warning("investigation history unavailable, continuing in memory: %s", reason)
            self.degraded_reason = reason
            return None
        self.degraded_reason = None
        return result


def build_job_store(settings: Settings, *, fallback_to_memory: bool = True) -> JobStore:
    """Return the job store the settings ask for.

    ``memory`` is the default. A database that cannot be reached degrades to
    memory with a warning unless ``fallback_to_memory`` is off, so a missing
    Postgres never takes the gateway down with it.
    """

    if settings.history_backend == "memory":
        return JobStore()
    try:
        target = parse_database_url(settings.database_url, settings.history_sqlite_path)
        history = InvestigationHistory(
            target,
            hash_salt=settings.id_hash_salt,
            connect_timeout=settings.history_connect_timeout_seconds,
        )
    except DatabaseUnavailableError as error:
        if not fallback_to_memory:
            raise
        logger.warning("history disabled, falling back to memory: %s", error)
        return JobStore()
    return SqlJobStore(history, keep=settings.history_keep)
