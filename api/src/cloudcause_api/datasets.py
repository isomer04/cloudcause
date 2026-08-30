"""Bring-your-own-data ingest endpoints.

One file per request, raw body, read from ``Request.stream()``. There is no
multipart and no ``UploadFile`` on purpose: Starlette's ``UploadFile`` is a
``SpooledTemporaryFile`` that flushes to a real temp file above 1 MB, so every
file that matters would land on disk, and ``api`` does not declare
``python-multipart`` either.

Streaming buys four things at once: the byte cap is enforced while reading rather
than after buffering, no temp file exists, no dependency is added, and each file
gets its own wall clock instead of fifteen files sharing one request budget.

No filename is accepted anywhere. A source is addressed by ``{provider}/{kind}``
in the path, so there is no filename to use as a path or to echo back.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TypeVar

from cloudcause_contracts import (
    DATASET_SOURCE_KINDS,
    PROVIDERS,
    DatasetCreated,
    DatasetIngestReport,
    DatasetSourceKind,
    DatasetSummary,
    InvestigationRequest,
    Provider,
    Settings,
)
from cloudcause_datasets import (
    Dataset,
    DatasetError,
    DatasetExpiredError,
    DatasetNotSealedError,
    DatasetProviderMissingError,
    DatasetSealedError,
    DatasetStore,
    DatasetStoreFullError,
    DatasetTooLargeError,
    IngestError,
    UnknownDatasetError,
    UploadTooLargeError,
    add_source,
    build_dataset_store,
    check_content_type,
    dataset_store_reason,
    parse_source,
    seal_dataset,
    try_build_dataset_store,
)
from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])

#: Mapped to HTTP so the UI can tell "too big" from "not parseable" without
#: reading prose. 409 is used for state, not for size or shape.
_STATUS_FOR: dict[type[Exception], int] = {
    UnknownDatasetError: 404,
    DatasetExpiredError: 410,
    DatasetSealedError: 409,
    DatasetNotSealedError: 409,
    DatasetProviderMissingError: 422,
    DatasetStoreFullError: 507,
    DatasetTooLargeError: 413,
}


#: Which demo fixture each downloadable template is cut from. Cost exports are
#: provider-native, so there is no CloudCause template to offer for them.
TEMPLATE_FIXTURES: dict[str, tuple[str, str]] = {
    "metrics": ("aws", "cloudwatch_metrics.json"),
    "audit": ("aws", "cloudtrail_events.json"),
    "inventory": ("aws", "resources.json"),
    "recommendations": ("aws", "recommendations.json"),
}


class _State:
    """Rebuilt by ``configure`` so tests can retarget the gateway."""

    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.store: DatasetStore | None = None
        self.reason: str | None = "the gateway has not been configured"
        self.executor: ThreadPoolExecutor | None = None


state = _State()

#: Parsing is CPU-bound and runs off the event loop. It gets its own small pool
#: rather than ``asyncio.to_thread``'s default executor: a parse that outlives its
#: timeout keeps running, and on the shared pool it would hold a slot every other
#: endpoint also draws from. Bounded here, so slow uploads queue behind each other
#: instead of starving the gateway.
INGEST_WORKERS = 4
_StoreResult = TypeVar("_StoreResult")


def ingest_executor() -> ThreadPoolExecutor:
    if state.executor is None:
        state.executor = ThreadPoolExecutor(
            max_workers=INGEST_WORKERS, thread_name_prefix="cloudcause-ingest"
        )
    return state.executor


def shutdown() -> None:
    """Release the ingest pool. Called from the gateway's lifespan."""

    executor, state.executor = state.executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def configure(settings: Settings) -> None:
    """Resolve the dataset store this topology allows, or record why it cannot."""

    state.settings = settings
    state.store, state.reason = try_build_dataset_store(settings)


async def _store_call(
    store: DatasetStore, operation: Callable[..., _StoreResult], *args: object
) -> _StoreResult:
    """Run SQL-backed store work away from the gateway event loop.

    The memory store is an in-process dict with its own short locks, so sending
    it to a worker thread would only add scheduling overhead and cross-thread
    access for no I/O benefit.
    """

    if store.kind == "sql":
        return await asyncio.to_thread(operation, *args)
    return operation(*args)


async def describe() -> dict[str, object]:
    """What ``/health`` says about uploads. Separate from the history store."""

    if state.store is None:
        return {"enabled": False, "reason": state.reason}
    store = _store()
    return await _store_call(store, store.describe)


def _settings() -> Settings:
    if state.settings is None:  # pragma: no cover - configure runs at import
        raise HTTPException(status_code=503, detail="the gateway is not configured")
    return state.settings


