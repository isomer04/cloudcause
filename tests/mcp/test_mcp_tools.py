"""MCP boundary tests.

The tool functions are exercised directly (no model, no subprocess) and the two
stdio servers are checked for their advertised, read-only tool set.
"""

from __future__ import annotations

import pytest
from cloudcause_contracts import Settings
from cloudcause_mcp import (
    KNOWLEDGE_TOOL_ALLOWLIST,
    OPERATIONAL_TOOL_ALLOWLIST,
    BillingKnowledgeTools,
    OperationalDataTools,
    knowledge_server_params,
    operational_server_params,
)

MUTATING_WORDS = ("delete", "stop", "start", "scale", "modify", "update", "create", "rotate", "put")


@pytest.fixture(params=["aws", "azure", "gcp"])
def operational(request: pytest.FixtureRequest, settings: Settings) -> OperationalDataTools:
    return OperationalDataTools(request.param, settings)


def test_no_operational_tool_can_change_a_resource() -> None:
    for name in OPERATIONAL_TOOL_ALLOWLIST:
        assert name.startswith("get_")
        assert not any(word in name for word in MUTATING_WORDS)


async def test_cost_breakdown_carries_provenance(operational: OperationalDataTools) -> None:
    payload = await operational.get_cost_breakdown(
        "2026-07-13", "2026-07-19", "2026-07-06", "2026-07-12", group_by="service"
    )
    assert payload["read_only"] is True
    assert payload["groups"]
    provenance = payload["provenance"]
    assert provenance["provider"] == operational.provider
    assert provenance["is_fixture"] is True
    assert provenance["data_through"]
    assert provenance["query_reference"]


async def test_inventory_metrics_audit_and_recommendations(operational: OperationalDataTools) -> None:
    inventory = await operational.get_resource_inventory()
    assert inventory["items"]
    resource_id = inventory["items"][0]["resource_id"]

    filtered = await operational.get_resource_inventory(resource_id=resource_id)
    assert [item["resource_id"] for item in filtered["items"]] == [resource_id]

    events = await operational.get_audit_events("2026-07-13", "2026-07-19")
    assert events["untrusted_content"] is True

    recommendations = await operational.get_recommendations()
    assert recommendations["items"]

    freshness = await operational.get_data_freshness()
    assert freshness["sources"]["costs"]["data_through"]


async def test_metrics_are_scoped_to_one_resource(settings: Settings) -> None:
    tools = OperationalDataTools("aws", settings)
    payload = await tools.get_resource_metrics("nat-0ab12cd34ef56789a")
    assert payload["items"]
    assert {item["resource_id"] for item in payload["items"]} == {"nat-0ab12cd34ef56789a"}


def test_knowledge_tools_are_date_aware(settings: Settings) -> None:
    tools = BillingKnowledgeTools(settings)
    older = tools.get_billing_rule("gcp", category="commitment_change", usage_date="2025-06-01")
    newer = tools.get_billing_rule("gcp", category="commitment_change", usage_date="2026-03-01")
    assert older["rule"]["id"] == "gcp-committed-use-discount-v1"
    assert newer["rule"]["id"] == "gcp-committed-use-discount-multiprice"
    for payload in (older, newer):
        assert payload["read_only"] is True
        assert payload["rule"]["source"]["url"].startswith("https://")
        assert payload["citation"]["reviewed_at"]


def test_knowledge_tool_surface_matches_the_plan(settings: Settings) -> None:
    tools = BillingKnowledgeTools(settings)
    assert set(KNOWLEDGE_TOOL_ALLOWLIST) == {
        "get_billing_rule",
        "get_cost_driver_definitions",
        "get_provider_data_freshness_rules",
        "get_export_schema_version",
        "get_api_deprecation_status",
        "get_pricing_source",
        "get_known_billing_change",
    }
    for name in KNOWLEDGE_TOOL_ALLOWLIST:
        assert callable(getattr(tools, name))

    freshness = tools.get_provider_data_freshness_rules("aws", usage_date="2026-07-19")
    assert freshness["rule"]["data"]["treat_missing_as_zero"] is False
    changes = tools.get_known_billing_change("gcp", start="2026-01-01", end="2026-12-31")
    assert changes["changes"][0]["source_url"].startswith("https://")


async def test_servers_register_only_allowlisted_tools() -> None:
    from cloudcause_mcp.knowledge_server import build_server as build_knowledge_server
    from cloudcause_mcp.operational_server import build_server as build_operational_server

    operational_server, _ = build_operational_server("aws", "default")
    knowledge_server, _ = build_knowledge_server()
    operational_names = {tool.name for tool in await operational_server.list_tools()}
    knowledge_names = {tool.name for tool in await knowledge_server.list_tools()}
    assert operational_names == set(OPERATIONAL_TOOL_ALLOWLIST)
    assert knowledge_names == set(KNOWLEDGE_TOOL_ALLOWLIST)


def test_stdio_launch_parameters_target_this_interpreter() -> None:
    operational = operational_server_params("azure", "azure-functions-retry-loop")
    assert operational["args"] == ["-m", "cloudcause_mcp.operational_server"]
    assert operational["env"]["CLOUDCAUSE_MCP_PROVIDER"] == "azure"
    assert operational["env"]["CLOUDCAUSE_SCENARIO_ID"] == "azure-functions-retry-loop"
    assert knowledge_server_params()["args"] == ["-m", "cloudcause_mcp.knowledge_server"]


def test_the_stdio_child_carries_the_dataset_id() -> None:
    """The child is a subprocess, so its selector travels in the environment.

    Without this a live agent's tools would resolve the demo fixtures while its
    parent read the user's upload, and nothing would say so.
    """

    with_dataset = operational_server_params("aws", "", "ds-abc123")
    assert with_dataset["env"]["CLOUDCAUSE_DATASET_ID"] == "ds-abc123"
    assert with_dataset["env"]["CLOUDCAUSE_SCENARIO_ID"] == ""

    without = operational_server_params("aws", "default")
    assert without["env"]["CLOUDCAUSE_DATASET_ID"] == "", (
        "an empty value must not be mistaken for a dataset by the child"
    )


def test_the_child_resolves_the_dataset_its_environment_names(
    monkeypatch: pytest.MonkeyPatch, upload_settings
) -> None:
    from cloudcause_datasets import (
        add_source,
        build_dataset_store,
        parse_cost_source,
        seal_dataset,
    )
    from cloudcause_providers import UploadDataProvider
    from conftest import aws_cur_json

    store = build_dataset_store(upload_settings)
    dataset = store.create()
    add_source(
        store,
        dataset.dataset_id,
        "aws",
        "cost",
        parse_cost_source("aws", aws_cur_json(), upload_settings),
        512,
        upload_settings,
    )
    seal_dataset(store, dataset.dataset_id)

    monkeypatch.setenv("CLOUDCAUSE_MCP_PROVIDER", "aws")
    monkeypatch.setenv("CLOUDCAUSE_DATASET_ID", dataset.dataset_id)
    from cloudcause_mcp.operational_server import build_server as build_operational_server

    _, tools = build_operational_server()
    assert tools.dataset_id == dataset.dataset_id
    assert isinstance(tools._adapter, UploadDataProvider)
