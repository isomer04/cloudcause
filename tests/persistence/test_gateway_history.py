"""The gateway contract must not change when history becomes durable.

Same endpoints, same payloads. The difference is that a restarted gateway can
still serve the state, the report, and the progress log of an investigation it
no longer holds in memory.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from cloudcause_api import API_PREFIX, app, main
from cloudcause_contracts import InvestigationReport, Settings
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
def sqlite_gateway(tmp_path: Path) -> Iterator[str]:
    """Point the gateway at a temporary SQLite history, then restore the default."""

    url = f"sqlite:///{(tmp_path / 'history.sqlite3').as_posix()}"
    main.configure(
        Settings.from_env({}).with_overrides(
            history_backend="sqlite", database_url=url, id_hash_salt="test-salt"
        )
    )
    try:
        yield url
    finally:
        main.configure(Settings.from_env({}))


def restart_gateway(url: str) -> None:
    """Drop every in-memory job the way a process restart would."""

    main.configure(
        Settings.from_env({}).with_overrides(
            history_backend="sqlite", database_url=url, id_hash_salt="test-salt"
        )
    )


def test_health_reports_a_durable_history_backend(sqlite_gateway: str) -> None:
    with TestClient(app) as client:
        history = client.get("/health").json()["history"]
    assert history["backend"] == "sqlite"
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


def test_state_report_and_progress_survive_a_gateway_restart(sqlite_gateway: str) -> None:
    with TestClient(app) as client:
        created = client.post(f"{API_PREFIX}/investigations?wait=true", json=PAYLOAD).json()
        investigation_id = created["investigation_id"]
        assert created["state"]["status"] == "completed"
        before = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()

    restart_gateway(sqlite_gateway)

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


def test_stored_reports_do_not_keep_raw_account_identifiers(sqlite_gateway: str) -> None:
    with TestClient(app) as client:
        created = client.post(f"{API_PREFIX}/investigations?wait=true", json=PAYLOAD).json()
        investigation_id = created["investigation_id"]
        assert created["state"]["request"]["account_ids"] == ["123456789012"]

    restart_gateway(sqlite_gateway)

    with TestClient(app) as client:
        state = client.get(f"{API_PREFIX}/investigations/{investigation_id}").json()
        report = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()
    for account_id in state["request"]["account_ids"] + report["request"]["account_ids"]:
        assert account_id.startswith("acct-")
        assert account_id != "123456789012"