def _store() -> DatasetStore:
    """The store, or a 503 that names the missing configuration.

    The memory store is re-resolved every call. It is a process-wide singleton, so
    this is a dict lookup, and it means the router can never end up holding a stale
    instance while ``get_data_provider`` in the orchestrator resolves a live one.
    A SQL store is cached, because reconnecting per request would not be free.
    """

    if state.store is None:
        raise HTTPException(
            status_code=503,
            detail=state.reason or dataset_store_reason(_settings()) or "uploads are unavailable",
        )
    if state.store.kind == "memory":
        return build_dataset_store(_settings())
    return state.store


def _fail(error: Exception) -> HTTPException:
    status = _STATUS_FOR.get(type(error))
    if status is None and isinstance(error, IngestError):
        status = error.status
    if status is None:
        status = 422
    detail = getattr(error, "detail", None) or str(error)
    return HTTPException(status_code=status, detail=detail)


def _check_path(provider: str, kind: str) -> tuple[Provider, DatasetSourceKind]:
    if provider not in PROVIDERS:
        raise HTTPException(
            status_code=404, detail=f"unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        )
    if kind not in DATASET_SOURCE_KINDS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown source kind {kind!r}; expected one of {', '.join(DATASET_SOURCE_KINDS)}",
        )
    return provider, kind  # type: ignore[return-value]


async def _read_body(request: Request, limit: int) -> bytes:
    """Read the raw body, refusing at the first byte over the limit.

    Nothing is written to disk and nothing is logged: the bytes live in this
    function, are handed to the parser, and are dropped when it returns.
    """

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise UploadTooLargeError(
            f"the upload declares {int(declared):,} bytes, over the {limit:,} byte limit"
        )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise UploadTooLargeError(
                f"the upload passed the {limit:,} byte limit and was refused mid-stream"
            )
    if not body:
        raise IngestError("the request body was empty")
    return bytes(body)


def suggested_request(summary: DatasetSummary) -> InvestigationRequest | None:
    """A brief over the period the data actually covers.

    Split in half: the later half is the period under investigation, the earlier
    half its baseline. Computed here rather than in the browser so no UI has to
    derive a date, and returned with the summary so the demo dates are never
    silently applied to somebody's own export.
    """

    if summary.period_start is None or summary.period_end is None:
        return None
    span = (summary.period_end - summary.period_start).days + 1
    if span < 2:
        current_start = baseline_start = summary.period_start
        current_end = baseline_end = summary.period_end
    else:
        half = span // 2
        baseline_start = summary.period_start
        baseline_end = summary.period_start + timedelta(days=half - 1)
        current_start = baseline_end + timedelta(days=1)
        current_end = summary.period_end
    return InvestigationRequest(
        providers=list(summary.providers) or ["aws"],
        start_date=current_start,
        end_date=current_end,
        comparison_start_date=baseline_start,
        comparison_end_date=baseline_end,
        question="Why did our cloud spending increase in this period?",
        scenario_id="",
        dataset_id=summary.dataset_id,
    )


def _summary(dataset: Dataset) -> DatasetSummary:
    summary = dataset.summary()
    summary.suggested_request = suggested_request(summary)
    return summary




@router.get("/templates/{kind}")
async def source_template(kind: str) -> dict[str, object]:
    """A minimal, valid example of one evidence shape, cut from the demo fixtures.

    Nobody produces the CloudCause evidence shapes correctly from prose, so the UI
    offers a download for each. Generated here rather than in the browser so the
    template can never drift from the model that validates it.
    """

    if kind not in TEMPLATE_FIXTURES:
        raise HTTPException(
            status_code=404,
            detail=f"no template for {kind!r}; expected one of {', '.join(TEMPLATE_FIXTURES)}",
        )
    settings = _settings()
    provider, filename = TEMPLATE_FIXTURES[kind]
    path = settings.fixture_root / provider / filename
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:  # pragma: no cover - fixtures ship with the repo
        raise HTTPException(status_code=500, detail=f"template unavailable: {error}") from error
    items = [
        {key: value for key, value in item.items() if key != "provider"}
        for item in document.get("items", [])[:1]
    ]
    return {
        "_comment": (
            f"CloudCause {kind} template. Replace the example item, keep the 'items' array, "
            f"and upload it to PUT /api/v1/datasets/{{id}}/sources/{{provider}}/{kind}. "
            "The 'provider' field is filled in from the URL. See fixtures/README.md."
        ),
        "items": items,
    }


