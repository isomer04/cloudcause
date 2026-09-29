"""Investigation history. The durable tests need ``CLOUDCAUSE_TEST_DATABASE_URL``."""

from __future__ import annotations

from datetime import date

import pytest
from cloudcause_contracts import InvestigationRequest, Settings
from cloudcause_worker_core import (
    DatabaseUnavailableError,
    JobStore,
    SqlJobStore,
    UnsupportedDatabaseUrlError,
    build_job_store,
    hash_identifier,
    parse_database_url,
)
from cloudcause_worker_core import history as history_module
from cloudcause_worker_core.history import MIGRATIONS_DIR


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


def postgres_settings(url: str, **overrides) -> Settings:
    base = Settings.from_env({}).with_overrides(history_backend="postgres", database_url=url)
    return base.with_overrides(**overrides) if overrides else base


def store_for(url: str, **overrides) -> SqlJobStore:
    store = build_job_store(postgres_settings(url, **overrides))
    assert isinstance(store, SqlJobStore)
    return store


def test_memory_is_the_default_backend() -> None:
    store = build_job_store(Settings.from_env({}))
    assert type(store) is JobStore
    assert store.describe() == {"backend": "memory", "durable": False, "retention": 50}


def test_memory_store_reports_custom_retention_and_prunes_oldest() -> None:
    store = JobStore(keep=2)
    for index in range(3):
        store.create(f"inv-{index}", a_request())

    store.prune()

    assert store.describe() == {"backend": "memory", "durable": False, "retention": 2}
    assert [state.investigation_id for state in store.list()] == ["inv-2", "inv-1"]
    assert store.get("inv-0") is None


def test_a_postgres_url_resolves_to_a_dsn_and_a_credential_free_label() -> None:
    postgres = parse_database_url("postgres://cloudcause:secret@db:5432/cloudcause")
    assert postgres.backend == "postgres"
    assert postgres.dsn.startswith("postgresql://"), "postgres:// is normalized for psycopg"
    assert "secret" not in postgres.label, "credentials must never reach a log line"
    assert postgres.label.endswith("@db:5432/cloudcause")

    already = parse_database_url("postgresql://db:5432/cloudcause")
    assert already.dsn == "postgresql://db:5432/cloudcause"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "sqlite:///history.sqlite3",
        "/var/lib/cloudcause/history.sqlite3",
        "mysql://user:pw@db:3306/cloudcause",
    ],
)
def test_anything_that_is_not_postgres_is_refused_by_name(url: str) -> None:
    """A storage URL this build cannot honour must never resolve to somewhere else."""

    with pytest.raises(DatabaseUnavailableError) as raised:
        parse_database_url(url)
    assert "postgres" in str(raised.value).lower()


def test_a_url_that_is_not_postgres_never_silently_becomes_memory() -> None:
    """Fatal on the default path, not only when a caller opts out of the fallback."""

    settings = Settings.from_env({"CLOUDCAUSE_DATABASE_URL": "sqlite:///history.sqlite3"})
    assert settings.history_backend == "postgres"
    with pytest.raises(UnsupportedDatabaseUrlError):
        build_job_store(settings)


def test_asking_for_postgres_without_a_dsn_is_fatal_too() -> None:
    settings = Settings.from_env({}).with_overrides(history_backend="postgres", database_url="")
    with pytest.raises(UnsupportedDatabaseUrlError):
        build_job_store(settings)


def test_a_password_in_the_query_string_is_redacted_from_the_label() -> None:

    target = parse_database_url(
        "postgresql://cloudcause@db:5432/cloudcause?sslmode=require&password=hunter2"
    )
    assert "hunter2" not in target.label
    assert "password=***" in target.label
    assert "sslmode=require" in target.label, "only the secret is redacted"
    assert "password=hunter2" in target.dsn, "psycopg still receives the real DSN"


def test_no_database_url_means_the_memory_backend() -> None:
    assert Settings.from_env({}).history_backend == "memory"
    assert Settings.from_env({"CLOUDCAUSE_HISTORY_BACKEND": "memory"}).history_backend == "memory"


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
        history_backend="postgres",
        database_url="postgresql://cloudcause@db:5432/cloudcause",
        history_keep=7,
    )
    store = build_job_store(settings)
    assert type(store) is JobStore, "default is to degrade, not crash"
    assert store.describe()["retention"] == 7


def test_migrations_apply_once_and_are_idempotent(database_url: str) -> None:
    versions = sorted(path.stem for path in MIGRATIONS_DIR.glob("*.sql"))
    assert versions, "the migration directory must ship with the package"

    first = store_for(database_url)
    assert first.history.applied_migrations == versions
    first.history.close()

    second = store_for(database_url)
    assert second.history.applied_migrations == [], "a second start must not re-run migrations"
    assert second.describe()["schema_versions"] == []
    second.history.close()


def test_state_and_events_survive_a_new_store(database_url: str) -> None:
    store = store_for(database_url)
    job = store.create("inv-durable", a_request())
    job.mark_running()
    job.emit("normalize", "normalized 3 providers")
    job.emit("investigate", "aws specialist started", provider="aws")
    job.finish()
    store.history.close()

    restarted = store_for(database_url)
    reloaded = restarted.get("inv-durable")
    assert reloaded is not None
    assert reloaded.is_restored is True
    assert reloaded.state.status == "completed"
    assert reloaded.state.question == "Why did AWS spending increase?"
    assert [event.stage for event in reloaded.events] == ["normalize", "investigate"]
    assert [event.sequence for event in reloaded.events] == [1, 2]
    assert reloaded.events[1].provider == "aws"
    restarted.history.close()


async def test_a_restored_job_streams_its_stored_events_and_closes(database_url: str) -> None:
    store = store_for(database_url)
    job = store.create("inv-stream", a_request())
    job.emit("plan", "plan ready")
    job.finish()
    store.history.close()

    restarted = store_for(database_url)
    restored = restarted.get("inv-stream")
    assert restored is not None
    stages = [event.stage async for event in restored.stream()]
    assert stages == ["plan"]
    restarted.history.close()


def test_unknown_investigation_stays_unknown(database_url: str) -> None:
    store = store_for(database_url)
    assert store.get("inv-never-existed") is None
    store.history.close()


def test_the_backend_is_reported_as_durable_postgres(database_url: str) -> None:
    store = store_for(database_url)
    described = store.describe()
    assert described["backend"] == "postgres"
    assert described["durable"] is True
    assert described["identifiers_hashed"] is True
    store.history.close()


def test_account_identifiers_are_hashed_before_storage(database_url: str) -> None:
    store = store_for(database_url, id_hash_salt="pepper")
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


def test_history_lists_newest_first_and_prunes_the_oldest(database_url: str) -> None:
    store = store_for(database_url, history_keep=2)
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


def test_a_broken_database_degrades_to_memory_without_failing_the_job(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected rather than staged by closing the connection: psycopg reconnects."""

    store = store_for(database_url)
    job = store.create("inv-degraded", a_request())

    def refuse(*args: object, **kwargs: object) -> None:
        raise DatabaseUnavailableError("the database went away mid-investigation")

    monkeypatch.setattr(store.history, "save", refuse)
    monkeypatch.setattr(store.history, "save_event", refuse)

    job.emit("analyze", "still running")
    job.finish()

    assert job.state.status == "completed", "persistence must never fail an investigation"
    assert store.degraded_reason is not None
    described = store.describe()
    assert described["durable"] is False
    assert described["backend"] == "postgres"
    assert store.get("inv-degraded") is not None, "the in-memory job is still served"
    store.history.close()
