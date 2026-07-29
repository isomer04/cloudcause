"""Ingest behaviour for a user's own cost export.

Everything here is offline and file-free: the samples are built in
``tests/conftest.py`` so the demo fixtures are never read or modified.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from cloudcause_contracts import Settings
from cloudcause_datasets import (
    CredentialInUploadError,
    CurrencyConflictError,
    FormatMismatchError,
    IngestError,
    UnsupportedContentTypeError,
    UploadTooLargeError,
    aggregate_cost_records,
    check_content_type,
    check_single_currency,
    decompress_if_needed,
    parse_cost_source,
    parse_evidence_source,
    parse_source,
    sniff_cost_format,
)
from conftest import (
    NAT_RESOURCE,
    aws_audit_json,
    aws_cur_csv,
    aws_cur_json,
    aws_inventory_json,
    aws_metrics_json,
    azure_cost_management_json,
    gcp_billing_export_csv,
    gzipped,
)

BASELINE_START = date(2026, 7, 6)
CURRENT_END = date(2026, 7, 19)


# ------------------------------------------------------------------- detection


@pytest.mark.parametrize(
    ("provider", "build", "expected"),
    [
        ("aws", aws_cur_json, "aws-cur-csv"),
        ("aws", aws_cur_csv, "aws-cur-csv"),
        ("azure", azure_cost_management_json, "azure-cost-management-json"),
        ("gcp", gcp_billing_export_csv, "gcp-billing-export-csv"),
    ],
    ids=["aws-json", "aws-csv", "azure-json", "gcp-csv"],
)
def test_each_provider_export_is_detected_by_content(
    provider: str, build, expected: str
) -> None:
    assert sniff_cost_format(provider, build()) == expected  # type: ignore[arg-type]


def test_a_provider_mismatch_names_both_formats_not_the_url() -> None:
    with pytest.raises(FormatMismatchError) as error:
        sniff_cost_format("aws", azure_cost_management_json())
    message = str(error.value)
    assert "azure-cost-management-json" in message
    assert "/sources/azure/cost" in message


def test_an_unrecognized_file_lists_the_columns_that_were_expected() -> None:
    with pytest.raises(FormatMismatchError) as error:
        sniff_cost_format("aws", b'{"totally": "unrelated"}')
    message = str(error.value)
    assert "line_item_usage_start_date" in message
    assert "usage_start_time" in message
    assert "UsageDate" in message


# ---------------------------------------------------------------------- parsing


def test_aws_hourly_rows_collapse_to_daily_rows_without_losing_money(
    upload_settings: Settings,
) -> None:
    parsed = parse_cost_source("aws", aws_cur_json(), upload_settings)

    assert parsed.detected_format == "aws-cur-csv"
    assert parsed.raw_rows == 14 * 24 * 2
    assert len(parsed.costs) == 14 * 2, "one row per day per series"
    assert parsed.period_start == BASELINE_START
    assert parsed.period_end == CURRENT_END

    nat_current = [
        record
        for record in parsed.costs
        if record.resource_id == NAT_RESOURCE and record.usage_date >= date(2026, 7, 13)
    ]
    assert len(nat_current) == 7
    assert sum(record.effective_cost for record in nat_current) == pytest.approx(140.0, abs=0.01)
    assert all(record.usage_quantity == pytest.approx(40.0 * 24) for record in nat_current)


def test_aggregation_preserves_the_account_dimension() -> None:
    parsed_rows = json.loads(aws_cur_json())["rows"]
    accounts = {row["line_item_usage_account_id"] for row in parsed_rows}
    parsed = parse_cost_source("aws", aws_cur_json(), Settings.from_env({}))
    assert {record.billing_account_id for record in parsed.costs} == accounts, (
        "dropping the account would break the 'account' grouping dimension that "
        "group_changes and the MCP get_cost_breakdown tool advertise"
    )


def test_aggregation_keeps_tags_in_every_encoding(upload_settings: Settings) -> None:
    parsed = parse_cost_source("aws", aws_cur_json(), upload_settings)
    tagged = [record for record in parsed.costs if record.resource_id == NAT_RESOURCE]
    assert tagged and all(record.tags == {"env": "prod", "owner": "network"} for record in tagged)

    gcp = parse_cost_source("gcp", gcp_billing_export_csv(), upload_settings)
    assert all(record.tags == {"env": "prod"} for record in gcp.costs), (
        "GCP labels arrive as a JSON array of key/value objects"
    )


def test_gcp_credits_reduce_the_effective_cost(upload_settings: Settings) -> None:
    rows = gcp_billing_export_csv().decode().splitlines()
    header = rows[0].split(",")
    credit_index = header.index("credits.amount")
    body = [rows[0]]
    for line in rows[1:]:
        cells = line.split(",")
        cells[credit_index] = "-1.0"
        body.append(",".join(cells))
    parsed = parse_cost_source("gcp", "\n".join(body).encode(), upload_settings)
    for record in parsed.costs:
        assert record.effective_cost == pytest.approx(record.billed_cost - 1.0)


def test_azure_daily_rows_survive_intact(upload_settings: Settings) -> None:
    parsed = parse_cost_source("azure", azure_cost_management_json(), upload_settings)
    assert len(parsed.costs) == 14
    assert parsed.currency == "USD"
    assert parsed.costs[0].billing_account_id == "00000000-0000-0000-0000-000000000000"


def test_a_malformed_row_is_rejected_by_number_and_never_quoted(
    upload_settings: Settings,
) -> None:
    rows = json.loads(aws_cur_json())["rows"]
    secret_value = "s3://very-private-bucket-name"
    rows[3] = {
        "identity_line_item_id": "broken",
        "line_item_usage_start_date": "2026-07-06T03:00:00Z",
        "line_item_usage_account_id": "111122223333",
        "line_item_resource_id": secret_value,
    }
    parsed = parse_cost_source("aws", json.dumps({"rows": rows}).encode(), upload_settings)

    assert parsed.rejections, "the row should be reported, not silently dropped"
    rejection = parsed.rejections[0]
    assert rejection.row_number == 4
    assert rejection.code in ("missing_service", "empty_row")
    for entry in parsed.rejections:
        assert secret_value not in entry.detail, "an error message must not echo a row value"


def test_a_file_whose_every_row_is_unusable_is_refused(upload_settings: Settings) -> None:
    rows = [
        {
            "identity_line_item_id": f"row-{index}",
            "line_item_usage_start_date": "2026-07-06T00:00:00Z",
            "line_item_usage_account_id": "111122223333",
        }
        for index in range(3)
    ]
    with pytest.raises(IngestError) as error:
        parse_cost_source("aws", json.dumps({"rows": rows}).encode(), upload_settings)
    assert "no usable cost rows" in str(error.value)


def test_an_empty_export_is_refused_as_an_unrecognized_shape(
    upload_settings: Settings,
) -> None:
    with pytest.raises(FormatMismatchError):
        parse_cost_source("aws", json.dumps({"rows": []}).encode(), upload_settings)


# ------------------------------------------------------------------ data_through


def test_a_partial_aws_day_is_excluded_from_data_through(upload_settings: Settings) -> None:
    parsed = parse_cost_source("aws", aws_cur_json(hours_on_last_day=9), upload_settings)

    assert parsed.data_through is not None
    assert parsed.data_through.date() == date(2026, 7, 18), (
        "a final date with fewer than 24 hourly buckets is incomplete, and that can only "
        "be seen before the daily aggregation"
    )
    assert "9 of 24" in parsed.data_through_note
    assert parsed.period_end == CURRENT_END, "the partial day's real cost is still stored"


def test_a_complete_aws_day_reaches_the_end_of_the_period(upload_settings: Settings) -> None:
    parsed = parse_cost_source("aws", aws_cur_json(), upload_settings)
    assert parsed.data_through is not None
    assert parsed.data_through.date() == CURRENT_END
    assert parsed.data_through_note == ""


def test_gcp_compares_usage_end_time_against_midnight(upload_settings: Settings) -> None:
    complete = parse_cost_source("gcp", gcp_billing_export_csv(), upload_settings)
    assert complete.data_through is not None
    assert complete.data_through.date() == CURRENT_END
    assert complete.data_through_note == ""

    partial = parse_cost_source(
        "gcp", gcp_billing_export_csv(last_day_ends_at_hour=14), upload_settings
    )
    assert partial.data_through is not None
    assert partial.data_through.date() == date(2026, 7, 18)
    assert "rather than midnight" in partial.data_through_note


def test_azure_says_out_loud_that_it_cannot_see_a_partial_day(
    upload_settings: Settings,
) -> None:
    parsed = parse_cost_source(
        "azure", azure_cost_management_json(last_day_partial=True), upload_settings
    )
    assert parsed.data_through is not None
    assert parsed.data_through.date() == CURRENT_END
    assert "daily grain with no intraday detail" in parsed.data_through_note
    assert "provisional" in parsed.data_through_note


# --------------------------------------------------------------------- currency


def test_a_file_mixing_currencies_is_refused_naming_both(upload_settings: Settings) -> None:
    with pytest.raises(CurrencyConflictError) as error:
        parse_cost_source("aws", aws_cur_json(extra_currency="EUR"), upload_settings)
    message = str(error.value)
    assert "EUR" in message and "USD" in message
    assert "does not convert" in message


def test_a_single_currency_file_reports_it() -> None:
    parsed = parse_cost_source("aws", aws_cur_json(currency="GBP"), Settings.from_env({}))
    assert parsed.currency == "GBP"
    assert check_single_currency(parsed.costs) == "GBP"


# ------------------------------------------------------------------- transport


def test_only_three_content_types_are_accepted() -> None:
    assert check_content_type("application/json; charset=utf-8") == "application/json"
    assert check_content_type("text/csv") == "text/csv"
    assert check_content_type("application/x-gzip") == "application/gzip"
    with pytest.raises(UnsupportedContentTypeError):
        check_content_type("multipart/form-data; boundary=x")
    with pytest.raises(UnsupportedContentTypeError):
        check_content_type(None)


def test_a_gzip_upload_is_expanded_and_parsed(upload_settings: Settings) -> None:
    parsed = parse_source("aws", "cost", gzipped(aws_cur_json()), upload_settings)
    assert parsed.compressed is True
    assert len(parsed.costs) == 28


def test_a_gzip_bomb_is_refused_before_it_expands(upload_settings: Settings) -> None:
    bomb = gzipped(b"0" * (64 * 1024 * 1024))
    tight = upload_settings.with_overrides(upload_max_decompressed_bytes=1024 * 1024)
    with pytest.raises(UploadTooLargeError) as error:
        decompress_if_needed(bomb, tight)
    assert "decompressed limit" in str(error.value)


def test_a_multi_member_gzip_is_refused(upload_settings: Settings) -> None:
    with pytest.raises(IngestError) as error:
        decompress_if_needed(gzipped(b'{"rows": []}') + gzipped(b'{"rows": []}'), upload_settings)
    assert "exactly one member" in str(error.value)


def test_a_row_cap_answers_too_big_not_unparseable(upload_settings: Settings) -> None:
    tight = upload_settings.with_overrides(upload_max_rows=10)
    with pytest.raises(UploadTooLargeError):
        parse_cost_source("aws", aws_cur_csv(), tight)


def test_anything_shaped_like_a_credential_is_refused(upload_settings: Settings) -> None:
    payload = json.dumps(
        {"rows": [{"line_item_usage_start_date": "2026-07-06", "aws_secret_access_key": "x"}]}
    ).encode()
    with pytest.raises(CredentialInUploadError) as error:
        parse_source("aws", "cost", payload, upload_settings)
    assert "needs no secret" in str(error.value)


# --------------------------------------------------------------------- evidence


@pytest.mark.parametrize(
    ("kind", "build"),
    [("metrics", aws_metrics_json), ("audit", aws_audit_json), ("inventory", aws_inventory_json)],
    ids=["metrics", "audit", "inventory"],
)
def test_each_documented_evidence_shape_is_accepted(
    kind: str, build, upload_settings: Settings
) -> None:
    parsed = parse_evidence_source("aws", kind, build(), upload_settings)  # type: ignore[arg-type]
    assert parsed.accepted_rows == 1
    assert parsed.stored_records() == 1
    assert parsed.data_through is not None


def test_a_provider_native_shape_is_refused_with_the_documented_alternative(
    upload_settings: Settings,
) -> None:
    cloudwatch = json.dumps({"MetricDataResults": [{"Id": "m1", "Values": [1.0]}]}).encode()
    with pytest.raises(IngestError) as error:
        parse_evidence_source("aws", "metrics", cloudwatch, upload_settings)
    assert "fixtures/README.md" in str(error.value)


def test_an_evidence_row_that_fails_validation_names_fields_not_values(
    upload_settings: Settings,
) -> None:
    payload = json.dumps({"items": [{"resource_id": "r-1", "metric_name": "X", "unit": []}]}).encode()
    with pytest.raises(IngestError) as error:
        parse_evidence_source("aws", "metrics", payload, upload_settings)
    assert "unit" in str(error.value)


# ------------------------------------------------------------------ aggregation


def test_aggregation_is_stable_and_renumbers_source_records() -> None:
    parsed = parse_cost_source("aws", aws_cur_json(), Settings.from_env({}))
    ids = [record.source_record_id for record in parsed.costs]
    assert len(set(ids)) == len(ids)
    assert all(str(value).startswith("upload-") for value in ids)

    again = aggregate_cost_records(parsed.costs)
    assert len(again) == len(parsed.costs), "aggregating an aggregate changes nothing"
