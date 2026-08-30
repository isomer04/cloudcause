"""Where an uploaded dataset lives, and which store the topology allows.

Two implementations, selected by topology rather than by preference:

===================================  ======  ==========================
``orchestrator_mode`` / ``worker_mode``  Store   Uploads
===================================  ======  ==========================
both ``inprocess``                    memory  enabled
either ``http``, database configured  SQL     enabled
either ``http``, no database          none    refused at ingest, ``503``
===================================  ======  ==========================

``scenario_id`` is the only data selector that crosses a process boundary today:
``get_data_provider`` is called independently in the orchestrator, in each worker,
and in every MCP stdio child. Data does not travel in the request; each process
rebuilds it from an id. So when those processes are separate containers, the
dataset has to be somewhere all of them can read.

A SQL failure here raises. It never degrades to memory the way investigation
history does, because a dataset only the gateway can see produces a mid-run
"unknown dataset", or worse, a silent fall-through to the demo fixtures under an
uploaded label.
"""

from __future__ import annotations

import logging
import secrets
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from cloudcause_contracts import Settings, utcnow

from .models import Dataset, DatasetStoreKind, expiry_from
from .sql import (
    Database,
    DatabaseTarget,
    DatabaseUnavailableError,
    apply_migrations,
    parse_database_url,
)

logger = logging.getLogger("cloudcause.datasets")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: Unguessable and not enumerable. The gateway is unauthenticated in the MVP, so
#: the id is the only thing standing between two users' uploads.
DATASET_ID_BYTES = 16


class DatasetError(RuntimeError):
    """Base class for every refusal the dataset layer can produce."""


class UnknownDatasetError(DatasetError):
    """No such dataset. Never falls through to fixtures."""


class DatasetExpiredError(DatasetError):
    """The dataset lived its TTL and was evicted."""


class DatasetNotSealedError(DatasetError):
    """An unsealed dataset is still being written and cannot start a run."""


class DatasetSealedError(DatasetError):
    """A sealed dataset is immutable, which is what makes concurrent reads safe."""


class DatasetStoreFullError(DatasetError):
    """The store-wide byte cap was reached. Refused rather than grown."""


class DatasetTooLargeError(DatasetError):
    """One dataset exceeded the per-dataset record cap."""


class DatasetProviderMissingError(DatasetError):
    """The dataset is fine, it just holds nothing for the provider that was asked."""


class UploadsUnavailableError(DatasetError):
    """The topology cannot resolve a dataset, so ingest is refused up front."""


class DatasetStore(ABC):
    """The four operations the gateway needs, plus the read every process needs."""

    kind: DatasetStoreKind
    label: str

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._dataset_locks: dict[str, threading.RLock] = {}
        self._dataset_locks_guard = threading.Lock()

    @contextmanager
    def mutate(self, dataset_id: str) -> Iterator[None]:
        """Serialize one dataset's get-validate-mutate-put against itself.

        Ingest reads a dataset, checks the seal, the source cap, and the currency,
        then writes the whole dataset back. Two uploads to the same dataset
        interleaving there would lose a source or resurrect an unsealed state, so
        the sequence is held under one lock per dataset id.

        Process-local by design: every write goes through the gateway's ingest
        endpoints, and a sealed dataset is immutable, so the orchestrator, the
        workers, and the MCP children only ever read. Sharing writes across
        processes would need row locking in SQL, and nothing does that today.
        """

        with self._dataset_locks_guard:
            lock = self._dataset_locks.setdefault(dataset_id, threading.RLock())
        with lock:
            yield

    def forget_lock(self, dataset_id: str) -> None:
        with self._dataset_locks_guard:
            self._dataset_locks.pop(dataset_id, None)

    def new_id(self) -> str:
        return secrets.token_urlsafe(DATASET_ID_BYTES)

    def create(self) -> Dataset:
        self.evict_expired()
        dataset = Dataset(
            dataset_id=self.new_id(), expires_at=expiry_from(self.settings.dataset_ttl_seconds)
        )
        self.put(dataset)
        return dataset

    def seal(self, dataset_id: str) -> Dataset:
        with self.mutate(dataset_id):
            dataset = self.get(dataset_id)
            if dataset.sealed:
                return dataset
            dataset.sealed_at = utcnow()
            self.put(dataset)
            return dataset

    def get_for_investigation(self, dataset_id: str) -> Dataset:
        """Fetch a dataset that is allowed to start or serve an investigation."""

        dataset = self.get(dataset_id)
        if not dataset.sealed:
            raise DatasetNotSealedError(
                f"dataset {dataset_id} has not been sealed; seal it before investigating so every "
                "reader sees the same immutable data"
            )
        return dataset

    @abstractmethod
    def get(self, dataset_id: str) -> Dataset: ...

    @abstractmethod
    def put(self, dataset: Dataset) -> None: ...

    @abstractmethod
    def delete(self, dataset_id: str) -> bool: ...

    @abstractmethod
    def evict_expired(self, now: datetime | None = None) -> list[str]: ...

    @abstractmethod
    def total_bytes(self) -> int: ...

    @abstractmethod
    def count(self) -> int: ...

    def check_capacity(self, dataset: Dataset, payload_bytes: int) -> None:
        """Refuse before writing rather than growing without bound."""

        if dataset.record_count() > self.settings.dataset_max_records:
            raise DatasetTooLargeError(
                f"dataset {dataset.dataset_id} would hold {dataset.record_count():,} normalized "
                f"records, over the {self.settings.dataset_max_records:,} limit. Every reader loads "
                "the whole set once per investigation, so this cap is what keeps a run cheap."
            )
        existing = self._bytes_excluding(dataset.dataset_id)
        if existing + payload_bytes > self.settings.dataset_store_max_bytes:
            raise DatasetStoreFullError(
                f"the dataset store holds {existing:,} bytes and this write needs "
                f"{payload_bytes:,} more, over the {self.settings.dataset_store_max_bytes:,} byte "
                "cap. Delete a dataset or wait for one to expire."
            )

    @abstractmethod
    def _bytes_excluding(self, dataset_id: str) -> int: ...

    def describe(self) -> dict[str, object]:
        return {
            "backend": self.kind,
            "target": self.label,
            "datasets": self.count(),
            "bytes": self.total_bytes(),
            "max_bytes": self.settings.dataset_store_max_bytes,
            "max_records_per_dataset": self.settings.dataset_max_records,
            "ttl_seconds": self.settings.dataset_ttl_seconds,
            "uploads_enabled": True,
        }


