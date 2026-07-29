"""Add one parsed upload to a dataset, or refuse it against what is already there.

This is the only place that turns a :class:`ParsedSource` into stored state, so
the seal rules, the source cap, and the single-currency rule live together rather
than being re-checked in the router.
"""

from __future__ import annotations

from cloudcause_contracts import (
    DatasetIngestReport,
    DatasetSourceKind,
    DatasetSourceSummary,
    Provenance,
    Provider,
    Settings,
    utcnow,
)

from .ingest import CurrencyConflictError, IngestError, ParsedSource
from .models import UPLOAD_SOURCE_NAMES, Dataset, DatasetSource
from .store import DatasetSealedError, DatasetStore


class TooManySourcesError(IngestError):
    code = "too_many_sources"


class MissingCostSourceError(IngestError):
    """Sealing needs at least one accepted cost export: no cost, no comparison."""

    code = "no_cost_source"


def _provenance(
    provider: Provider, kind: DatasetSourceKind, parsed: ParsedSource, received_at
) -> Provenance:
    data_through = parsed.data_through or received_at
    return Provenance(
        provider=provider,
        source=UPLOAD_SOURCE_NAMES[kind],
        observed_at=data_through,
        retrieved_at=received_at,
        data_through=data_through,
        origin="upload",
        schema_version="1",
        query_reference=f"upload:{provider}/{kind}",
    )


def build_source(
    provider: Provider, kind: DatasetSourceKind, parsed: ParsedSource, byte_size: int
) -> DatasetSource:
    received_at = utcnow()
    summary = DatasetSourceSummary(
        provider=provider,
        kind=kind,
        detected_format=parsed.detected_format,
        received_at=received_at,
        raw_rows=parsed.raw_rows,
        accepted_rows=parsed.accepted_rows,
        rejected_rows=len(parsed.rejections),
        stored_records=parsed.stored_records(),
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        data_through=parsed.data_through or received_at,
        data_through_note=parsed.data_through_note,
        currency=parsed.currency,
        byte_size=byte_size,
        compressed=parsed.compressed,
    )
    return DatasetSource(
        summary=summary,
        provenance=_provenance(provider, kind, parsed, received_at),
        costs=parsed.costs,
        metrics=parsed.metrics,
        audit_events=parsed.audit_events,
        resources=parsed.resources,
        recommendations=parsed.recommendations,
    )


def add_source(
    store: DatasetStore,
    dataset_id: str,
    provider: Provider,
    kind: DatasetSourceKind,
    parsed: ParsedSource,
    byte_size: int,
    settings: Settings,
) -> DatasetIngestReport:
    """Attach one accepted upload to an unsealed dataset and report what landed.

    The whole get-validate-mutate-put runs under the store's per-dataset lock, so
    two uploads to the same dataset cannot interleave and drop one another's
    source or write past a seal that landed in between.
    """

    with store.mutate(dataset_id):
        dataset = store.get(dataset_id)
        if dataset.sealed:
            raise DatasetSealedError(
                f"dataset {dataset_id} is sealed and immutable. Sealing is what makes it safe for "
                "the orchestrator, both workers, and every MCP child to read the same data at "
                "once; create a new dataset to change anything."
            )
        replacing = dataset.source(provider, kind) is not None
        if not replacing and len(dataset.sources) >= settings.upload_max_sources:
            raise TooManySourcesError(
                f"a dataset holds at most {settings.upload_max_sources} sources and this one "
                f"already has {len(dataset.sources)}"
            )
        if parsed.currency and dataset.currency and parsed.currency != dataset.currency:
            raise CurrencyConflictError(
                f"this file is priced in {parsed.currency} but the dataset already holds "
                f"{dataset.currency}. CloudCause does not convert between currencies, so one "
                "dataset carries one currency."
            )

        source = build_source(provider, kind, parsed, byte_size)
        dataset.replace_source(source)
        if parsed.currency:
            dataset.currency = parsed.currency
        store.put(dataset)

    return DatasetIngestReport(
        dataset_id=dataset.dataset_id,
        expires_at=dataset.expires_at,
        sealed=dataset.sealed,
        source=source.summary,
        rejections=parsed.rejections[:50],
        warnings=list(parsed.warnings),
        total_records=dataset.record_count(),
        source_count=len(dataset.sources),
    )


def seal_dataset(store: DatasetStore, dataset_id: str) -> Dataset:
    """Seal a dataset, refusing one that could not start an investigation."""

    with store.mutate(dataset_id):
        dataset = store.get(dataset_id)
        if dataset.sealed:
            return dataset
        if not any(source.kind == "cost" and source.costs for source in dataset.sources):
            raise MissingCostSourceError(
                "a dataset needs at least one accepted cost export before it can be sealed: "
                "without cost rows there is no period comparison to make."
            )
        return store.seal(dataset_id)
