"""FOCUS 1.4 normalization and filtering helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, time, timedelta

from cloudcause_contracts import SUPPORTED_FOCUS_VERSION, CostRecord, DateRange, FocusRecord, Provider

from .versions import require_supported_focus_version


def to_focus_record(record: CostRecord, focus_version: str = SUPPORTED_FOCUS_VERSION) -> FocusRecord:
    require_supported_focus_version(focus_version)
    start = datetime.combine(record.usage_date, time.min, tzinfo=UTC)
    period_start = record.usage_date.replace(day=1)
    next_month = (period_start + timedelta(days=32)).replace(day=1)
    return FocusRecord(
        ProviderName=record.provider,
        BillingAccountId=record.billing_account_id,
        ChargePeriodStart=start,
        ChargePeriodEnd=start + timedelta(days=1),
        BillingPeriodStart=period_start,
        BillingPeriodEnd=next_month - timedelta(days=1),
        ServiceName=record.service_name,
        ServiceCategory=record.service_category,
        ChargeCategory=record.charge_category.capitalize(),
        ChargeDescription=record.charge_description,
        RegionId=record.region_id,
        ResourceId=record.resource_id,
        ResourceName=record.resource_name,
        SkuId=record.sku_id,
        ConsumedQuantity=record.usage_quantity,
        ConsumedUnit=record.usage_unit,
        BilledCost=record.billed_cost,
        EffectiveCost=record.effective_cost,
        BillingCurrency=record.currency,
        Tags=record.tags,
        CommitmentDiscountId=record.commitment_discount_id,
        FocusVersion=focus_version,
    )


def to_focus_records(
    records: Iterable[CostRecord], focus_version: str = SUPPORTED_FOCUS_VERSION
) -> list[FocusRecord]:
    require_supported_focus_version(focus_version)
    return [to_focus_record(record, focus_version) for record in records]


def filter_records(
    records: Sequence[CostRecord],
    *,
    periods: Sequence[DateRange] | None = None,
    providers: Sequence[Provider] | None = None,
    account_ids: Sequence[str] | None = None,
    include_charge_categories: Sequence[str] = ("usage", "purchase", "adjustment"),
) -> list[CostRecord]:
    """Restrict records to the investigated scope.

    Credits and tax are excluded from anomaly analysis by default because they
    are not usage signals, but they stay available for reconciliation.
    """

    provider_set = set(providers) if providers else None
    account_set = {value for value in (account_ids or []) if value}
    category_set = set(include_charge_categories)
    selected: list[CostRecord] = []
    for record in records:
        if provider_set and record.provider not in provider_set:
            continue
        if account_set and record.billing_account_id not in account_set:
            continue
        if category_set and record.charge_category not in category_set:
            continue
        if periods and not any(period.contains(record.usage_date) for period in periods):
            continue
        selected.append(record)
    return selected


def total_cost(records: Iterable[CostRecord], use_effective: bool = True) -> float:
    return round(
        sum(record.effective_cost if use_effective else record.billed_cost for record in records), 6
    )
