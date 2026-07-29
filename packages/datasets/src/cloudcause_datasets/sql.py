"""Small SQL kit shared by the dataset store and the investigation history.

One connection, one lock, two paramstyles, and portable migrations applied in
filename order. It lives here rather than in ``worker_core`` because the dataset
store sits below the worker layer: ``worker_core`` imports ``providers``, which
imports this package, so the reverse direction would be a cycle.

The two callers want opposite failure behaviour and that is deliberate. History
degrades to memory when the database disappears, because losing history must
never fail an investigation. A dataset store never degrades, because a dataset
only the gateway can see produces a mid-run "unknown dataset" or, worse, a silent
fall-through to demo fixtures under an uploaded label.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("cloudcause.sql")

SqlBackend = Literal["sqlite", "postgres"]

SCHEMA_TABLE = "cloudcause_schema_migrations"

_CREDENTIALS = re.compile(r"//[^/@]*@")

#: A DSN that names a scheme, as opposed to a bare filesystem path. Windows paths
#: such as ``C:/db.sqlite3`` contain a colon but never ``://``.
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


class DatabaseUnavailableError(RuntimeError):
    """The configured database cannot be reached or the driver is missing."""


@dataclass(frozen=True)
class DatabaseTarget:
    """Where rows are stored, plus a label that is safe to log."""

    backend: SqlBackend
    dsn: str
    label: str


def parse_database_url(url: str, sqlite_path: Path) -> DatabaseTarget:
    """Resolve ``CLOUDCAUSE_DATABASE_URL`` into a backend and a DSN.

    Accepted forms, following the usual SQLAlchemy spelling:

    ``""``                            the caller's default SQLite file
    ``sqlite:///relative.sqlite3``    relative to the working directory
    ``sqlite:///C:/dir/db.sqlite3``   Windows absolute
    ``sqlite:////var/lib/db.sqlite3`` POSIX absolute
    ``sqlite:///:memory:``            ephemeral, used by tests
    ``postgresql://user:pw@host:5432/db``  handed to psycopg as-is
    ``/var/lib/db.sqlite3``           a bare filesystem path, treated as SQLite

    Any other URI scheme raises: ``mysql://host/db`` silently becoming a SQLite
    file named ``mysql://host/db`` is worse than a refusal that names the two
    backends that exist.
    """

    raw = (url or "").strip()
    if not raw:
        return DatabaseTarget("sqlite", str(sqlite_path), str(sqlite_path))
    lowered = raw.lower()
    if lowered.startswith(("postgres://", "postgresql://")):
        dsn = "postgresql://" + raw.split("://", 1)[1]
        return DatabaseTarget("postgres", dsn, _CREDENTIALS.sub("//***@", dsn))
    if lowered.startswith("sqlite:"):
        remainder = raw[len("sqlite:") :]
        for prefix in ("///", "//"):
            if remainder.startswith(prefix):
                remainder = remainder[len(prefix) :]
                break
        if not remainder or remainder == ":memory:":
            return DatabaseTarget("sqlite", ":memory:", "sqlite in-memory")
        return DatabaseTarget("sqlite", remainder, remainder)
    if _URI_SCHEME.match(raw):
        scheme = raw.split("://", 1)[0]
        raise DatabaseUnavailableError(
            f"unsupported database scheme {scheme!r}: CloudCause storage is sqlite or postgresql. "
            "Use sqlite:///path/to/file.sqlite3, postgresql://user:pw@host:5432/db, or a bare "
            "filesystem path."
        )
    return DatabaseTarget("sqlite", raw, raw)


def hash_identifier(value: str, salt: str = "") -> str:
    """Pseudonymize an account, subscription, or project identifier."""

    if not value:
        return value
    digest = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
    return f"acct-{digest[:12]}"


class Database:
    """Minimal connection wrapper: one connection, one lock, two paramstyles."""

    def __init__(self, target: DatabaseTarget, connect_timeout: float = 5.0) -> None:
        self.target = target
        self.connect_timeout = connect_timeout
        self._lock = threading.RLock()
        self._connection = self._connect()

    def _connect(self) -> Any:
        if self.target.backend == "sqlite":
            import sqlite3

            if self.target.dsn != ":memory:":
                Path(self.target.dsn).parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.target.dsn, check_same_thread=False, isolation_level=None)
            connection.execute("PRAGMA journal_mode=WAL")
            return connection
        try:
            import psycopg
        except ModuleNotFoundError as error:  # pragma: no cover - needs the driver absent
            raise DatabaseUnavailableError(
                'postgres storage needs psycopg: uv pip install "psycopg[binary]>=3.2"'
            ) from error
        try:
            return psycopg.connect(
                self.target.dsn, autocommit=True, connect_timeout=max(int(self.connect_timeout), 1)
            )
        except Exception as error:  # noqa: BLE001 - any driver error means unavailable
            raise DatabaseUnavailableError(f"cannot reach {self.target.label}: {error}") from error

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.target.backend == "postgres" else statement

    def _disconnect_errors(self) -> tuple[type[BaseException], ...]:
        """Driver exceptions that mean "the connection died", not "the query is wrong".

        Only PostgreSQL: a SQLite database is a local file, so there is no socket
        to drop, and ``sqlite3.OperationalError`` also covers real errors such as
        a missing table that a retry would only repeat.
        """

        if self.target.backend != "postgres":
            return ()
        try:
            import psycopg
        except ModuleNotFoundError:  # pragma: no cover - checked at connect time
            return ()
        return (psycopg.OperationalError, psycopg.InterfaceError)

    def _run(self, statement: str, params: tuple[object, ...], *, fetch: bool) -> list[tuple[Any, ...]]:
        """Execute once, and on a dropped connection reconnect and execute again.

        A long-lived connection outlives a Postgres restart or an idle timeout in
        Compose. Errors that are not connection failures propagate untouched, so a
        constraint violation stays a constraint violation instead of being
        reported as an unreachable database.
        """

        with self._lock:
            try:
                return self._attempt(statement, params, fetch=fetch)
            except self._disconnect_errors() as first:
                logger.warning("reconnecting to %s after %s", self.target.label, first)
                try:
                    self._connection = self._connect()
                    return self._attempt(statement, params, fetch=fetch)
                except DatabaseUnavailableError:
                    raise
                except Exception as error:  # noqa: BLE001 - one retry, then refuse
                    raise DatabaseUnavailableError(
                        f"cannot reach {self.target.label}: {error}"
                    ) from error

    def _attempt(
        self, statement: str, params: tuple[object, ...], *, fetch: bool
    ) -> list[tuple[Any, ...]]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(self._sql(statement), tuple(params))
            return [tuple(row) for row in cursor.fetchall()] if fetch else []
        finally:
            cursor.close()

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        self._run(statement, params, fetch=False)

    def query(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        return self._run(statement, params, fetch=True)

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                logger.debug("connection close failed", exc_info=True)


def statements(script: str) -> list[str]:
    body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in body.split(";") if statement.strip()]


def apply_migrations(database: Database, directory: Path) -> list[str]:
    """Apply every unapplied ``*.sql`` file in ``directory``, in filename order."""

    database.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {row[0] for row in database.query(f"SELECT version FROM {SCHEMA_TABLE}")}
    applied: list[str] = []
    for path in sorted(Path(directory).glob("*.sql")):
        if path.stem in done:
            continue
        for statement in statements(path.read_text(encoding="utf-8")):
            database.execute(statement)
        database.execute(
            f"INSERT INTO {SCHEMA_TABLE} (version, applied_at) VALUES (?, ?) "
            "ON CONFLICT (version) DO NOTHING",
            (path.stem, datetime.now(tz=UTC).isoformat()),
        )
        applied.append(path.stem)
    return applied
