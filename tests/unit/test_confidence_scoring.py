"""Confidence must be derived from the evidence, not asserted.

A score that is the same on every finding is a constant with a decimal point on
it. These tests pin the properties that make it a measurement: it moves with the
evidence gathered, it stays clear of the playbook ceiling so the ceiling is not
what is being reported, and the four demo findings do not all land on one value.
"""

from __future__ import annotations

from datetime import date

import pytest
from cloudcause_contracts import AnomalyCandidate, Evidence, InvestigationRequest, Settings
from cloudcause_orchestrator import Orchestrator
from cloudcause_worker_core.playbooks import (
    CONFIDENCE_FLOOR,
    EVIDENCE_WEIGHTS,
    MAX_CORROBORATION,
    MAX_RATE_COHERENCE,
    MAX_SEPARATION,
    PlaybookSpec,
    _score_confidence,
)

SPEC = PlaybookSpec(category="test_pattern", root_cause="{key}", recommendation="review it")


def evidence(source_type: str, index: int = 0, *, untrusted: bool = False) -> Evidence:
    return Evidence(
        evidence_id=f"AWS-E{index:03d}",
        provider="aws",
        source_type=source_type,
        source_id=f"{source_type}-{index}",
        observed_at="2026-07-19T12:00:00+00:00",
        statement="observation",
        contains_untrusted_text=untrusted,
    )


def candidate(**overrides: object) -> AnomalyCandidate:
    base: dict[str, object] = {
        "candidate_id": "aws-cand-00",
        "provider": "aws",
        "dimension": "resource",
        "key": "i-0a1b2c3d4e5f67890",
        "baseline_cost": 100.0,
        "current_cost": 200.0,
        "expected_baseline_cost": 100.0,
        "absolute_change": 100.0,
        "percent_change": 100.0,
        "baseline_quantity": 100.0,
        "current_quantity": 200.0,
        "unit_cost_baseline": 1.0,
        "unit_cost_current": 1.0,
        "first_spike_date": date(2026, 7, 15),
    }
    base.update(overrides)
    return AnomalyCandidate.model_validate(base)


def test_the_derived_score_cannot_reach_the_default_ceiling() -> None:
    """The default cap must be a safety limit, never the value being displayed.

    Stated against the *default* only. The lower per-playbook caps are meant to
    bind, which is what makes ADR 0006 enforceable; see the fallback test below.
    """

    best_case = CONFIDENCE_FLOOR + sum(EVIDENCE_WEIGHTS.values())
    best_case += MAX_CORROBORATION + MAX_SEPARATION + MAX_RATE_COHERENCE
    assert best_case < SPEC.max_confidence


def test_every_evidence_kind_moves_the_score() -> None:
    baseline = _score_confidence(SPEC, [evidence("cost")], candidate())
    for index, kind in enumerate(EVIDENCE_WEIGHTS, start=1):
        with_kind = _score_confidence(SPEC, [evidence("cost"), evidence(kind, index)], candidate())
        assert with_kind > baseline, kind


def test_a_second_item_of_the_same_kind_corroborates_the_first() -> None:
    one = _score_confidence(SPEC, [evidence("cost"), evidence("audit", 1)], candidate())
    two = _score_confidence(
        SPEC, [evidence("cost"), evidence("audit", 1), evidence("audit", 2)], candidate()
    )
    assert two > one


def test_a_change_that_barely_clears_the_baseline_scores_lower_than_one_that_quadruples() -> None:
    items = [evidence("cost"), evidence("usage", 1), evidence("metric", 2)]
    small = _score_confidence(SPEC, items, candidate(percent_change=25.0))
    large = _score_confidence(SPEC, items, candidate(percent_change=400.0))
    assert large - small == pytest.approx(MAX_SEPARATION * (1.0 - 25.0 / 300.0), abs=1e-6)


def test_a_drifting_unit_cost_weakens_a_usage_explanation() -> None:
    items = [evidence("cost"), evidence("usage", 1), evidence("metric", 2)]
    steady = _score_confidence(SPEC, items, candidate(unit_cost_current=1.0))
    drifted = _score_confidence(SPEC, items, candidate(unit_cost_current=1.5))
    assert steady > drifted


def test_a_rate_change_playbook_is_rewarded_for_drift_not_penalised() -> None:
    """The rate axis is directional, because the two playbook families disagree.

    `pricing_change` is selected precisely when cost moved and usage did not, so
    scoring drift as a penalty marked every such finding down for showing the
    signature its own playbook matched on.
    """

    rate_spec = PlaybookSpec(
        category="pricing_change",
        root_cause="{key}",
        recommendation="check the rate",
        requires_rate_change=True,
    )
    items = [evidence("cost"), evidence("usage", 1), evidence("metric", 2)]
    steady = _score_confidence(rate_spec, items, candidate(unit_cost_current=1.0))
    drifted = _score_confidence(rate_spec, items, candidate(unit_cost_current=1.5))

    assert drifted > steady, "drift is the mechanism a rate-change playbook claims"
    assert drifted - steady == pytest.approx(MAX_RATE_COHERENCE, abs=1e-6)

    # And the two families must disagree on the same candidate.
    growth = _score_confidence(SPEC, items, candidate(unit_cost_current=1.5))
    assert drifted > growth


def test_incomplete_data_and_untrusted_text_still_subtract() -> None:
    items = [evidence("cost"), evidence("usage", 1), evidence("audit", 2)]
    clean = _score_confidence(SPEC, items, candidate())
    stale = _score_confidence(SPEC, [*items, evidence("freshness", 3)], candidate())
    tainted = _score_confidence(
        SPEC, [evidence("cost"), evidence("usage", 1), evidence("audit", 2, untrusted=True)],
        candidate(),
    )
    assert stale < clean
    assert tainted < clean


FALLBACK = PlaybookSpec(
    category="unexplained_increase",
    root_cause="{key}",
    recommendation="review it",
    is_fallback=True,
    max_confidence=0.4,
)


def test_the_fallback_ceiling_binds_when_the_derived_score_would_exceed_it() -> None:
    """The lower per-playbook caps are meant to bind; prove this one does.

    Asserting only ``<= 0.4`` passes vacuously on evidence that never scores that
    high. This feeds a full evidence set and a fully separated candidate, so the
    uncapped score is well above the ceiling and the assertion is about the cap.
    """

    items = [
        evidence("cost"),
        evidence("usage", 1),
        evidence("inventory", 2),
        evidence("metric", 3),
        evidence("audit", 4),
        evidence("recommendation", 5),
    ]
    strong = candidate(percent_change=400.0)

    assert _score_confidence(SPEC, items, strong) > 0.4, "the uncapped score must clear the ceiling"
    assert _score_confidence(FALLBACK, items, strong) == pytest.approx(FALLBACK.max_confidence)


def test_a_cost_only_finding_stays_under_the_unsupported_cause_ceiling() -> None:
    score = _score_confidence(FALLBACK, [evidence("cost"), evidence("usage", 1)], candidate())
    assert score <= 0.4


async def test_the_demo_findings_do_not_all_report_one_confidence(
    multi_cloud_request: InvestigationRequest, settings: Settings
) -> None:
    report = await Orchestrator(settings).run(multi_cloud_request)
    scores = [finding.confidence for finding in report.findings]
    assert len(scores) >= 4
    assert len(set(scores)) == len(scores), scores
    assert max(scores) < PlaybookSpec.max_confidence
