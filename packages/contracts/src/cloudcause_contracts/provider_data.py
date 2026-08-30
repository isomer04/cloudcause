"""Normalized provider operational data.

``CostRecord`` is the internal cost row. ``FocusRecord`` is the FOCUS 1.4
projection used for cross-provider comparison and for anything user visible.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from .common import CloudCauseModel, DataOrigin, Provenance, Provider, SourceResult

ChargeCategory = Literal["usage", "purchase", "tax", "credit", "adjustment", "unknown"]


class CostRecord(CloudCauseModel):
    """One provider cost line for one day."""

    provider: Provider
    billing_account_id: str
    usage_date: date
    service_name: str
    service_category: str = "Other"
    charge_category: ChargeCategory = "usage"
    charge_description: str = ""
    region_id: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    sku_id: str | None = None
    usage_quantity: float = 0.0
    usage_unit: str = "unit"
    billed_cost: float = 0.0
    effective_cost: float = 0.0
    currency: str = "USD"
    tags: dict[str, str] = Field(default_factory=dict)
    commitment_discount_id: str | None = None
    source_record_id: str | None = None

    def resource_key(self) -> str:
        return self.resource_id or f"{self.service_name}::untagged-aggregate"


class FocusRecord(CloudCauseModel):
    """FOCUS 1.4 subset. Column names follow the specification casing."""

    ProviderName: Provider
    BillingAccountId: str
    ChargePeriodStart: datetime
    ChargePeriodEnd: datetime
    BillingPeriodStart: date
    BillingPeriodEnd: date
    ServiceName: str
    ServiceCategory: str
    ChargeCategory: str
    ChargeDescription: str = ""
    RegionId: str | None = None
    ResourceId: str | None = None
    ResourceName: str | None = None
    SkuId: str | None = None
    ConsumedQuantity: float = 0.0
    ConsumedUnit: str = "unit"
    BilledCost: float = 0.0
    EffectiveCost: float = 0.0
    BillingCurrency: str = "USD"
    Tags: dict[str, str] = Field(default_factory=dict)
    CommitmentDiscountId: str | None = None
    FocusVersion: str = "1.4"


class CloudResource(CloudCauseModel):
    provider: Provider
    resource_id: str
    resource_name: str | None = None
    resource_type: str
    region_id: str | None = None
    state: str = "unknown"
    created_at: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, str] = Field(default_factory=dict)

    @property
    def owner(self) -> str | None:
        for key in ("owner", "Owner", "team", "Team"):
            if key in self.tags:
                return self.tags[key]
        return None


class MetricPoint(CloudCauseModel):
    timestamp: datetime
    value: float


class MetricSeries(CloudCauseModel):
    provider: Provider
    resource_id: str
    metric_name: str
    unit: str = "Count"
    statistic: str = "Sum"
    points: list[MetricPoint] = Field(default_factory=list)

    def window_total(self, start: date, end: date) -> float:
        return sum(p.value for p in self.points if start <= p.timestamp.date() <= end)

    def window_average(self, start: date, end: date) -> float:
        values = [p.value for p in self.points if start <= p.timestamp.date() <= end]
        return sum(values) / len(values) if values else 0.0

    def window_max(self, start: date, end: date) -> float:
        values = [p.value for p in self.points if start <= p.timestamp.date() <= end]
        return max(values) if values else 0.0


class AuditEvent(CloudCauseModel):
    """A control-plane event. ``summary`` and ``attributes`` are untrusted text."""

    provider: Provider
    event_id: str
    event_name: str
    event_time: datetime
    source: str
    actor: str | None = None
    actor_type: str = "unknown"
    region_id: str | None = None
    source_ip: str | None = None
    source_location: str | None = None
    resource_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class Recommendation(CloudCauseModel):
    provider: Provider
    recommendation_id: str
    source: str
    category: str
    resource_id: str | None = None
    description: str = ""
    estimated_monthly_savings: float = 0.0
    currency: str = "USD"


class ProviderDataBundle(CloudCauseModel):
    """Everything one provider specialist may read for one investigation."""

    provider: Provider
    costs: SourceResult[CostRecord]
    resources: SourceResult[CloudResource]
    metrics: SourceResult[MetricSeries]
    audit_events: SourceResult[AuditEvent]
    recommendations: SourceResult[Recommendation]

    @property
    def sources(self) -> list[Provenance]:
        return [
            self.costs.provenance,
            self.resources.provenance,
            self.metrics.provenance,
            self.audit_events.provenance,
            self.recommendations.provenance,
        ]

    def resource_ids(self) -> set[str]:
        ids = {resource.resource_id for resource in self.resources.items}
        ids.update(record.resource_id for record in self.costs.items if record.resource_id)
        return {value for value in ids if value}

    def resource(self, resource_id: str) -> CloudResource | None:
        for candidate in self.resources.items:
            if candidate.resource_id == resource_id:
                return candidate
        return None

    def metrics_for(self, resource_id: str) -> list[MetricSeries]:
        return [series for series in self.metrics.items if series.resource_id == resource_id]

    def events_for(self, resource_id: str) -> list[AuditEvent]:
        return [event for event in self.audit_events.items if resource_id in event.resource_ids]

    def recommendations_for(self, resource_id: str) -> list[Recommendation]:
        return [rec for rec in self.recommendations.items if rec.resource_id == resource_id]

    def data_through(self) -> datetime:
        return min(source.data_through for source in self.sources)

    def origin(self) -> DataOrigin:
        """The least-verified origin present, so nothing is over-claimed."""

        origins = {source.origin for source in self.sources}
        for candidate in ("upload", "fixture", "live"):
            if candidate in origins:
                return candidate  # type: ignore[return-value]
        return "fixture"

    def available_source_types(self) -> set[str]:
        """Which ``Evidence.source_type`` values this bundle could actually back.

        A cost-only upload can support "what changed" and nothing else, so the
        validator needs to know the difference between "no metric matched" and
        "no metric series exists".
        """

        available: set[str] = set()
        if self.costs.items:
            available.update(("cost", "usage"))
        if self.resources.items:
            available.add("inventory")
        if self.metrics.items:
            available.add("metric")
        if self.audit_events.items:
            available.add("audit")
        if self.recommendations.items:
            available.add("recommendation")
        return available

    def is_fixture(self) -> bool:
        return any(source.is_fixture for source in self.sources)
