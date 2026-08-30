"""Fixture adapters: synthetic provider data read from ``fixtures/``.

Cost data keeps the provider-native export shape (AWS Data Exports rows, an Azure
Cost Management query envelope, a GCP BigQuery export CSV) and goes through the
same parsers a live adapter will use. Inventory, metrics, audit events, and
recommendations use the CloudCause fixture shape documented in
``fixtures/README.md``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from cloudcause_contracts import (
    AuditEvent,
    CloudResource,
    CostRecord,
    DateRange,
    MetricSeries,
    Provenance,
    Provider,
    Recommendation,
    SourceResult,
)
from cloudcause_focus import (
    parse_aws_rows,
    parse_azure_cost_management,
    parse_gcp_billing_export,
    require_supported_export_schema,
)

from .protocols import BaseDataProvider

FIXTURE_FILES: dict[Provider, dict[str, str]] = {
    "aws": {
        "costs": "cost_and_usage.json",
        "resources": "resources.json",
        "metrics": "cloudwatch_metrics.json",
        "audit_events": "cloudtrail_events.json",
        "recommendations": "recommendations.json",
    },
    "azure": {
        "costs": "cost_management.json",
        "resources": "resources.json",
        "metrics": "monitor_metrics.json",
        "audit_events": "activity_log.json",
        "recommendations": "advisor_recommendations.json",
    },
    "gcp": {
        "costs": "billing_export.csv",
        "resources": "assets.json",
        "metrics": "monitoring_metrics.json",
        "audit_events": "audit_logs.json",
        "recommendations": "recommendations.json",
    },
}


class FixtureError(RuntimeError):
    """Raised when a fixture directory is missing or malformed."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FixtureError(f"fixture file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _as_datetime(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text)


class FixtureDataProvider(BaseDataProvider):
    """One provider's fixture directory, exposed through the adapter boundary."""

    def __init__(self, provider: Provider, fixture_root: Path) -> None:
        self.provider = provider
        self.root = Path(fixture_root) / provider
        if not self.root.exists():
            raise FixtureError(f"fixture directory not found: {self.root}")
        self.manifest = _read_json(self.root / "manifest.json")
        self.scenario_id = str(self.manifest.get("scenario_id", "default"))

    def _source_meta(self, key: str) -> dict[str, Any]:
        sources = self.manifest.get("sources", {})
        if key not in sources:
            raise FixtureError(f"{self.root / 'manifest.json'}: missing source metadata for {key!r}")
        return sources[key]

    def _provenance(self, key: str) -> Provenance:
        meta = self._source_meta(key)
        return Provenance(
            provider=self.provider,
            source=str(meta["source"]),
            observed_at=_as_datetime(meta["observed_at"]),
            retrieved_at=_as_datetime(meta["retrieved_at"]),
            data_through=_as_datetime(meta["data_through"]),
            origin="fixture",
            schema_version=str(meta.get("schema_version", "1")),
            query_reference=f"fixture:{self.provider}/{FIXTURE_FILES[self.provider][key]}",
        )

    def _path(self, key: str) -> Path:
        return self.root / FIXTURE_FILES[self.provider][key]

    def _items(self, key: str) -> list[dict[str, Any]]:
        document = _read_json(self._path(key))
        items = document.get("items")
        if items is None:
            raise FixtureError(f"{self._path(key)}: expected an 'items' array")
        return list(items)

    def _load_cost_records(self) -> list[CostRecord]:
        provenance_meta = self._source_meta("costs")
        require_supported_export_schema(self.provider, str(provenance_meta.get("schema_version", "1")))
        path = self._path("costs")
        if self.provider == "aws":
            document = _read_json(path)
            return parse_aws_rows(document.get("rows", []))
        if self.provider == "azure":
            return parse_azure_cost_management(_read_json(path))
        if not path.exists():
            raise FixtureError(f"fixture file not found: {path}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            return parse_gcp_billing_export(list(csv.DictReader(handle)))

    async def get_costs(self, periods: Sequence[DateRange]) -> SourceResult[CostRecord]:
        records = self._load_cost_records()
        if periods:
            records = [
                record
                for record in records
                if any(period.contains(record.usage_date) for period in periods)
            ]
        return SourceResult[CostRecord](provenance=self._provenance("costs"), items=records)

    async def get_resources(self) -> SourceResult[CloudResource]:
        items = [CloudResource.model_validate(item) for item in self._items("resources")]
        return SourceResult[CloudResource](provenance=self._provenance("resources"), items=items)

    async def get_metrics(self, resource_ids: Sequence[str] | None = None) -> SourceResult[MetricSeries]:
        items = [MetricSeries.model_validate(item) for item in self._items("metrics")]
        if resource_ids:
            wanted = set(resource_ids)
            items = [series for series in items if series.resource_id in wanted]
        return SourceResult[MetricSeries](provenance=self._provenance("metrics"), items=items)

    async def get_audit_events(self, periods: Sequence[DateRange]) -> SourceResult[AuditEvent]:
        items = [AuditEvent.model_validate(item) for item in self._items("audit_events")]
        if periods:
            items = [
                event
                for event in items
                if any(period.contains(event.event_time.date()) for period in periods)
            ]
        return SourceResult[AuditEvent](provenance=self._provenance("audit_events"), items=items)

    async def get_recommendations(self) -> SourceResult[Recommendation]:
        items = [Recommendation.model_validate(item) for item in self._items("recommendations")]
        return SourceResult[Recommendation](
            provenance=self._provenance("recommendations"), items=items
        )


class FixtureAwsDataProvider(FixtureDataProvider):
    def __init__(self, fixture_root: Path) -> None:
        super().__init__("aws", fixture_root)


class FixtureAzureDataProvider(FixtureDataProvider):
    def __init__(self, fixture_root: Path) -> None:
        super().__init__("azure", fixture_root)


class FixtureGcpDataProvider(FixtureDataProvider):
    def __init__(self, fixture_root: Path) -> None:
        super().__init__("gcp", fixture_root)
