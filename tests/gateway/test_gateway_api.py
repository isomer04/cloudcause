"""Gateway contract tests.

These carry the assurance for the UI: the frontend calls exactly these endpoints
and computes nothing of its own.
"""

from __future__ import annotations

import json

from cloudcause_api import API_PREFIX, app
from cloudcause_contracts import InvestigationReport, InvestigationRequest
from fastapi.testclient import TestClient


def run_investigation(client: TestClient, **overrides) -> dict:
    payload = {
        "providers": ["aws", "azure", "gcp"],
        "start_date": "2026-07-13",
        "end_date": "2026-07-19",
        "comparison_start_date": "2026-07-06",
        "comparison_end_date": "2026-07-12",
        "question": "Why did our cloud spending increase last week?",
        "scenario_id": "default",
    }
    payload.update(overrides)
    response = client.post(f"{API_PREFIX}/investigations?wait=true", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_health_exposes_mode_and_orchestrator_transport() -> None:
    with TestClient(app) as client:
        payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["data_mode"] == "fixtures"
    assert payload["agent_mode"] == "stub"
    assert payload["read_only"] is True
    assert payload["orchestrator"]["transport"] == "inprocess"
    assert set(payload["orchestrator"]["workers"]) == {"aws", "azure", "gcp"}


def test_scenarios_include_the_demo_and_every_seeded_case() -> None:
    with TestClient(app) as client:
        scenarios = client.get(f"{API_PREFIX}/scenarios").json()
    ids = [entry["id"] for entry in scenarios]
    assert ids[0] == "default"
    assert "aws-nat-gateway-misroute" in ids
    assert "gcp-compromised-api-key" in ids
    for entry in scenarios:
        request = InvestigationRequest.model_validate(entry["suggested_request"])
        assert request.providers


def test_synchronous_investigation_returns_a_finished_state() -> None:
    with TestClient(app) as client:
        created = run_investigation(client)
        assert created["state"]["status"] == "completed"
        investigation_id = created["investigation_id"]

        state = client.get(f"{API_PREFIX}/investigations/{investigation_id}").json()
        assert state["status"] == "completed"
        assert {status["provider"] for status in state["provider_statuses"]} == {"aws", "azure", "gcp"}

        report = InvestigationReport.model_validate(
            client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()
        )
        assert report.investigation_id == investigation_id
        assert report.contract_version == "v1"
        assert report.findings
        assert report.knowledge is not None and report.knowledge.focus_version == "1.4"
        assert report.data_through() is not None

        markdown = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report.md").text
        assert "# CloudCause investigation" in markdown
        assert "Evidence ID" in markdown
        assert "read-only" in markdown.lower()

        progress = client.get(f"{API_PREFIX}/investigations/{investigation_id}/progress").json()
        stages = [event["stage"] for event in progress]
        for stage in ("normalize", "analyze", "plan", "investigate", "validate", "reconcile", "report"):
            assert stage in stages


def test_progress_stream_is_server_sent_events() -> None:
    with TestClient(app) as client:
        created = run_investigation(client)
        investigation_id = created["investigation_id"]
        with client.stream("GET", f"{API_PREFIX}/investigations/{investigation_id}/events") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            body = "".join(chunk for chunk in stream.iter_text())
    payloads = [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert payloads
    assert payloads[0]["investigation_id"] == investigation_id
    assert payloads[-1]["status"] in ("completed", "progress")


def test_single_provider_scenario_runs_without_the_others() -> None:
    with TestClient(app) as client:
        created = run_investigation(
            client,
            providers=["azure"],
            scenario_id="azure-functions-retry-loop",
            question="Why did Azure spending increase?",
        )
        report = client.get(
            f"{API_PREFIX}/investigations/{created['investigation_id']}/report"
        ).json()
    assert [status["provider"] for status in report["provider_statuses"]] == ["azure"]
    assert report["findings"][0]["category"] == "functions_retry_loop"


def test_unknown_investigation_and_missing_report_are_handled() -> None:
    with TestClient(app) as client:
        assert client.get(f"{API_PREFIX}/investigations/inv-nope").status_code == 404
        assert client.get(f"{API_PREFIX}/investigations/inv-nope/report").status_code == 404


def test_invalid_request_is_rejected_by_the_contract() -> None:
    with TestClient(app) as client:
        response = client.post(
            f"{API_PREFIX}/investigations",
            json={
                "providers": [],
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "comparison_start_date": "2026-07-06",
                "comparison_end_date": "2026-07-12",
            },
        )
    assert response.status_code == 422


def test_investigation_history_is_listed() -> None:
    with TestClient(app) as client:
        run_investigation(client)
        history = client.get(f"{API_PREFIX}/investigations").json()
    assert history
    assert history[0]["status"] in ("completed", "running", "queued")
