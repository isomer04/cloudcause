"""Outputs of the deterministic analytics layer.

Nothing in here is produced by a model. Agents consume these values, they never
recompute them.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from .common import CloudCauseModel, DateRange, Provider

Dimension = Literal["service", "region", "account", "resource", "tag_owner"]


class AnalyticsConfig(CloudCauseModel):
    """Materiality and reconciliation thresholds."""

    min_absolute_change: float = 5.0
    min_percent_change: float = 20.0
    reconciliation_tolerance: float = 0.05
    max_candidates_per_provider: int = 8
    currency: str = "USD"


class DailyTotal(CloudCauseModel):
    usage_date: date
    billed_cost: float
    effective_cost: float


class AnomalyCandidate(CloudCauseModel):
    """A material cost increase worth an agent investigation."""

    candidate_id: str
    provider: Provider
    dimension: Dimension
    key: str
    billing_account_id: str | None = None
    service_name: str | None = None
    service_category: str | None = None
    region_id: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    sku_ids: list[str] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)
    baseline_cost: float = 0.0
    current_cost: float = 0.0
    expected_baseline_cost: float = 0.0
    absolute_change: float = 0.0
    percent_change: float | None = None
    baseline_daily_average: float = 0.0
    current_daily_average: float = 0.0
    baseline_quantity: float = 0.0
    current_quantity: float = 0.0
    quantity_percent_change: float | None = None
    unit_cost_baseline: float | None = None
    unit_cost_current: float | None = None
    first_spike_date: date | None = None
    is_new: bool = False
    currency: str = "USD"

    @property
    def is_quantity_driven(self) -> bool:
        """True when usage grew, false when the unit rate moved instead."""
        if self.quantity_percent_change is None:
            return True
        return self.quantity_percent_change >= 10.0


class Reconciliation(CloudCauseModel):
    total_change: float
    attributed_change: float
    unattributed_change: float
    tolerance: float
    within_tolerance: bool
    note: str = ""


class ProviderComparison(CloudCauseModel):
    provider: Provider
    current_period: DateRange
    baseline_period: DateRange
    current_cost: float
    baseline_cost: float
    expected_baseline_cost: float
    absolute_change: float
    percent_change: float | None
    daily_current: list[DailyTotal] = Field(default_factory=list)
    daily_baseline: list[DailyTotal] = Field(default_factory=list)
    candidates: list[AnomalyCandidate] = Field(default_factory=list)
    reconciliation: Reconciliation
    currency: str = "USD"


class PeriodComparison(CloudCauseModel):
    """Cross-provider deterministic comparison for one investigation."""

    current_period: DateRange
    baseline_period: DateRange
    config: AnalyticsConfig
    providers: list[ProviderComparison] = Field(default_factory=list)
    total_current_cost: float = 0.0
    total_baseline_cost: float = 0.0
    total_absolute_change: float = 0.0
    total_percent_change: float | None = None
    reconciliation: Reconciliation | None = None

    def for_provider(self, provider: Provider) -> ProviderComparison | None:
        for comparison in self.providers:
            if comparison.provider == provider:
                return comparison
        return None

    def candidates_for(self, provider: Provider) -> list[AnomalyCandidate]:
        comparison = self.for_provider(provider)
        return list(comparison.candidates) if comparison else []

    def all_candidates(self) -> list[AnomalyCandidate]:
        return [candidate for comparison in self.providers for candidate in comparison.candidates]
