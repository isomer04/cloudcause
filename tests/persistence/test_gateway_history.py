"""The gateway contract must not change when history becomes durable.

Same endpoints, same payloads. The difference is that a restarted gateway can
still serve the state, the report, and the progress log of an investigation it
no longer holds in memory.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator

import pytest
from cloudcause_api import API_PREFIX, app, datasets, main
from cloudcause_contracts import InvestigationReport, Settings
from cloudcause_datasets import SqlDatasetStore
from cloudcause_worker_core import SqlJobStore
from fastapi.testclient import TestClient

PAYLOAD = {
    "providers": ["aws", "azure", "gcp"],
    "start_date": "2026-07-13",
    "end_date": "2026-07-19",
    "comparison_start_date": "2026-07-06",
    "comparison_end_date": "2026-07-12",
    "question": "Why did our cloud spending increase last week?",
    "scenario_id": "default",
    "account_ids": ["123456789012"],
}


@pytest.fixture
def durable_gateway(database_url: str) -> Iterator[str]:
    """Point the gateway at an empty PostgreSQL history, then restore the default."""

    restart_gateway(database_url)
    try:
        yield database_url
    finally:
        main.configure(Settings.from_env({}))


def restart_gateway(url: str) -> None:
    """Drop every in-memory job the way a process restart would."""

    main.configure(
        Settings.from_env({}).with_overrides(
            history_backend="postgres", database_url=url, id_hash_salt="test-salt"
        )
    )


def test_health_reports_a_durable_history_backend(durable_gateway: str) -> None:
    with TestClient(app) as client:
        history = client.get("/health").json()["history"]
    assert history["backend"] == "postgres"
    assert history["durable"] is True
    assert history["identifiers_hashed"] is True
    assert history["schema_versions"] == [
        "0001_investigation_history",
        "0002_investigation_dataset_id",
    ]


def test_default_gateway_still_reports_in_memory_history() -> None:
    with TestClient(app) as client:
        history = client.get("/health").json()["history"]
    assert history == {"backend": "memory", "durable": False, "retention": 50}


def test_state_report_and_progress_survive_a_gateway_restart(durable_gateway: str) -> None:
    with TestClient(app) as client:
        created = client.post(f"{API_PREFIX}/investigations?wait=true", json=PAYLOAD).json()
        investigation_id = created["investigation_id"]
        assert created["state"]["status"] == "completed"
        before = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()

    restart_gateway(durable_gateway)

    with TestClient(app) as client:
        state = client.get(f"{API_PREFIX}/investigations/{investigation_id}").json()
        assert state["status"] == "completed"
        assert {status["provider"] for status in state["provider_statuses"]} == {"aws", "azure", "gcp"}

        after = InvestigationReport.model_validate(
            client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()
        )
        assert after.investigation_id == investigation_id
        assert after.contract_version == "v1"
        assert [finding.finding_id for finding in after.findings] == [
            finding["finding_id"] for finding in before["findings"]
        ]
        assert after.evidence_count() == sum(len(f["evidence"]) for f in before["findings"])
        assert after.reconciliation is not None

        markdown = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report.md").text
        assert "# CloudCause investigation" in markdown

        stages = [
            event["stage"]
            for event in client.get(f"{API_PREFIX}/investigations/{investigation_id}/progress").json()
        ]
        for stage in ("normalize", "analyze", "plan", "investigate", "validate", "reconcile", "report"):
            assert stage in stages

        history = client.get(f"{API_PREFIX}/investigations").json()
        assert [entry["investigation_id"] for entry in history] == [investigation_id]

        assert client.get(f"{API_PREFIX}/investigations/inv-missing").status_code == 404


def test_stored_reports_do_not_keep_raw_account_identifiers(durable_gateway: str) -> None:
    with TestClient(app) as client:
        created = client.post(f"{API_PREFIX}/investigations?wait=true", json=PAYLOAD).json()
        investigation_id = created["investigation_id"]
        assert created["state"]["request"]["account_ids"] == ["123456789012"]

    restart_gateway(durable_gateway)

    with TestClient(app) as client:
        state = client.get(f"{API_PREFIX}/investigations/{investigation_id}").json()
        report = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()
    for account_id in state["request"]["account_ids"] + report["request"]["account_ids"]:
        assert account_id.startswith("acct-")
        assert account_id != "123456789012"


async def test_sql_persistence_leaves_the_gateway_event_loop(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History and upload operations retain their results without blocking FastAPI."""

    configured = Settings.from_env({}).with_overrides(
        history_backend="postgres",
        database_url=database_url,
        # Any distributed boundary requires the sealed upload store to be SQL.
        # This test calls only gateway-local endpoints, so it needs no worker.
        orchestrator_mode="http",
    )
    main.configure(configured)
    try:
        assert isinstance(main.jobs, SqlJobStore)
        assert isinstance(datasets.state.store, SqlDatasetStore)
        history_threads: set[int] = set()
        dataset_threads: set[int] = set()

        history_database = main.jobs.history._database
        dataset_database = datasets.state.store._database
        original_history_run = history_database._run
        original_dataset_run = dataset_database._run

        def record_history(*args: object, **kwargs: object):
            history_threads.add(threading.get_ident())
            return original_history_run(*args, **kwargs)

        def record_dataset(*args: object, **kwargs: object):
            dataset_threads.add(threading.get_ident())
            return original_dataset_run(*args, **kwargs)

        monkeypatch.setattr(history_database, "_run", record_history)
        monkeypatch.setattr(dataset_database, "_run", record_dataset)

        loop_thread = threading.get_ident()
        dataset = await datasets.create_dataset()
        job = main.jobs.create("inv-thread-offload", main._suggested_request("default"))
        job.mark_running()
        job.emit("normalize", "normalized")
        job.finish()
        await main._flush_job_persistence()

        listed = await main.list_investigations()
        returned_dataset = await datasets.get_dataset(dataset.dataset_id)

        assert [state.investigation_id for state in listed] == ["inv-thread-offload"]
        assert returned_dataset.dataset_id == dataset.dataset_id
        assert history_threads and dataset_threads
        assert loop_thread not in history_threads
        assert loop_thread not in dataset_threads
    finally:
        main.configure(Settings.from_env({}))


async def test_cancelled_background_job_publishes_a_failed_terminal_state() -> None:
    """Cancellation is visible to clients instead of leaving an endless run."""

    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingLink:
        async def run(self, *_args: object) -> object:
            started.set()
            await release.wait()
            raise AssertionError("the cancelled job must not resume")

    main.configure(Settings.from_env({}))
    main.link = BlockingLink()
    job = main.jobs.create("inv-cancelled", main._suggested_request("default"))
    task = asyncio.create_task(main._run_job(job.investigation_id, job.state.request))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert job.state.status == "failed"
        assert job.state.error == "investigation_cancelled"
        assert job.events[-1].message == "investigation cancelled"
    finally:
        release.set()
        main.configure(Settings.from_env({}))
