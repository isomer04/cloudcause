"""Adapter selection.

One function, one branch point. Resolution order:

1. ``dataset_id``  - a sealed upload, which wins over everything else
2. ``CLOUDCAUSE_DATA_MODE=live``  - the live provider connectors
3. ``scenario_id``  - one of the twelve seeded scenarios
4. ``default``  - the demo fixtures

An unresolvable ``dataset_id`` raises. It never falls through to fixtures: a run
labelled with somebody's upload that quietly reported demo numbers is the exact
class of silent wrongness this project exists to avoid.
"""

from __future__ import annotations

from cloudcause_contracts import Provider, Settings
from cloudcause_datasets import (
    DatasetProviderMissingError,
    build_dataset_store,
)

from .fixtures import FixtureDataProvider
from .live import LiveAwsDataProvider, LiveAzureDataProvider, LiveGcpDataProvider
from .protocols import BaseDataProvider
from .scenarios import ScenarioDataProvider, get_scenario
from .uploads import UploadDataProvider

_LIVE_PROVIDERS = {
    "aws": LiveAwsDataProvider,
    "azure": LiveAzureDataProvider,
    "gcp": LiveGcpDataProvider,
}


class UnknownScenarioError(LookupError):
    pass


def get_data_provider(
    provider: Provider,
    settings: Settings,
    scenario_id: str = "default",
    dataset_id: str | None = None,
) -> BaseDataProvider:
    if dataset_id:
        return _upload_provider(provider, settings, dataset_id)
    if settings.data_mode == "live":
        return _LIVE_PROVIDERS[provider]()
    if scenario_id in ("", "default"):
        return FixtureDataProvider(provider, settings.fixture_root)
    spec = get_scenario(settings.scenario_root, scenario_id)
    if spec is None:
        raise UnknownScenarioError(
            f"unknown scenario {scenario_id!r}; expected 'default' or a file in {settings.scenario_root}"
        )
    if spec.provider != provider:
        raise UnknownScenarioError(
            f"scenario {scenario_id!r} describes provider {spec.provider!r}, not {provider!r}"
        )
    return ScenarioDataProvider(spec)


def _upload_provider(
    provider: Provider, settings: Settings, dataset_id: str
) -> UploadDataProvider:
    """Rebuild one provider's view of a sealed dataset from its id alone.

    Every process does this independently, which is why the dataset has to be
    sealed first: three readers plus an MCP child must see identical data while an
    investigation runs.

    A SQL store opens a connection per call, so it is closed on every path
    including a refusal. The memory store is the process-wide singleton every
    reader shares and must outlive this call, so it is left alone.
    """

    store = build_dataset_store(settings)
    try:
        dataset = store.get_for_investigation(dataset_id)
        if not dataset.sources_for(provider):
            raise DatasetProviderMissingError(
                f"dataset {dataset_id} carries no {provider} data; it holds "
                f"{', '.join(dataset.providers()) or 'nothing'}"
            )
        return UploadDataProvider(provider, dataset)
    finally:
        close = getattr(store, "close", None)
        if store.kind == "sql" and callable(close):
            close()


def available_scenarios(settings: Settings) -> list[dict[str, str]]:
    from .scenarios import list_scenarios

    entries = [
        {
            "id": "default",
            "title": "Multi-cloud demo: NAT Gateway misroute, Functions retry loop, exposed API key",
            "providers": "aws,azure,gcp",
            "category": "multi_cloud",
        }
    ]
    for spec in list_scenarios(settings.scenario_root):
        entries.append(
            {
                "id": spec.id,
                "title": spec.title,
                "providers": spec.provider,
                "category": spec.category,
            }
        )
    return entries
