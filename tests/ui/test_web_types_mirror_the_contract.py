"""``apps/web/lib/types.ts`` must mirror the gateway contract.

The Next.js UI is a thin client: it formats what the gateway
computed and never derives a cost, a percentage, or a confidence score. Nothing
under ``tests/`` used to read ``apps/web`` at all, so a contract field could be
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
    CloudCauseModel,
    DatasetCreated,
    DatasetIngestReport,
    DatasetRowRejection,
    DatasetSourceSummary,
    DatasetSummary,
    Evidence,
    Finding,
    InvestigationPlan,
    InvestigationReport,
    InvestigationRequest,
    InvestigationState,
    ProgressEvent,
    Provenance,
    ProviderStatus,
    ProviderTask,
    ValidationIssue,
)

WEB_ROOT = Path(__file__).resolve().parents[2] / "apps" / "web"
TYPES = WEB_ROOT / "lib" / "types.ts"

#: Python model -> TypeScript interface. Every model the UI reads is here; adding
#: a field to one of these without adding it to types.ts fails this test.
MIRRORED: dict[str, type[CloudCauseModel]] = {
    "InvestigationRequest": InvestigationRequest,
    "Provenance": Provenance,
    "Evidence": Evidence,
    "Finding": Finding,
    "ProviderStatus": ProviderStatus,
    "ValidationIssue": ValidationIssue,
    "ProviderTask": ProviderTask,
    "InvestigationPlan": InvestigationPlan,
    "InvestigationReport": InvestigationReport,
    "InvestigationState": InvestigationState,
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


def test_the_web_ui_computes_no_money_of_its_own() -> None:
    """The thin-client rule, enforced over apps/web for the first time.

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
