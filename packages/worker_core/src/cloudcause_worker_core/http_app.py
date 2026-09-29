"""Worker HTTP surface shared by the Strands and MAF services.

Independently deployable services, one versioned contract, common error
envelope. Nothing here can modify a cloud resource.
"""

from __future__ import annotations

import asyncio

from cloudcause_contracts import ErrorResponse, WorkerRequest, WorkerResponse
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .engine import ProviderInvestigator

CONTRACT_VERSION = "v1"


def create_worker_app(investigator: ProviderInvestigator, title: str) -> FastAPI:
    app = FastAPI(title=title, version="0.1.0")
    app.state.investigator = investigator

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "provider": investigator.provider,
            "framework": investigator.framework,
            "default_agent_mode": investigator.settings.agent_mode,
            "agent_mode_selection": "per_investigation",
            "supported_agent_modes": ["live", "stub"],
            "live_agents_available": investigator.settings.live_agents_available,
            "data_mode": investigator.settings.data_mode,
            "contract_version": CONTRACT_VERSION,
            "read_only": True,
        }

    @app.get("/capabilities")
    async def capabilities() -> dict[str, object]:
        return investigator.capabilities()

    @app.post("/investigate", response_model=WorkerResponse)
    async def investigate(request: WorkerRequest) -> WorkerResponse | JSONResponse:
        if request.provider != investigator.provider:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error="provider_mismatch",
                    detail=f"this worker serves {investigator.provider}, not {request.provider}",
                    investigation_id=request.investigation_id,
                ).model_dump(mode="json"),
            )
        timeout = investigator.settings.worker_timeout_seconds
        try:
            return await asyncio.wait_for(investigator.investigate(request), timeout=timeout)
        except TimeoutError:
            return JSONResponse(
                status_code=504,
                content=ErrorResponse(
                    error="worker_timeout",
                    detail=f"{investigator.provider} investigation exceeded {timeout}s",
                    investigation_id=request.investigation_id,
                    provider=investigator.provider,
                    retryable=True,
                ).model_dump(mode="json"),
            )

    return app