class MemoryDatasetStore(DatasetStore):
    """One process, one dict. Correct only when nothing is over HTTP."""

    kind: DatasetStoreKind = "memory"
    label = "in-process memory"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._lock = threading.RLock()
        self._datasets: dict[str, Dataset] = {}
        self._sizes: dict[str, int] = {}

    def get(self, dataset_id: str) -> Dataset:
        with self._lock:
            dataset = self._datasets.get(dataset_id)
            if dataset is None:
                raise UnknownDatasetError(f"unknown dataset {dataset_id}")
            if dataset.is_expired():
                self._forget(dataset_id)
                raise DatasetExpiredError(
                    f"dataset {dataset_id} expired at {dataset.expires_at.isoformat()}"
                )
            return dataset.model_copy(deep=True)

    def put(self, dataset: Dataset) -> None:
        size = len(dataset.model_dump_json().encode())
        self.check_capacity(dataset, size)
        with self._lock:
            self._datasets[dataset.dataset_id] = dataset.model_copy(deep=True)
            self._sizes[dataset.dataset_id] = size

    def delete(self, dataset_id: str) -> bool:
        with self._lock:
            return self._forget(dataset_id)

    def _forget(self, dataset_id: str) -> bool:
        self._sizes.pop(dataset_id, None)
        return self._datasets.pop(dataset_id, None) is not None

    def evict_expired(self, now: datetime | None = None) -> list[str]:
        moment = now or utcnow()
        with self._lock:
            doomed = [
                dataset_id
                for dataset_id, dataset in self._datasets.items()
                if dataset.is_expired(moment)
            ]
            for dataset_id in doomed:
                self._forget(dataset_id)
        return doomed

    def total_bytes(self) -> int:
        with self._lock:
            return sum(self._sizes.values())

    def count(self) -> int:
        with self._lock:
            return len(self._datasets)

    def _bytes_excluding(self, dataset_id: str) -> int:
        with self._lock:
            return sum(size for key, size in self._sizes.items() if key != dataset_id)


