"""CloudCause FastAPI gateway.

    uv run cloudcause-api      # http://127.0.0.1:8000/docs

Every investigation, report, and provenance field the UI shows comes from here.
The frontend calls exactly these endpoints, so a UI swap is not a rewrite.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta

from cloudcause_contracts import (
    GatewayHealth,
    InvestigationCreated,
    InvestigationReport,
    InvestigationRequest,
    InvestigationState,
    ProgressEvent,
    ScenarioSummary,
    Settings,
    get_settings,
    report_headline,
    report_to_markdown,
    resolve_agent_mode,
)
from cloudcause_providers import available_scenarios, get_scenario
from cloudcause_rate_limit import AdmissionGuard, RateLimiter, RateLimitExceeded, build_rate_limiter
from cloudcause_worker_core import (
    InvestigationJob,
    JobStore,
    LiveCapacityTimeoutError,
    LiveInvestigationCapacity,
    SqlJobStore,
    build_job_store,
)
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import datasets
from .client_ip import resolve_peer_ip
from .orchestration import build_orchestrator_link
from .rendering_pdf import render_report_pdf
from .tasks import CloudTasksDispatcher

API_PREFIX = "/api/v1"
CONTRACT_VERSION = "v1"

logger = logging.getLogger("cloudcause.rate_limit")


def _build_jobs(config: Settings) -> JobStore:
    store = build_job_store(config)
    if isinstance(store, SqlJobStore):
        store.enable_async_persistence()
    return store


def _build_admission(config: Settings, limiter: RateLimiter) -> AdmissionGuard:
    if config.live_rate_limit_enabled and config.rate_limit_backend == "redis" and not config.id_hash_salt:
        # Client keys are HMACed so the backend never holds anything that
        # identifies a real address -- but an empty salt makes the digest a
        # plain hash of the IP, and IPv4 is small enough to enumerate. That
        # only matters once the keys land in a store outside this process.
        raise ValueError(
            "CLOUDCAUSE_RATE_LIMIT_BACKEND=redis requires CLOUDCAUSE_ID_HASH_SALT: "
            "without it, admission bucket keys in Redis are reversible to client IPs"
        )
    return AdmissionGuard(
        limiter,
        enabled=config.live_rate_limit_enabled,
        client_per_hour=config.live_investigations_per_hour,
        client_burst=config.live_investigation_burst,
        global_per_minute=config.global_live_starts_per_minute,
        id_hash_salt=config.id_hash_salt,
    )


def _build_tasks_dispatcher(config: Settings) -> CloudTasksDispatcher | None:
    return CloudTasksDispatcher(config) if config.dispatch_mode == "cloud_tasks" else None


def _close_rate_limiter(limiter: RateLimiter) -> None:
    """Best-effort release of a replaced limiter's connections.

    Only the Redis backend holds anything to release; the in-memory one has no
    ``aclose``. ``configure`` is synchronous and is called both at import (no
    running loop) and from tests (sometimes inside one), so both cases are
    handled rather than assuming either.
    """

    aclose = getattr(limiter, "aclose", None)
    if aclose is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(aclose())
        return
    loop.create_task(aclose())


settings = get_settings()
jobs: JobStore = _build_jobs(settings)
link = build_orchestrator_link(settings)
live_capacity = LiveInvestigationCapacity(
    settings.max_concurrent_live_investigations, settings.live_queue_timeout_seconds
)
rate_limiter: RateLimiter = build_rate_limiter(settings)
admission = _build_admission(settings, rate_limiter)
tasks_dispatcher = _build_tasks_dispatcher(settings)
#: Local idempotency guard for the /internal claim endpoint. Correct for a
#: single worker replica; a multi-replica cloud_tasks deployment additionally
#: needs the job store's own compare-and-swap, which is future work -- Cloud
#: Tasks dispatch is opt-in and has not been verified against real GCP infra.
_claimed_investigations: set[str] = set()
datasets.configure(settings)


def configure(new_settings: Settings | None = None) -> Settings:
    """Rebuild gateway state from settings.

    The process entry point calls this implicitly at import. Tests call it to
    point the gateway at a temporary history database and to restore the default
    afterwards, which keeps the module-level wiring honest instead of mocked.
    """

    global settings, jobs, link, live_capacity, rate_limiter, admission, tasks_dispatcher
    settings = new_settings or get_settings()
    jobs = _build_jobs(settings)
    link = build_orchestrator_link(settings)
    live_capacity = LiveInvestigationCapacity(
        settings.max_concurrent_live_investigations, settings.live_queue_timeout_seconds
    )
    _close_rate_limiter(rate_limiter)
    rate_limiter = build_rate_limiter(settings)
    admission = _build_admission(settings, rate_limiter)
    tasks_dispatcher = _build_tasks_dispatcher(settings)
    _claimed_investigations.clear()
    datasets.configure(settings)
    return settings

_EVICTION_INTERVAL_SECONDS = 120.0
_EVICTION_INACTIVE_SECONDS = 300.0


async def _evict_inactive_rate_limit_buckets() -> None:
    """Periodic sweep so arbitrary client IPs cannot grow the bucket dict forever.

    A no-op against the Redis backend (server-side TTLs already expire idle
    buckets there); only the in-memory backend needs this loop.
    """

    while True:
        await asyncio.sleep(_EVICTION_INTERVAL_SECONDS)
        try:
            await rate_limiter.evict_inactive(_EVICTION_INACTIVE_SECONDS)
        except Exception:  # noqa: BLE001 - a sweep failure must not take the gateway down
            logger.warning("rate limiter eviction sweep failed", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own the ingest thread pool and the rate-limit eviction sweep for the life of the process."""

    eviction_task = asyncio.create_task(_evict_inactive_rate_limit_buckets())
    try:
        yield
    finally:
        eviction_task.cancel()
        try:
            await eviction_task
        except asyncio.CancelledError:
            pass
        aclose = getattr(rate_limiter, "aclose", None)
        if aclose is not None:
            await aclose()
        datasets.shutdown()


