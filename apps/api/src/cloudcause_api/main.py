"""CloudCause FastAPI gateway.

    uv run cloudcause-api      # http://127.0.0.1:8000/docs

Every investigation, report, and provenance field the UI shows comes from here.
The frontend calls exactly these endpoints, so a UI swap is not a rewrite.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

from cloudcause_contracts import (
    CloudCauseModel,
    InvestigationReport,
    InvestigationRequest,
    InvestigationState,
    ProgressEvent,
    Settings,
    get_settings,
    report_headline,
    report_to_markdown,
)
from cloudcause_providers import available_scenarios, get_scenario
from cloudcause_worker_core import JobStore, build_job_store
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse

from . import datasets
from .orchestration import build_orchestrator_link

API_PREFIX = "/api/v1"
CONTRACT_VERSION = "v1"

settings = get_settings()
jobs: JobStore = build_job_store(settings)
link = build_orchestrator_link(settings)
datasets.configure(settings)


def configure(new_settings: Settings | None = None) -> Settings:
    """Rebuild gateway state from settings.

    The process entry point calls this implicitly at import. Tests call it to
    point the gateway at a temporary history database and to restore the default
    afterwards, which keeps the module-level wiring honest instead of mocked.
    """

    global settings, jobs, link
    settings = new_settings or get_settings()
    jobs = build_job_store(settings)
    link = build_orchestrator_link(settings)
    datasets.configure(settings)
    return settings

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own the ingest thread pool for the life of the process."""

    try:
        yield
    finally:
        datasets.shutdown()


app = FastAPI(
    title="CloudCause gateway",
    version="0.1.0",
    description="Evidence-grounded multi-cloud cost investigation. Read-only.",
    lifespan=lifespan,
)
app.include_router(datasets.router)


class ScenarioSummary(CloudCauseModel):
    id: str
    title: str
    providers: list[str]
    category: str
    suggested_request: InvestigationRequest


class InvestigationCreated(CloudCauseModel):
    investigation_id: str
    status: str
    headline: str = ""
    state: InvestigationState


def _default_periods() -> tuple[date, date, date, date]:
    end = date(2026, 7, 19)
    start = end - timedelta(days=6)
    baseline_end = start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=6)
    return start, end, baseline_start, baseline_end


def _suggested_request(scenario_id: str) -> InvestigationRequest:
    spec = None if scenario_id == "default" else get_scenario(settings.scenario_root, scenario_id)
    if spec is not None:
        return spec.to_request()
    start, end, baseline_start, baseline_end = _default_periods()
    return InvestigationRequest(
        providers=["aws", "azure", "gcp"],
        start_date=start,
        end_date=end,
        comparison_start_date=baseline_start,
        comparison_end_date=baseline_end,
        question="Why did our cloud spending increase last week?",
        scenario_id="default",
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "contract_version": CONTRACT_VERSION,
        "data_mode": settings.data_mode,
        "agent_mode": settings.agent_mode,
        "orchestrator": await link.health(),
        "history": jobs.describe(),
        "datasets": datasets.describe(),
        "read_only": True,
    }


@app.get(f"{API_PREFIX}/scenarios", response_model=list[ScenarioSummary])
async def scenarios() -> list[ScenarioSummary]:
    return [
        ScenarioSummary(
            id=entry["id"],
            title=entry["title"],
            providers=entry["providers"].split(","),
            category=entry["category"],
            suggested_request=_suggested_request(entry["id"]),
        )
        for entry in available_scenarios(settings)
    ]


async def _run_job(investigation_id: str, request: InvestigationRequest) -> None:
    job = jobs.get(investigation_id)
    if job is None:  # pragma: no cover - defensive
        return
    job.mark_running()
    try:
        report = await link.run(request, investigation_id, job.emit)
        job.state.report = report
        job.set_provider_statuses(report.provider_statuses)
        job.finish()
    except Exception as error:  # noqa: BLE001 - surfaced to the client as job failure
        job.emit("report", f"{type(error).__name__}: {error}", status="failed")
        job.finish(error=f"{type(error).__name__}: {error}")


@app.post(f"{API_PREFIX}/investigations", response_model=InvestigationCreated)
async def create_investigation(
    request: InvestigationRequest,
    background: BackgroundTasks,
    wait: bool = Query(False, description="Run synchronously and return the finished report"),
) -> InvestigationCreated:
    if request.dataset_id:
        # An unsealed, expired, or unreachable dataset must be refused here rather
        # than half way through a run, where three readers would disagree.
        datasets.require_runnable_dataset(request.dataset_id)
    investigation_id = f"inv-{uuid.uuid4().hex[:12]}"
    job = jobs.create(investigation_id, request)
    jobs.prune()
    if wait:
        await _run_job(investigation_id, request)
    else:
        background.add_task(_run_job, investigation_id, request)
    headline = report_headline(job.state.report) if job.state.report else ""
    return InvestigationCreated(
        investigation_id=investigation_id,
        status=job.state.status,
        headline=headline,
        state=job.state,
    )


@app.get(f"{API_PREFIX}/investigations", response_model=list[InvestigationState])
async def list_investigations() -> list[InvestigationState]:
    return jobs.list()


def _job_or_404(investigation_id: str):
    job = jobs.get(investigation_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown investigation {investigation_id}")
    return job


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}", response_model=InvestigationState)
async def get_investigation(investigation_id: str) -> InvestigationState:
    return _job_or_404(investigation_id).state


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}/events")
async def stream_events(investigation_id: str) -> StreamingResponse:
    job = _job_or_404(investigation_id)

    async def event_source() -> AsyncIterator[bytes]:
        async for event in job.stream():
            yield f"data: {event.model_dump_json()}\n\n".encode()
        payload = json.dumps({"stage": "closed", "status": job.state.status})
        yield f"event: close\ndata: {payload}\n\n".encode()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}/progress", response_model=list[ProgressEvent])
async def get_progress(investigation_id: str) -> list[ProgressEvent]:
    return list(_job_or_404(investigation_id).events)


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}/report", response_model=InvestigationReport)
async def get_report(investigation_id: str) -> InvestigationReport:
    job = _job_or_404(investigation_id)
    if job.state.report is None:
        raise HTTPException(
            status_code=409,
            detail=f"investigation {investigation_id} is {job.state.status}; no report yet",
        )
    return job.state.report


@app.get(
    f"{API_PREFIX}/investigations/{{investigation_id}}/report.md",
    response_class=PlainTextResponse,
)
async def get_report_markdown(investigation_id: str) -> str:
    return report_to_markdown(await get_report(investigation_id))


async def _wait_for(investigation_id: str, timeout: float) -> InvestigationState:
    job = _job_or_404(investigation_id)
    deadline = asyncio.get_running_loop().time() + timeout
    while job.state.status in ("queued", "running"):
        if asyncio.get_running_loop().time() > deadline:
            break
        await asyncio.sleep(0.05)
    return job.state


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}/wait", response_model=InvestigationState)
async def wait_for_investigation(
    investigation_id: str, timeout: float = Query(120.0, ge=0.1, le=600.0)
) -> InvestigationState:
    return await _wait_for(investigation_id, timeout)


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    uvicorn.run(
        "cloudcause_api.main:app",
        host=os.environ.get("CLOUDCAUSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLOUDCAUSE_API_PORT", "8000")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
