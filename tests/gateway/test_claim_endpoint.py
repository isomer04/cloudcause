"""The private Cloud Tasks delivery target must execute a job at most once."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cloudcause_api import app, main
from cloudcause_contracts import InvestigationRequest, Settings
from fastapi.testclient import TestClient


def _stub_request() -> InvestigationRequest:
    return InvestigationRequest.model_validate(
        {
            "providers": ["aws"],
            "start_date": "2026-07-13",
            "end_date": "2026-07-19",
            "comparison_start_date": "2026-07-06",
            "comparison_end_date": "2026-07-12",
            "agent_mode": "stub",
        }
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    original = main.settings
    main.configure(Settings.from_env({}))
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        main.configure(original)


def test_first_claim_runs_the_job_a_duplicate_delivery_is_a_no_op(client: TestClient) -> None:
    job = main.jobs.create("inv-claim-0001", _stub_request())
    assert job.state.status == "queued"

    first = client.post(f"/internal/investigations/{job.investigation_id}/claim")
    assert first.status_code == 200
    assert first.json()["claimed"] == "true"

    # A second, duplicate Cloud Tasks delivery for the same investigation must
    # not re-run it, regardless of whether the first run has finished yet.
    duplicate = client.post(f"/internal/investigations/{job.investigation_id}/claim")
    assert duplicate.status_code == 200
    assert duplicate.json()["claimed"] == "false"


def test_claim_of_an_unknown_investigation_is_404(client: TestClient) -> None:
    response = client.post("/internal/investigations/inv-does-not-exist/claim")
    assert response.status_code == 404


def test_a_failed_enqueue_leaves_no_queued_orphan(client: TestClient) -> None:
    """Nothing will ever claim a job Cloud Tasks refused, so it must not stay queued."""

    class _RefusingDispatcher:
        async def enqueue_claim(self, investigation_id: str) -> None:
            raise PermissionError("cloudtasks.tasks.create denied")

    original = main.tasks_dispatcher
    main.tasks_dispatcher = _RefusingDispatcher()
    try:
        payload = _stub_request().model_dump(mode="json") | {"agent_mode": "live"}
        with TestClient(app, raise_server_exceptions=False) as failing_client:
            response = failing_client.post("/api/v1/investigations", json=payload)
        assert response.status_code == 500
    finally:
        main.tasks_dispatcher = original

    states = main.jobs.list()
    assert states, "the job was created before dispatch, so it must still be listed"
    assert states[0].status == "failed"
    assert "dispatch_failed" in (states[0].error or "")
