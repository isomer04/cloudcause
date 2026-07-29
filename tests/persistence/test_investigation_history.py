"""Investigation history.

Everything here runs offline against SQLite, which uses the same migration files
and the same SQL as the Compose PostgreSQL service. The one PostgreSQL test skips
itself unless a database is reachable, so CI stays offline and a developer with
`docker compose up postgres` gets the extra coverage for free.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from cloudcause_contracts import InvestigationRequest, InvestigationState, Settings
from cloudcause_worker_core import (
    DatabaseUnavailableError,
    InvestigationHistory,
    JobStore,
    SqlJobStore,
    build_job_store,
    hash_identifier,
    parse_database_url,
)
from cloudcause_worker_core import history as history_module
from cloudcause_worker_core.history import MIGRATIONS_DIR

POSTGRES_URL_ENV = "CLOUDCAUSE_TEST_DATABASE_URL"


def a_request(**overrides) -> InvestigationRequest:
    payload = {
        "providers": ["aws"],
        "start_date": date(2026, 7, 13),
        "end_date": date(2026, 7, 19),
        "comparison_start_date": date(2026, 7, 6),
        "comparison_end_date": date(2026, 7, 12),
        "question": "Why did AWS spending increase?",
        "scenario_id": "aws-nat-gateway-misroute",
    }
    payload.update(overrides)
    return InvestigationRequest(**payload)


def sqlite_settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings.from_env({}).with_overrides(
        history_backend="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'history.sqlite3').as_posix()}",
    )
    return base.with_overrides(**overrides) if overrides else base


def store_for(tmp_path: Path, **overrides) -> SqlJobStore:
    store = build_job_store(sqlite_settings(tmp_path, **overrides))
    assert isinstance(store, SqlJobStore)
    return store


def test_memory_is_the_default_backend() -> None:
    store = build_job_store(Settings.from_env({}))
    assert type(store) is JobStore
    assert store.describe() == {"backend": "memory", "durable": False, "retention": 50}


def test_database_url_forms_resolve_to_a_backend_and_a_safe_label() -> None:
    default = Path("/var/lib/cloudcause/history.sqlite3")
    assert parse_database_url("", default).backend == "sqlite"
    assert parse_database_url("", default).dsn == str(default)
    assert parse_database_url("sqlite:///:memory:", default).dsn == ":memory:"
    assert parse_database_url("sqlite:///relative.sqlite3", default).dsn == "relative.sqlite3"
    assert parse_database_url("sqlite:///C:/tmp/db.sqlite3", default).dsn == "C:/tmp/db.sqlite3"
    assert parse_database_url("sqlite:////var/lib/db.sqlite3", default).dsn == "/var/lib/db.sqlite3"

    postgres = parse_database_url("postgres://cloudcause:secret@db:5432/cloudcause", default)
    assert postgres.backend == "postgres"
    assert postgres.dsn.startswith("postgresql://")
    assert "secret" not in postgres.label, "credentials must never reach a log line"
    assert postgres.label.endswith("@db:5432/cloudcause")


def test_migrations_apply_once_and_are_idempotent(tmp_path: Path) -> None:
    versions = sorted(path.stem for path in MIGRATIONS_DIR.glob("*.sql"))
    assert versions, "the migration directory must ship with the package"

    settings = sqlite_settings(tmp_path)
    first = build_job_store(settings)
    assert isinstance(first, SqlJobStore)
    assert first.history.applied_migrations == versions
    first.history.close()

    second = build_job_store(settings)
    assert isinstance(second, SqlJobStore)
    assert second.history.applied_migrations == [], "a second start must not re-run migrations"
    assert second.describe()["schema_versions"] == []
    second.history.close()


def test_state_and_events_survive_a_new_store(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    job = store.create("inv-durable", a_request())
    job.mark_running()
    job.emit("normalize", "normalized 3 providers")
    job.emit("investigate", "aws specialist started", provider="aws")
    job.finish()
    store.history.close()

    restarted = store_for(tmp_path)
    reloaded = restarted.get("inv-durable")
    assert reloaded is not None
    assert reloaded.is_restored is True
    assert reloaded.state.status == "completed"
    assert reloaded.state.question == "Why did AWS spending increase?"
    assert [event.stage for event in reloaded.events] == ["normalize", "investigate"]
    assert [event.sequence for event in reloaded.events] == [1, 2]
    assert reloaded.events[1].provider == "aws"
    restarted.history.close()


async def test_a_restored_job_streams_its_stored_events_and_closes(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    job = store.create("inv-stream", a_request())
    job.emit("plan", "plan ready")
    job.finish()
    store.history.close()

    restarted = store_for(tmp_path)
    restored = restarted.get("inv-stream")
    assert restored is not None
    stages = [event.stage async for event in restored.stream()]
    assert stages == ["plan"]
    restarted.history.close()


def test_unknown_investigation_stays_unknown(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    assert store.get("inv-never-existed") is None
    store.history.close()


def test_account_identifiers_are_hashed_before_storage(tmp_path: Path) -> None:
    store = store_for(tmp_path, id_hash_salt="pepper")
    job = store.create("inv-redacted", a_request(account_ids=["123456789012", "sub-alpha"]))
    job.finish()

    assert job.state.request.account_ids == ["123456789012", "sub-alpha"], "live state is untouched"

    stored = store.history.load("inv-redacted")
    assert stored is not None
    assert stored.request.account_ids == [
        hash_identifier("123456789012", "pepper"),
        hash_identifier("sub-alpha", "pepper"),
    ]
    assert all(value.startswith("acct-") for value in stored.request.account_ids)
    assert "123456789012" not in stored.model_dump_json()
    assert hash_identifier("123456789012", "pepper") != hash_identifier("123456789012")
    store.history.close()


def test_history_lists_newest_first_and_prunes_the_oldest(tmp_path: Path) -> None:
    store = store_for(tmp_path, history_keep=2)
    for index in range(4):
        job = store.create(f"inv-{index}", a_request())
        job.finish()

    listed = [state.investigation_id for state in store.list()]
    assert listed[0] == "inv-3", "most recent first"
    assert len(listed) == 2, "the listing respects the retention limit"

    store.prune()
    remaining = {state.investigation_id for state in store.list()}
    assert remaining == {"inv-2", "inv-3"}
    assert store.get("inv-0") is None, "pruned investigations are gone from both layers"
    store.history.close()


def test_a_broken_database_degrades_to_memory_without_failing_the_job(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    job = store.create("inv-degraded", a_request())
    store.history.close()  # simulate the database disappearing mid-investigation

    job.emit("analyze", "still running")
    job.finish()

    assert job.state.status == "completed", "persistence must never fail an investigation"
    assert store.degraded_reason is not None
    described = store.describe()
    assert described["durable"] is False
    assert described["backend"] == "sqlite"
    assert store.get("inv-degraded") is not None, "the in-memory job is still served"


def test_an_unreachable_database_is_fatal_only_when_asked() -> None:
    unreachable = Settings.from_env({}).with_overrides(
        history_backend="postgres",
        database_url="postgresql://cloudcause:nope@127.0.0.1:1/cloudcause",
        history_connect_timeout_seconds=1.0,
    )
    with pytest.raises(DatabaseUnavailableError):
        build_job_store(unreachable, fallback_to_memory=False)


def test_an_unreachable_database_degrades_to_memory_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise DatabaseUnavailableError("postgres is not listening")

    monkeypatch.setattr(history_module, "InvestigationHistory", refuse)
    settings = Settings.from_env({}).with_overrides(
        history_backend="postgres", database_url="postgresql://cloudcause@db:5432/cloudcause"
    )
    assert type(build_job_store(settings)) is JobStore, "default is to degrade, not crash"


def postgres_history(url: str) -> InvestigationHistory:
    target = parse_database_url(url, Path("unused"))
    return InvestigationHistory(target)


def test_postgres_backend_runs_the_same_schema() -> None:
    """Opt-in: set CLOUDCAUSE_TEST_DATABASE_URL with Compose's postgres running."""

    url = os.environ.get(POSTGRES_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"set {POSTGRES_URL_ENV} to test the PostgreSQL backend")
    try:
        history = postgres_history(url)
    except DatabaseUnavailableError as error:
        pytest.skip(f"postgres unavailable: {error}")

    store = SqlJobStore(history, keep=10)
    job = store.create("inv-postgres", a_request(account_ids=["123456789012"]))
    job.emit("normalize", "normalized")
    job.finish()

    state = InvestigationState.model_validate(store.history.load("inv-postgres").model_dump())
    assert state.status == "completed"
    assert state.request.account_ids == [hash_identifier("123456789012")]
    assert [event.stage for event in store.history.events("inv-postgres")] == ["normalize"]
    store.history.prune(keep=0)
    history.close()
