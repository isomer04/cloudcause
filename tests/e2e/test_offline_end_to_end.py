"""Offline end-to-end test.

Gateway, ADK orchestrator, Strands AWS worker, MAF Azure worker, GCP specialist,
evidence validator, and report generator, with no cloud accounts, no model keys,
and no network.
"""

from __future__ import annotations

import pytest
from cloudcause_contracts import (
    InvestigationReport,
    InvestigationRequest,
    Settings,
    report_headline,
    report_to_markdown,
)
from cloudcause_orchestrator import Orchestrator

PLANTED_CAUSES = {
    "aws": ("nat_gateway_misroute", "nat-0ab12cd34ef56789a"),
    "azure": (
        "functions_retry_loop",
        "/subscriptions/8f3c2b71-9d4e-4a5f-8c21-7b6e5d4c3a2b/resourceGroups/rg-prod/providers/Microsoft.Web/sites/orders-processor",
    ),
    "gcp": (
        "api_key_abuse",
        "//serviceusage.googleapis.com/projects/cloudcause-demo/services/translate.googleapis.com",
    ),
}


@pytest.fixture
async def report(multi_cloud_request: InvestigationRequest, settings: Settings) -> InvestigationReport:
    events: list[tuple[str, str]] = []

    def emit(stage: str, message: str = "", **_: object) -> None:
        events.append((stage, message))

    result = await Orchestrator(settings).run(multi_cloud_request, emit=emit)
    assert [stage for stage, _ in events][0] == "normalize"
    return result


async def test_all_three_workers_participate(report: InvestigationReport) -> None:
    statuses = {status.provider: status for status in report.provider_statuses}
    assert set(statuses) == {"aws", "azure", "gcp"}
    for provider, status in statuses.items():
        assert status.status == "ok", provider
        assert status.finding_count >= 1
        assert status.is_fixture is True
        assert status.data_through is not None


async def test_every_planted_cause_is_found(report: InvestigationReport) -> None:
    for provider, (category, resource_id) in PLANTED_CAUSES.items():
        matches = [
            finding
            for finding in report.findings
            if finding.provider == provider and finding.category == category
        ]
        assert matches, f"{provider} {category} was not found"
        assert resource_id in matches[0].affected_resources


async def test_findings_are_ranked_by_impact(report: InvestigationReport) -> None:
    impacts = [finding.actual_cost_increase for finding in report.findings if not finding.is_uncertain]
    assert impacts == sorted(impacts, reverse=True)
    assert report_headline(report).startswith("GCP")


async def test_evidence_ids_resolve_and_are_unique(report: InvestigationReport) -> None:
    seen: set[str] = set()
    for finding in report.findings:
        assert finding.evidence, finding.finding_id
        for item in finding.evidence:
            assert item.evidence_id not in seen
            seen.add(item.evidence_id)
            assert item.query_reference
            assert item.data_through is not None
            assert item.is_fixture is True
    assert len(seen) == report.evidence_count()


async def test_costs_reconcile_within_tolerance(report: InvestigationReport) -> None:
    assert report.reconciliation is not None
    assert report.reconciliation.within_tolerance is True
    attributed = sum(finding.actual_cost_increase for finding in report.findings)
    assert attributed == pytest.approx(report.reconciliation.attributed_change, abs=0.01)
    assert report.total_absolute_change == pytest.approx(
        report.total_current_cost - report.comparison.providers[0].expected_baseline_cost
        - report.comparison.providers[1].expected_baseline_cost
        - report.comparison.providers[2].expected_baseline_cost,
        abs=0.01,
    )


async def test_every_interpretation_cites_a_versioned_rule(report: InvestigationReport) -> None:
    assert report.knowledge is not None
    assert report.knowledge.focus_version == "1.4"
    assert report.knowledge.stale_rule_ids == []
    for finding in report.findings:
        assert finding.applied_rules, finding.finding_id
        for rule in finding.applied_rules:
            assert rule.source_url.startswith("https://")
            assert rule.valid_from is not None
            assert rule.selected_for_date is not None
            assert rule.valid_from <= rule.selected_for_date
            assert rule.rule_id in report.knowledge.rule_ids


async def test_no_finding_claims_an_action_was_taken(report: InvestigationReport) -> None:
    for finding in report.findings:
        assert finding.requires_human_approval is True
        assert finding.risk in ("low", "medium", "high")
        lowered = finding.recommendation.lower()
        assert "we deleted" not in lowered
        assert "has been terminated" not in lowered


async def test_no_mutating_tool_is_available_to_any_worker(settings: Settings) -> None:
    from cloudcause_mcp import KNOWLEDGE_TOOL_ALLOWLIST, OPERATIONAL_TOOL_ALLOWLIST
    from cloudcause_worker_core import NativeToolset

    orchestrator = Orchestrator(settings)
    for client in orchestrator.workers.values():
        health = await client.health()
        assert health["read_only"] is True
        assert health["mutating_tools"] == []
    for name in (*OPERATIONAL_TOOL_ALLOWLIST, *KNOWLEDGE_TOOL_ALLOWLIST):
        assert name.startswith("get_")
    native_names = {"get_investigation_plan", "get_anomaly_candidates", "get_candidate_evidence",
                    "recalculate_attribution", "record_finding"}
    assert {method.__name__ for method in NativeToolset.__dict__.values() if callable(method)} >= native_names


async def test_report_renders_to_markdown_with_provenance(report: InvestigationReport) -> None:
    markdown = report_to_markdown(report)
    assert "## Findings" in markdown
    assert "## Cost reconciliation" in markdown
    assert "Data through:" in markdown
    assert "FOCUS version:** 1.4" in markdown
    assert "read-only" in markdown.lower()
    for finding in report.findings:
        assert finding.finding_id in markdown
        for item in finding.evidence:
            assert item.evidence_id in markdown


async def test_investigation_is_reproducible(
    multi_cloud_request: InvestigationRequest, settings: Settings
) -> None:
    first = await Orchestrator(settings).run(multi_cloud_request)
    second = await Orchestrator(settings).run(multi_cloud_request)
    assert [f.category for f in first.findings] == [f.category for f in second.findings]
    assert [f.actual_cost_increase for f in first.findings] == [
        f.actual_cost_increase for f in second.findings
    ]
    assert first.investigation_id != second.investigation_id