app = FastAPI(
    title="CloudCause gateway",
    version="0.1.0",
    description="Evidence-grounded multi-cloud cost investigation. Read-only.",
    lifespan=lifespan,
)
app.include_router(datasets.router)


@app.exception_handler(RequestValidationError)
async def request_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    """Return schema failures without reflecting submitted values to the client."""

    details = [
        {key: value for key, value in item.items() if key not in {"input", "ctx"}}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": details})


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded(_: Request, error: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error.to_response_body(),
        headers={"Retry-After": str(max(1, round(error.retry_after_seconds)))},
    )


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


@app.get("/health", response_model=GatewayHealth)
async def health() -> GatewayHealth:
    return GatewayHealth(
        status="ok",
        contract_version=CONTRACT_VERSION,
        data_mode=settings.data_mode,
        default_agent_mode=settings.agent_mode,
        agent_mode_selection="per_investigation",
        supported_agent_modes=["live", "stub"],
        live_agents_available=settings.live_agents_available,
        orchestrator=await link.health(),
        history=jobs.describe(),
        datasets=await datasets.describe(),
        rate_limiter=await _describe_rate_limiter(),
        read_only=True,
    )


async def _describe_rate_limiter() -> dict[str, object]:
    """Non-secret limiter status, including live backend reachability.

    Never exposes bucket contents or keys -- only whether the backend answers.
    """

    return {
        "backend": settings.rate_limit_backend,
        "available": await rate_limiter.ping(),
        "admission_enabled": admission.enabled,
        "live_concurrency_active": live_capacity.active,
        "live_concurrency_maximum": live_capacity.maximum,
        "dispatch_mode": settings.dispatch_mode,
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
    job = await _get_job(investigation_id)
    if job is None:  # pragma: no cover - defensive
        return

    async def execute() -> None:
        try:
            report = await link.run(request, investigation_id, job.emit)
            job.state.report = report
            job.set_provider_statuses(report.provider_statuses)
            job.finish()
        except Exception as error:  # noqa: BLE001 - surfaced to the client as job failure
            job.emit("report", f"{type(error).__name__}: {error}", status="failed")
            job.finish(error=f"{type(error).__name__}: {error}")

    try:
        if request.live_agents_requested:
            job.emit(
                "queue",
                "Waiting for local live AI capacity",
                capacity_status="queued",
                max_concurrent_live_investigations=live_capacity.maximum,
            )
            try:
                async with live_capacity.reserve():
                    job.mark_running()
                    job.emit("queue", "Local live AI capacity acquired", capacity_status="running")
                    await execute()
            except LiveCapacityTimeoutError as error:
                job.emit(
                    "queue",
                    str(error),
                    status="failed",
                    capacity_status="timed_out",
                    retryable=True,
                )
                job.finish(error=f"live_capacity_timeout: {error}")
            return

        job.mark_running()
        await execute()
    except asyncio.CancelledError:
        # A cancelled background task must not leave a durable job claiming it
        # is still running. Propagate cancellation after recording the outcome.
        if job.state.status in {"queued", "running"}:
            job.emit("report", "investigation cancelled", status="failed")
            job.finish(error="investigation_cancelled")
        raise
    finally:
        await _flush_job_persistence()


@app.post(f"{API_PREFIX}/investigations", response_model=InvestigationCreated)
async def create_investigation(
    request: InvestigationRequest,
    background: BackgroundTasks,
    http_request: Request,
    wait: bool = Query(False, description="Run synchronously and return the finished report"),
) -> InvestigationCreated:
    request = resolve_agent_mode(request, settings.agent_mode)
    if request.live_agents_requested:
        # Admission control only bounds *live* investigations; stub runs make
        # no paid model calls and are never rate-limited or counted here.
        peer_ip = resolve_peer_ip(http_request, trust_proxy_headers=settings.trust_proxy_headers)
        try:
            await admission.check(peer_ip)
        except RateLimitExceeded as error:
            logger.info("admission rejected scope=%s retry_after=%.1fs", error.scope, error.retry_after_seconds)
            raise
        logger.info("admission accepted")
    if request.dataset_id:
        # An unsealed, expired, or unreachable dataset must be refused here rather
        # than half way through a run, where three readers would disagree.
        await datasets.require_runnable_dataset(request.dataset_id)
    investigation_id = f"inv-{uuid.uuid4().hex[:12]}"
    job = jobs.create(investigation_id, request)
    await _job_store_call(jobs.prune)
    if wait:
        await _run_job(investigation_id, request)
    else:
        await _dispatch_live_job(investigation_id, request, job, background)
    headline = report_headline(job.state.report) if job.state.report else ""
    return InvestigationCreated(
        investigation_id=investigation_id,
        status=job.state.status,
        headline=headline,
        state=job.state,
    )


async def _dispatch_live_job(
    investigation_id: str,
    request: InvestigationRequest,
    job: InvestigationJob,
    background: BackgroundTasks,
) -> None:
    """Schedule a created job for execution.

    ``cloud_tasks`` dispatch replaces BackgroundTasks for *live* investigations
    only (Layer 2 of the rate-limiting plan): stub runs, and any deployment
    left on the ``background`` default, keep today's in-process path.
    """

    if request.live_agents_requested and tasks_dispatcher is not None:
        try:
            await tasks_dispatcher.enqueue_claim(investigation_id)
        except Exception as error:
            job.emit("queue", f"{type(error).__name__}: {error}", status="failed")
            job.finish(error=f"dispatch_failed: {type(error).__name__}: {error}")
            await _flush_job_persistence()
            raise
        return
    background.add_task(_run_job, investigation_id, request)


@app.post("/internal/investigations/{investigation_id}/claim")
async def claim_investigation(investigation_id: str, background: BackgroundTasks) -> dict[str, str]:
    """Private Cloud Tasks delivery target. Claims a queued job at most once.

    Not part of the public API contract; only reachable inside the deployment
    (or in tests). A duplicate Cloud Tasks delivery for an already-claimed
    investigation is a no-op rather than a second execution.
    """

    job = await _get_job(investigation_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown investigation {investigation_id}")
    if job.state.status != "queued" or investigation_id in _claimed_investigations:
        return {"investigation_id": investigation_id, "status": job.state.status, "claimed": "false"}
    _claimed_investigations.add(investigation_id)
    background.add_task(_run_job, investigation_id, job.state.request)
    return {"investigation_id": investigation_id, "status": "queued", "claimed": "true"}


@app.get(f"{API_PREFIX}/investigations", response_model=list[InvestigationState])
async def list_investigations() -> list[InvestigationState]:
    return await _job_store_call(jobs.list)


async def _job_store_call(operation, *args):  # type: ignore[no-untyped-def]
    """Offload only the durable store's synchronous database operations."""

    if isinstance(jobs, SqlJobStore):
        # A read or prune after a state mutation must observe the queued writes
        # in the same order as the original synchronous listener did.
        await jobs.flush_persistence()
        return await asyncio.to_thread(operation, *args)
    return operation(*args)


async def _flush_job_persistence() -> None:
    if isinstance(jobs, SqlJobStore):
        await jobs.flush_persistence()


async def _get_job(investigation_id: str):  # type: ignore[no-untyped-def]
    return await _job_store_call(jobs.get, investigation_id)


async def _job_or_404(investigation_id: str):  # type: ignore[no-untyped-def]
    job = await _get_job(investigation_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown investigation {investigation_id}")
    return job


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}", response_model=InvestigationState)
async def get_investigation(investigation_id: str) -> InvestigationState:
    return (await _job_or_404(investigation_id)).state


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}/events")
async def stream_events(investigation_id: str) -> StreamingResponse:
    job = await _job_or_404(investigation_id)

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
    return list((await _job_or_404(investigation_id)).events)


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}/report", response_model=InvestigationReport)
async def get_report(investigation_id: str) -> InvestigationReport:
    job = await _job_or_404(investigation_id)
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


@app.get(f"{API_PREFIX}/investigations/{{investigation_id}}/report.pdf")
async def get_report_pdf(investigation_id: str) -> Response:
    report = await get_report(investigation_id)
    # PDF rendering is CPU-bound; keep it off the event loop like every other
    # unavoidable blocking call in this module (see _job_store_call).
    pdf_bytes = await asyncio.to_thread(render_report_pdf, report)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{investigation_id}.pdf"'},
    )


async def _wait_for(investigation_id: str, timeout: float) -> InvestigationState:
    job = await _job_or_404(investigation_id)
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
