"""The upload adapter: a user's own data behind the same boundary as a fixture.

Nothing downstream can tell an uploaded dataset from a fixture one except by
reading ``Provenance.origin``, which is exactly the point. The comparison, the
playbooks, the evidence factory, and the MCP tools all see the same
``ProviderDataBundle`` shape.

What they *can* tell is what is missing. A cost-only dataset returns empty
metric, audit, inventory, and recommendation results, and the validator uses that
to refuse a named mechanism the data cannot support.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from cloudcause_contracts import (
    AuditEvent,
    CloudResource,
    CostRecord,
    DatasetSourceKind,
    DateRange,
    MetricSeries,
    Provenance,
    Provider,
    Recommendation,
    SourceResult,
)
from cloudcause_datasets import UPLOAD_SOURCE_NAMES, Dataset

from .protocols import BaseDataProvider


class UploadDataProvider(BaseDataProvider):
    """One provider's slice of a sealed dataset."""

    def __init__(self, provider: Provider, dataset: Dataset) -> None:
        self.provider = provider
        self.dataset = dataset

    def _provenance(self, kind: DatasetSourceKind) -> Provenance:
        source = self.dataset.source(self.provider, kind)
        if source is not None:
            return source.provenance
        return self._absent_provenance(kind)

    def _absent_provenance(self, kind: DatasetSourceKind) -> Provenance:
        """Provenance for a source the user did not supply.

        It still says ``origin="upload"``: the honest statement is "this dataset
        has no metrics", not "these metrics came from somewhere else".
        """

        boundary: datetime = self.dataset.data_through() or self.dataset.created_at
        retrieved = max(self.dataset.created_at, boundary)
        return Provenance(
            provider=self.provider,
            source=f"{UPLOAD_SOURCE_NAMES[kind]}-absent",
            observed_at=boundary,
            retrieved_at=retrieved,
            data_through=boundary,
            origin="upload",
            schema_version="1",
            query_reference=f"upload:{self.provider}/{kind}#not-supplied",
        )

    async def get_costs(self, periods: Sequence[DateRange]) -> SourceResult[CostRecord]:
        records = self.dataset.cost_records(self.provider)
        if periods:
            records = [
                record
                for record in records
                if any(period.contains(record.usage_date) for period in periods)
            ]
        return SourceResult[CostRecord](provenance=self._provenance("cost"), items=records)

    async def get_resources(self) -> SourceResult[CloudResource]:
        source = self.dataset.source(self.provider, "inventory")
        items = list(source.resources) if source else []
        return SourceResult[CloudResource](provenance=self._provenance("inventory"), items=items)

    async def get_metrics(
        self, resource_ids: Sequence[str] | None = None
    ) -> SourceResult[MetricSeries]:
        source = self.dataset.source(self.provider, "metrics")
        items = list(source.metrics) if source else []
        if resource_ids:
            wanted = set(resource_ids)
            items = [series for series in items if series.resource_id in wanted]
        return SourceResult[MetricSeries](provenance=self._provenance("metrics"), items=items)

    async def get_audit_events(self, periods: Sequence[DateRange]) -> SourceResult[AuditEvent]:
        source = self.dataset.source(self.provider, "audit")
        items = list(source.audit_events) if source else []
        if periods:
            items = [
                event
                for event in items
                if any(period.contains(event.event_time.date()) for period in periods)
            ]
        return SourceResult[AuditEvent](provenance=self._provenance("audit"), items=items)

    async def get_recommendations(self) -> SourceResult[Recommendation]:
        source = self.dataset.source(self.provider, "recommendations")
        items = list(source.recommendations) if source else []
        return SourceResult[Recommendation](
            provenance=self._provenance("recommendations"), items=items
        )
