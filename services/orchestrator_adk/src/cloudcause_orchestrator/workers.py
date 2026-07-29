"""Worker clients.

``inprocess`` imports the sibling services directly, which is what the offline
default and the test suite use. ``http`` calls them over their versioned HTTP
contract, which is how Docker Compose and any real deployment run.
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx
from cloudcause_contracts import Provider, Settings, WorkerRequest, WorkerResponse
from cloudcause_knowledge import KnowledgeStore
from cloudcause_worker_core import ProviderInvestigator


class WorkerClient(Protocol):
    provider: Provider

    async def investigate(self, request: WorkerRequest) -> WorkerResponse: ...

    async def health(self) -> dict[str, object]: ...


class InProcessWorkerClient:
    """Runs a provider specialist inside this process."""

    def __init__(self, investigator: ProviderInvestigator) -> None:
        self.investigator = investigator
        self.provider = investigator.provider

    async def investigate(self, request: WorkerRequest) -> WorkerResponse:
        return await self.investigator.investigate(request)

    async def health(self) -> dict[str, object]:
        return {"status": "ok", "transport": "inprocess", **self.investigator.capabilities()}


class HttpWorkerClient:
    """Calls a provider specialist over its versioned HTTP contract."""

    def __init__(self, provider: Provider, base_url: str, timeout: float) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def investigate(self, request: WorkerRequest) -> WorkerResponse:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/investigate", json=request.model_dump(mode="json"))
                if response.status_code >= 400:
                    detail = response.json().get("detail", response.text)
                    return WorkerResponse(
                        investigation_id=request.investigation_id,
                        provider=self.provider,
                        status="failed",
                        message=f"worker returned {response.status_code}: {detail}",
                        duration_seconds=round(time.perf_counter() - started, 3),
                    )
                return WorkerResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as error:
            return WorkerResponse(
                investigation_id=request.investigation_id,
                provider=self.provider,
                status="failed",
                message=f"{type(error).__name__}: {error}",
                duration_seconds=round(time.perf_counter() - started, 3),
            )

    async def health(self) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return {"transport": "http", "url": self.base_url, **response.json()}
        except httpx.HTTPError as error:
            return {"status": "unreachable", "transport": "http", "url": self.base_url, "error": str(error)}


def build_worker_clients(
    settings: Settings, knowledge: KnowledgeStore | None = None
) -> dict[Provider, WorkerClient]:
    """GCP always runs in-process (this service is the GCP specialist)."""

    from .gcp_investigator import GcpInvestigator

    clients: dict[Provider, WorkerClient] = {
        "gcp": InProcessWorkerClient(GcpInvestigator(settings, knowledge))
    }
    if settings.worker_mode == "http":
        clients["aws"] = HttpWorkerClient("aws", settings.aws_worker_url, settings.worker_timeout_seconds)
        clients["azure"] = HttpWorkerClient(
            "azure", settings.azure_worker_url, settings.worker_timeout_seconds
        )
        return clients

    from cloudcause_aws import AwsInvestigator
    from cloudcause_azure import AzureInvestigator

    clients["aws"] = InProcessWorkerClient(AwsInvestigator(settings, knowledge))
    clients["azure"] = InProcessWorkerClient(AzureInvestigator(settings, knowledge))
    return clients
