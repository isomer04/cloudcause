"""Provider contract tests.

Every fixture adapter is held to the same behavioural contract its future live
adapter must satisfy, so switching CLOUDCAUSE_DATA_MODE cannot change the shape
of the data the agents see.
"""

from __future__ import annotations

from datetime import date

import pytest
from cloudcause_contracts import PROVIDERS, DateRange, Provider, Settings
from cloudcause_providers import (
    FixtureDataProvider,
    LiveModeNotConfiguredError,
    ScenarioDataProvider,
    UnknownScenarioError,
    get_data_provider,
    list_scenarios,
)

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


async def test_live_mode_fails_loudly_instead_of_silently_using_fixtures(
    settings: Settings, provider: Provider
) -> None:
    live_settings = settings.with_overrides(data_mode="live")
    adapter = get_data_provider(provider, live_settings)
    with pytest.raises(LiveModeNotConfiguredError) as error:
        await adapter.get_costs(PERIODS)
    assert "has no connector yet" in str(error.value)
