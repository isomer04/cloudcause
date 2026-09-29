"""The local-only live connectors, tested without a cloud (ADR 0015).

Each connector is an SDK fetch plus a pure mapper. The fetch is opt-in
(``tests/live/test_cloud_connectors.py``); the mapper is held here against a
recorded response in ``tests/data/live_responses/``. That split is what makes a
local-only connector maintainable, which is the objection ADR 0014 raised.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, date, datetime

import pytest
from cloudcause_contracts import DateRange, Settings
from cloudcause_focus import aws_charge_category
from cloudcause_providers import (
    LiveAwsDataProvider,
    LiveAzureDataProvider,
    LiveConnectorError,
    LiveGcpDataProvider,
    LiveModeNotConfiguredError,
    connector_error,
    get_data_provider,
    live_window,
)
from cloudcause_providers.live import (
    LIVE_EXTRAS,
    aws_data_through,
    aws_time_period,
    azure_data_through,
    azure_query_body,
    gcp_billing_query,
    gcp_live_data_through,
    gcp_query_bounds,
    map_aws_cost_explorer_response,
    map_azure_query_response,
    map_gcp_export_rows,
    merge_azure_pages,
    validate_gcp_billing_table,
)
from conftest import recorded

SUBSCRIPTION = "8f3c2b71-9d4e-4a5f-8c21-7b6e5d4c3a2b"
WINDOW = DateRange(start=date(2026, 9, 1), end=date(2026, 9, 3))
TODAY = date(2026, 9, 15)




def live_settings(settings: Settings, **changes: object) -> Settings:
    return settings.with_overrides(data_mode="live", **changes)


# --- the window -----------------------------------------------------------


def test_the_window_is_the_union_of_the_periods_clipped_to_yesterday() -> None:
    periods = [
        DateRange(start=date(2026, 9, 8), end=date(2026, 9, 20)),
        DateRange(start=date(2026, 9, 1), end=date(2026, 9, 7)),
    ]
    window = live_window(periods, today=TODAY)
    assert window == DateRange(start=date(2026, 9, 1), end=date(2026, 9, 14))


def test_a_window_entirely_in_the_future_is_none() -> None:
    assert live_window([DateRange(start=date(2026, 9, 15), end=date(2026, 9, 20))], today=TODAY) is None
    assert live_window([], today=TODAY) is None


# --- Azure ----------------------------------------------------------------


def test_azure_request_uses_two_groupings_and_never_today() -> None:
    body = azure_query_body(WINDOW)
    assert body == recorded("azure_cost_management_query.json")["request"]
    assert len(body["dataset"]["grouping"]) == 2, "the Query API allows two group-by clauses"


def test_azure_pages_merge_before_parsing_so_row_ids_stay_unique() -> None:
    pages = recorded("azure_cost_management_query.json")["pages"]
    document = merge_azure_pages(pages)
    records = map_azure_query_response(document, SUBSCRIPTION)

    assert len(records) == 4
    assert len({record.source_record_id for record in records}) == 4
    assert {record.billing_account_id for record in records} == {SUBSCRIPTION}
    assert {record.service_name for record in records} == {"Functions", "Storage"}
    assert all(record.resource_id and record.resource_id.startswith("/subscriptions/") for record in records)
    assert sum(record.billed_cost for record in records) == pytest.approx(14.72 + 3.10 + 41.35 + 3.08)
    # What the two-grouping path loses, made visible rather than guessed.
    assert all(record.charge_category == "usage" for record in records)
    assert all(record.region_id is None for record in records)


def test_azure_boundary_is_the_last_day_with_a_row() -> None:
    records = map_azure_query_response(
        merge_azure_pages(recorded("azure_cost_management_query.json")["pages"]), SUBSCRIPTION
    )
    assert azure_data_through(records, WINDOW) == datetime(2026, 9, 2, 23, 59, 59, tzinfo=UTC)
    assert azure_data_through([], WINDOW) == datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)


# --- AWS ------------------------------------------------------------------


def test_aws_end_date_is_exclusive() -> None:
    assert aws_time_period(WINDOW) == {"Start": "2026-09-01", "End": "2026-09-04"}


def _aws_recording() -> tuple[dict, DateRange]:
    raw = recorded("aws_cost_explorer.json")
    starts = sorted(
        date.fromisoformat(result["TimePeriod"]["Start"])
        for batch in raw["batches"]
        for result in batch["ResultsByTime"]
    )
    return raw, DateRange(start=starts[0], end=starts[-1])


def test_aws_mapper_sets_charge_category_from_the_record_type_filter() -> None:
    """Held against a response recorded from a real account, so the numbers are whatever it returned."""

    raw, _window = _aws_recording()
    records = map_aws_cost_explorer_response(raw)

    groups = [
        (batch["record_type"], result, group)
        for batch in raw["batches"]
        for result in batch["ResultsByTime"]
        for group in result["Groups"]
    ]
    assert records and len(records) == len(groups)
    assert len({record.source_record_id for record in records}) == len(records)
    assert {record.billing_account_id for record in records} == {"111122223333"}, "scrubbed to the fixture account"
    assert all(record.resource_id is None for record in records), "Cost Explorer carries no resource ids"
    assert all(record.currency == "USD" for record in records)
    assert "unknown" not in {record.charge_category for record in records}, "every RECORD_TYPE in the recording maps"
    assert {batch["record_type"] for batch in raw["batches"]} >= {"Usage", "Credit"}

    for record, (record_type, result, group) in zip(records, groups, strict=True):
        assert record.charge_category == aws_charge_category(record_type)
        assert record.usage_date == date.fromisoformat(result["TimePeriod"]["Start"])
        assert record.service_name == group["Keys"][0]
        assert record.sku_id == group["Keys"][1]
        assert record.billed_cost == pytest.approx(float(group["Metrics"]["UnblendedCost"]["Amount"]))
        assert record.effective_cost == pytest.approx(float(group["Metrics"]["AmortizedCost"]["Amount"]))
        assert record.usage_quantity == pytest.approx(float(group["Metrics"]["UsageQuantity"]["Amount"]))
    assert all(record.billed_cost <= 0 for record in records if record.charge_category == "credit")


def test_aws_dimension_discovery_is_paginated(settings: Settings) -> None:
    dimension_requests: list[dict] = []

    class Sts:
        def get_caller_identity(self) -> dict:
            return {"Account": "111122223333"}

    class Explorer:
        def get_dimension_values(self, **request):
            dimension_requests.append(request)
            if "NextPageToken" not in request:
                return {"DimensionValues": [{"Value": "Usage"}], "NextPageToken": "next"}
            return {"DimensionValues": [{"Value": "Credit"}]}

        def get_cost_and_usage(self, **request):
            return {"ResultsByTime": []}

    class Session:
        def get_credentials(self) -> object:
            return object()

        def client(self, name: str, **kwargs):
            return Sts() if name == "sts" else Explorer()

    fake_boto3 = types.SimpleNamespace(session=types.SimpleNamespace(Session=Session))
    adapter = LiveAwsDataProvider(live_settings(settings))

    raw = adapter._fetch_sync(fake_boto3, WINDOW)

    assert [batch["record_type"] for batch in raw["batches"]] == ["Usage", "Credit"]
    assert dimension_requests[1]["NextPageToken"] == "next"


def test_aws_boundary_is_the_last_day_with_any_group_not_the_estimated_flag() -> None:
    raw, window = _aws_recording()
    populated = sorted(
        date.fromisoformat(result["TimePeriod"]["Start"])
        for batch in raw["batches"]
        for result in batch["ResultsByTime"]
        if result["Groups"]
    )
    assert all(result["Estimated"] for batch in raw["batches"] for result in batch["ResultsByTime"]), (
        "the recording shows every unbilled day flagged Estimated, which is why the flag is not the rule"
    )
    assert aws_data_through(raw, window) == datetime.combine(
        populated[-1], datetime.max.time().replace(microsecond=0), tzinfo=UTC
    )
    assert aws_data_through({"batches": []}, WINDOW) == datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)
    empty_days = {
        "batches": [
            {
                "record_type": "Usage",
                "ResultsByTime": [
                    {
                        "TimePeriod": {"Start": "2026-09-01", "End": "2026-09-02"},
                        "Estimated": True,
                        "Groups": [{"Keys": ["S", "U"], "Metrics": {}}],
                    },
                    {"TimePeriod": {"Start": "2026-09-02", "End": "2026-09-03"}, "Estimated": True, "Groups": []},
                ],
            }
        ]
    }
    assert aws_data_through(empty_days, WINDOW) == datetime(2026, 9, 1, 23, 59, 59, tzinfo=UTC)


# --- GCP ------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        "",
        "billing.gcp_billing_export_resource_v1_01ABCD_2345EF_6789GH",
        "my-project.billing.gcp_billing_export_resource_v1_01ABCD_2345EF_6789GH; DROP TABLE x",
        "my-project.billing.`gcp_billing_export_resource_v1_01ABCD`",
        "my-project.billing.some_other_table",
    ],
)
def test_gcp_table_reference_is_validated_before_it_touches_sql(table: str) -> None:
    with pytest.raises(LiveModeNotConfiguredError) as error:
        validate_gcp_billing_table(table)
    assert "CLOUDCAUSE_GCP_BILLING_TABLE" in str(error.value)


def test_gcp_query_prunes_partitions_and_parameterises_the_window() -> None:
    table = "my-project.billing.gcp_billing_export_resource_v1_01ABCD_2345EF_6789GH"
    sql = gcp_billing_query(table)
    assert f"FROM `{table}`" in sql
    assert "_PARTITIONTIME >= @partition_floor" in sql
    assert "ANY_VALUE(TO_JSON_STRING(labels))" not in sql
    assert "ANY_VALUE(usage.unit)" not in sql
    assert "@window_start" in sql and "@window_end_exclusive" in sql
    assert "2026" not in sql, "dates are parameters, never formatted into the SQL"

    bounds = gcp_query_bounds(WINDOW)
    assert bounds["window_start"] == datetime(2026, 9, 1, tzinfo=UTC)
    assert bounds["window_end_exclusive"] == datetime(2026, 9, 4, tzinfo=UTC)
    assert bounds["partition_floor"] == datetime(2026, 8, 25, tzinfo=UTC)


def test_gcp_query_reads_resource_columns_only_from_the_detailed_export() -> None:
    detailed = gcp_billing_query("my-project.billing.gcp_billing_export_resource_v1_01ABCD_2345EF_6789GH")
    standard = gcp_billing_query("my-project.billing.gcp_billing_export_v1_01ABCD_2345EF_6789GH")

    assert "resource.global_name" in detailed and "resource.name" in detailed
    # The standard export has no resource column; naming it would fail every run.
    assert "resource." not in standard
    assert standard.count("CAST(NULL AS STRING)") == 2
    assert "AS resource_name" in standard and "AS resource_global_name" in standard


def test_gcp_mapper_renames_aliases_and_the_upload_parser_does_the_rest() -> None:
    rows = recorded("gcp_billing_export_rows.json")["rows"]
    records = map_gcp_export_rows(rows)

    assert len(records) == 3
    compute = records[0]
    assert compute.service_name == "Compute Engine"
    assert compute.resource_id.startswith("//compute.googleapis.com/")
    assert compute.region_id == "us-central1"
    assert compute.sku_id == "2E27-4F75-95CD"
    assert compute.billed_cost == pytest.approx(15.0)
    assert compute.effective_cost == pytest.approx(13.5), "credits reduce the effective cost"
    assert compute.tags == {"env": "prod", "owner": "platform"}
    assert records[1].tags == {}
    assert {record.billing_account_id for record in records} == {"01ABCD-2345EF-6789GH"}


def test_gcp_boundary_drops_a_day_whose_usage_stops_before_midnight() -> None:
    rows = recorded("gcp_billing_export_rows.json")["rows"]
    records = map_gcp_export_rows(rows)
    assert gcp_live_data_through(rows, records, WINDOW) == datetime(2026, 9, 1, 23, 59, 59, tzinfo=UTC)
    assert gcp_live_data_through([], [], WINDOW) == datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)


# --- the shared shape -----------------------------------------------------


def test_live_mode_refuses_a_non_loopback_bind(settings: Settings) -> None:
    exposed = live_settings(settings, bind_host="0.0.0.0")

    with pytest.raises(RuntimeError, match="local-only"):
        get_data_provider("aws", exposed)


async def test_live_queries_have_a_bounded_date_span(settings: Settings) -> None:
    adapter = LiveAzureDataProvider(
        live_settings(
            settings,
            azure_subscription_id=SUBSCRIPTION,
            live_query_max_days=2,
        )
    )

    with pytest.raises(LiveModeNotConfiguredError, match="limited to 2 days"):
        await adapter.get_costs([WINDOW])


@pytest.mark.parametrize("cls", [LiveAwsDataProvider, LiveAzureDataProvider, LiveGcpDataProvider])
async def test_the_four_absent_sources_are_empty_and_import_no_sdk(cls, settings: Settings) -> None:
    adapter = cls(live_settings(settings))
    before = set(sys.modules)

    results = {
        "inventory": await adapter.get_resources(),
        "metrics": await adapter.get_metrics(),
        "audit": await adapter.get_audit_events([WINDOW]),
        "recommendations": await adapter.get_recommendations(),
    }

    for kind, result in results.items():
        assert result.items == []
        assert result.provenance.origin == "live"
        assert result.provenance.source == f"{adapter.provider}-{kind}-absent"
        assert result.provenance.query_reference.endswith("#not-supplied")
        assert result.provenance.retrieved_at >= result.provenance.data_through
    prefixes = tuple(module for module, _extra in LIVE_EXTRAS.values())
    imported = {name for name in set(sys.modules) - before if name.startswith(prefixes)}
    assert not imported


async def test_a_live_bundle_is_cost_only_and_carries_one_boundary(settings: Settings) -> None:
    """The degradation ADR 0006 promises: costs present, everything else absent."""

    adapter = LiveAzureDataProvider(live_settings(settings, azure_subscription_id=SUBSCRIPTION))
    document = merge_azure_pages(recorded("azure_cost_management_query.json")["pages"])

    async def fake_fetch(window: DateRange) -> dict:
        assert window.end < datetime.now(tz=UTC).date()
        return document

    adapter._fetch = fake_fetch  # type: ignore[method-assign]
    bundle = await adapter.get_bundle([WINDOW])

    assert len(bundle.costs.items) == 4
    assert bundle.costs.provenance.origin == "live"
    assert bundle.costs.provenance.source == "azure-cost-management"
    assert not bundle.resources.items and not bundle.metrics.items
    assert not bundle.audit_events.items and not bundle.recommendations.items
    boundary = datetime(2026, 9, 2, 23, 59, 59, tzinfo=UTC)
    assert {source.data_through for source in bundle.sources} == {boundary}
    assert bundle.data_through() == boundary


async def test_costs_outside_the_requested_periods_are_dropped(settings: Settings) -> None:
    adapter = LiveAwsDataProvider(live_settings(settings))
    raw, window = _aws_recording()

    async def fake_fetch(window: DateRange) -> dict:
        return raw

    adapter._fetch = fake_fetch  # type: ignore[method-assign]
    first = DateRange(start=window.start, end=window.start)
    result = await adapter.get_costs([first])
    assert result.items
    assert {record.usage_date for record in result.items} == {window.start}


@pytest.mark.parametrize("cls", [LiveAwsDataProvider, LiveAzureDataProvider, LiveGcpDataProvider])
async def test_a_missing_extra_names_the_extra_to_install(
    cls, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = cls(live_settings(settings))
    module, extra = LIVE_EXTRAS[adapter.provider]
    monkeypatch.setitem(sys.modules, module, None)  # makes ``import module`` raise ImportError

    with pytest.raises(LiveModeNotConfiguredError) as error:
        await adapter.get_costs([WINDOW])
    assert f"uv sync --extra {extra}" in str(error.value)


async def test_azure_without_a_subscription_says_so_before_touching_the_sdk(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "azure.identity", types.ModuleType("azure.identity"))
    adapter = LiveAzureDataProvider(live_settings(settings, azure_subscription_id=""))
    with pytest.raises(LiveModeNotConfiguredError) as error:
        await adapter.get_costs([WINDOW])
    assert "CLOUDCAUSE_AZURE_SUBSCRIPTION_ID" in str(error.value)


async def test_an_sdk_failure_is_wrapped_without_its_message(settings: Settings) -> None:
    adapter = LiveGcpDataProvider(live_settings(settings))

    class QuotaExceeded(Exception):
        code = 403

    async def failing_fetch(window: DateRange) -> list:
        raise QuotaExceeded("Access Denied: project secret-project-name: user sensitive@example.com")

    adapter._fetch = failing_fetch  # type: ignore[method-assign]
    with pytest.raises(LiveConnectorError) as error:
        await adapter.get_costs([WINDOW])
    detail = str(error.value)
    assert "QuotaExceeded" in detail and "HTTP 403" in detail
    assert "secret-project-name" not in detail and "sensitive@example.com" not in detail
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_connector_error_reads_a_status_from_the_common_sdk_shapes() -> None:
    class HttpxLike(Exception):
        def __init__(self) -> None:
            super().__init__("401 Unauthorized for url https://management.azure.com/subscriptions/secret")
            self.response = types.SimpleNamespace(status_code=401)

    class BotoLike(Exception):
        def __init__(self) -> None:
            super().__init__("User: arn:aws:iam::111122223333:user/secret-user is not authorized")
            self.response = {"ResponseMetadata": {"HTTPStatusCode": 400}}

    for error, status in ((HttpxLike(), 401), (BotoLike(), 400)):
        wrapped = connector_error("azure", error)
        assert f"HTTP {status}" in str(wrapped)
        assert "secret" not in str(wrapped)