@router.post("", response_model=DatasetCreated)
async def create_dataset() -> DatasetCreated:
    """Mint an empty dataset and tell the client every limit it must respect."""

    settings = _settings()
    store = _store()
    try:
        dataset = await _store_call(store, store.create)
    except DatasetError as error:
        raise _fail(error) from error
    return DatasetCreated(
        dataset_id=dataset.dataset_id,
        created_at=dataset.created_at,
        expires_at=dataset.expires_at,
        max_bytes_per_file=settings.upload_max_bytes,
        max_rows_per_file=settings.upload_max_rows,
        max_sources=settings.upload_max_sources,
        max_records=settings.dataset_max_records,
    )


@router.put("/{dataset_id}/sources/{provider}/{kind}", response_model=DatasetIngestReport)
async def put_source(
    dataset_id: str, provider: str, kind: str, request: Request
) -> DatasetIngestReport:
    """Stream one file in, parse it, and attach the normalized result."""

    settings = _settings()
    store = _store()
    resolved_provider, resolved_kind = _check_path(provider, kind)
    started = time.perf_counter()
    try:
        check_content_type(request.headers.get("content-type"))
        payload = await _read_body(request, settings.upload_max_bytes)
        loop = asyncio.get_running_loop()
        parsed = await asyncio.wait_for(
            loop.run_in_executor(
                ingest_executor(), parse_source, resolved_provider, resolved_kind, payload, settings
            ),
            timeout=settings.upload_timeout_seconds,
        )
        report = await _store_call(
            store,
            add_source,
            store,
            dataset_id,
            resolved_provider,
            resolved_kind,
            parsed,
            len(payload),
            settings,
        )
    except TimeoutError as error:
        raise HTTPException(
            status_code=504,
            detail=(
                f"parsing this file passed the {settings.upload_timeout_seconds:.0f}s budget. "
                "Pre-aggregate it to daily grain, or split it, and try again."
            ),
        ) from error
    except (DatasetError, IngestError) as error:
        raise _fail(error) from error
    report.warnings.append(f"parsed in {time.perf_counter() - started:.2f}s")
    return report


@router.post("/{dataset_id}/seal", response_model=DatasetSummary)
async def seal(dataset_id: str) -> DatasetSummary:
    """Freeze the dataset. Nothing can investigate an unsealed one."""

    store = _store()
    try:
        dataset = await _store_call(store, seal_dataset, store, dataset_id)
    except (DatasetError, IngestError) as error:
        raise _fail(error) from error
    return _summary(dataset)


@router.get("/{dataset_id}", response_model=DatasetSummary)
async def get_dataset(dataset_id: str) -> DatasetSummary:
    store = _store()
    try:
        dataset = await _store_call(store, store.get, dataset_id)
    except DatasetError as error:
        raise _fail(error) from error
    return _summary(dataset)


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(dataset_id: str) -> Response:
    """Honoured immediately. A user who changes their mind is done in one call."""

    store = _store()
    if not await _store_call(store, store.delete, dataset_id):
        raise HTTPException(status_code=404, detail=f"unknown dataset {dataset_id}")
    return Response(status_code=204)


def dataset_state(dataset_id: str) -> str:
    """``live``, ``expired``, ``unknown``, or ``unavailable``, for the 409 a stored
    report needs.

    ``unavailable`` means the topology has no dataset store at all, so the id can
    be neither confirmed nor denied.
    """

    if state.store is None:
        return "unavailable"
    try:
        _store().get(dataset_id)
    except DatasetExpiredError:
        return "expired"
    except UnknownDatasetError:
        return "unknown"
    except DatasetError:  # pragma: no cover - defensive
        return "unknown"
    return "live"


async def require_runnable_dataset(dataset_id: str) -> None:
    """Refuse to start a run whose dataset is gone, unsealed, or unreachable.

    History keeps ``dataset_id`` forever while a dataset lives two hours, so
    re-running a stored investigation is a normal thing to try and deserves an
    answer that reads like an explanation rather than a bug.
    """

    store = _store()
    try:
        await _store_call(store, store.get_for_investigation, dataset_id)
    except DatasetExpiredError as error:
        raise HTTPException(
            status_code=409,
            detail=(
                f"dataset_expired: {error}. Uploaded data is kept for a fixed window and then "
                "deleted; the report it produced is still in history. Upload the export again to "
                "re-run it."
            ),
        ) from error
    except UnknownDatasetError as error:
        raise HTTPException(
            status_code=409,
            detail=(
                f"dataset_expired: {error}. Uploaded data is not kept beyond its window, so a "
                "stored investigation cannot be re-run from it. Upload the export again."
            ),
        ) from error
    except DatasetError as error:
        raise _fail(error) from error
