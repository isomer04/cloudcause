"""How the gateway reaches the ADK orchestrator.

Same contract either way, so the gateway (and therefore both UIs) never care
whether the orchestrator is in-process or a separate service.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from cloudcause_contracts import InvestigationReport, InvestigationRequest, Settings


class OrchestratorLink(Protocol):
    async def run(
        self, request: InvestigationRequest, investigation_id: str, emit: Any
    ) -> InvestigationReport: ...

    async def health(self) -> dict[str, Any]: ...


class InProcessOrchestrator:
    """Offline default: one process, full progress streaming."""

    transport = "inprocess"

    def __init__(self, settings: Settings) -> None:
        from cloudcause_orchestrator import Orchestrator

        self.orchestrator = Orchestrator(settings)

    async def run(
        self, request: InvestigationRequest, investigation_id: str, emit: Any
    ) -> InvestigationReport:
        return await self.orchestrator.run(request, investigation_id=investigation_id, emit=emit)

    async def health(self) -> dict[str, Any]:
        return {"transport": self.transport, **await self.orchestrator.health()}


class HttpOrchestrator:
    """Calls the ADK orchestrator service over HTTP."""

    transport = "http"

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.orchestrator_url.rstrip("/")
        self.timeout = max(settings.worker_timeout_seconds * 3, 120.0)

    async def run(
        self, request: InvestigationRequest, investigation_id: str, emit: Any
    ) -> InvestigationReport:
        emit("investigate", f"Delegating to the ADK orchestrator at {self.base_url}")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/investigate", json=request.model_dump(mode="json")
            )
            response.raise_for_status()
            report = InvestigationReport.model_validate(response.json())
        for status in report.provider_statuses:
            emit(
                "investigate",
                f"{status.provider}: {status.status}, {status.finding_count} finding(s)",
                provider=status.provider,
                status="completed",
            )
        emit("report", "Investigation complete", status="completed", findings=len(report.findings))
        return report

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return {"transport": self.transport, "url": self.base_url, **response.json()}
        except httpx.HTTPError as error:
            return {
                "transport": self.transport,
                "url": self.base_url,
                "status": "unreachable",
                "error": str(error),
            }


def build_orchestrator_link(settings: Settings) -> OrchestratorLink:
    if settings.orchestrator_mode == "http":
        return HttpOrchestrator(settings)
    return InProcessOrchestrator(settings)
