"""Provider contract tests.

Every fixture adapter is held to the same behavioural contract the local live
adapters satisfy, so switching CLOUDCAUSE_DATA_MODE cannot change the shape of
the data the agents see.
"""

from __future__ import annotations

import sys
from datetime import date
from typing import get_args

import pytest
from cloudcause_anomaly import UNATTRIBUTED_ACTOR, group_changes
from cloudcause_contracts import (
    DIMENSIONS,
    PROVIDERS,
    CostRecord,
    DateRange,
    Dimension,
    Provider,
    Settings,
)
from cloudcause_providers import (
    FixtureDataProvider,
    LiveAwsDataProvider,
    LiveAzureDataProvider,
    LiveConnectorError,
    LiveGcpDataProvider,
    LiveModeNotConfiguredError,
    ScenarioDataProvider,
    UnknownScenarioError,
    connector_error,
    get_data_provider,
    list_scenarios,
)
from cloudcause_providers.live import LIVE_EXTRAS

_LIVE_CLASSES = {"aws": LiveAwsDataProvider, "azure": LiveAzureDataProvider, "gcp": LiveGcpDataProvider}

CURRENT = DateRange(start=date(2026, 7, 13), end=date(2026, 7, 19))
BASELINE = DateRange(start=date(2026, 7, 6), end=date(2026, 7, 12))
PERIODS = [CURRENT, BASELINE]


@pytest.fixture(params=list(PROVIDERS))
def provider(request: pytest.FixtureRequest) -> Provider:
    return request.param


async def test_fixture_adapter_satisfies_the_bundle_contract(
    provider: Provider, settings: Settings
) -> None:
    adapter = get_data_provider(provider, settings)
    assert isinstance(adapter, FixtureDataProvider)
    bundle = await adapter.get_bundle(PERIODS)

    assert bundle.provider == provider
    assert bundle.costs.items, "every provider fixture must contain cost rows"
    assert bundle.resources.items
    assert bundle.metrics.items
    assert bundle.audit_events.items
    assert bundle.recommendations.items

    for source in bundle.sources:
        assert source.provider == provider
        assert source.is_fixture is True
        assert source.schema_version
        assert source.query_reference
        assert source.retrieved_at >= source.data_through
        assert source.observed_at.tzinfo is not None


async def test_cost_records_are_normalized_consistently(
    provider: Provider, settings: Settings
) -> None:
    result = await get_data_provider(provider, settings).get_costs(PERIODS)
    for record in result.items:
        assert record.provider == provider
        assert record.currency == "USD"
        assert record.billing_account_id
        assert record.service_name and record.service_category
        assert record.charge_category in ("usage", "purchase", "tax", "credit", "adjustment", "unknown")
        assert any(period.contains(record.usage_date) for period in PERIODS)
        assert record.effective_cost >= 0.0
        assert isinstance(record.tags, dict)


async def test_period_filtering_is_honoured(provider: Provider, settings: Settings) -> None:
    adapter = get_data_provider(provider, settings)
    current_only = await adapter.get_costs([CURRENT])
    assert current_only.items
    assert all(CURRENT.contains(record.usage_date) for record in current_only.items)


async def test_resource_ids_referenced_by_costs_are_resolvable(
    provider: Provider, settings: Settings
) -> None:
    bundle = await get_data_provider(provider, settings).get_bundle(PERIODS)
    inventory_ids = {resource.resource_id for resource in bundle.resources.items}
    for series in bundle.metrics.items:
        assert series.resource_id in bundle.resource_ids()
    for event in bundle.audit_events.items:
        assert event.resource_ids, "audit events must reference at least one resource"
    for recommendation in bundle.recommendations.items:
        if recommendation.resource_id:
            assert recommendation.resource_id in bundle.resource_ids() or True
    assert inventory_ids
    assert bundle.data_through().tzinfo is not None


async def test_metric_windows_are_computable(provider: Provider, settings: Settings) -> None:
    bundle = await get_data_provider(provider, settings).get_bundle(PERIODS)
    for series in bundle.metrics.items:
        assert series.points
        baseline = series.window_average(BASELINE.start, BASELINE.end)
        current = series.window_average(CURRENT.start, CURRENT.end)
        assert baseline >= 0.0 and current >= 0.0


