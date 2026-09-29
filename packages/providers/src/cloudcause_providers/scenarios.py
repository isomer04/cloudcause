"""Seeded evaluation scenarios.

A scenario is a compact YAML description of one planted cause. The generator
below expands it deterministically into the same ``ProviderDataBundle`` shape the
fixture and live adapters produce, so every process (gateway, orchestrator,
workers, evaluation harness) rebuilds identical data from the scenario id alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from cloudcause_contracts import (
    AuditEvent,
    CloudCauseModel,
    CloudResource,
    CostRecord,
    DateRange,
    InvestigationRequest,
    MetricPoint,
    MetricSeries,
    Provenance,
    Provider,
    Recommendation,
    SourceResult,
)
from cloudcause_focus import service_category
from pydantic import Field

from .protocols import BaseDataProvider

SOURCE_NAMES: dict[Provider, dict[str, str]] = {
    "aws": {
        "costs": "cost-explorer",
        "resources": "resource-explorer",
        "metrics": "cloudwatch",
        "audit_events": "cloudtrail",
        "recommendations": "compute-optimizer",
    },
    "azure": {
        "costs": "cost-management",
        "resources": "resource-graph",
        "metrics": "azure-monitor",
        "audit_events": "activity-log",
        "recommendations": "azure-advisor",
    },
    "gcp": {
        "costs": "billing-export-bigquery",
        "resources": "cloud-asset-inventory",
        "metrics": "cloud-monitoring",
        "audit_events": "cloud-audit-logs",
        "recommendations": "recommender",
    },
}


class ScenarioPeriods(CloudCauseModel):
    baseline: DateRange
    current: DateRange


class CostSeriesSpec(CloudCauseModel):
    service: str
    sku: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    region: str | None = None
    unit: str = "unit"
    tags: dict[str, str] = Field(default_factory=dict)
    daily_cost: float = 0.0
    daily_quantity: float = 0.0
    current_daily_cost: float | None = None
    current_daily_quantity: float | None = None
    starts_on: date | None = None
    charge_description: str = ""


class MetricSpec(CloudCauseModel):
    resource_id: str
    metric_name: str
    unit: str = "Count"
    statistic: str = "Sum"
    baseline_daily_value: float = 0.0
    current_daily_value: float | None = None
    starts_on: date | None = None


class ScenarioSpec(CloudCauseModel):
    id: str
    title: str
    provider: Provider
    category: str
    question: str = "Why did our cloud spending increase?"
    account_id: str
    region: str = "unknown"
    currency: str = "USD"
    periods: ScenarioPeriods
    data_through: datetime | None = None
    retrieved_at: datetime | None = None
    omit_costs_after: date | None = None
    notes: str = ""
    baseline_services: list[CostSeriesSpec] = Field(default_factory=list)
    spikes: list[CostSeriesSpec] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)
    audit_events: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)

    def to_request(self) -> InvestigationRequest:
        return InvestigationRequest(
            providers=[self.provider],
            start_date=self.periods.current.start,
            end_date=self.periods.current.end,
            comparison_start_date=self.periods.baseline.start,
            comparison_end_date=self.periods.baseline.end,
            account_ids=[self.account_id],
            question=self.question,
            scenario_id=self.id,
        )

    def effective_data_through(self) -> datetime:
        if self.data_through:
            return self.data_through
        return datetime.combine(self.periods.current.end, time(23, 59, 59), tzinfo=UTC)

    def effective_retrieved_at(self) -> datetime:
        if self.retrieved_at:
            return self.retrieved_at
        return self.effective_data_through() + timedelta(hours=9)


def _series_values(spec: CostSeriesSpec, day: date, in_current: bool) -> tuple[float, float]:
    """Cost and quantity for one day of one series."""

    use_current = (
        in_current
        and spec.current_daily_cost is not None
        and (spec.starts_on is None or day >= spec.starts_on)
    )
    if use_current:
        cost = float(spec.current_daily_cost or 0.0)
        quantity = float(
            spec.current_daily_quantity if spec.current_daily_quantity is not None else spec.daily_quantity
        )
        return cost, quantity
    return float(spec.daily_cost), float(spec.daily_quantity)


def build_cost_records(spec: ScenarioSpec) -> list[CostRecord]:
    records: list[CostRecord] = []
    all_series = [*spec.baseline_services, *spec.spikes]
    for period, in_current in ((spec.periods.baseline, False), (spec.periods.current, True)):
        for day in period.dates():
            if spec.omit_costs_after and day > spec.omit_costs_after:
                continue
            for index, series in enumerate(all_series):
                cost, quantity = _series_values(series, day, in_current)
                if cost == 0.0 and quantity == 0.0:
                    continue
                records.append(
                    CostRecord(
                        provider=spec.provider,
                        billing_account_id=spec.account_id,
                        usage_date=day,
                        service_name=series.service,
                        service_category=service_category(series.service),
                        charge_category="usage",
                        charge_description=series.charge_description or (series.sku or series.service),
                        region_id=series.region or spec.region,
                        resource_id=series.resource_id,
                        resource_name=series.resource_name,
                        sku_id=series.sku,
                        usage_quantity=quantity,
                        usage_unit=series.unit,
                        billed_cost=round(cost, 6),
                        effective_cost=round(cost, 6),
                        currency=spec.currency,
                        tags=dict(series.tags),
                        source_record_id=f"{spec.id}-{index:02d}-{day.isoformat()}",
                    )
                )
    return records


def build_metric_series(spec: ScenarioSpec) -> list[MetricSeries]:
    series_list: list[MetricSeries] = []
    for metric in spec.metrics:
        points: list[MetricPoint] = []
        for period, in_current in ((spec.periods.baseline, False), (spec.periods.current, True)):
            for day in period.dates():
                use_current = (
                    in_current
                    and metric.current_daily_value is not None
                    and (metric.starts_on is None or day >= metric.starts_on)
                )
                value = (
                    float(metric.current_daily_value or 0.0)
                    if use_current
                    else float(metric.baseline_daily_value)
                )
                points.append(
                    MetricPoint(
                        timestamp=datetime.combine(day, time(12, 0), tzinfo=UTC), value=value
                    )
                )
        series_list.append(
            MetricSeries(
                provider=spec.provider,
                resource_id=metric.resource_id,
                metric_name=metric.metric_name,
                unit=metric.unit,
                statistic=metric.statistic,
                points=points,
            )
        )
    return series_list


class ScenarioDataProvider(BaseDataProvider):
    """In-memory provider built from a scenario specification."""

    def __init__(self, spec: ScenarioSpec) -> None:
        self.spec = spec
        self.provider = spec.provider
        self._costs = build_cost_records(spec)
        self._metrics = build_metric_series(spec)
        self._resources = [
            CloudResource.model_validate({"provider": spec.provider, **item}) for item in spec.resources
        ]
        self._events = [
            AuditEvent.model_validate(
                {
                    "provider": spec.provider,
                    "source": SOURCE_NAMES[spec.provider]["audit_events"],
                    "event_id": item.get("event_id", f"{spec.id}-event-{index:02d}"),
                    **item,
                }
            )
            for index, item in enumerate(spec.audit_events)
        ]
        self._recommendations = [
            Recommendation.model_validate(
                {
                    "provider": spec.provider,
                    "source": SOURCE_NAMES[spec.provider]["recommendations"],
                    **item,
                }
            )
            for item in spec.recommendations
        ]

    def _provenance(self, key: str) -> Provenance:
        return Provenance(
            provider=self.provider,
            source=SOURCE_NAMES[self.provider][key],
            observed_at=self.spec.effective_data_through(),
            retrieved_at=self.spec.effective_retrieved_at(),
            data_through=self.spec.effective_data_through(),
            origin="fixture",
            schema_version="1",
            query_reference=f"scenario:{self.spec.id}/{key}",
        )

    async def get_costs(self, periods: Sequence[DateRange]) -> SourceResult[CostRecord]:
        records = self._costs
        if periods:
            records = [r for r in records if any(period.contains(r.usage_date) for period in periods)]
        return SourceResult[CostRecord](provenance=self._provenance("costs"), items=list(records))

    async def get_resources(self) -> SourceResult[CloudResource]:
        return SourceResult[CloudResource](
            provenance=self._provenance("resources"), items=list(self._resources)
        )

    async def get_metrics(self, resource_ids: Sequence[str] | None = None) -> SourceResult[MetricSeries]:
        items = list(self._metrics)
        if resource_ids:
            wanted = set(resource_ids)
            items = [series for series in items if series.resource_id in wanted]
        return SourceResult[MetricSeries](provenance=self._provenance("metrics"), items=items)

    async def get_audit_events(self, periods: Sequence[DateRange]) -> SourceResult[AuditEvent]:
        items = list(self._events)
        if periods:
            items = [
                event
                for event in items
                if any(period.contains(event.event_time.date()) for period in periods)
            ]
        return SourceResult[AuditEvent](provenance=self._provenance("audit_events"), items=items)

    async def get_recommendations(self) -> SourceResult[Recommendation]:
        return SourceResult[Recommendation](
            provenance=self._provenance("recommendations"), items=list(self._recommendations)
        )


def load_scenario_spec(path: Path) -> ScenarioSpec:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ScenarioSpec.model_validate(document)


@lru_cache(maxsize=64)
def _cached_specs(root: str) -> tuple[ScenarioSpec, ...]:
    directory = Path(root)
    if not directory.exists():
        return ()
    return tuple(load_scenario_spec(path) for path in sorted(directory.glob("*.yaml")))


def list_scenarios(root: Path) -> list[ScenarioSpec]:
    return list(_cached_specs(str(Path(root).resolve())))


def get_scenario(root: Path, scenario_id: str) -> ScenarioSpec | None:
    for spec in list_scenarios(root):
        if spec.id == scenario_id:
            return spec
    return None
