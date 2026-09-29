"""The gate between agent output and the published report."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from cloudcause_contracts import (
    AnalyticsConfig,
    AnomalyCandidate,
    DateRange,
    Evidence,
    Finding,
    PeriodComparison,
    ProviderComparison,
    Reconciliation,
    RuleCitation,
)
from cloudcause_evidence import rank_findings, validate_findings

CURRENT = DateRange(start=date(2026, 7, 13), end=date(2026, 7, 19))
BASELINE = DateRange(start=date(2026, 7, 6), end=date(2026, 7, 12))
OBSERVED = datetime(2026, 7, 19, 23, 59, 59, tzinfo=UTC)


def candidate(change: float = 100.0) -> AnomalyCandidate:
    return AnomalyCandidate(
        candidate_id="aws-cand-00",
        provider="aws",
        dimension="resource",
        key="nat-1",
        resource_id="nat-1",
        service_name="Amazon Virtual Private Cloud",
        baseline_cost=10.0,
        current_cost=10.0 + change,
        expected_baseline_cost=10.0,
        absolute_change=change,
        percent_change=change * 10,
    )


def comparison(change: float = 100.0) -> PeriodComparison:
    provider_comparison = ProviderComparison(
        provider="aws",
        current_period=CURRENT,
        baseline_period=BASELINE,
        current_cost=110.0,
        baseline_cost=10.0,
        expected_baseline_cost=10.0,
        absolute_change=change,
        percent_change=change * 10,
        candidates=[candidate(change)],
        reconciliation=Reconciliation(
            total_change=change,
            attributed_change=change,
            unattributed_change=0.0,
            tolerance=0.05,
            within_tolerance=True,
        ),
    )
    return PeriodComparison(
        current_period=CURRENT,
        baseline_period=BASELINE,
        config=AnalyticsConfig(),
        providers=[provider_comparison],
        total_absolute_change=change,
    )


def evidence(source_type: str = "cost", **overrides) -> Evidence:
    payload = {
        "evidence_id": f"AWS-E{source_type[:3].upper()}",
        "provider": "aws",
        "source_type": source_type,
        "source_id": "nat-1",
        "observed_at": OBSERVED,
        "statement": "Cost rose.",
        "query_reference": "fixture:aws/cost_and_usage.json",
    }
    payload.update(overrides)
    return Evidence(**payload)


def citation(**overrides) -> RuleCitation:
    payload = {
        "rule_id": "aws-nat-gateway-data-processing",
        "provider": "aws",
        "rule_type": "cost_driver",
        "valid_from": date(2025, 1, 1),
        "reviewed_at": date(2026, 7, 27),
        "source_url": "https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-pricing.html",
        "selected_for_date": date(2026, 7, 15),
    }
    payload.update(overrides)
    return RuleCitation(**payload)


def finding(**overrides) -> Finding:
    payload = {
        "finding_id": "AWS-F01",
        "provider": "aws",
        "category": "nat_gateway_misroute",
        "suspected_root_cause": "Route change sends S3 traffic through the NAT Gateway.",
        "affected_resources": ["nat-1"],
        "evidence": [evidence("cost"), evidence("metric"), evidence("audit")],
        "confidence": 0.85,
        "actual_cost_increase": 100.0,
        "estimated_monthly_impact": 434.0,
        "recommendation": "Restore the gateway endpoint.",
        "risk": "medium",
        "candidate_id": "aws-cand-00",
        "applied_rules": [citation()],
    }
    payload.update(overrides)
    return Finding(**payload)


def test_a_well_supported_finding_is_published_unchanged() -> None:
    result = validate_findings(
        [finding()], known_resource_ids={"aws": {"nat-1"}}, comparison=comparison()
    )
    assert [f.finding_id for f in result.findings] == ["AWS-F01"]
    assert result.unsupported_claim_count == 0
    assert result.supported_claim_ratio == 1.0


def test_findings_without_evidence_are_dropped() -> None:
    result = validate_findings([finding(evidence=[], confidence=0.9)])
    assert result.findings == []
    assert [f.finding_id for f in result.dropped] == ["AWS-F01"]
    assert "missing_evidence" in {issue.code for issue in result.issues}


def test_invented_resource_ids_are_removed_and_flagged() -> None:
    result = validate_findings(
        [finding(affected_resources=["nat-1", "nat-does-not-exist"])],
        known_resource_ids={"aws": {"nat-1"}},
        comparison=comparison(),
    )
    published = result.findings[0]
    assert published.affected_resources == ["nat-1"]
    assert published.is_uncertain is True
    assert any(issue.code == "unsupported_resource_id" for issue in result.issues)


def test_cost_attribution_is_corrected_to_the_measured_value() -> None:
    result = validate_findings(
        [finding(actual_cost_increase=900.0)],
        known_resource_ids={"aws": {"nat-1"}},
        comparison=comparison(100.0),
    )
    published = result.findings[0]
    assert published.actual_cost_increase == pytest.approx(100.0)
    assert any(issue.code == "cost_attribution_mismatch" for issue in result.issues)


def test_missing_rule_citation_caps_confidence() -> None:
    result = validate_findings([finding(applied_rules=[], confidence=0.9)])
    published = result.findings[0]
    assert published.confidence <= 0.45
    assert published.is_uncertain is True


def test_stale_rule_caps_confidence() -> None:
    result = validate_findings([finding(applied_rules=[citation(is_stale=True)], confidence=0.9)])
    assert result.findings[0].confidence <= 0.6
    assert any(issue.code == "stale_billing_knowledge" for issue in result.issues)


def test_retroactive_rule_application_is_rejected() -> None:
    result = validate_findings(
        [finding(applied_rules=[citation(valid_from=date(2027, 1, 1))])]
    )
    assert result.findings == []
    assert any(issue.code == "rule_applied_retroactively" for issue in result.issues)


def test_rule_without_a_source_is_an_error() -> None:
    result = validate_findings([finding(applied_rules=[citation(source_url="")])])
    assert any(issue.code == "rule_without_source" for issue in result.issues)


def test_high_confidence_requires_corroborating_evidence() -> None:
    result = validate_findings([finding(evidence=[evidence("cost")], confidence=0.9)])
    assert result.findings[0].confidence <= 0.7
    assert any(issue.code == "insufficient_corroboration" for issue in result.issues)


def test_untrusted_text_is_reported_as_info() -> None:
    flagged = evidence("audit", contains_untrusted_text=True)
    result = validate_findings([finding(evidence=[evidence("cost"), evidence("metric"), flagged])])
    assert any(issue.code == "untrusted_text_in_evidence" for issue in result.issues)


def test_ranking_puts_uncertain_findings_last() -> None:
    ranked = rank_findings(
        [
            finding(finding_id="A", actual_cost_increase=10.0),
            finding(finding_id="B", actual_cost_increase=500.0, is_uncertain=True),
            finding(finding_id="C", actual_cost_increase=50.0),
        ]
    )
    assert [f.finding_id for f in ranked] == ["C", "A", "B"]