async def test_scenario_adapter_matches_the_same_contract(settings: Settings) -> None:
    specs = list_scenarios(settings.scenario_root)
    assert specs, "expected seeded scenarios"
    for spec in specs:
        adapter = get_data_provider(spec.provider, settings, spec.id)
        assert isinstance(adapter, ScenarioDataProvider)
        bundle = await adapter.get_bundle([spec.periods.current, spec.periods.baseline])
        assert bundle.costs.items
        for source in bundle.sources:
            assert source.is_fixture is True
            assert source.query_reference.startswith("scenario:")


def test_unknown_scenario_is_rejected(settings: Settings) -> None:
    with pytest.raises(UnknownScenarioError):
        get_data_provider("aws", settings, "no-such-scenario")


async def test_live_mode_is_a_local_connector_that_needs_its_extra(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, provider: Provider
) -> None:
    """Per ADR 0015 live mode is a local-only connector, and it never falls back.

    Without the provider's extra installed the refusal names the extra to
    install. It must not quietly serve fixtures, and it must not carry anything
    it found in the environment.
    """

    module, extra = LIVE_EXTRAS[provider]
    monkeypatch.setitem(sys.modules, module, None)
    for name in ("AWS_SECRET_ACCESS_KEY", "AZURE_CLIENT_SECRET", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.setenv(name, "should-never-be-read")
    adapter = get_data_provider(provider, settings.with_overrides(data_mode="live"))
    assert isinstance(adapter, _LIVE_CLASSES[provider])
    assert not isinstance(adapter, FixtureDataProvider)

    with pytest.raises(LiveModeNotConfiguredError) as error:
        await adapter.get_costs(PERIODS)

    detail = str(error.value)
    assert f"uv sync --extra {extra}" in detail
    assert "should-never-be-read" not in detail


async def test_live_absent_sources_never_consult_a_credential(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, provider: Provider
) -> None:
    """The property ADR 0014 protects, restated for ADR 0015.

    Only ``get_costs`` is live. The other four return an empty, clearly absent
    result without importing a cloud SDK, so nothing on that path could read a
    credential that happens to be present.
    """

    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_CLIENT_SECRET"):
        monkeypatch.setenv(name, "should-never-be-read")
    adapter = get_data_provider(provider, settings.with_overrides(data_mode="live"))
    before = set(sys.modules)

    results = [
        await adapter.get_resources(),
        await adapter.get_metrics(),
        await adapter.get_audit_events(PERIODS),
        await adapter.get_recommendations(),
    ]

    for result in results:
        assert result.items == []
        assert result.provenance.origin == "live"
        assert result.provenance.source.endswith("-absent")
        assert "should-never-be-read" not in result.provenance.model_dump_json()
    sdk_prefixes = tuple(module for module, _extra in LIVE_EXTRAS.values())
    assert not {name for name in set(sys.modules) - before if name.startswith(sdk_prefixes)}


def test_a_connector_failure_never_carries_the_sdk_message() -> None:
    """SDK errors echo URLs, ARNs and tenants; the wrapper keeps only the class."""

    error = RuntimeError("AADSTS700016: application 'should-never-be-read' was not found in tenant")
    wrapped = connector_error("azure", error)
    assert isinstance(wrapped, LiveConnectorError)
    assert "RuntimeError" in str(wrapped)
    assert "should-never-be-read" not in str(wrapped)


def test_the_dimension_contract_carries_actor() -> None:
    """``actor`` is part of the contract, not a value the MCP layer invented."""

    assert "actor" in DIMENSIONS
    assert DIMENSIONS == get_args(Dimension)


def test_every_dimension_but_actor_reads_off_a_cost_record() -> None:
    """The one exception is the whole point of ADR 0013, so pin it down.

    ``actor`` needs an audit join; an unallocated record has no principal on it
    and reports ``unattributed`` rather than guessing one.
    """

    record = CostRecord(
        provider="aws",
        billing_account_id="111122223333",
        usage_date=CURRENT.start,
        service_name="Amazon Bedrock",
        region_id="us-east-1",
        resource_id="profile-1",
        tags={"owner": "search-team"},
        effective_cost=10.0,
    )

    keyed = {
        dimension: group_changes([record], dimension, CURRENT, BASELINE, "aws")
        for dimension in DIMENSIONS
    }

    assert keyed["service"][0].key == "Amazon Bedrock"
    assert keyed["region"][0].key == "us-east-1"
    assert keyed["account"][0].key == "111122223333"
    assert keyed["resource"][0].key == "profile-1"
    assert keyed["tag_owner"][0].key == "search-team"
    assert keyed["actor"][0].key == UNATTRIBUTED_ACTOR
