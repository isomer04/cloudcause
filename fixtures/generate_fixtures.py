"""Generate the committed CloudCause demo fixtures.

    python fixtures/generate_fixtures.py

Writes the provider-shaped export files plus the CloudCause fixture files for
inventory, metrics, audit events, and recommendations. Pure standard library, so
it runs before the workspace is installed.

The default scenario plants three causes:

1. AWS: S3 traffic starts passing through a NAT Gateway after a route change.
2. Azure: a Function App enters a retry loop after a deployment.
3. GCP: an exposed API key creates translation usage from new locations.

Plus a fourth, quieter one: a forgotten EC2 sandbox instance.

It also plants diffuse drift that no playbook should ever explain: each provider
has one untagged, resource-less SKU whose usage creeps up over the current week
by less than the materiality threshold. Real exports are never fully
attributable, and a demo that reconciles to exactly zero on every provider reads
as an answer written backwards from the question. The drift is what makes the
reconciler's tolerance band observable: roughly 10.66 USD of a 430.26 USD change
stays unattributed, inside tolerance, on purpose.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

BASELINE = (date(2026, 7, 6), date(2026, 7, 12))
CURRENT = (date(2026, 7, 13), date(2026, 7, 19))
DATA_THROUGH = "2026-07-19T23:59:59+00:00"
OBSERVED_AT = "2026-07-19T23:59:59+00:00"
RETRIEVED_AT = "2026-07-20T09:00:00+00:00"

AWS_ACCOUNT = "111122223333"
AWS_REGION = "us-east-1"
AZURE_SUBSCRIPTION = "8f3c2b71-9d4e-4a5f-8c21-7b6e5d4c3a2b"
AZURE_REGION = "eastus"
GCP_BILLING_ACCOUNT = "01ABCD-2345EF-6789GH"
GCP_PROJECT = "cloudcause-demo"
GCP_REGION = "us-central1"

AZURE_RG = f"/subscriptions/{AZURE_SUBSCRIPTION}/resourceGroups/rg-prod/providers"
FUNCTION_APP = f"{AZURE_RG}/Microsoft.Web/sites/orders-processor"
APP_PLAN = f"{AZURE_RG}/Microsoft.Web/serverFarms/plan-prod"
STORAGE_ACCOUNT = f"{AZURE_RG}/Microsoft.Storage/storageAccounts/ccdemoprod"
SQL_DATABASE = f"{AZURE_RG}/Microsoft.Sql/servers/cc-demo-sql/databases/orders"

GCP_INSTANCE = f"//compute.googleapis.com/projects/{GCP_PROJECT}/zones/us-central1-a/instances/api-prod-1"
GCP_BUCKET = "//storage.googleapis.com/projects/_/buckets/cc-demo-assets"
GCP_TRANSLATE = f"//serviceusage.googleapis.com/projects/{GCP_PROJECT}/services/translate.googleapis.com"
GCP_SQL = f"//cloudsql.googleapis.com/projects/{GCP_PROJECT}/instances/orders-db"
GCP_API_KEY = f"//apikeys.googleapis.com/projects/{GCP_PROJECT}/keys/8f2a4d19-7c33-4f81-93ab-19b0f5d2e1c1"


def days(period: tuple[date, date]) -> Iterator[date]:
    start, end = period
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def series_value(entry: dict[str, Any], day: date, in_current: bool) -> tuple[float, float]:
    """Cost and quantity for one series on one day."""

    starts_on = entry.get("starts_on")
    if in_current and entry.get("current") and (starts_on is None or day >= starts_on):
        return float(entry["current"]["cost"]), float(entry["current"]["qty"])
    base = entry["base"]
    return float(base["cost"]), float(base["qty"])


def iter_series(entries: list[dict[str, Any]]) -> Iterator[tuple[date, dict[str, Any], float, float]]:
    for period, in_current in ((BASELINE, False), (CURRENT, True)):
        for day in days(period):
            for entry in entries:
                cost, quantity = series_value(entry, day, in_current)
                if cost == 0.0 and quantity == 0.0:
                    continue
                yield day, entry, cost, quantity


def manifest(provider: str, cost_source: str, cost_schema: str, files: dict[str, str]) -> dict[str, Any]:
    def source(name: str, schema: str = "1") -> dict[str, Any]:
        return {
            "source": name,
            "schema_version": schema,
            "observed_at": OBSERVED_AT,
            "retrieved_at": RETRIEVED_AT,
            "data_through": DATA_THROUGH,
        }

    return {
        "provider": provider,
        "scenario_id": "default",
        "is_fixture": True,
        "currency": "USD",
        "period": {"start": BASELINE[0].isoformat(), "end": CURRENT[1].isoformat()},
        "baseline_period": {"start": BASELINE[0].isoformat(), "end": BASELINE[1].isoformat()},
        "current_period": {"start": CURRENT[0].isoformat(), "end": CURRENT[1].isoformat()},
        "sources": {
            "costs": {**source(cost_source, cost_schema), "file": files["costs"]},
            "resources": {**source(files["resources_source"]), "file": files["resources"]},
            "metrics": {**source(files["metrics_source"]), "file": files["metrics"]},
            "audit_events": {**source(files["audit_source"]), "file": files["audit"]},
            "recommendations": {**source(files["rec_source"]), "file": files["recommendations"]},
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" rather than the platform default, so the bytes are identical
    # on Windows and on a Linux CI runner and the reproducibility gate compares
    # content instead of line endings.
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT.parent)}")


def points(baseline_value: float, current_value: float, starts_on: date | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for period, in_current in ((BASELINE, False), (CURRENT, True)):
        for day in days(period):
            use_current = in_current and (starts_on is None or day >= starts_on)
            result.append(
                {
                    "timestamp": datetime.combine(
                        day, datetime.min.time(), tzinfo=UTC
                    ).replace(hour=12).isoformat(),
                    "value": current_value if use_current else baseline_value,
                }
            )
    return result


def metric(
    provider: str,
    resource_id: str,
    name: str,
    unit: str,
    statistic: str,
    baseline_value: float,
    current_value: float,
    starts_on: date | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "resource_id": resource_id,
        "metric_name": name,
        "unit": unit,
        "statistic": statistic,
        "points": points(baseline_value, current_value, starts_on),
    }


AWS_SERIES: list[dict[str, Any]] = [
    {
        "service": "Amazon Elastic Compute Cloud",
        "code": "AmazonEC2",
        "usage_type": "BoxUsage:m6i.large",
        "description": "$0.0714 per On Demand Linux m6i.large Instance Hour",
        "resource": "i-0a1b2c3d4e5f67890",
        "unit": "Hrs",
        "tags": {"env": "prod", "owner": "platform"},
        "base": {"cost": 12.0, "qty": 24},
    },
    {
        "service": "Amazon Simple Storage Service",
        "code": "AmazonS3",
        "usage_type": "TimedStorage-ByteHrs",
        "description": "$0.023 per GB-month of standard storage",
        "resource": "arn:aws:s3:::cc-demo-prod-data-lake",
        "unit": "GB-Mo",
        "tags": {"env": "prod", "owner": "data-platform"},
        "base": {"cost": 8.0, "qty": 320},
    },
    {
        "service": "Amazon Virtual Private Cloud",
        "code": "AmazonVPC",
        "usage_type": "NatGateway-Hours",
        "description": "$0.045 per NAT Gateway Hour",
        "resource": "nat-0ab12cd34ef56789a",
        "unit": "Hrs",
        "tags": {"env": "prod", "owner": "platform"},
        "base": {"cost": 1.08, "qty": 24},
    },
    {
        "service": "Amazon Virtual Private Cloud",
        "code": "AmazonVPC",
        "usage_type": "NatGateway-Bytes",
        "description": "$0.045 per GB data processed by NAT Gateways",
        "resource": "nat-0ab12cd34ef56789a",
        "unit": "GB",
        "tags": {"env": "prod", "owner": "platform"},
        "base": {"cost": 0.9, "qty": 20},
        "current": {"cost": 26.1, "qty": 580},
        "starts_on": date(2026, 7, 15),
    },
    {
        "service": "Amazon Elastic Block Store",
        "code": "AmazonEBS",
        "usage_type": "EBS:VolumeUsage.gp3",
        "description": "$0.08 per GB-month of gp3 provisioned storage",
        "resource": "vol-0c9d8e7f6a5b43210",
        "unit": "GB-Mo",
        "tags": {"env": "prod", "owner": "data-platform"},
        "base": {"cost": 3.2, "qty": 100},
    },
    {
        "service": "AmazonCloudWatch",
        "code": "AmazonCloudWatch",
        "usage_type": "CW:MetricMonitorUsage",
        "description": "$0.30 per custom metric per month",
        "resource": None,
        "unit": "Metrics",
        "tags": {},
        # Diffuse drift, same unit rate: +2.89 over the week, under the 5.00
        # materiality floor, so it never becomes a candidate and stays in the
        # unattributed residual.
        "base": {"cost": 1.1, "qty": 40},
        "current": {"cost": 1.5125, "qty": 55},
    },
    {
        "service": "Amazon Elastic Compute Cloud",
        "code": "AmazonEC2",
        "usage_type": "BoxUsage:m6i.xlarge",
        "description": "$0.20 per On Demand Linux m6i.xlarge Instance Hour",
        "resource": "i-0dev1234567890abc",
        "unit": "Hrs",
        "tags": {"env": "dev"},
        "base": {"cost": 0.0, "qty": 0},
        "current": {"cost": 4.8, "qty": 24},
        "starts_on": date(2026, 7, 14),
    },
]


def write_aws() -> None:
    rows: list[dict[str, Any]] = []
    for index, (day, entry, cost, quantity) in enumerate(iter_series(AWS_SERIES)):
        rows.append(
            {
                "identity_line_item_id": f"aws-{index:05d}",
                "bill_payer_account_id": AWS_ACCOUNT,
                "line_item_usage_account_id": AWS_ACCOUNT,
                "line_item_usage_start_date": day.isoformat(),
                "line_item_line_item_type": "Usage",
                "line_item_product_code": entry["code"],
                "product_servicename": entry["service"],
                "product_region_code": AWS_REGION,
                "line_item_resource_id": entry["resource"],
                "line_item_usage_type": entry["usage_type"],
                "line_item_line_item_description": entry["description"],
                "line_item_usage_amount": quantity,
                "pricing_unit": entry["unit"],
                "line_item_unblended_cost": round(cost, 6),
                "line_item_net_amortized_cost": round(cost, 6),
                "line_item_currency_code": "USD",
                "resource_tags": entry["tags"],
            }
        )
    write_json(
        ROOT / "aws" / "cost_and_usage.json",
        {
            "export_name": "cloudcause-demo-cur2",
            "schema_version": "2.0",
            "billing_period": "2026-07",
            "rows": rows,
        },
    )

    write_json(
        ROOT / "aws" / "resources.json",
        {
            "items": [
                {
                    "provider": "aws",
                    "resource_id": "i-0a1b2c3d4e5f67890",
                    "resource_name": "web-prod-1",
                    "resource_type": "AWS::EC2::Instance",
                    "region_id": AWS_REGION,
                    "state": "running",
                    "created_at": "2025-11-02T14:11:00+00:00",
                    "tags": {"env": "prod", "owner": "platform", "Name": "web-prod-1"},
                    "attributes": {"instance_type": "m6i.large"},
                },
                {
                    "provider": "aws",
                    "resource_id": "i-0dev1234567890abc",
                    "resource_name": "ml-experiment-scratch",
                    "resource_type": "AWS::EC2::Instance",
                    "region_id": AWS_REGION,
                    "state": "running",
                    "created_at": "2026-07-14T09:03:00+00:00",
                    "tags": {"env": "dev", "Name": "ml-experiment-scratch"},
                    "attributes": {"instance_type": "m6i.xlarge"},
                },
                {
                    "provider": "aws",
                    "resource_id": "nat-0ab12cd34ef56789a",
                    "resource_name": "prod-nat-a",
                    "resource_type": "AWS::EC2::NatGateway",
                    "region_id": AWS_REGION,
                    "state": "available",
                    "created_at": "2025-06-18T08:40:00+00:00",
                    "tags": {"env": "prod", "owner": "platform"},
                    "attributes": {"subnet_id": "subnet-0aa1b2c3d4e5f6789", "vpc_id": "vpc-0f9e8d7c6b5a4321"},
                },
                {
                    "provider": "aws",
                    "resource_id": "vol-0c9d8e7f6a5b43210",
                    "resource_name": "prod-data-1",
                    "resource_type": "AWS::EC2::Volume",
                    "region_id": AWS_REGION,
                    "state": "in-use",
                    "created_at": "2025-11-02T14:11:30+00:00",
                    "tags": {"env": "prod", "owner": "data-platform"},
                    "attributes": {"volume_type": "gp3", "size_gib": "100"},
                },
                {
                    "provider": "aws",
                    "resource_id": "arn:aws:s3:::cc-demo-prod-data-lake",
                    "resource_name": "cc-demo-prod-data-lake",
                    "resource_type": "AWS::S3::Bucket",
                    "region_id": AWS_REGION,
                    "state": "available",
                    "created_at": "2025-03-11T10:00:00+00:00",
                    "tags": {"env": "prod", "owner": "data-platform"},
                    "attributes": {},
                },
                {
                    "provider": "aws",
                    "resource_id": "rtb-0f1e2d3c4b5a69870",
                    "resource_name": "prod-private-rt",
                    "resource_type": "AWS::EC2::RouteTable",
                    "region_id": AWS_REGION,
                    "state": "available",
                    "created_at": "2025-06-18T08:39:00+00:00",
                    "tags": {"env": "prod", "owner": "platform"},
                    "attributes": {"vpc_id": "vpc-0f9e8d7c6b5a4321"},
                },
                {
                    "provider": "aws",
                    "resource_id": "vpce-0abc123def4567890",
                    "resource_name": "prod-s3-gateway-endpoint",
                    "resource_type": "AWS::EC2::VPCEndpoint",
                    "region_id": AWS_REGION,
                    "state": "deleted",
                    "created_at": "2025-06-18T08:41:00+00:00",
                    "tags": {"env": "prod", "owner": "platform"},
                    "attributes": {
                        "service_name": "com.amazonaws.us-east-1.s3",
                        "endpoint_type": "Gateway",
                        "deleted_at": "2026-07-15T02:13:20+00:00",
                    },
                },
            ]
        },
    )

    write_json(
        ROOT / "aws" / "cloudwatch_metrics.json",
        {
            "items": [
                metric(
                    "aws",
                    "nat-0ab12cd34ef56789a",
                    "BytesOutToDestination",
                    "Bytes",
                    "Sum",
                    21_474_836_480,
                    622_770_257_920,
                    date(2026, 7, 15),
                ),
                metric(
                    "aws",
                    "nat-0ab12cd34ef56789a",
                    "ActiveConnectionCount",
                    "Count",
                    "Maximum",
                    1200,
                    4260,
                    date(2026, 7, 15),
                ),
                metric("aws", "i-0dev1234567890abc", "CPUUtilization", "Percent", "Average", 0.0, 0.7,
                       date(2026, 7, 14)),
                metric("aws", "i-0dev1234567890abc", "NetworkOut", "Bytes", "Sum", 0.0, 18_400_000,
                       date(2026, 7, 14)),
                metric("aws", "i-0a1b2c3d4e5f67890", "CPUUtilization", "Percent", "Average", 34.2, 35.1),
                metric("aws", "vol-0c9d8e7f6a5b43210", "VolumeReadOps", "Count", "Sum", 45_000, 46_100),
            ]
        },
    )

    write_json(
        ROOT / "aws" / "cloudtrail_events.json",
        {
            "items": [
                {
                    "provider": "aws",
                    "event_id": "aws-evt-0001",
                    "event_name": "DeleteVpcEndpoint",
                    "event_time": "2026-07-15T02:13:20+00:00",
                    "source": "cloudtrail",
                    "actor": f"arn:aws:iam::{AWS_ACCOUNT}:user/deploy-bot",
                    "actor_type": "IAMUser",
                    "region_id": AWS_REGION,
                    "source_ip": "198.51.100.24",
                    "resource_ids": ["vpce-0abc123def4567890", "nat-0ab12cd34ef56789a"],
                    "summary": "Deleted gateway endpoint com.amazonaws.us-east-1.s3 during a network refactor",
                    "attributes": {"requestId": "b1f2c3d4-5566-7788-99aa-bbccddeeff00"},
                },
                {
                    "provider": "aws",
                    "event_id": "aws-evt-0002",
                    "event_name": "ReplaceRoute",
                    "event_time": "2026-07-15T02:14:00+00:00",
                    "source": "cloudtrail",
                    "actor": f"arn:aws:iam::{AWS_ACCOUNT}:user/deploy-bot",
                    "actor_type": "IAMUser",
                    "region_id": AWS_REGION,
                    "source_ip": "198.51.100.24",
                    "resource_ids": ["rtb-0f1e2d3c4b5a69870", "nat-0ab12cd34ef56789a"],
                    "summary": (
                        "Replaced the 0.0.0.0/0 route in rtb-0f1e2d3c4b5a69870 to target "
                        "nat-0ab12cd34ef56789a, so S3 traffic now leaves through the NAT Gateway"
                    ),
                    "attributes": {"destinationCidrBlock": "0.0.0.0/0"},
                },
                {
                    "provider": "aws",
                    "event_id": "aws-evt-0003",
                    "event_name": "RunInstances",
                    "event_time": "2026-07-14T09:03:00+00:00",
                    "source": "cloudtrail",
                    "actor": f"arn:aws:iam::{AWS_ACCOUNT}:user/intern-sandbox",
                    "actor_type": "IAMUser",
                    "region_id": AWS_REGION,
                    "source_ip": "203.0.113.19",
                    "resource_ids": ["i-0dev1234567890abc"],
                    "summary": "Launched one m6i.xlarge instance for a one-off experiment",
                    "attributes": {"instanceType": "m6i.xlarge"},
                },
            ]
        },
    )

    write_json(
        ROOT / "aws" / "recommendations.json",
        {
            "items": [
                {
                    "provider": "aws",
                    "recommendation_id": "aws-rec-0001",
                    "source": "compute-optimizer",
                    "category": "idle_instance",
                    "resource_id": "i-0dev1234567890abc",
                    "description": "Instance has been idle for 5 days; consider stopping or terminating it",
                    "estimated_monthly_savings": 144.0,
                    "currency": "USD",
                },
                {
                    "provider": "aws",
                    "recommendation_id": "aws-rec-0002",
                    "source": "trusted-advisor",
                    "category": "networking",
                    "resource_id": "nat-0ab12cd34ef56789a",
                    "description": (
                        "Use a gateway VPC endpoint for S3 so this traffic avoids NAT Gateway data "
                        "processing charges"
                    ),
                    "estimated_monthly_savings": 780.0,
                    "currency": "USD",
                },
            ]
        },
    )

    write_json(
        ROOT / "aws" / "manifest.json",
        manifest(
            "aws",
            "cost-explorer",
            "2.0",
            {
                "costs": "cost_and_usage.json",
                "resources": "resources.json",
                "resources_source": "resource-explorer",
                "metrics": "cloudwatch_metrics.json",
                "metrics_source": "cloudwatch",
                "audit": "cloudtrail_events.json",
                "audit_source": "cloudtrail",
                "recommendations": "recommendations.json",
                "rec_source": "compute-optimizer",
            },
        ),
    )


AZURE_SERIES: list[dict[str, Any]] = [
    {
        "meter_category": "Azure App Service",
        "meter": "P1v3 App Service Plan Hours",
        "resource": APP_PLAN,
        "unit": "1 Hour",
        "tags": {"env": "prod", "owner": "platform"},
        "base": {"cost": 14.0, "qty": 24},
    },
    {
        "meter_category": "Storage",
        "meter": "Hot LRS Data Stored",
        "resource": STORAGE_ACCOUNT,
        "unit": "1 GB/Month",
        "tags": {"env": "prod", "owner": "platform"},
        "base": {"cost": 6.5, "qty": 240},
    },
    {
        "meter_category": "Azure Functions",
        "meter": "Total Executions",
        "resource": FUNCTION_APP,
        "unit": "10K",
        "tags": {"env": "prod", "owner": "orders-team"},
        "base": {"cost": 1.2, "qty": 12},
        "current": {"cost": 18.4, "qty": 190},
        "starts_on": date(2026, 7, 14),
    },
    {
        "meter_category": "SQL Database",
        "meter": "S3 DTUs",
        "resource": SQL_DATABASE,
        "unit": "1 Hour",
        "tags": {"env": "prod", "owner": "orders-team"},
        "base": {"cost": 9.0, "qty": 24},
    },
    {
        "meter_category": "Bandwidth",
        "meter": "Data Transfer Out - Zone 1",
        "resource": None,
        "unit": "1 GB",
        "tags": {},
        # Diffuse drift, same unit rate: +3.85 over the week, under the
        # materiality floor.
        "base": {"cost": 1.3, "qty": 26},
        "current": {"cost": 1.85, "qty": 37},
    },
]

AZURE_COLUMNS = [
    ("UsageDate", "Number"),
    ("Cost", "Number"),
    ("AmortizedCost", "Number"),
    ("UsageQuantity", "Number"),
    ("MeterCategory", "String"),
    ("Meter", "String"),
    ("ResourceId", "String"),
    ("ResourceLocation", "String"),
    ("SubscriptionId", "String"),
    ("UnitOfMeasure", "String"),
    ("Currency", "String"),
    ("ChargeType", "String"),
    ("Tags", "String"),
]


def write_azure() -> None:
    rows: list[list[Any]] = []
    for day, entry, cost, quantity in iter_series(AZURE_SERIES):
        rows.append(
            [
                int(day.strftime("%Y%m%d")),
                round(cost, 6),
                round(cost, 6),
                quantity,
                entry["meter_category"],
                entry["meter"],
                entry["resource"],
                AZURE_REGION,
                AZURE_SUBSCRIPTION,
                entry["unit"],
                "USD",
                "Usage",
                json.dumps(entry["tags"]),
            ]
        )
    write_json(
        ROOT / "azure" / "cost_management.json",
        {
            "id": f"/subscriptions/{AZURE_SUBSCRIPTION}/providers/Microsoft.CostManagement/query/cloudcause-demo",
            "name": "cloudcause-demo",
            "type": "Microsoft.CostManagement/query",
            "properties": {
                "nextLink": None,
                "columns": [{"name": name, "type": kind} for name, kind in AZURE_COLUMNS],
                "rows": rows,
            },
        },
    )

    write_json(
        ROOT / "azure" / "resources.json",
        {
            "items": [
                {
                    "provider": "azure",
                    "resource_id": FUNCTION_APP,
                    "resource_name": "orders-processor",
                    "resource_type": "Microsoft.Web/sites",
                    "region_id": AZURE_REGION,
                    "state": "Running",
                    "created_at": "2025-09-30T11:20:00+00:00",
                    "tags": {"env": "prod", "owner": "orders-team"},
                    "attributes": {"kind": "functionapp", "plan": "Consumption"},
                },
                {
                    "provider": "azure",
                    "resource_id": APP_PLAN,
                    "resource_name": "plan-prod",
                    "resource_type": "Microsoft.Web/serverFarms",
                    "region_id": AZURE_REGION,
                    "state": "Ready",
                    "created_at": "2025-04-14T09:00:00+00:00",
                    "tags": {"env": "prod", "owner": "platform"},
                    "attributes": {"sku": "P1v3"},
                },
                {
                    "provider": "azure",
                    "resource_id": STORAGE_ACCOUNT,
                    "resource_name": "ccdemoprod",
                    "resource_type": "Microsoft.Storage/storageAccounts",
                    "region_id": AZURE_REGION,
                    "state": "Available",
                    "created_at": "2025-04-14T09:05:00+00:00",
                    "tags": {"env": "prod", "owner": "platform"},
                    "attributes": {"replication": "LRS"},
                },
                {
                    "provider": "azure",
                    "resource_id": SQL_DATABASE,
                    "resource_name": "orders",
                    "resource_type": "Microsoft.Sql/servers/databases",
                    "region_id": AZURE_REGION,
                    "state": "Online",
                    "created_at": "2025-04-14T09:10:00+00:00",
                    "tags": {"env": "prod", "owner": "orders-team"},
                    "attributes": {"tier": "Standard", "dtu": "100"},
                },
            ]
        },
    )

    write_json(
        ROOT / "azure" / "monitor_metrics.json",
        {
            "items": [
                metric("azure", FUNCTION_APP, "FunctionExecutionCount", "Count", "Total", 120_000,
                       1_900_000, date(2026, 7, 14)),
                metric("azure", FUNCTION_APP, "FunctionErrors", "Count", "Total", 40, 1_750_000,
                       date(2026, 7, 14)),
                metric("azure", APP_PLAN, "Percentage CPU", "Percent", "Average", 38.4, 41.2),
                metric("azure", SQL_DATABASE, "connection_successful", "Count", "Total", 8_400, 8_600),
            ]
        },
    )

    write_json(
        ROOT / "azure" / "activity_log.json",
        {
            "items": [
                {
                    "provider": "azure",
                    "event_id": "azure-evt-0001",
                    "event_name": "Microsoft.Web/sites/publish/action",
                    "event_time": "2026-07-14T18:20:00+00:00",
                    "source": "activity-log",
                    "actor": "deploy@cloudcause.example",
                    "actor_type": "User",
                    "region_id": AZURE_REGION,
                    "resource_ids": [FUNCTION_APP],
                    "summary": (
                        "Deployment release-2026.07.14 succeeded; the queue trigger binding and "
                        "retry policy were updated"
                    ),
                    "attributes": {"status": "Succeeded", "correlationId": "8fa1c2d3-4455-6677-8899-aabbccddeeff"},
                },
                {
                    "provider": "azure",
                    "event_id": "azure-evt-0002",
                    "event_name": "Microsoft.Web/sites/restart/action",
                    "event_time": "2026-07-14T18:25:00+00:00",
                    "source": "activity-log",
                    "actor": "deploy@cloudcause.example",
                    "actor_type": "User",
                    "region_id": AZURE_REGION,
                    "resource_ids": [FUNCTION_APP],
                    "summary": "Restarted the function app after deployment",
                    "attributes": {"status": "Succeeded"},
                },
            ]
        },
    )

    write_json(
        ROOT / "azure" / "advisor_recommendations.json",
        {
            "items": [
                {
                    "provider": "azure",
                    "recommendation_id": "azure-rec-0001",
                    "source": "azure-advisor",
                    "category": "reliability",
                    "resource_id": FUNCTION_APP,
                    "description": (
                        "Function app failure rate is above 90 percent; review the retry policy and "
                        "the failing dependency"
                    ),
                    "estimated_monthly_savings": 0.0,
                    "currency": "USD",
                },
                {
                    "provider": "azure",
                    "recommendation_id": "azure-rec-0002",
                    "source": "azure-advisor",
                    "category": "cost",
                    "resource_id": SQL_DATABASE,
                    "description": "Database is oversized for its load; consider a lower tier or serverless",
                    "estimated_monthly_savings": 62.0,
                    "currency": "USD",
                },
            ]
        },
    )

    write_json(
        ROOT / "azure" / "manifest.json",
        manifest(
            "azure",
            "cost-management",
            "2023-11-01",
            {
                "costs": "cost_management.json",
                "resources": "resources.json",
                "resources_source": "resource-graph",
                "metrics": "monitor_metrics.json",
                "metrics_source": "azure-monitor",
                "audit": "activity_log.json",
                "audit_source": "activity-log",
                "recommendations": "advisor_recommendations.json",
                "rec_source": "azure-advisor",
            },
        ),
    )


GCP_SERIES: list[dict[str, Any]] = [
    {
        "service": "Compute Engine",
        "sku_id": "2E27-4F75-95CD",
        "sku": "N2 Instance Core running in Americas",
        "resource": GCP_INSTANCE,
        "unit": "hour",
        "labels": {"env": "prod", "owner": "platform"},
        "base": {"cost": 15.0, "qty": 24},
    },
    {
        "service": "Cloud Storage",
        "sku_id": "E5F4-6A45-A2F1",
        "sku": "Standard Storage US Multi-region",
        "resource": GCP_BUCKET,
        "unit": "gibibyte month",
        "labels": {"env": "prod", "owner": "platform"},
        "base": {"cost": 5.0, "qty": 200},
    },
    {
        "service": "Cloud Translation API",
        "sku_id": "9B4A-1C22-0D7E",
        "sku": "Translation API - Characters Translated",
        "resource": GCP_TRANSLATE,
        "unit": "million characters",
        "labels": {"env": "prod", "owner": "content-team"},
        "base": {"cost": 0.6, "qty": 0.03},
        "current": {"cost": 41.0, "qty": 2.05},
        "starts_on": date(2026, 7, 16),
    },
    {
        "service": "Cloud SQL",
        "sku_id": "1C4B-9E22-5A31",
        "sku": "Cloud SQL for PostgreSQL: Zonal - vCPU",
        "resource": GCP_SQL,
        "unit": "hour",
        "labels": {"env": "prod", "owner": "orders-team"},
        "base": {"cost": 7.4, "qty": 24},
    },
    {
        "service": "Networking",
        "sku_id": "7D3A-2B11-4C90",
        "sku": "Network Internet Egress from Americas to Americas",
        "resource": None,
        "unit": "gibibyte",
        "labels": {},
        # Diffuse drift, same unit rate: +3.92 over the week, under the
        # materiality floor.
        "base": {"cost": 2.1, "qty": 18},
        "current": {"cost": 2.66, "qty": 22.8},
    },
]

GCP_CSV_COLUMNS = [
    "billing_account_id",
    "service.description",
    "sku.id",
    "sku.description",
    "usage_start_time",
    "usage_end_time",
    "project.id",
    "location.location",
    "resource.name",
    "resource.global_name",
    "usage.amount",
    "usage.unit",
    "cost",
    "currency",
    "credits.amount",
    "credits.name",
    "labels",
]


def write_gcp() -> None:
    path = ROOT / "gcp" / "billing_export.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        # newline="" leaves line endings to csv, and lineterminator pins them to
        # LF. csv defaults to CRLF on every platform, which would make the
        # generated file differ from the LF committed here and fail the
        # "fixtures are reproducible" gate on a Linux runner.
        writer = csv.DictWriter(handle, fieldnames=GCP_CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for day, entry, cost, quantity in iter_series(GCP_SERIES):
            resource = entry["resource"]
            writer.writerow(
                {
                    "billing_account_id": GCP_BILLING_ACCOUNT,
                    "service.description": entry["service"],
                    "sku.id": entry["sku_id"],
                    "sku.description": entry["sku"],
                    "usage_start_time": f"{day.isoformat()}T00:00:00Z",
                    "usage_end_time": f"{day.isoformat()}T23:59:59Z",
                    "project.id": GCP_PROJECT,
                    "location.location": GCP_REGION,
                    "resource.name": resource.rsplit("/", 1)[-1] if resource else "",
                    "resource.global_name": resource or "",
                    "usage.amount": quantity,
                    "usage.unit": entry["unit"],
                    "cost": round(cost, 6),
                    "currency": "USD",
                    "credits.amount": 0,
                    "credits.name": "",
                    "labels": json.dumps(
                        [{"key": key, "value": value} for key, value in entry["labels"].items()]
                    ),
                }
            )
    print(f"wrote {path.relative_to(ROOT.parent)}")

    write_json(
        ROOT / "gcp" / "assets.json",
        {
            "items": [
                {
                    "provider": "gcp",
                    "resource_id": GCP_INSTANCE,
                    "resource_name": "api-prod-1",
                    "resource_type": "compute.googleapis.com/Instance",
                    "region_id": GCP_REGION,
                    "state": "RUNNING",
                    "created_at": "2025-08-21T07:45:00+00:00",
                    "tags": {"env": "prod", "owner": "platform"},
                    "attributes": {"machine_type": "n2-standard-4"},
                },
                {
                    "provider": "gcp",
                    "resource_id": GCP_TRANSLATE,
                    "resource_name": "translate.googleapis.com",
                    "resource_type": "serviceusage.googleapis.com/Service",
                    "region_id": "global",
                    "state": "ENABLED",
                    "created_at": "2025-05-04T12:00:00+00:00",
                    "tags": {"env": "prod", "owner": "content-team"},
                    "attributes": {"quota_project": GCP_PROJECT},
                },
                {
                    "provider": "gcp",
                    "resource_id": GCP_API_KEY,
                    "resource_name": "public-site-translate-key",
                    "resource_type": "apikeys.googleapis.com/Key",
                    "region_id": "global",
                    "state": "ENABLED",
                    "created_at": "2025-05-04T12:05:00+00:00",
                    "tags": {"env": "prod", "owner": "content-team"},
                    "attributes": {"api_restrictions": "none", "referrer_restrictions": "none"},
                },
                {
                    "provider": "gcp",
                    "resource_id": GCP_SQL,
                    "resource_name": "orders-db",
                    "resource_type": "sqladmin.googleapis.com/Instance",
                    "region_id": GCP_REGION,
                    "state": "RUNNABLE",
                    "created_at": "2025-06-02T10:15:00+00:00",
                    "tags": {"env": "prod", "owner": "orders-team"},
                    "attributes": {"tier": "db-custom-2-7680"},
                },
                {
                    "provider": "gcp",
                    "resource_id": GCP_BUCKET,
                    "resource_name": "cc-demo-assets",
                    "resource_type": "storage.googleapis.com/Bucket",
                    "region_id": "us",
                    "state": "ACTIVE",
                    "created_at": "2025-03-19T09:00:00+00:00",
                    "tags": {"env": "prod", "owner": "platform"},
                    "attributes": {"storage_class": "STANDARD"},
                },
            ]
        },
    )

    write_json(
        ROOT / "gcp" / "monitoring_metrics.json",
        {
            "items": [
                metric(
                    "gcp",
                    GCP_TRANSLATE,
                    "serviceruntime.googleapis.com/api/request_count",
                    "Count",
                    "Sum",
                    12_000,
                    820_000,
                    date(2026, 7, 16),
                ),
                metric(
                    "gcp",
                    GCP_TRANSLATE,
                    "serviceruntime.googleapis.com/api/characters_translated",
                    "Count",
                    "Sum",
                    30_000,
                    2_050_000,
                    date(2026, 7, 16),
                ),
                metric(
                    "gcp",
                    GCP_INSTANCE,
                    "compute.googleapis.com/instance/cpu/utilization",
                    "Percent",
                    "Average",
                    41.5,
                    42.8,
                ),
                metric("gcp", GCP_SQL, "database/network/connections", "Count", "Average", 46.0, 45.2),
            ]
        },
    )

    write_json(
        ROOT / "gcp" / "audit_logs.json",
        {
            "items": [
                {
                    "provider": "gcp",
                    "event_id": "gcp-evt-0001",
                    "event_name": "google.cloud.translation.v3.TranslationService.TranslateText",
                    "event_time": "2026-07-16T03:40:00+00:00",
                    "source": "cloud-audit-logs",
                    "actor": "api-key:8f2a...e1c1",
                    "actor_type": "ApiKey",
                    "region_id": "global",
                    "source_ip": "203.0.113.77",
                    "source_location": "unrecognized network AS13335",
                    "resource_ids": [GCP_TRANSLATE, GCP_API_KEY],
                    "summary": (
                        "First TranslateText request from a source location never seen before, using "
                        "API key public-site-translate-key"
                    ),
                    "attributes": {"userAgent": "python-requests/2.32", "status": "OK"},
                },
                {
                    "provider": "gcp",
                    "event_id": "gcp-evt-0002",
                    "event_name": "google.cloud.translation.v3.TranslationService.TranslateText",
                    "event_time": "2026-07-16T09:12:00+00:00",
                    "source": "cloud-audit-logs",
                    "actor": "api-key:8f2a...e1c1",
                    "actor_type": "ApiKey",
                    "region_id": "global",
                    "source_ip": "198.51.100.211",
                    "source_location": "unrecognized network AS20473",
                    "resource_ids": [GCP_TRANSLATE, GCP_API_KEY],
                    "summary": "Sustained TranslateText volume from a second unrecognized network",
                    "attributes": {"userAgent": "curl/8.6.0", "status": "OK"},
                },
                {
                    "provider": "gcp",
                    "event_id": "gcp-evt-0003",
                    "event_name": "google.cloud.translation.v3.TranslationService.TranslateText",
                    "event_time": "2026-07-17T14:02:00+00:00",
                    "source": "cloud-audit-logs",
                    "actor": "api-key:8f2a...e1c1",
                    "actor_type": "ApiKey",
                    "region_id": "global",
                    "source_ip": "203.0.113.77",
                    "source_location": "unrecognized network AS13335",
                    "resource_ids": [GCP_TRANSLATE, GCP_API_KEY],
                    "summary": "Request volume continues from the same unrecognized network",
                    "attributes": {"userAgent": "python-requests/2.32", "status": "OK"},
                },
            ]
        },
    )

    write_json(
        ROOT / "gcp" / "recommendations.json",
        {
            "items": [
                {
                    "provider": "gcp",
                    "recommendation_id": "gcp-rec-0001",
                    "source": "recommender",
                    "category": "security",
                    "resource_id": GCP_API_KEY,
                    "description": (
                        "API key has no API or referrer restrictions; restrict it and rotate the key"
                    ),
                    "estimated_monthly_savings": 0.0,
                    "currency": "USD",
                },
                {
                    "provider": "gcp",
                    "recommendation_id": "gcp-rec-0002",
                    "source": "recommender",
                    "category": "cost",
                    "resource_id": GCP_TRANSLATE,
                    "description": "Set a quota limit on translate.googleapis.com to cap unexpected usage",
                    "estimated_monthly_savings": 320.0,
                    "currency": "USD",
                },
            ]
        },
    )

    write_json(
        ROOT / "gcp" / "manifest.json",
        manifest(
            "gcp",
            "billing-export-bigquery",
            "1",
            {
                "costs": "billing_export.csv",
                "resources": "assets.json",
                "resources_source": "cloud-asset-inventory",
                "metrics": "monitoring_metrics.json",
                "metrics_source": "cloud-monitoring",
                "audit": "audit_logs.json",
                "audit_source": "cloud-audit-logs",
                "recommendations": "recommendations.json",
                "rec_source": "recommender",
            },
        ),
    )


def main() -> None:
    write_aws()
    write_azure()
    write_gcp()
    print("\nFixtures regenerated. Baseline "
          f"{BASELINE[0]}..{BASELINE[1]}, current {CURRENT[0]}..{CURRENT[1]}.")


if __name__ == "__main__":
    main()
