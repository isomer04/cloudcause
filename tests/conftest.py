"""Shared test fixtures. Offline by default: no cloud accounts, no model keys."""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sys
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from cloudcause_contracts import InvestigationRequest, Settings, find_repo_root, load_env_file
from cloudcause_knowledge import KnowledgeStore, load_knowledge_store

REPO_ROOT = find_repo_root(Path(__file__))

# Make .env available to the opt-in live suite, which needs the model keys in
# os.environ for the framework SDKs to pick them up.
for _key, _value in load_env_file(REPO_ROOT / ".env").items():
    os.environ.setdefault(_key, _value)

# The gateway and worker apps build their settings at import time, before any
# fixture runs, so pin the modes here in conftest. Without this a developer's
# .env or an exported shell variable could put the offline suite into live mode.
# API keys are left alone: the opt-in live suite still needs them.
os.environ["CLOUDCAUSE_DATA_MODE"] = "fixtures"
os.environ["CLOUDCAUSE_AGENT_MODE"] = "stub"
os.environ.setdefault("CLOUDCAUSE_ORCHESTRATOR_MODE", "inprocess")
os.environ.setdefault("CLOUDCAUSE_WORKER_MODE", "inprocess")

# The evaluation harness lives outside the installed packages.
sys.path.insert(0, str(REPO_ROOT / "evaluations"))

CURRENT = (date(2026, 7, 13), date(2026, 7, 19))
BASELINE = (date(2026, 7, 6), date(2026, 7, 12))


