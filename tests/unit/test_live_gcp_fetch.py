"""The GCP fetch plumbing, with BigQuery faked (ADR 0015).

The mapper and the SQL text are covered in ``test_live_connectors.py``. This
file covers what the fetch does around them: the client project, the three
timestamp parameters, the row conversion, and how a missing Application
Default Credential is reported. A stub module plays ``google.cloud.bigquery``.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, date, datetime

import pytest
from cloudcause_contracts import DateRange, Settings
from cloudcause_providers import LiveConnectorError, LiveGcpDataProvider, LiveModeNotConfiguredError
from cloudcause_providers.live import gcp_billing_query
from conftest import recorded

TABLE = "cloudcause-demo.billing.gcp_billing_export_resource_v1_01ABCD_2345EF_6789GH"
WINDOW = DateRange(start=date(2026, 9, 1), end=date(2026, 9, 3))
SECRET_PATH = r"C:\Users\someone\secret-sa-key.json"


class _Row:
    def __init__(self, values: dict) -> None:
        self._values = values

    def items(self):
        return self._values.items()


class _Job:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def result(self):
        return [_Row(row) for row in self._rows]


class _ScalarQueryParameter:
    def __init__(self, name: str, type_: str, value) -> None:
        self.name, self.type_, self.value = name, type_, value


class _QueryJobConfig:
    def __init__(self, query_parameters=(), maximum_bytes_billed=None) -> None:
        self.query_parameters = list(query_parameters)
        self.maximum_bytes_billed = maximum_bytes_billed


def _bigquery_module(rows: list[dict], calls: list[dict], client_error: Exception | None = None) -> types.ModuleType:
    module = types.ModuleType("google.cloud.bigquery")

    class Client:
        def __init__(self, project: str | None = None) -> None:
            if client_error is not None:
                raise client_error
            calls.append({"project": project})

        def query(self, sql: str, job_config=None):
            calls[-1].update(sql=sql, job_config=job_config)
            return _Job(rows)

    module.Client = Client  # type: ignore[attr-defined]
    module.QueryJobConfig = _QueryJobConfig  # type: ignore[attr-defined]
    module.ScalarQueryParameter = _ScalarQueryParameter  # type: ignore[attr-defined]
    return module


def _adapter(settings: Settings, table: str = TABLE) -> LiveGcpDataProvider:
    return LiveGcpDataProvider(settings.with_overrides(data_mode="live", gcp_billing_table=table))


async def test_fetch_runs_the_validated_query_with_bounded_timestamp_parameters(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = recorded("gcp_billing_export_rows.json")["rows"]
    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", _bigquery_module(rows, calls))

    result = await _adapter(settings).get_costs([WINDOW])

    assert calls[0]["project"] == "cloudcause-demo", "the job runs in the table's project"
    assert calls[0]["sql"] == gcp_billing_query(TABLE)
    parameters = {p.name: (p.type_, p.value) for p in calls[0]["job_config"].query_parameters}
    assert parameters == {
        "partition_floor": ("TIMESTAMP", datetime(2026, 8, 25, tzinfo=UTC)),
        "window_start": ("TIMESTAMP", datetime(2026, 9, 1, tzinfo=UTC)),
        "window_end_exclusive": ("TIMESTAMP", datetime(2026, 9, 4, tzinfo=UTC)),
    }
    assert calls[0]["job_config"].maximum_bytes_billed == settings.gcp_max_bytes_billed
    assert len(result.items) == 2, "the incomplete final day is excluded from analysis"
    assert result.provenance.source == "gcp-billing-export-bigquery"
    assert result.provenance.data_through == datetime(2026, 9, 1, 23, 59, 59, tzinfo=UTC)


async def test_missing_application_default_credentials_name_the_gcloud_command(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DefaultCredentialsError(Exception):
        pass

    error = DefaultCredentialsError(f"Could not automatically determine credentials; checked {SECRET_PATH}")
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", _bigquery_module([], [], client_error=error))

    with pytest.raises(LiveModeNotConfiguredError) as raised:
        await _adapter(settings).get_costs([WINDOW])
    detail = str(raised.value)
    assert "gcloud auth application-default login" in detail
    assert SECRET_PATH not in detail
    assert raised.value.__cause__ is None


async def test_any_other_client_failure_is_wrapped(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    class Forbidden(Exception):
        code = 403

    error = Forbidden(f"403 Access Denied: Dataset {TABLE}: Permission bigquery.tables.get denied")
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", _bigquery_module([], [], client_error=error))

    with pytest.raises(LiveConnectorError) as raised:
        await _adapter(settings).get_costs([WINDOW])
    detail = str(raised.value)
    assert "Forbidden" in detail and "HTTP 403" in detail
    assert "Access Denied" not in detail and TABLE not in detail


async def test_an_invalid_table_never_reaches_the_client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", _bigquery_module([], calls))

    with pytest.raises(LiveModeNotConfiguredError):
        await _adapter(settings, table="cloudcause-demo.billing.not_an_export; DROP TABLE x").get_costs([WINDOW])
    assert calls == []
