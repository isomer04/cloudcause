"""Deterministic analytics: period comparison, materiality, reconciliation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from cloudcause_anomaly import compare_periods, compare_provider, daily_totals, group_changes, reconcile
from cloudcause_contracts import AnalyticsConfig, CostRecord, DateRange

CURRENT = DateRange(start=date(2026, 7, 13), end=date(2026, 7, 19))
BASELINE = DateRange(start=date(2026, 7, 6), end=date(2026, 7, 12))


def record(
    day: date,
    cost: float,
    *,
    service: str = "Amazon Virtual Private Cloud",
    resource: str | None = "nat-1",
    quantity: float = 1.0,
    region: str = "us-east-1",
    account: str = "111122223333",
    tags: dict[str, str] | None = None,
) -> CostRecord:
    return CostRecord(
        provider="aws",
        billing_account_id=account,
        usage_date=day,
        service_name=service,
        region_id=region,
        resource_id=resource,
        usage_quantity=quantity,
        billed_cost=cost,
        effective_cost=cost,
        tags=tags or {},
    )


def flat_series(period: DateRange, cost: float, **kwargs) -> list[CostRecord]:
    return [record(day, cost, **kwargs) for day in period.dates()]


def test_daily_totals_cover_every_day_in_the_period() -> None:
    totals = daily_totals(flat_series(CURRENT, 10.0), CURRENT)
    assert len(totals) == 7
    assert all(total.effective_cost == pytest.approx(10.0) for total in totals)


def test_baseline_is_length_adjusted() -> None:
    short_baseline = DateRange(start=date(2026, 7, 10), end=date(2026, 7, 12))
    records = [*flat_series(short_baseline, 10.0), *flat_series(CURRENT, 10.0)]
    comparison = compare_provider(records, "aws", CURRENT, short_baseline)
    # 3 baseline days scaled to 7 current days: no change at all.
    assert comparison.expected_baseline_cost == pytest.approx(70.0)
    assert comparison.absolute_change == pytest.approx(0.0)


def test_material_increase_becomes_a_candidate_with_a_spike_date() -> None:
    records = [*flat_series(BASELINE, 1.0)]
    for index, day in enumerate(CURRENT.dates()):
        records.append(record(day, 1.0 if index < 2 else 30.0))
    comparison = compare_provider(records, "aws", CURRENT, BASELINE)
    (candidate,) = comparison.candidates
    assert candidate.absolute_change == pytest.approx(145.0)
    assert candidate.first_spike_date == CURRENT.start + timedelta(days=2)
    assert candidate.percent_change == pytest.approx(2071.429, abs=0.01)
    assert candidate.dimension == "resource"


def test_small_and_low_percentage_changes_are_filtered_out() -> None:
    records = [*flat_series(BASELINE, 100.0), *flat_series(CURRENT, 101.0)]
    config = AnalyticsConfig(min_absolute_change=5.0, min_percent_change=20.0)
    comparison = compare_provider(records, "aws", CURRENT, BASELINE, config)
    assert comparison.candidates == []
    assert comparison.absolute_change == pytest.approx(7.0)


def test_new_spend_is_material_even_without_a_percentage() -> None:
    records = [*flat_series(BASELINE, 10.0, resource="old"), *flat_series(CURRENT, 10.0, resource="old")]
    records += flat_series(CURRENT, 4.0, resource="brand-new")
    comparison = compare_provider(records, "aws", CURRENT, BASELINE)
    (candidate,) = comparison.candidates
    assert candidate.resource_id == "brand-new"
    assert candidate.is_new is True
    assert candidate.percent_change is None


def test_rate_change_is_distinguishable_from_usage_growth() -> None:
    records = [*flat_series(BASELINE, 10.0, quantity=10.0), *flat_series(CURRENT, 20.0, quantity=10.0)]
    comparison = compare_provider(records, "aws", CURRENT, BASELINE)
    (candidate,) = comparison.candidates
    assert candidate.quantity_percent_change == pytest.approx(0.0)
    assert candidate.is_quantity_driven is False
    assert candidate.unit_cost_current == pytest.approx(2 * candidate.unit_cost_baseline)


def test_dominant_service_names_a_multi_service_resource() -> None:
    records = [
        *flat_series(BASELINE, 9.0, service="Amazon Relational Database Service", resource="db"),
        *flat_series(BASELINE, 1.5, service="AWS Data Transfer", resource="db"),
        *flat_series(CURRENT, 9.0, service="Amazon Relational Database Service", resource="db"),
        *flat_series(CURRENT, 21.0, service="AWS Data Transfer", resource="db"),
    ]
    comparison = compare_provider(records, "aws", CURRENT, BASELINE)
    (candidate,) = comparison.candidates
    assert candidate.service_name == "AWS Data Transfer"


def test_group_changes_supports_every_dimension() -> None:
    records = [
        *flat_series(BASELINE, 10.0, region="us-east-1", tags={"owner": "platform"}),
        *flat_series(CURRENT, 40.0, region="us-east-1", tags={"owner": "platform"}),
    ]
    for dimension in ("service", "region", "account", "resource", "tag_owner"):
        changes = group_changes(records, dimension, CURRENT, BASELINE, "aws")
        assert changes and changes[0].absolute_change == pytest.approx(210.0)


def test_cross_provider_totals_and_reconciliation() -> None:
    aws = [*flat_series(BASELINE, 10.0), *flat_series(CURRENT, 40.0)]
    azure = [
        CostRecord(
            provider="azure",
            billing_account_id="sub",
            usage_date=day,
            service_name="Azure Functions",
            resource_id="app",
            billed_cost=5.0,
            effective_cost=5.0,
        )
        for day in [*BASELINE.dates(), *CURRENT.dates()]
    ]
    comparison = compare_periods([*aws, *azure], ["aws", "azure"], CURRENT, BASELINE)
    assert comparison.total_absolute_change == pytest.approx(210.0)
    assert comparison.reconciliation is not None
    assert comparison.reconciliation.within_tolerance is True
    assert comparison.for_provider("azure").candidates == []


def test_reconcile_flags_unattributed_cost() -> None:
    result = reconcile(total_change=100.0, attributed=80.0, tolerance=0.05)
    assert result.unattributed_change == pytest.approx(20.0)
    assert result.within_tolerance is False
