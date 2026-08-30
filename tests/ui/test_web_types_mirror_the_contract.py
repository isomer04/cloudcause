"""``web/lib/types.ts`` must mirror the gateway contract.

The Next.js UI is a thin client: it formats what the gateway
computed and never derives a cost, a percentage, or a confidence score. Nothing
under ``tests/`` used to read ``web`` at all, so a contract field could be
added in Python and silently missing in TypeScript until a runtime ``undefined``.

This reads the real Pydantic models and the real ``.ts`` file. It does not parse
TypeScript properly, and it does not need to: interface bodies in this file are one
``name: type;`` per line by convention, and the assertions are about field names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from cloudcause_contracts import (
    DATA_ORIGINS,
    DATASET_SOURCE_KINDS,
    PROVIDERS,
    AnalyticsConfig,
    AnomalyCandidate,
    CloudCauseModel,
    DailyTotal,
    DatasetCreated,
    DatasetIngestReport,
    DatasetRowRejection,
    DatasetSourceSummary,
    DatasetSummary,
    DateRange,
    Evidence,
    Finding,
    GatewayHealth,
    InvestigationCreated,
    InvestigationPlan,
    InvestigationReport,
    InvestigationRequest,
    InvestigationState,
    KnowledgeProvenance,
    PeriodComparison,
    ProgressEvent,
    Provenance,
    ProviderComparison,
    ProviderStatus,
    ProviderTask,
    Reconciliation,
    RuleCitation,
    ScenarioSummary,
    ValidationIssue,
)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
TYPES = WEB_ROOT / "lib" / "types.ts"

#: Python model -> TypeScript interface. Every model the UI reads is here; adding
#: a field to one of these without adding it to types.ts fails this test.
MIRRORED: dict[str, type[CloudCauseModel]] = {
    "AnalyticsConfig": AnalyticsConfig,
    "DateRange": DateRange,
    "ScenarioSummary": ScenarioSummary,
    "InvestigationRequest": InvestigationRequest,
    "Provenance": Provenance,
    "Evidence": Evidence,
    "RuleCitation": RuleCitation,
    "Finding": Finding,
    "ProviderStatus": ProviderStatus,
    "DailyTotal": DailyTotal,
    "AnomalyCandidate": AnomalyCandidate,
    "Reconciliation": Reconciliation,
    "ProviderComparison": ProviderComparison,
    "PeriodComparison": PeriodComparison,
    "KnowledgeProvenance": KnowledgeProvenance,
    "ValidationIssue": ValidationIssue,
    "ProviderTask": ProviderTask,
    "InvestigationPlan": InvestigationPlan,
    "InvestigationReport": InvestigationReport,
    "InvestigationState": InvestigationState,
    "InvestigationCreated": InvestigationCreated,
    "GatewayHealth": GatewayHealth,
    "ProgressEvent": ProgressEvent,
    "DatasetRowRejection": DatasetRowRejection,
    "DatasetSourceSummary": DatasetSourceSummary,
    "DatasetIngestReport": DatasetIngestReport,
    "DatasetSummary": DatasetSummary,
    "DatasetCreated": DatasetCreated,
}

FORBIDDEN_IN_WEB = (
    "cloudcause_orchestrator",
    "cloudcause_anomaly",
    "cloudcause_knowledge",
    "cloudcause_providers",
    "cloudcause_worker_core",
    "cloudcause_datasets",
)

SERVER_GATEWAY = WEB_ROOT / "lib" / "gateway-server.ts"
CLIENT_GATEWAY = WEB_ROOT / "lib" / "gateway-client.ts"
SHARED_GATEWAY = WEB_ROOT / "lib" / "gateway-shared.ts"


@pytest.fixture(scope="module")
def source() -> str:
    assert TYPES.is_file(), f"expected the contract mirror at {TYPES}"
    return TYPES.read_text(encoding="utf-8")


def interface_fields(source: str, name: str) -> set[str]:
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.DOTALL)
    assert match, f"types.ts is missing `export interface {name}`"
    return set(re.findall(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)\??:", match.group(1), re.MULTILINE))


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_every_contract_field_is_mirrored(name: str, source: str) -> None:
    expected = set(MIRRORED[name].model_fields)
    mirrored = interface_fields(source, name)
    missing = expected - mirrored
    assert not missing, f"types.ts {name} is missing {sorted(missing)}"
    extra = mirrored - expected
    assert not extra, f"types.ts {name} declares fields the gateway does not send: {sorted(extra)}"


def test_the_three_data_origins_are_mirrored(source: str) -> None:
    match = re.search(r"export type DataOrigin = ([^;]+);", source)
    assert match, "types.ts must declare DataOrigin"
    assert {value.strip().strip('"') for value in match.group(1).split("|")} == set(DATA_ORIGINS)


def test_the_provider_and_source_kind_unions_are_mirrored(source: str) -> None:
    providers = re.search(r"export type Provider = ([^;]+);", source)
    assert providers is not None
    assert {value.strip().strip('"') for value in providers.group(1).split("|")} == set(PROVIDERS)

    kinds = re.search(r"export type DatasetSourceKind =([^;]+);", source)
    assert kinds is not None
    assert {value.strip().strip('"') for value in kinds.group(1).split("|")} == set(
        DATASET_SOURCE_KINDS
    )


def test_gateway_health_exposes_per_investigation_mode_selection(source: str) -> None:
    """Keep the Playwright-observed capability signal in the web type mirror."""

    assert 'agent_mode_selection: "per_investigation";' in source


def test_the_deprecated_flag_is_marked_as_such(source: str) -> None:
    """``origin`` is the source of truth; ``is_fixture`` is on its way out."""

    assert source.count("@deprecated") >= 3, (
        "each carrier of is_fixture should say so, so the field is removed rather than relied on"
    )


def test_the_web_ui_never_imports_the_investigation_stack() -> None:
    for path in sorted(WEB_ROOT.rglob("*.ts")) + sorted(WEB_ROOT.rglob("*.tsx")):
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for module in FORBIDDEN_IN_WEB:
            assert module not in text, f"{path.name} must not depend on {module}"


def _web_source_files() -> list[Path]:
    return sorted(
        path
        for suffix in ("*.ts", "*.tsx")
        for path in WEB_ROOT.rglob(suffix)
        if "node_modules" not in path.parts and ".next" not in path.parts
    )


def _local_imports(path: Path) -> set[Path]:
    """Resolve local TypeScript imports well enough to protect gateway boundaries."""

    imports = re.findall(
        r"""(?:import|export)\s+(?:type\s+)?(?:[^"']*?\s+from\s+)?["']([^"']+)["']""",
        path.read_text(encoding="utf-8"),
    )
    resolved: set[Path] = set()
    for module in imports:
        base = WEB_ROOT / module[2:] if module.startswith("@/") else path.parent / module
        if not module.startswith(("@/", ".")):
            continue
        candidates = [base] if base.suffix else [base.with_suffix(".ts"), base.with_suffix(".tsx")]
        for candidate in candidates:
            if candidate.is_file():
                resolved.add(candidate.resolve())
                break
    return resolved


