"""Provider adapters for CloudCause. Read-only by construction."""

from .fixtures import (
    FIXTURE_FILES,
    FixtureAwsDataProvider,
    FixtureAzureDataProvider,
    FixtureDataProvider,
    FixtureError,
    FixtureGcpDataProvider,
)
from .live import (
    LiveAwsDataProvider,
    LiveAzureDataProvider,
    LiveGcpDataProvider,
    LiveModeNotConfiguredError,
)
from .protocols import BaseDataProvider, CloudDataProvider
from .registry import UnknownScenarioError, available_scenarios, get_data_provider
from .scenarios import (
    ScenarioDataProvider,
    ScenarioSpec,
    build_cost_records,
    get_scenario,
    list_scenarios,
    load_scenario_spec,
)
from .uploads import UploadDataProvider

__all__ = [
    "BaseDataProvider",
    "CloudDataProvider",
    "FIXTURE_FILES",
    "FixtureAwsDataProvider",
    "FixtureAzureDataProvider",
    "FixtureDataProvider",
    "FixtureError",
    "FixtureGcpDataProvider",
    "LiveAwsDataProvider",
    "LiveAzureDataProvider",
    "LiveGcpDataProvider",
    "LiveModeNotConfiguredError",
    "ScenarioDataProvider",
    "ScenarioSpec",
    "UnknownScenarioError",
    "UploadDataProvider",
    "available_scenarios",
    "build_cost_records",
    "get_data_provider",
    "get_scenario",
    "list_scenarios",
    "load_scenario_spec",
]