@pytest.fixture(autouse=True)
def offline_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee fixture + stub mode regardless of the developer's .env.

    Tests marked ``live`` are exempt: they need the model keys this fixture would
    otherwise strip, and they set their own modes. Everything else, which is all
    of CI, still runs with no keys and no live paths reachable.
    """

    if request.node.get_closest_marker("live"):
        monkeypatch.setenv("CLOUDCAUSE_DATA_MODE", "fixtures")
        return

    monkeypatch.setenv("CLOUDCAUSE_DATA_MODE", "fixtures")
    monkeypatch.setenv("CLOUDCAUSE_AGENT_MODE", "stub")
    monkeypatch.setenv("CLOUDCAUSE_ORCHESTRATOR_MODE", "inprocess")
    monkeypatch.setenv("CLOUDCAUSE_WORKER_MODE", "inprocess")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({})


@pytest.fixture
def knowledge(settings: Settings) -> KnowledgeStore:
    return load_knowledge_store(settings.knowledge_root)


@pytest.fixture
def multi_cloud_request() -> InvestigationRequest:
    return InvestigationRequest(
        providers=["aws", "azure", "gcp"],
        start_date=CURRENT[0],
        end_date=CURRENT[1],
        comparison_start_date=BASELINE[0],
        comparison_end_date=BASELINE[1],
        question="Why did our cloud spending increase last week?",
    )


#
# Realistic provider export shapes, built in code so the bring-your-own-data
# tests never read a file the demo fixtures own. The AWS builder emits hourly rows
# on purpose: the trailing-partial-day rule can only be checked before the daily
# aggregation collapses it.

AWS_ACCOUNT = "111122223333"
NAT_RESOURCE = "nat-0ab12cd34ef56789a"
EC2_RESOURCE = "i-0a1b2c3d4e5f67890"


def _dates(start: date, end: date) -> list[date]:
    span = (end - start).days + 1
    return [start + timedelta(days=offset) for offset in range(span)]


def aws_cur_rows(
    *,
    baseline: tuple[date, date] = BASELINE,
    current: tuple[date, date] = CURRENT,
    hours_on_last_day: int = 24,
    currency: str = "USD",
    extra_currency: str | None = None,
    daily_grain: bool = False,
    last_day_hour: int = 0,
) -> list[dict[str, object]]:
    """CUR 2.0 rows: a flat EC2 baseline plus a NAT Gateway spike.

    Hourly by default, which is the real CUR grain. ``daily_grain`` emits one row
    per day carrying the same daily total instead, which is what Cost Explorer and
    most hand-built exports produce; ``last_day_hour`` stamps that final row at an
    hour other than midnight, as a real daily export often does.
    """

    series = [
        {
            "service": "Amazon Elastic Compute Cloud",
            "usage_type": "BoxUsage:m6i.large",
            "resource": EC2_RESOURCE,
            "baseline_hourly_cost": 0.5,
            "current_hourly_cost": 0.5,
            "baseline_hourly_quantity": 1.0,
            "current_hourly_quantity": 1.0,
            "tags": {"env": "prod", "owner": "platform"},
        },
        {
            "service": "Amazon Virtual Private Cloud",
            "usage_type": "NatGateway-Bytes",
            "resource": NAT_RESOURCE,
            "baseline_hourly_cost": 2.0 / 24,
            "current_hourly_cost": 20.0 / 24,
            "baseline_hourly_quantity": 4.0,
            "current_hourly_quantity": 40.0,
            "tags": {"env": "prod", "owner": "network"},
        },
    ]
    rows: list[dict[str, object]] = []
    all_days = [(day, False) for day in _dates(*baseline)] + [
        (day, True) for day in _dates(*current)
    ]
    last_day = all_days[-1][0]
    for day, in_current in all_days:
        if daily_grain:
            hour_slots = [last_day_hour if day == last_day else 0]
            scale = 24.0
        else:
            hour_slots = list(range(hours_on_last_day if day == last_day else 24))
            scale = 1.0
        for hour in hour_slots:
            for entry in series:
                cost = scale * (
                    entry["current_hourly_cost"] if in_current else entry["baseline_hourly_cost"]
                )
                quantity = scale * (
                    entry["current_hourly_quantity"]
                    if in_current
                    else entry["baseline_hourly_quantity"]
                )
                rows.append(
                    {
                        "identity_line_item_id": f"{entry['resource']}-{day}-{hour:02d}",
                        "line_item_usage_start_date": f"{day.isoformat()}T{hour:02d}:00:00Z",
                        "line_item_usage_account_id": AWS_ACCOUNT,
                        "product_servicename": entry["service"],
                        "line_item_line_item_type": "Usage",
                        "line_item_resource_id": entry["resource"],
                        "line_item_usage_type": entry["usage_type"],
                        "line_item_line_item_description": f"{entry['usage_type']} charge",
                        "product_region_code": "us-east-1",
                        "line_item_usage_amount": quantity,
                        "line_item_unblended_cost": cost,
                        "line_item_currency_code": (
                            extra_currency if extra_currency and hour == 0 and in_current else currency
                        ),
                        "resource_tags": json.dumps(entry["tags"]),
                    }
                )
    return rows


def aws_cur_json(**kwargs) -> bytes:
    return json.dumps({"rows": aws_cur_rows(**kwargs)}).encode()


def aws_cur_csv(**kwargs) -> bytes:
    rows = aws_cur_rows(**kwargs)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def azure_cost_management_json(*, last_day_partial: bool = False) -> bytes:
    """A Cost Management query result: daily grain, no intraday detail."""

    columns = [
        {"name": "UsageDate", "type": "Number"},
        {"name": "SubscriptionId", "type": "String"},
        {"name": "MeterCategory", "type": "String"},
        {"name": "Meter", "type": "String"},
        {"name": "ResourceId", "type": "String"},
        {"name": "ResourceLocation", "type": "String"},
        {"name": "ChargeType", "type": "String"},
        {"name": "UsageQuantity", "type": "Number"},
        {"name": "Cost", "type": "Number"},
        {"name": "Currency", "type": "String"},
    ]
    resource = (
        "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-app/"
        "providers/Microsoft.Web/sites/orders-api"
    )
    rows: list[list[object]] = []
    for day in _dates(*BASELINE):
        rows.append(
            [
                int(day.strftime("%Y%m%d")),
                "00000000-0000-0000-0000-000000000000",
                "Azure Functions",
                "Standard Execution Time",
                resource,
                "eastus",
                "Usage",
                1000.0,
                3.0,
                "USD",
            ]
        )
    current_days = _dates(*CURRENT)
    for day in current_days:
        cost = 1.5 if (last_day_partial and day == current_days[-1]) else 18.0
        rows.append(
            [
                int(day.strftime("%Y%m%d")),
                "00000000-0000-0000-0000-000000000000",
                "Azure Functions",
                "Standard Execution Time",
                resource,
                "eastus",
                "Usage",
                cost * 400,
                cost,
                "USD",
            ]
        )
    return json.dumps({"properties": {"columns": columns, "rows": rows}}).encode()


def gcp_billing_export_csv(*, last_day_ends_at_hour: int = 24) -> bytes:
    """A BigQuery detailed usage export, with usage_end_time so partial days show."""

    header = [
        "billing_account_id",
        "service.description",
        "sku.id",
        "sku.description",
        "usage_start_time",
        "usage_end_time",
        "location.location",
        "resource.name",
        "usage.amount",
        "usage.unit",
        "cost",
        "currency",
        "credits.amount",
        "labels",
    ]
    rows: list[list[object]] = []
    all_days = [(day, False) for day in _dates(*BASELINE)] + [
        (day, True) for day in _dates(*CURRENT)
    ]
    last_day = all_days[-1][0]
    for day, in_current in all_days:
        end_hour = last_day_ends_at_hour if day == last_day else 24
        end = datetime.combine(day, time(0, 0)) + timedelta(hours=end_hour)
        rows.append(
            [
                "01ABCD-234567-89EFGH",
                "Cloud Translation API",
                "1234-5678-90AB",
                "Translation characters",
                f"{day.isoformat()}T00:00:00Z",
                end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "us-central1",
                "translate-endpoint",
                4_000_000 if in_current else 400_000,
                "characters",
                26.0 if in_current else 2.6,
                "USD",
                0.0,
                json.dumps([{"key": "env", "value": "prod"}]),
            ]
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def aws_metrics_json() -> bytes:
    points = []
    for day in _dates(*BASELINE):
        points.append({"timestamp": f"{day.isoformat()}T12:00:00Z", "value": 2_000_000.0})
    for day in _dates(*CURRENT):
        points.append({"timestamp": f"{day.isoformat()}T12:00:00Z", "value": 60_000_000.0})
    return json.dumps(
        {
            "items": [
                {
                    "resource_id": NAT_RESOURCE,
                    "metric_name": "BytesOutToDestination",
                    "unit": "Bytes",
                    "statistic": "Sum",
                    "points": points,
                }
            ]
        }
    ).encode()


def aws_inventory_json() -> bytes:
    return json.dumps(
        {
            "items": [
                {
                    "resource_id": NAT_RESOURCE,
                    "resource_name": "nat-prod-1a",
                    "resource_type": "AWS::EC2::NatGateway",
                    "region_id": "us-east-1",
                    "state": "available",
                    "created_at": "2025-09-01T10:00:00Z",
                    "tags": {"env": "prod", "owner": "network"},
                }
            ]
        }
    ).encode()


def aws_audit_json(*, summary: str = "Route table rtb-01 route to 0.0.0.0/0 replaced") -> bytes:
    return json.dumps(
        {
            "items": [
                {
                    "event_id": "cc-upload-event-01",
                    "event_name": "ReplaceRoute",
                    "event_time": f"{CURRENT[0].isoformat()}T08:14:00Z",
                    "source": "cloudtrail",
                    "actor": "deploy-pipeline",
                    "actor_type": "assumed_role",
                    "region_id": "us-east-1",
                    "resource_ids": [NAT_RESOURCE],
                    "summary": summary,
                }
            ]
        }
    ).encode()


def aws_recommendations_json() -> bytes:
    return json.dumps(
        {
            "items": [
                {
                    "recommendation_id": "cc-upload-rec-01",
                    "source": "compute-optimizer",
                    "category": "network",
                    "resource_id": NAT_RESOURCE,
                    "description": "Consider a gateway VPC endpoint for S3 traffic",
                    "estimated_monthly_savings": 380.0,
                    "currency": "USD",
                }
            ]
        }
    ).encode()


def gzipped(payload: bytes) -> bytes:
    return gzip.compress(payload)


@pytest.fixture
def upload_settings(settings: Settings) -> Settings:
    """In-process topology, so the memory dataset store is the allowed one."""

    return settings.with_overrides(orchestrator_mode="inprocess", worker_mode="inprocess")


@pytest.fixture(autouse=True)
def clean_dataset_store() -> Iterator[None]:
    """Never let one test's uploads be visible to the next one."""

    from cloudcause_datasets import reset_memory_store

    reset_memory_store()
    yield
    reset_memory_store()