class SqlDatasetStore(DatasetStore):
    """PostgreSQL, shared by every container. Errors are errors."""

    kind: DatasetStoreKind = "sql"

    def __init__(self, target: DatabaseTarget, settings: Settings) -> None:
        super().__init__(settings)
        self.target = target
        self.label = target.label
        self._database = Database(target, settings.history_connect_timeout_seconds)
        self.applied_migrations = apply_migrations(self._database, MIGRATIONS_DIR)

    def get(self, dataset_id: str) -> Dataset:
        rows = self._database.query(
            "SELECT dataset_json FROM cloudcause_datasets WHERE dataset_id = ?", (dataset_id,)
        )
        if not rows:
            raise UnknownDatasetError(f"unknown dataset {dataset_id}")
        dataset = Dataset.model_validate_json(rows[0][0])
        if dataset.is_expired():
            self.delete(dataset_id)
            raise DatasetExpiredError(
                f"dataset {dataset_id} expired at {dataset.expires_at.isoformat()}"
            )
        return dataset

    def put(self, dataset: Dataset) -> None:
        payload = dataset.model_dump_json()
        size = len(payload.encode())
        self.check_capacity(dataset, size)
        self._database.execute(
            "INSERT INTO cloudcause_datasets "
            "(dataset_id, created_at, expires_at, sealed, byte_size, record_count, dataset_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (dataset_id) DO UPDATE SET "
            "expires_at = excluded.expires_at, sealed = excluded.sealed, "
            "byte_size = excluded.byte_size, record_count = excluded.record_count, "
            "dataset_json = excluded.dataset_json",
            (
                dataset.dataset_id,
                dataset.created_at.isoformat(),
                dataset.expires_at.isoformat(),
                1 if dataset.sealed else 0,
                size,
                dataset.record_count(),
                payload,
            ),
        )

    def delete(self, dataset_id: str) -> bool:
        existed = bool(
            self._database.query(
                "SELECT 1 FROM cloudcause_datasets WHERE dataset_id = ?", (dataset_id,)
            )
        )
        self._database.execute("DELETE FROM cloudcause_datasets WHERE dataset_id = ?", (dataset_id,))
        return existed

    def evict_expired(self, now: datetime | None = None) -> list[str]:
        cutoff = (now or utcnow()).isoformat()
        rows = self._database.query(
            "SELECT dataset_id FROM cloudcause_datasets WHERE expires_at <= ?", (cutoff,)
        )
        doomed = [str(row[0]) for row in rows]
        if doomed:
            self._database.execute(
                "DELETE FROM cloudcause_datasets WHERE expires_at <= ?", (cutoff,)
            )
        return doomed

    def total_bytes(self) -> int:
        rows = self._database.query(
            "SELECT COALESCE(SUM(byte_size), 0) FROM cloudcause_datasets"
        )
        return int(rows[0][0]) if rows else 0

    def count(self) -> int:
        rows = self._database.query("SELECT COUNT(*) FROM cloudcause_datasets")
        return int(rows[0][0]) if rows else 0

    def _bytes_excluding(self, dataset_id: str) -> int:
        rows = self._database.query(
            "SELECT COALESCE(SUM(byte_size), 0) FROM cloudcause_datasets WHERE dataset_id <> ?",
            (dataset_id,),
        )
        return int(rows[0][0]) if rows else 0

    def describe(self) -> dict[str, object]:
        return {**super().describe(), "schema_versions": list(self.applied_migrations)}

    def close(self) -> None:
        self._database.close()


#: One memory store per process. ``get_data_provider`` is called from the gateway,
#: the orchestrator, both workers, and the MCP tools; in the in-process topology
#: those are the same process and must see the same dict.
_MEMORY_STORES: dict[str, MemoryDatasetStore] = {}
_MEMORY_LOCK = threading.RLock()


def _memory_store(settings: Settings) -> MemoryDatasetStore:
    with _MEMORY_LOCK:
        store = _MEMORY_STORES.get("default")
        if store is None:
            store = MemoryDatasetStore(settings)
            _MEMORY_STORES["default"] = store
        else:
            # Settings are rebuilt per request; the data must outlive them.
            store.settings = settings
        return store


def reset_memory_store() -> None:
    """Drop the process-wide memory store. Tests use this; nothing else should."""

    with _MEMORY_LOCK:
        _MEMORY_STORES.clear()


def dataset_store_reason(settings: Settings) -> str | None:
    """Why uploads are unavailable, or ``None`` when they work.

    The message names the missing configuration rather than saying "disabled",
    because the fix is a DSN on three services and nobody guesses that.
    """

    if not settings.uploads_enabled:
        return (
            "uploads are disabled by CLOUDCAUSE_UPLOADS_ENABLED=false on this deployment"
        )
    distributed = settings.orchestrator_mode == "http" or settings.worker_mode == "http"
    if distributed and not settings.database_url.strip():
        return (
            "this deployment runs the orchestrator or the workers over HTTP "
            f"(orchestrator_mode={settings.orchestrator_mode}, worker_mode={settings.worker_mode}), "
            "so an uploaded dataset has to be shared through a database. Set "
            "CLOUDCAUSE_DATABASE_URL on the api, orchestrator, aws-worker, and azure-worker "
            "services, or run everything in one process."
        )
    return None


def uploads_available(settings: Settings) -> bool:
    return dataset_store_reason(settings) is None


def build_dataset_store(settings: Settings) -> DatasetStore:
    """The store this topology allows, or a refusal naming what is missing."""

    reason = dataset_store_reason(settings)
    if reason is not None:
        raise UploadsUnavailableError(reason)
    if settings.orchestrator_mode == "inprocess" and settings.worker_mode == "inprocess":
        return _memory_store(settings)
    target: DatabaseTarget | None = None
    try:
        target = parse_database_url(settings.database_url)
        return SqlDatasetStore(target, settings)
    except DatabaseUnavailableError as error:
        # Deliberately not a degradation: see the module docstring.
        where = target.label if target is not None else "the configured database"
        raise UploadsUnavailableError(
            f"the dataset store cannot reach {where}: {error}. Uploads stay refused rather "
            "than silently visible to the gateway alone."
        ) from error


def try_build_dataset_store(settings: Settings) -> tuple[DatasetStore | None, str | None]:
    """Non-raising variant for ``/health`` and for the feature flag."""

    try:
        return build_dataset_store(settings), None
    except UploadsUnavailableError as error:
        return None, str(error)
