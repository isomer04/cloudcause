"""Native tool calling guardrails.

Live agents may only cite evidence they were given, and may never supply their own
cost figures.
"""

from __future__ import annotations

from datetime import date

import pytest
from cloudcause_anomaly import compare_provider
from cloudcause_aws import AWS_PLAYBOOKS, AwsInvestigator
from cloudcause_contracts import InvestigationRequest, ProviderTask, Settings, WorkerRequest
from cloudcause_worker_core import NativeToolset


@pytest.fixture
async def toolset(settings: Settings) -> NativeToolset:
    request = InvestigationRequest(
        providers=["aws"],
        start_date=date(2026, 7, 13),
        end_date=date(2026, 7, 19),
        comparison_start_date=date(2026, 7, 6),
        comparison_end_date=date(2026, 7, 12),
        question="Why did AWS spending increase?",
    )
    investigator = AwsInvestigator(settings)
    provider_data = await investigator.build_context(
        WorkerRequest(
            investigation_id="inv-native-0001",
            provider="aws",
            request=request,
            task=ProviderTask(provider="aws", question="Explain the increase"),
            candidates=[],
        )
    )
    comparison = compare_provider(
        provider_data.bundle.costs.items,
        "aws",
        request.current_period,
        request.baseline_period,
        settings.analytics,
    )
    provider_data.candidates = comparison.candidates
    return NativeToolset(provider_data, AWS_PLAYBOOKS)


async def test_candidates_and_plan_are_readable(toolset: NativeToolset) -> None:
    plan = toolset.get_investigation_plan()
    assert plan["provider"] == "aws"
    assert plan["current_period"] == "2026-07-13..2026-07-19"

    candidates = toolset.get_anomaly_candidates()
    assert candidates
    assert candidates[0]["suggested_category"] == "nat_gateway_misroute"
    assert candidates[0]["absolute_change"] == pytest.approx(126.0)


async def test_recorded_findings_use_deterministic_numbers(toolset: NativeToolset) -> None:
    candidate = toolset.get_anomaly_candidates()[0]
    evidence = toolset.get_candidate_evidence(candidate["candidate_id"])
    assert evidence and all("evidence_id" in item for item in evidence)

    result = toolset.record_finding(
        candidate_id=candidate["candidate_id"],
        category="nat_gateway_misroute",
        suspected_root_cause="A route change sent S3 traffic through the NAT Gateway.",
        recommendation="Restore the S3 gateway endpoint after human review.",
        evidence_ids=[item["evidence_id"] for item in evidence[:3]],
        risk="medium",
    )
    assert result["accepted"] is True
    assert result["actual_cost_increase"] == pytest.approx(126.0)
    finding = toolset.findings[0]
    assert finding.agent_mode == "live"
    assert finding.applied_rules
    assert len(finding.evidence) == 3


async def test_unknown_evidence_ids_are_rejected(toolset: NativeToolset) -> None:
    candidate = toolset.get_anomaly_candidates()[0]
    result = toolset.record_finding(
        candidate_id=candidate["candidate_id"],
        category="nat_gateway_misroute",
        suspected_root_cause="Invented evidence",
        recommendation="none",
        evidence_ids=["AWS-E999"],
    )
    assert result["accepted"] is False
    assert "unknown evidence ids" in result["error"]
    assert toolset.findings == []


async def test_unknown_candidate_is_rejected(toolset: NativeToolset) -> None:
    assert toolset.record_finding(
        candidate_id="aws-cand-99",
        category="idle_compute",
        suspected_root_cause="x",
        recommendation="y",
        evidence_ids=[],
    )["accepted"] is False
    assert toolset.get_candidate_evidence("aws-cand-99")[0]["error"]


async def test_recalculation_comes_from_the_deterministic_layer(toolset: NativeToolset) -> None:
    candidate = toolset.get_anomaly_candidates()[0]
    payload = toolset.recalculate_attribution(candidate["candidate_id"])
    assert payload["absolute_change"] == pytest.approx(126.0)
    assert payload["estimated_monthly_impact"] == pytest.approx(126.0 / 7 * 30.4, rel=1e-3)
    assert toolset.recalculate_attribution("nope")["error"]