def test_gateway_server_boundary_cannot_enter_client_component_graphs() -> None:
    """Private upstream configuration must fail before it can reach a browser bundle."""

    assert not (WEB_ROOT / "lib" / "gateway.ts").exists(), "the mixed gateway module is retired"
    assert SERVER_GATEWAY.is_file()
    assert CLIENT_GATEWAY.is_file()
    assert SHARED_GATEWAY.is_file()
    assert 'import "server-only";' in SERVER_GATEWAY.read_text(encoding="utf-8")

    all_sources = _web_source_files()
    env_references = [
        path
        for path in all_sources
        if "CLOUDCAUSE_API_URL" in path.read_text(encoding="utf-8")
    ]
    assert env_references == [SERVER_GATEWAY], "the upstream URL is server-only configuration"

    client_roots = [
        path
        for path in all_sources
        if path.read_text(encoding="utf-8").lstrip().startswith(('"use client"', "'use client'"))
    ]
    for root in client_roots:
        pending = [root.resolve()]
        visited: set[Path] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            assert current != SERVER_GATEWAY.resolve(), (
                f"{root.relative_to(WEB_ROOT)} imports the server gateway through its client graph"
            )
            pending.extend(_local_imports(current) - visited)


def test_gateway_callers_use_their_environment_specific_module() -> None:
    for path in (
        WEB_ROOT / "app" / "page.tsx",
        WEB_ROOT / "app" / "history" / "page.tsx",
        WEB_ROOT / "app" / "investigations" / "[id]" / "page.tsx",
        WEB_ROOT / "components" / "rail.tsx",
        WEB_ROOT / "app" / "gw" / "[...path]" / "route.ts",
    ):
        assert 'from "@/lib/gateway-server"' in path.read_text(encoding="utf-8")

    for path in (
        WEB_ROOT / "components" / "console.tsx",
        WEB_ROOT / "components" / "data-source.tsx",
        WEB_ROOT / "components" / "report" / "exports.tsx",
    ):
        text = path.read_text(encoding="utf-8")
        assert 'from "@/lib/gateway-client"' in text
        assert "gateway-server" not in text