# PostgreSQL, for tests/persistence only. Unset means skip, which is what keeps
# `pytest tests` runnable with no Docker; CI sets it in the postgres-storage job.

POSTGRES_URL_ENV = "CLOUDCAUSE_TEST_DATABASE_URL"

#: Children before parents.
_POSTGRES_TABLES = (
    "investigation_events",
    "investigations",
    "cloudcause_datasets",
    "cloudcause_schema_migrations",
)


def _drop_cloudcause_tables(url: str) -> None:
    from cloudcause_datasets import Database, parse_database_url

    database = Database(parse_database_url(url), connect_timeout=5.0)
    try:
        for table in _POSTGRES_TABLES:
            database.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    finally:
        database.close()


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """The DSN for a reachable PostgreSQL, or a skip explaining how to get one."""

    from cloudcause_datasets import Database, DatabaseUnavailableError, parse_database_url

    url = os.environ.get(POSTGRES_URL_ENV, "").strip()
    if not url:
        pytest.skip(
            f"set {POSTGRES_URL_ENV} to a PostgreSQL DSN to run the persistence suite "
            "(docker compose -f docker/docker-compose.yml up -d postgres)"
        )
    try:
        Database(parse_database_url(url), connect_timeout=5.0).close()
    except DatabaseUnavailableError as error:
        pytest.skip(f"postgres unavailable: {error}")
    return url


@pytest.fixture
def database_url(postgres_url: str) -> Iterator[str]:
    """A database with no CloudCause tables: dropped, so migrations re-run."""

    _drop_cloudcause_tables(postgres_url)
    yield postgres_url
    _drop_cloudcause_tables(postgres_url)
