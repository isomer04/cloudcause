"""HTTP transport tests.

Proves the orchestrator can drive the provider specialists over their versioned
HTTP contract (the Docker Compose layout), not only in-process.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from cloudcause_aws.app import app as aws_app
from cloudcause_azure.app import app as azure_app
from cloudcause_contracts import InvestigationRequest, ProviderTask, Settings, WorkerRequest
from cloudcause_orchestrator import HttpWorkerClient, Orchestrator, build_worker_clients

APPS = {"aws": aws_app, "azure": azure_app}


@pytest.fixture
def asgi_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route httpx traffic to the worker apps in-process."""

    real_client = httpx.AsyncClient

    class RoutingClient(real_client):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, **kwargs)

        async def request(self, method: str, url, **kwargs):  # type: ignore[override]
            target = str(url)
            provider = "aws" if ":8101" in target else "azure" if ":8102" in target else None
            if provider is None:
                return await super().request(method, url, **kwargs)
            path = httpx.URL(target).path
            async with real_client(
                transport=httpx.ASGITransport(app=APPS[provider]), base_url=f"http://{provider}"
            ) as client:
                return await client.request(method, path, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", RoutingClient)


def build_worker_request(provider: str) -> WorkerRequest:
    request = InvestigationRequest(
        providers=[provider],  # type: ignore[list-item]
        start_date=date(2026, 7, 13),
        end_date=date(2026, 7, 19),
        comparison_start_date=date(2026, 7, 6),
        comparison_end_date=date(2026, 7, 12),
        question="Why did spending increase?",
    )
    return WorkerRequest(
        investigation_id="inv-http-000001",
        provider=provider,  # type: ignore[arg-type]
        request=request,
        task=ProviderTask(provider=provider, question="Explain the increase"),  # type: ignore[arg-type]
    )


async def test_http_worker_client_reaches_the_worker(asgi_httpx: None) -> None:
    client = HttpWorkerClient("aws", "http://127.0.0.1:8101", 30.0)
    health = await client.health()
    assert health["transport"] == "http"
    assert health["provider"] == "aws"

    response = await client.investigate(build_worker_request("aws"))
    assert response.status == "ok"
    assert response.provider == "aws"


async def test_orchestrator_runs_workers_over_http(asgi_httpx: None, settings: Settings) -> None:
    http_settings = settings.with_overrides(worker_mode="http")
    orchestrator = Orchestrator(http_settings, workers=build_worker_clients(http_settings))
    assert isinstance(orchestrator.workers["azure"], HttpWorkerClient)

    request = InvestigationRequest(
        providers=["aws", "azure", "gcp"],
        start_date=date(2026, 7, 13),
        end_date=date(2026, 7, 19),
        comparison_start_date=date(2026, 7, 6),
        comparison_end_date=date(2026, 7, 12),
        question="Why did our cloud spending increase last week?",
    )
    report = await orchestrator.run(request)
    assert {status.provider for status in report.provider_statuses} == {"aws", "azure", "gcp"}
    assert all(status.status == "ok" for status in report.provider_statuses)
    assert {finding.provider for finding in report.findings} == {"aws", "azure", "gcp"}


async def test_unreachable_worker_degrades_instead_of_crashing(settings: Settings) -> None:
    client = HttpWorkerClient("aws", "http://127.0.0.1:1", 0.5)
    response = await client.investigate(build_worker_request("aws"))
    assert response.status == "failed"
    assert response.findings == []
    health = await client.health()
    assert health["status"] == "unreachable"