def test_the_web_ui_defaults_each_investigation_to_deterministic_playbooks() -> None:
    console = (WEB_ROOT / "components" / "console.tsx").read_text(encoding="utf-8")
    brief = (WEB_ROOT / "components" / "brief.tsx").read_text(encoding="utf-8")
    assert 'agent_mode: "stub"' in console
    assert 'current?.agent_mode ?? "stub"' in console
    assert 'onRequestChange({ ...request, agent_mode: "live" })' in brief
    assert 'onRequestChange({ ...request, agent_mode: "stub" })' in brief
    assert "Live AI agents" in brief
    assert "Deterministic playbooks" in brief


def test_the_web_ui_offers_live_whenever_a_model_key_makes_it_possible() -> None:
    """Availability follows the key, never a mode setting, so a deployment that
    can run live always offers both paths without any further configuration."""

    page = (WEB_ROOT / "app" / "page.tsx").read_text(encoding="utf-8")
    brief = (WEB_ROOT / "components" / "brief.tsx").read_text(encoding="utf-8")
    assert "health?.live_agents_available" in page
    assert "default_agent_mode" not in page, "the brief must not gate on the client default"
    assert "liveAllowed={liveAllowed}" in (WEB_ROOT / "components" / "console.tsx").read_text(
        encoding="utf-8"
    )
    assert "disabled={!liveAllowed}" in brief


def test_the_web_ui_computes_no_money_of_its_own() -> None:
    """The thin-client rule, enforced over web for the first time.

    The frontend may format and it may total nothing. Anything that looks like
    arithmetic over cost fields belongs in the gateway.
    """

    arithmetic = re.compile(
        r"(current_cost|baseline_cost|absolute_change|actual_cost_increase|"
        r"estimated_monthly_impact|total_current_cost|total_absolute_change)\s*[-+*/]"
    )
    for path in sorted((WEB_ROOT / "components").rglob("*.tsx")) + sorted(
        (WEB_ROOT / "lib").rglob("*.ts")
    ):
        text = path.read_text(encoding="utf-8")
        assert not arithmetic.search(text), (
            f"{path.name} appears to compute a cost figure; the gateway owns every number"
        )


def test_the_proxy_allowlists_every_method_it_forwards() -> None:
    route = (WEB_ROOT / "app" / "gw" / "[...path]" / "route.ts").read_text(encoding="utf-8")
    for method in ("GET", "POST", "PUT", "DELETE"):
        assert f"{method}_PATTERNS" in route, f"{method} needs its own allowlist, not a shared one"
        assert f"export const {method} = handle" in route
    assert 'duplex: "half"' in route, "upload bodies must stream rather than buffer in Node"
    assert "MAX_UPLOAD_BYTES" in route, "the proxy needs its own byte guard"


def test_history_preserves_the_gateways_newest_first_order() -> None:
    """Repeat runs may be collapsed, but the page must never re-order the list.

    Grouping walks the gateway's response once and keys into a ``Map``, whose
    iteration order is insertion order, so the newest run of each distinct
    question still appears where the gateway put it.
    """

    page = (WEB_ROOT / "app" / "history" / "page.tsx").read_text(encoding="utf-8")
    assert ".reverse(" not in page, "the gateway is the ordering authority"
    assert "investigations.sort(" not in page, "the gateway is the ordering authority"
    assert "for (const state of investigations)" in page
    assert "groups.map(" in page


def test_the_rail_can_clear_a_finished_report_from_the_console() -> None:
    """"Investigate" has to work from the console, not only from a report page.

    The console keeps a finished report in React state. Clicking a link to the
    route you are already on is a soft navigation, so the component never
    remounts and the report survives - the rail item looks dead. The rail
    therefore intercepts that one case and asks the console to reset, and the
    console listens. Both halves must be present or the link silently does
    nothing again.
    """

    event = "NEW_INVESTIGATION_EVENT"
    nav = (WEB_ROOT / "components" / "nav-links.tsx").read_text(encoding="utf-8")
    console = (WEB_ROOT / "components" / "console.tsx").read_text(encoding="utf-8")

    assert f"dispatchEvent(new Event({event}))" in nav, "the rail must announce the reset"
    assert 'pathname !== "/"' in nav, "only the same-route click may be intercepted"
    assert f"window.addEventListener({event}" in console, "the console must listen"
    assert f"window.removeEventListener({event}" in console, "and must unsubscribe"
    assert "resetInvestigation();" in console
    assert "New investigation" not in console, (
        "the header button was removed; the rail is the single way back to a blank brief"
    )
