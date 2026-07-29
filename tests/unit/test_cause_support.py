"""A cost export answers "what changed". It cannot answer "why".

These cases pin the honest degradation rule: when the data
supplied contains no metric, audit, inventory, or recommendation source at all, no
specific mechanism is publishable, however cleanly a playbook's service or SKU
pattern matched on the cost rows.
"""

from __future__ import annotations

from cloudcause_evidence import (
    CAUSE_SUPPORTING_SOURCES,
    FALLBACK_CATEGORY,
    UNSUPPORTED_CAUSE_MAX_CONFIDENCE,
    missing_cause_sources,
    validate_findings,
)
from test_evidence_validation import comparison, evidence, finding

COST_ONLY = {"cost", "usage"}
KNOWN = {"aws": {"nat-1"}}


def test_missing_cause_sources_is_all_or_nothing() -> None:
    assert missing_cause_sources(COST_ONLY) == list(CAUSE_SUPPORTING_SOURCES)
    assert missing_cause_sources({"cost", "inventory"}) == []
    assert missing_cause_sources({"metric"}) == []
    assert missing_cause_sources(set()) == list(CAUSE_SUPPORTING_SOURCES)


def test_a_named_mechanism_is_rewritten_when_the_data_cannot_support_one() -> None:
    cost_only_finding = finding(
        evidence=[evidence("cost"), evidence("usage")], confidence=0.7
    )
    result = validate_findings(
        [cost_only_finding],
        known_resource_ids=KNOWN,
        comparison=comparison(),
        available_source_types={"aws": COST_ONLY},
    )

    assert len(result.findings) == 1, "the measured cost change is still published"
    published = result.findings[0]
    assert published.category == FALLBACK_CATEGORY
    assert published.confidence <= UNSUPPORTED_CAUSE_MAX_CONFIDENCE
    assert published.is_uncertain is True
    assert "cannot explain" in published.suspected_root_cause
    assert "nat-1" in published.suspected_root_cause
    assert any("mechanism is unconfirmed" in warning for warning in published.warnings)

    codes = [issue.code for issue in result.issues]
    assert "cause_unsupported_by_available_sources" in codes
    assert result.missing_source_types["aws"] == list(CAUSE_SUPPORTING_SOURCES)


def test_the_rewrite_names_the_data_that_would_raise_it() -> None:
    result = validate_findings(
        [finding(evidence=[evidence("cost")])],
        known_resource_ids=KNOWN,
        comparison=comparison(),
        available_source_types={"aws": COST_ONLY},
    )
    warning = " ".join(result.findings[0].warnings)
    for source_type in CAUSE_SUPPORTING_SOURCES:
        assert source_type in warning


def test_one_corroborating_source_is_enough_to_keep_the_mechanism() -> None:
    result = validate_findings(
        [finding()],
        known_resource_ids=KNOWN,
        comparison=comparison(),
        available_source_types={"aws": {"cost", "usage", "metric", "audit"}},
    )
    published = result.findings[0]
    assert published.category == "nat_gateway_misroute"
    assert published.confidence > UNSUPPORTED_CAUSE_MAX_CONFIDENCE
    assert result.missing_source_types == {}
    assert "cause_unsupported_by_available_sources" not in [i.code for i in result.issues]


def test_inventory_alone_counts_as_cause_supporting() -> None:
    """The seeded scenarios all ship inventory, which is why they are unaffected."""

    result = validate_findings(
        [finding()],
        known_resource_ids=KNOWN,
        comparison=comparison(),
        available_source_types={"aws": {"cost", "usage", "inventory"}},
    )
    assert result.findings[0].category == "nat_gateway_misroute"
    assert result.missing_source_types == {}


def test_omitting_available_source_types_changes_nothing() -> None:
    """Callers that do not know are not punished for it: the rule simply does not run."""

    without = validate_findings(
        [finding()], known_resource_ids=KNOWN, comparison=comparison()
    )
    assert without.findings[0].category == "nat_gateway_misroute"
    assert without.missing_source_types == {}


def test_an_already_uncertain_finding_is_capped_but_not_relabelled() -> None:
    fallback = finding(
        category=FALLBACK_CATEGORY,
        suspected_root_cause="Material cost increase on nat-1 that no pattern explains",
        evidence=[evidence("cost")],
        confidence=0.4,
        is_uncertain=True,
    )
    result = validate_findings(
        [fallback],
        known_resource_ids=KNOWN,
        comparison=comparison(),
        available_source_types={"aws": COST_ONLY},
    )
    published = result.findings[0]
    assert published.category == FALLBACK_CATEGORY
    assert published.confidence <= UNSUPPORTED_CAUSE_MAX_CONFIDENCE
    assert "cause_unsupported_by_available_sources" not in [i.code for i in result.issues], (
        "there is no mechanism to withdraw, so there is nothing to report"
    )


def test_a_provider_with_full_data_is_untouched_when_another_lacks_it() -> None:
    aws = finding()
    azure = finding(
        finding_id="AZ-F01",
        provider="azure",
        category="functions_retry_loop",
        affected_resources=[],
        candidate_id=None,
        evidence=[evidence("cost", provider="azure", evidence_id="AZ-E1")],
    )
    result = validate_findings(
        [aws, azure],
        known_resource_ids={"aws": {"nat-1"}},
        comparison=comparison(),
        available_source_types={
            "aws": {"cost", "usage", "metric", "audit"},
            "azure": {"cost", "usage"},
        },
    )
    published = {item.provider: item for item in result.findings}
    assert published["aws"].category == "nat_gateway_misroute"
    assert published["azure"].category == FALLBACK_CATEGORY
    assert set(result.missing_source_types) == {"azure"}
