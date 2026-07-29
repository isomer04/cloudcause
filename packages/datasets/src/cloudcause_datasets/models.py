"""What a dataset actually holds once the raw bytes are gone.

Only normalized contract objects survive ingest: ``CostRecord`` plus the optional
``MetricSeries``, ``AuditEvent``, ``CloudResource``, and ``Recommendation``
evidence, and one ``Provenance`` per source. The uploaded file itself is parsed
from the request stream and discarded; nothing raw is written to disk or logged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from cloudcause_contracts import (
    SOURCE_KIND_EVIDENCE_TYPES,
    AuditEvent,
    CloudCauseModel,
    CloudResource,
    CostRecord,
    DatasetSourceKind,
    DatasetSourceSummary,
    DatasetSummary,
    DateRange,
    MetricSeries,
    Provenance,
    Provider,
    Recommendation,
    utcnow,
)
from pydantic import Field

#: Source names the upload provider reports, one per kind, so a report can tell
#: an uploaded cost export from a fixture one at a glance.
UPLOAD_SOURCE_NAMES: dict[DatasetSourceKind, str] = {
    "cost": "uploaded-cost-export",
    "metrics": "uploaded-metrics",
    "audit": "uploaded-audit-events",
    "inventory": "uploaded-inventory",
    "recommendations": "uploaded-recommendations",
}


class DatasetSource(CloudCauseModel):
    """One accepted upload, normalized, with the provenance it travels under."""

    summary: DatasetSourceSummary
    provenance: Provenance
    costs: list[CostRecord] = Field(default_factory=list)
    metrics: list[MetricSeries] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)
    resources: list[CloudResource] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)

    @property
    def provider(self) -> Provider:
        return self.summary.provider

    @property
    def kind(self) -> DatasetSourceKind:
        return self.summary.kind

    def record_count(self) -> int:
        return (
            len(self.costs)
            + len(self.metrics)
            + len(self.audit_events)
            + len(self.resources)
            + len(self.recommendations)
        )


class Dataset(CloudCauseModel):
    """An addressable, normalized, sealed-once set of a user's own data."""

    dataset_id: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    sealed_at: datetime | None = None
    sources: list[DatasetSource] = Field(default_factory=list)
    currency: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def sealed(self) -> bool:
        return self.sealed_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or utcnow()) >= self.expires_at

    def providers(self) -> list[Provider]:
        seen: list[Provider] = []
        for source in self.sources:
            if source.provider not in seen:
                seen.append(source.provider)
        return seen

    def source(self, provider: Provider, kind: DatasetSourceKind) -> DatasetSource | None:
        for source in self.sources:
            if source.provider == provider and source.kind == kind:
                return source
        return None

    def sources_for(self, provider: Provider) -> list[DatasetSource]:
        return [source for source in self.sources if source.provider == provider]

    def replace_source(self, source: DatasetSource) -> None:
        """Add a source, or replace the same ``{provider}/{kind}`` slot.

        Re-uploading one file must not double count, and it must not need a new
        dataset either: a user who picked the wrong CSV fixes just that row.
        """

        for index, existing in enumerate(self.sources):
            if existing.provider == source.provider and existing.kind == source.kind:
                self.sources[index] = source
                return
        self.sources.append(source)

    def record_count(self) -> int:
        return sum(source.record_count() for source in self.sources)

    def cost_records(self, provider: Provider) -> list[CostRecord]:
        source = self.source(provider, "cost")
        return list(source.costs) if source else []

    def period(self) -> DateRange | None:
        """The period the *cost* rows cover, which is what gets compared.

        Deliberately not the union across every source. An inventory file carries
        a resource's ``created_at``, which is its birthday rather than a coverage
        window, and letting that widen the period would silently move the
        comparison the user asked for.
        """

        cost = [source for source in self.sources if source.kind == "cost"]
        considered = cost or list(self.sources)
        starts = [s.summary.period_start for s in considered if s.summary.period_start]
        ends = [s.summary.period_end for s in considered if s.summary.period_end]
        if not starts or not ends:
            return None
        return DateRange(start=min(starts), end=max(ends))

    def data_through(self) -> datetime | None:
        stamps = [s.summary.data_through for s in self.sources if s.summary.data_through]
        return min(stamps) if stamps else None

    def available_source_types(self) -> dict[Provider, list[str]]:
        """Per provider, the ``Evidence.source_type`` values this dataset backs."""

        available: dict[Provider, list[str]] = {}
        for source in self.sources:
            if not source.record_count():
                continue
            types = set(available.get(source.provider, []))
            types.update(SOURCE_KIND_EVIDENCE_TYPES[source.kind])
            available[source.provider] = sorted(types)
        return available

    def summary(self) -> DatasetSummary:
        period = self.period()
        return DatasetSummary(
            dataset_id=self.dataset_id,
            created_at=self.created_at,
            expires_at=self.expires_at,
            sealed=self.sealed,
            sealed_at=self.sealed_at,
            providers=self.providers(),
            sources=[source.summary for source in self.sources],
            currency=self.currency,
            total_records=self.record_count(),
            period_start=period.start if period else None,
            period_end=period.end if period else None,
            data_through=self.data_through(),
            available_source_types=self.available_source_types(),
            warnings=list(self.warnings),
        )


def expiry_from(ttl_seconds: float, now: datetime | None = None) -> datetime:
    """An absolute expiry, so the UI can quote it and never has to guess."""

    return (now or utcnow()) + timedelta(seconds=max(ttl_seconds, 1.0))


DatasetStoreKind = Literal["memory", "sql"]
