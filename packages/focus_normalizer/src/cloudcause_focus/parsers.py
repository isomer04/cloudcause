"""Provider-native cost parsers.

Fixture adapters and future live adapters share these functions, so a fixture
run and a live run produce identical ``CostRecord`` shapes.

* AWS: Data Exports / CUR 2.0 column names.
* Azure: Cost Management query result (``columns`` + ``rows``).
* GCP: BigQuery billing export CSV column names.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from cloudcause_contracts import ChargeCategory, CostRecord

from .categories import service_category

_AWS_CHARGE_TYPES: dict[str, ChargeCategory] = {
    "usage": "usage",
    "discountedusage": "usage",
    "savingsplancoverednusage": "usage",
    "savingsplancoveredusage": "usage",
    "savingsplanrecurringfee": "purchase",
    "ripurchase": "purchase",
    "fee": "purchase",
    "tax": "tax",
    "credit": "credit",
    "refund": "credit",
    "savingsplannegation": "adjustment",
    "edpdiscount": "adjustment",
}

_AZURE_CHARGE_TYPES: dict[str, ChargeCategory] = {
    "usage": "usage",
    "purchase": "purchase",
    "tax": "tax",
    "refund": "credit",
    "unusedreservation": "usage",
    "adjustment": "adjustment",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, int):  # Azure returns 20260713
        text = str(value)
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    text = str(value).strip()
    if text.isdigit() and len(text) == 8:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return date.fromisoformat(text[:10])


def _as_tags(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return {str(key): str(item) for key, item in parsed.items()}
        if isinstance(parsed, list):  # GCP labels: [{"key":..,"value":..}]
            tags: dict[str, str] = {}
            for entry in parsed:
                if isinstance(entry, Mapping) and "key" in entry:
                    tags[str(entry["key"])] = str(entry.get("value", ""))
            return tags
    return {}


def _resource_name_from_id(resource_id: str | None) -> str | None:
    if not resource_id:
        return None
    for separator in ("/", ":"):
        if separator in resource_id:
            resource_id = resource_id.rsplit(separator, 1)[-1]
    return resource_id or None


def parse_aws_rows(rows: Iterable[Mapping[str, Any]]) -> list[CostRecord]:
    """Parse AWS Data Exports / CUR rows into ``CostRecord`` objects."""

    records: list[CostRecord] = []
    for index, row in enumerate(rows):
        service = str(row.get("product_servicename") or row.get("line_item_product_code") or "unknown")
        line_type = str(row.get("line_item_line_item_type") or "Usage").lower()
        resource_id = row.get("line_item_resource_id") or None
        records.append(
            CostRecord(
                provider="aws",
                billing_account_id=str(
                    row.get("line_item_usage_account_id") or row.get("bill_payer_account_id") or "unknown"
                ),
                usage_date=_as_date(row.get("line_item_usage_start_date")),
                service_name=service,
                service_category=service_category(service),
                charge_category=_AWS_CHARGE_TYPES.get(line_type, "unknown"),
                charge_description=str(row.get("line_item_line_item_description") or ""),
                region_id=(str(row.get("product_region_code")) if row.get("product_region_code") else None),
                resource_id=str(resource_id) if resource_id else None,
                resource_name=str(row.get("resource_tags_user_name") or "")
                or _resource_name_from_id(str(resource_id) if resource_id else None),
                sku_id=str(row.get("line_item_usage_type") or row.get("product_sku") or "") or None,
                usage_quantity=_as_float(row.get("line_item_usage_amount")),
                usage_unit=str(row.get("pricing_unit") or "unit"),
                billed_cost=_as_float(row.get("line_item_unblended_cost")),
                effective_cost=_as_float(
                    row.get("line_item_net_amortized_cost"), _as_float(row.get("line_item_unblended_cost"))
                ),
                currency=str(row.get("line_item_currency_code") or "USD"),
                tags=_as_tags(row.get("resource_tags")),
                commitment_discount_id=str(row.get("savings_plan_savings_plan_a_r_n") or "") or None,
                source_record_id=str(row.get("identity_line_item_id") or f"aws-row-{index}"),
            )
        )
    return records


def parse_azure_cost_management(document: Mapping[str, Any]) -> list[CostRecord]:
    """Parse an Azure Cost Management query result document."""

    properties = document.get("properties", document)
    columns: Sequence[Mapping[str, Any]] = properties.get("columns", [])
    names = [str(column.get("name")) for column in columns]
    records: list[CostRecord] = []
    for index, row in enumerate(properties.get("rows", [])):
        values = dict(zip(names, row, strict=False))
        service = str(values.get("MeterCategory") or values.get("ServiceName") or "unknown")
        resource_id = values.get("ResourceId") or None
        charge_type = str(values.get("ChargeType") or "Usage").lower()
        cost = _as_float(values.get("Cost"), _as_float(values.get("CostUSD")))
        records.append(
            CostRecord(
                provider="azure",
                billing_account_id=str(values.get("SubscriptionId") or "unknown"),
                usage_date=_as_date(values.get("UsageDate")),
                service_name=service,
                service_category=service_category(service),
                charge_category=_AZURE_CHARGE_TYPES.get(charge_type, "unknown"),
                charge_description=str(values.get("Meter") or values.get("MeterSubCategory") or ""),
                region_id=str(values.get("ResourceLocation") or "") or None,
                resource_id=str(resource_id) if resource_id else None,
                resource_name=_resource_name_from_id(str(resource_id) if resource_id else None),
                sku_id=str(values.get("Meter") or "") or None,
                usage_quantity=_as_float(values.get("UsageQuantity")),
                usage_unit=str(values.get("UnitOfMeasure") or "unit"),
                billed_cost=cost,
                effective_cost=_as_float(values.get("AmortizedCost"), cost),
                currency=str(values.get("Currency") or "USD"),
                tags=_as_tags(values.get("Tags")),
                commitment_discount_id=str(values.get("ReservationId") or "") or None,
                source_record_id=f"azure-row-{index}",
            )
        )
    return records


def parse_gcp_billing_export(rows: Iterable[Mapping[str, Any]]) -> list[CostRecord]:
    """Parse GCP BigQuery billing export rows (CSV or JSON with the same columns)."""

    records: list[CostRecord] = []
    for index, row in enumerate(rows):
        service = str(row.get("service.description") or "unknown")
        resource_id = row.get("resource.global_name") or row.get("resource.name") or None
        credits = _as_float(row.get("credits.amount"))
        cost = _as_float(row.get("cost"))
        records.append(
            CostRecord(
                provider="gcp",
                billing_account_id=str(row.get("billing_account_id") or "unknown"),
                usage_date=_as_date(row.get("usage_start_time")),
                service_name=service,
                service_category=service_category(service),
                charge_category="usage" if cost >= 0 else "credit",
                charge_description=str(row.get("sku.description") or ""),
                region_id=str(row.get("location.location") or "") or None,
                resource_id=str(resource_id) if resource_id else None,
                resource_name=_resource_name_from_id(str(resource_id) if resource_id else None),
                sku_id=str(row.get("sku.id") or row.get("sku.description") or "") or None,
                usage_quantity=_as_float(row.get("usage.amount")),
                usage_unit=str(row.get("usage.unit") or "unit"),
                billed_cost=cost,
                effective_cost=round(cost + credits, 6),
                currency=str(row.get("currency") or "USD"),
                tags=_as_tags(row.get("labels")),
                commitment_discount_id=str(row.get("credits.name") or "") or None,
                source_record_id=f"gcp-row-{index}",
            )
        )
    return records


PARSERS = {
    "aws": parse_aws_rows,
    "azure": parse_azure_cost_management,
    "gcp": parse_gcp_billing_export,
}
