"""Worker API tests.

Each provider specialist runs independently behind its HTTP contract, with stub
agents, no network, and no cloud credentials.
"""

from __future__ import annotations

from datetime import date

import pytest
from cloudcause_anomaly import compare_provider
from cloudcause_aws import AwsInvestigator
from cloudcause_azure import AzureInvestigator
from cloudcause_contracts import (
    InvestigationRequest,
    Provider,
    ProviderTask,
    Settings,
    WorkerRequest,
    WorkerResponse,
)
from cloudcause_orchestrator import GcpInvestigator
from cloudcause_providers import get_data_provider
from cloudcause_worker_core import create_worker_app
from fastapi.testclient import TestClient

CURRENT = (date(2026, 7, 13), date(2026, 7, 19))
BASELINE = (date(2026, 7, 6), date(2026, 7, 12))

INVESTIGATORS = {
    "aws": (AwsInvestigator, "aws-strands"),
    "azure": (AzureInvestigator, "microsoft-agent-framework"),
    "gcp": (GcpInvestigator, "google-adk"),
}


def build_request() -> InvestigationRequest:
    return InvestigationRequest(
        providers=["aws", "azure", "gcp"],
        start_date=CURRENT[0],
        end_date=CURRENT[1],
        comparison_start_date=BASELINE[0],
        comparison_end_date=BASELINE[1],
        question="Why did spending increase?",
    )


async def worker_request(provider: Provider, settings: Settings) -> WorkerRequest:
    request = build_request()
    adapter = get_data_provider(provider, settings)
    costs = await adapter.get_costs([request.current_period, request.baseline_period])
    comparison = compare_provider(
        costs.items, provider, request.current_period, request.baseline_period, settings.analytics
    )
    return WorkerRequest(
        investigation_id="inv-test-000001",
        provider=provider,
        request=request,
        task=ProviderTask(
            provider=provider,
            question="Explain the increase",
            candidate_ids=[c.candidate_id for c in comparison.candidates],
            max_findings=5,
        ),
        candidates=comparison.candidates,
    )


@pytest.fixture(params=list(INVESTIGATORS))
def provider(request: pytest.FixtureRequest) -> Provider:
    return request.param


def client_for(provider: Provider, settings: Settings) -> TestClient:
    investigator_class, _ = INVESTIGATORS[provider]
    return TestClient(create_worker_app(investigator_class(settings), f"test-{provider}"))


def test_health_reports_mode_and_read_only(provider: Provider, settings: Settings) -> None:
    with client_for(provider, settings) as client:
        payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["provider"] == provider
    assert payload["framework"] == INVESTIGATORS[provider][1]
    assert payload["agent_mode"] == "stub"
    assert payload["data_mode"] == "fixtures"
    assert payload["read_only"] is True
    assert payload["contract_version"] == "v1"


def test_capabilities_expose_no_mutating_tools(provider: Provider, settings: Settings) -> None:
    with client_for(provider, settings) as client:
        payload = client.get("/capabilities").json()
    assert payload["mutating_tools"] == []
    assert payload["read_only"] is True
    assert payload["playbooks"]


async def test_investigate_returns_the_worker_contract(provider: Provider, settings: Settings) -> None:
    payload = (await worker_request(provider, settings)).model_dump(mode="json")
    with client_for(provider, settings) as client:
        response = client.post("/investigate", json=payload)
    assert response.status_code == 200
    worker_response = WorkerResponse.model_validate(response.json())
    assert worker_response.provider == provider
    assert worker_response.status == "ok"
    assert worker_response.investigation_id == "inv-test-000001"
    assert worker_response.agent_mode == "stub"
    assert worker_response.findings, "each provider fixture plants at least one cause"
    assert worker_response.sources
    assert worker_response.applied_rule_ids
    for finding in worker_response.findings:
        assert finding.requires_human_approval is True
        assert 0.0 <= finding.confidence <= 1.0
        assert finding.evidence


async def test_provider_mismatch_returns_a_common_error_envelope(settings: Settings) -> None:
    payload = (await worker_request("aws", settings)).model_dump(mode="json")
    payload["provider"] = "azure"
    with client_for("aws", settings) as client:
        response = client.post("/investigate", json=payload)
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "provider_mismatch"
    assert body["investigation_id"] == "inv-test-000001"


def test_malformed_request_is_rejected(settings: Settings) -> None:
    with client_for("aws", settings) as client:
        response = client.post("/investigate", json={"investigation_id": "x"})
    assert response.status_code == 422


async def test_worker_timeout_returns_504(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    import asyncio

    payload = (await worker_request("aws", settings)).model_dump(mode="json")
    slow_settings = settings.with_overrides(worker_timeout_seconds=0.01)
    investigator = AwsInvestigator(slow_settings)

    async def slow_investigate(_request):
        await asyncio.sleep(1.0)

    monkeypatch.setattr(investigator, "investigate", slow_investigate)
    with TestClient(create_worker_app(investigator, "test-slow")) as client:
        response = client.post("/investigate", json=payload)
    assert response.status_code == 504
    assert response.json()["error"] == "worker_timeout"


async def test_a_broken_data_source_is_reported_not_raised(settings: Settings) -> None:
    request = await worker_request("aws", settings)
    request.request.scenario_id = "does-not-exist"
    response = await AwsInvestigator(settings).investigate(request)
    assert response.status == "failed"
    assert "UnknownScenarioError" in response.message
