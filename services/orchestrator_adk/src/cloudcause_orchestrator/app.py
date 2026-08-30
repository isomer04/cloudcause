"""ADK orchestrator service.

    uv run cloudcause-orchestrator      # http://127.0.0.1:8100

Used when the gateway runs with ``CLOUDCAUSE_ORCHESTRATOR_MODE=http``. In the
offline default the gateway calls the same ``Orchestrator`` class in-process.
"""

from __future__ import annotations

import os

from cloudcause_contracts import (
    ErrorResponse,
    InvestigationReport,
    InvestigationRequest,
    resolve_agent_mode,
)
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .orchestrator import Orchestrator

app = FastAPI(title="CloudCause orchestrator (Google ADK)", version="0.1.0")
app.state.orchestrator = Orchestrator()


@app.get("/health")
async def health() -> dict[str, object]:
    return await app.state.orchestrator.health()


@app.post("/investigate", response_model=InvestigationReport)
async def investigate(request: InvestigationRequest) -> InvestigationReport | JSONResponse:
    orchestrator: Orchestrator = app.state.orchestrator
    request = resolve_agent_mode(request, orchestrator.settings.agent_mode)
    try:
        return await orchestrator.run(request)
    except Exception as error:  # noqa: BLE001 - returned as a common error envelope
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="investigation_failed",
                detail=f"{type(error).__name__}: {error}",
            ).model_dump(mode="json"),
        )


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    uvicorn.run(
        "cloudcause_orchestrator.app:app",
        host=os.environ.get("CLOUDCAUSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLOUDCAUSE_ORCHESTRATOR_PORT", "8100")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
