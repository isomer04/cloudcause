"""Deterministic unit tests for parsing and FOCUS normalization."""

from __future__ import annotations

from datetime import date

import pytest
from cloudcause_contracts import CostRecord, DateRange
from cloudcause_focus import (
    UnsupportedSchemaVersionError,
    filter_records,
    parse_aws_rows,
    parse_azure_cost_management,
    parse_gcp_billing_export,
    require_supported_export_schema,
    service_category,
    to_focus_records,
    total_cost,
)

AWS_ROW = {
    "identity_line_item_id": "aws-1",
    "line_item_usage_account_id": "111122223333",
    "line_item_usage_start_date": "2026-07-15",
    "line_item_line_item_type": "Usage",
    "product_servicename": "Amazon Virtual Private Cloud",
    "product_region_code": "us-east-1",
    "line_item_resource_id": "nat-0ab12cd34ef56789a",
    "line_item_usage_type": "NatGateway-Bytes",
    "line_item_usage_amount": "580",
    "pricing_unit": "GB",
    "line_item_unblended_cost": "26.1",
    "line_item_net_amortized_cost": "26.1",
    "line_item_currency_code": "USD",
    "resource_tags": {"env": "prod"},
}

AZURE_DOCUMENT = {
    "properties": {
        "columns": [
            {"name": "UsageDate"},
            {"name": "Cost"},
            {"name": "UsageQuantity"},
            {"name": "MeterCategory"},
            {"name": "ResourceId"},
            {"name": "SubscriptionId"},
            {"name": "Tags"},
        ],
        "rows": [[20260715, 18.4, 190, "Azure Functions", "/subscriptions/s/sites/app", "s", '{"env":"prod"}']],
    }
}

GCP_ROW = {
    "billing_account_id": "01ABCD-2345EF-6789GH",
    "service.description": "Cloud Translation API",
    "sku.id": "9B4A",
    "sku.description": "Characters",
    "usage_start_time": "2026-07-16T00:00:00Z",
    "location.location": "us-central1",
    "resource.global_name": "//serviceusage.googleapis.com/x",
    "usage.amount": "2.05",
    "usage.unit": "million characters",
    "cost": "41.0",
    "currency": "USD",
    "credits.amount": "-1.0",
    "labels": '[{"key":"env","value":"prod"}]',
}


def test_aws_rows_parse_into_cost_records() -> None:
    (record,) = parse_aws_rows([AWS_ROW])
    assert record.provider == "aws"
    assert record.usage_date == date(2026, 7, 15)
    assert record.service_category == "Networking"
    assert record.resource_id == "nat-0ab12cd34ef56789a"
    assert record.effective_cost == pytest.approx(26.1)
    assert record.tags == {"env": "prod"}


def test_azure_columns_and_rows_parse() -> None:
    (record,) = parse_azure_cost_management(AZURE_DOCUMENT)
    assert record.provider == "azure"
    assert record.usage_date == date(2026, 7, 15)
    assert record.service_name == "Azure Functions"
    assert record.service_category == "Compute"
    assert record.usage_quantity == pytest.approx(190)


def test_gcp_credits_are_added_not_subtracted() -> None:
    (record,) = parse_gcp_billing_export([GCP_ROW])
    assert record.billed_cost == pytest.approx(41.0)
    assert record.effective_cost == pytest.approx(40.0)
    assert record.tags == {"env": "prod"}


def test_focus_projection_uses_specification_columns() -> None:
    (record,) = parse_aws_rows([AWS_ROW])
    (focus,) = to_focus_records([record])
    assert focus.FocusVersion == "1.4"
    assert focus.ProviderName == "aws"
    assert focus.ServiceCategory == "Networking"
    assert focus.ChargePeriodStart.date() == date(2026, 7, 15)
    assert focus.ChargePeriodEnd.date() == date(2026, 7, 16)
    assert focus.BilledCost == pytest.approx(26.1)


def test_unknown_focus_version_fails_safely() -> None:
    (record,) = parse_aws_rows([AWS_ROW])
    with pytest.raises(UnsupportedSchemaVersionError) as error:
        to_focus_records([record], focus_version="2.0")
    assert "quarantined" in str(error.value)


def test_unknown_export_schema_fails_safely() -> None:
    require_supported_export_schema("aws", "2.0")
    with pytest.raises(UnsupportedSchemaVersionError):
        require_supported_export_schema("aws", "9.9")


def test_filter_records_scopes_by_period_account_and_category() -> None:
    records = [
        CostRecord(
            provider="aws",
            billing_account_id="111122223333",
            usage_date=date(2026, 7, 15),
            service_name="Amazon EC2",
            billed_cost=10.0,
            effective_cost=10.0,
        ),
        CostRecord(
            provider="aws",
            billing_account_id="999988887777",
            usage_date=date(2026, 7, 15),
            service_name="Amazon EC2",
            billed_cost=5.0,
            effective_cost=5.0,
        ),
        CostRecord(
            provider="aws",
            billing_account_id="111122223333",
            usage_date=date(2026, 7, 1),
            service_name="Amazon EC2",
            billed_cost=7.0,
            effective_cost=7.0,
        ),
        CostRecord(
            provider="aws",
            billing_account_id="111122223333",
            usage_date=date(2026, 7, 15),
            service_name="Refund",
            charge_category="credit",
            billed_cost=-3.0,
            effective_cost=-3.0,
        ),
    ]
    selected = filter_records(
        records,
        periods=[DateRange(start=date(2026, 7, 13), end=date(2026, 7, 19))],
        providers=["aws"],
        account_ids=["111122223333"],
    )
    assert total_cost(selected) == pytest.approx(10.0)


def test_service_category_falls_back_to_other() -> None:
    assert service_category("Amazon Bedrock") == "AI and Machine Learning"
    assert service_category("Some Brand New Service") == "Other"
