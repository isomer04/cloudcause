"""Read a real bill through the local-only connectors (opt-in, ADR 0015).

    uv sync --extra azure        # or --extra aws / --extra gcp
    uv run pytest tests/live -m cloud

Each test needs the extra, the identifier setting, and a CLI login on this
machine, and *skips* without them: a developer with no cloud account must be
able to run the whole suite. Excluded from offline CI by the ``cloud`` marker.

The assertions are structural. Money is whatever the provider returned, and the
recorded responses under ``tests/data/live_responses/`` are what pin the mapping.
Costs are real: an AWS run bills about $0.01 per Cost Explorer call.
"""

from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from cloudcause_contracts import DateRange, Settings
from cloudcause_providers import LiveModeNotConfiguredError, get_data_provider
from cloudcause_providers.live import LIVE_EXTRAS

pytestmark = pytest.mark.cloud


def _require_extra(provider: str) -> None:
    module, extra = LIVE_EXTRAS[provider]
    try:
        importlib.import_module(module)
    except ImportError:
        pytest.skip(f"{provider}: install the '{extra}' extra to run this test")


def _require(name: str) -> None:
    if not os.environ.get(name):
        pytest.skip(f"{name} is not set")


def _periods() -> list[DateRange]:
    yesterday = datetime.now(tz=UTC).date() - timedelta(days=1)
    current = DateRange(start=yesterday - timedelta(days=6), end=yesterday)
    baseline = DateRange(start=current.start - timedelta(days=7), end=current.start - timedelta(days=1))
    return [current, baseline]


async def _read(provider: str) -> None:
    settings = Settings.from_env().with_overrides(data_mode="live")
    adapter = get_data_provider(provider, settings)  # type: ignore[arg-type]
    periods = _periods()
    try:
        bundle = await adapter.get_bundle(periods)
    except LiveModeNotConfiguredError as error:
        pytest.skip(str(error))

    assert bundle.costs.provenance.origin == "live"
    assert bundle.costs.provenance.data_through <= datetime.now(tz=UTC)
    assert bundle.costs.provenance.retrieved_at >= bundle.costs.provenance.data_through
    for record in bundle.costs.items:
        assert record.provider == provider
        assert record.billing_account_id and record.service_name
        assert any(period.contains(record.usage_date) for period in periods)
    # Cost only: the other four are absent, so a run degrades under ADR 0006.
    for absent in (bundle.resources, bundle.metrics, bundle.audit_events, bundle.recommendations):
        assert absent.items == []
        assert absent.provenance.source.endswith("-absent")


async def test_azure_reads_the_operators_bill() -> None:
    _require_extra("azure")
    _require("CLOUDCAUSE_AZURE_SUBSCRIPTION_ID")
    await _read("azure")


async def test_aws_reads_the_operators_bill() -> None:
    _require_extra("aws")
    await _read("aws")


async def test_gcp_reads_the_operators_bill() -> None:
    _require_extra("gcp")
    _require("CLOUDCAUSE_GCP_BILLING_TABLE")
    await _read("gcp")
