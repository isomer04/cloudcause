"""Period comparison, grouping, materiality filtering, and reconciliation.

The agents never do this arithmetic. They receive the candidates below and are
asked to explain them with evidence.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from cloudcause_contracts import (
    AnalyticsConfig,
    AnomalyCandidate,
    CostRecord,
    DailyTotal,
    DateRange,
    Dimension,
    Finding,
    PeriodComparison,
    Provider,
    ProviderComparison,
    Reconciliation,
)

_SPIKE_MULTIPLIER = 1.5


def daily_totals(records: Iterable[CostRecord], period: DateRange) -> list[DailyTotal]:
    billed: dict[date, float] = defaultdict(float)
    effective: dict[date, float] = defaultdict(float)
    for record in records:
        if period.contains(record.usage_date):
            billed[record.usage_date] += record.billed_cost
            effective[record.usage_date] += record.effective_cost
    return [
        DailyTotal(
            usage_date=day,
            billed_cost=round(billed.get(day, 0.0), 6),
            effective_cost=round(effective.get(day, 0.0), 6),
        )
        for day in period.dates()
    ]


@dataclass
class _Aggregate:
    key: str
    dimension: Dimension
    provider: Provider
    billing_account_id: str | None = None
    service_name: str | None = None
    service_category: str | None = None
    region_id: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    currency: str = "USD"
    sku_ids: set[str] = field(default_factory=set)
    tags: dict[str, str] = field(default_factory=dict)
    current_cost: float = 0.0
    baseline_cost: float = 0.0
    current_quantity: float = 0.0
    baseline_quantity: float = 0.0
    current_daily: dict[date, float] = field(default_factory=lambda: defaultdict(float))
    service_costs: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    service_categories: dict[str, str] = field(default_factory=dict)

    def add(self, record: CostRecord, in_current: bool) -> None:
        self.billing_account_id = self.billing_account_id or record.billing_account_id
        # One resource can carry several services (an instance plus its data
        # transfer). The service with the largest current cost names the candidate.
        self.service_costs[record.service_name] += record.effective_cost if in_current else 0.0
        self.service_categories.setdefault(record.service_name, record.service_category)
        self.region_id = self.region_id or record.region_id
        self.resource_id = self.resource_id or record.resource_id
        self.resource_name = self.resource_name or record.resource_name
        self.currency = record.currency or self.currency
        if record.sku_id:
            self.sku_ids.add(record.sku_id)
        dominant = max(self.service_costs.items(), key=lambda item: item[1], default=None)
        if dominant and dominant[1] > 0.0:
            self.service_name = dominant[0]
            self.service_category = self.service_categories.get(dominant[0])
        elif self.service_name is None:
            self.service_name = record.service_name
            self.service_category = record.service_category
        for tag_key, tag_value in record.tags.items():
            self.tags.setdefault(tag_key, tag_value)
        if in_current:
            self.current_cost += record.effective_cost
            self.current_quantity += record.usage_quantity
            self.current_daily[record.usage_date] += record.effective_cost
        else:
            self.baseline_cost += record.effective_cost
            self.baseline_quantity += record.usage_quantity


def _dimension_key(record: CostRecord, dimension: Dimension) -> str | None:
    if dimension == "resource":
        return record.resource_id
    if dimension == "service":
        return record.service_name
    if dimension == "region":
        return record.region_id or "unknown-region"
    if dimension == "account":
        return record.billing_account_id
    if dimension == "tag_owner":
        for key in ("owner", "Owner", "team", "Team"):
            if record.tags.get(key):
                return record.tags[key]
        return "untagged"
    return None


def _percent(current: float, expected: float) -> float | None:
    if expected <= 0.0:
        return None
    return round(((current - expected) / expected) * 100.0, 3)


def _unit_cost(cost: float, quantity: float) -> float | None:
    if quantity <= 0.0:
        return None
    return round(cost / quantity, 8)


def _first_spike(aggregate: _Aggregate, current: DateRange, baseline_daily_average: float) -> date | None:
    threshold = max(baseline_daily_average * _SPIKE_MULTIPLIER, 0.01)
    for day in current.dates():
        if aggregate.current_daily.get(day, 0.0) > threshold:
            return day
    return None


def _build_candidate(
    aggregate: _Aggregate,
    *,
    candidate_id: str,
    current: DateRange,
    baseline: DateRange,
) -> AnomalyCandidate:
    scale = current.days / baseline.days if baseline.days else 1.0
    expected_baseline = round(aggregate.baseline_cost * scale, 6)
    expected_quantity = aggregate.baseline_quantity * scale
    baseline_daily_average = aggregate.baseline_cost / baseline.days if baseline.days else 0.0
    return AnomalyCandidate(
        candidate_id=candidate_id,
        provider=aggregate.provider,
        dimension=aggregate.dimension,
        key=aggregate.key,
        billing_account_id=aggregate.billing_account_id,
        service_name=aggregate.service_name,
        service_category=aggregate.service_category,
        region_id=aggregate.region_id,
        resource_id=aggregate.resource_id,
        resource_name=aggregate.resource_name,
        sku_ids=sorted(aggregate.sku_ids),
        tags=dict(aggregate.tags),
        baseline_cost=round(aggregate.baseline_cost, 6),
        current_cost=round(aggregate.current_cost, 6),
        expected_baseline_cost=expected_baseline,
        absolute_change=round(aggregate.current_cost - expected_baseline, 6),
        percent_change=_percent(aggregate.current_cost, expected_baseline),
        baseline_daily_average=round(baseline_daily_average, 6),
        current_daily_average=round(aggregate.current_cost / current.days if current.days else 0.0, 6),
        baseline_quantity=round(aggregate.baseline_quantity, 6),
        current_quantity=round(aggregate.current_quantity, 6),
        quantity_percent_change=_percent(aggregate.current_quantity, expected_quantity),
        unit_cost_baseline=_unit_cost(aggregate.baseline_cost, aggregate.baseline_quantity),
        unit_cost_current=_unit_cost(aggregate.current_cost, aggregate.current_quantity),
        first_spike_date=_first_spike(aggregate, current, baseline_daily_average),
        is_new=aggregate.baseline_cost <= 0.0 and aggregate.current_cost > 0.0,
        currency=aggregate.currency,
    )


def _is_material(candidate: AnomalyCandidate, config: AnalyticsConfig) -> bool:
    if candidate.absolute_change < config.min_absolute_change:
        return False
    if candidate.is_new:
        return True
    if candidate.percent_change is None:
        return True
    return candidate.percent_change >= config.min_percent_change


def group_changes(
    records: Sequence[CostRecord],
    dimension: Dimension,
    current: DateRange,
    baseline: DateRange,
    provider: Provider,
) -> list[AnomalyCandidate]:
    """Group a provider's records by one dimension and return all changes.

    Used for cross-checks and for the read-only breakdown tool; candidate
    selection uses :func:`compare_provider`.
    """

    aggregates: dict[str, _Aggregate] = {}
    for record in records:
        if record.provider != provider:
            continue
        in_current = current.contains(record.usage_date)
        if not in_current and not baseline.contains(record.usage_date):
            continue
        key = _dimension_key(record, dimension)
        if key is None:
            continue
        aggregate = aggregates.get(key)
        if aggregate is None:
            aggregate = _Aggregate(key=key, dimension=dimension, provider=provider)
            aggregates[key] = aggregate
        aggregate.add(record, in_current)

    changes = [
        _build_candidate(
            aggregate,
            candidate_id=f"{provider}-{dimension}-{index:02d}",
            current=current,
            baseline=baseline,
        )
        for index, aggregate in enumerate(
            sorted(aggregates.values(), key=lambda item: item.current_cost - item.baseline_cost, reverse=True)
        )
    ]
    return changes


def compare_provider(
    records: Sequence[CostRecord],
    provider: Provider,
    current: DateRange,
    baseline: DateRange,
    config: AnalyticsConfig | None = None,
) -> ProviderComparison:
    """Compare one provider's two periods and emit material candidates.

    The candidate partition is resource-level where a resource id exists and
    service-level otherwise, so candidate costs never double count.
    """

    config = config or AnalyticsConfig()
    provider_records = [record for record in records if record.provider == provider]
    aggregates: dict[str, _Aggregate] = {}
    currency = config.currency
    for record in provider_records:
        in_current = current.contains(record.usage_date)
        if not in_current and not baseline.contains(record.usage_date):
            continue
        currency = record.currency or currency
        if record.resource_id:
            dimension: Dimension = "resource"
            key = record.resource_id
        else:
            dimension = "service"
            key = record.service_name
        aggregate = aggregates.get(key)
        if aggregate is None:
            aggregate = _Aggregate(key=key, dimension=dimension, provider=provider)
            aggregates[key] = aggregate
        aggregate.add(record, in_current)

    ordered = sorted(
        aggregates.values(),
        key=lambda item: item.current_cost - item.baseline_cost,
        reverse=True,
    )
    candidates: list[AnomalyCandidate] = []
    for index, aggregate in enumerate(ordered):
        candidate = _build_candidate(
            aggregate,
            candidate_id=f"{provider}-cand-{index:02d}",
            current=current,
            baseline=baseline,
        )
        if _is_material(candidate, config):
            candidates.append(candidate)
    candidates = candidates[: config.max_candidates_per_provider]

    current_cost = round(sum(a.current_cost for a in aggregates.values()), 6)
    baseline_cost = round(sum(a.baseline_cost for a in aggregates.values()), 6)
    scale = current.days / baseline.days if baseline.days else 1.0
    expected_baseline = round(baseline_cost * scale, 6)
    total_change = round(current_cost - expected_baseline, 6)
    reconciliation = reconcile(
        total_change=total_change,
        attributed=sum(candidate.absolute_change for candidate in candidates),
        tolerance=config.reconciliation_tolerance,
        note=(
            "Attributed value is the sum of material increase candidates; "
            "the remainder is small changes and offsetting decreases."
        ),
    )
    return ProviderComparison(
        provider=provider,
        current_period=current,
        baseline_period=baseline,
        current_cost=current_cost,
        baseline_cost=baseline_cost,
        expected_baseline_cost=expected_baseline,
        absolute_change=total_change,
        percent_change=_percent(current_cost, expected_baseline),
        daily_current=daily_totals(provider_records, current),
        daily_baseline=daily_totals(provider_records, baseline),
        candidates=candidates,
        reconciliation=reconciliation,
        currency=currency,
    )


def compare_periods(
    records: Sequence[CostRecord],
    providers: Sequence[Provider],
    current: DateRange,
    baseline: DateRange,
    config: AnalyticsConfig | None = None,
) -> PeriodComparison:
    config = config or AnalyticsConfig()
    comparisons = [
        compare_provider(records, provider, current, baseline, config) for provider in providers
    ]
    total_current = round(sum(item.current_cost for item in comparisons), 6)
    total_expected = round(sum(item.expected_baseline_cost for item in comparisons), 6)
    total_baseline = round(sum(item.baseline_cost for item in comparisons), 6)
    total_change = round(total_current - total_expected, 6)
    attributed = sum(
        candidate.absolute_change for item in comparisons for candidate in item.candidates
    )
    return PeriodComparison(
        current_period=current,
        baseline_period=baseline,
        config=config,
        providers=comparisons,
        total_current_cost=total_current,
        total_baseline_cost=total_baseline,
        total_absolute_change=total_change,
        total_percent_change=_percent(total_current, total_expected),
        reconciliation=reconcile(
            total_change=total_change,
            attributed=attributed,
            tolerance=config.reconciliation_tolerance,
            note="Cross-provider candidate coverage before agent investigation.",
        ),
    )


def reconcile(
    *, total_change: float, attributed: float, tolerance: float, note: str = ""
) -> Reconciliation:
    attributed = round(attributed, 6)
    unattributed = round(total_change - attributed, 6)
    scale = max(abs(total_change), 1.0)
    return Reconciliation(
        total_change=round(total_change, 6),
        attributed_change=attributed,
        unattributed_change=unattributed,
        tolerance=tolerance,
        within_tolerance=abs(unattributed) <= tolerance * scale,
        note=note,
    )


def reconcile_findings(
    findings: Iterable[Finding], total_change: float, tolerance: float
) -> Reconciliation:
    """Reconcile evidence-backed findings against the measured total change."""

    attributed = sum(finding.actual_cost_increase for finding in findings)
    return reconcile(
        total_change=total_change,
        attributed=attributed,
        tolerance=tolerance,
        note="Attributed value is the sum of evidence-backed findings.",
    )
