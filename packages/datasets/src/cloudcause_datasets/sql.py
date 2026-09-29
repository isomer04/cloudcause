"""Small SQL kit shared by the dataset store and the investigation history.

One connection, one lock, and migrations applied in filename order against
PostgreSQL, the only persisted store CloudCause has. It lives here rather than in
``worker_core`` because the dataset store sits below the worker layer:
``worker_core`` imports ``providers``, which imports this package, so the reverse
direction would be a cycle.

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

SqlBackend = Literal["postgres"]

SCHEMA_TABLE = "cloudcause_schema_migrations"

_CREDENTIALS = re.compile(r"//[^/@]*@")

#: libpq takes the password as a query parameter as well as in the userinfo, so
#: redacting only ``//user:pw@`` leaves ``?password=`` intact in a logged label.
_QUERY_SECRETS = re.compile(r"(?i)(password|sslpassword)=[^&\s]*")

#: A DSN that names a scheme, as opposed to a bare filesystem path.
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _safe_label(dsn: str) -> str:
    """The only form of a DSN that may reach a log line."""

    return _QUERY_SECRETS.sub(r"\1=***", _CREDENTIALS.sub("//***@", dsn))


class DatabaseUnavailableError(RuntimeError):
    """The configured database cannot be reached or the driver is missing."""


class UnsupportedDatabaseUrlError(DatabaseUnavailableError):
    """The URL names a backend this build does not have, or names none.

    Separate from its parent because the recoveries differ: an unreachable
    database may come back, a misspelled DSN never will, so this never degrades.
    """


@dataclass(frozen=True)
class DatabaseTarget:
    """Where rows are stored, plus a label that is safe to log."""

    backend: SqlBackend
    dsn: str
    label: str


def parse_database_url(url: str) -> DatabaseTarget:
    """Resolve ``CLOUDCAUSE_DATABASE_URL`` into a PostgreSQL target.

    ``postgres://`` and ``postgresql://`` are normalized and handed to psycopg
    as-is; everything else, including an empty URL, raises.
    """

    raw = (url or "").strip()
    if not raw:
        raise UnsupportedDatabaseUrlError(
            "no database configured: set CLOUDCAUSE_DATABASE_URL to "
            "postgresql://user:pw@host:5432/db, or leave CLOUDCAUSE_HISTORY_BACKEND "
            "at memory to run without persistence."
        )
    lowered = raw.lower()
    if lowered.startswith(("postgres://", "postgresql://")):
        dsn = "postgresql://" + raw.split("://", 1)[1]
        return DatabaseTarget("postgres", dsn, _safe_label(dsn))
    scheme = raw.split("://", 1)[0] if _URI_SCHEME.match(raw) else raw.split(":", 1)[0]
    raise UnsupportedDatabaseUrlError(
        f"unsupported database scheme {scheme!r}: CloudCause storage is PostgreSQL. "
        "Use postgresql://user:pw@host:5432/db, or CLOUDCAUSE_HISTORY_BACKEND=memory "
        "for a run that keeps nothing."
    )


def hash_identifier(value: str, salt: str = "") -> str:
    """Pseudonymize an account, subscription, or project identifier."""

    if not value:
        return value
    digest = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
    return f"acct-{digest[:12]}"


class Database:
    """Minimal connection wrapper: one connection, one lock, one dialect."""

    def __init__(self, target: DatabaseTarget, connect_timeout: float = 5.0) -> None:
        self.target = target
        self.connect_timeout = connect_timeout
        self._lock = threading.RLock()
        self._connection = self._connect()

    def _connect(self) -> Any:
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
        """Callers write ``?``; psycopg wants ``%s``."""

        return statement.replace("?", "%s")

    def _disconnect_errors(self) -> tuple[type[BaseException], ...]:
        """Errors meaning the connection died, not that the query is wrong."""

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
