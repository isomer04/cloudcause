"""Findings are claims until evidence supports them.

The orchestrator runs this before a report is published. Unsupported claims are
dropped or downgraded here, never in the model's own words.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from cloudcause_contracts import (
    AnomalyCandidate,
    Finding,
    PeriodComparison,
    Provider,
    ValidationIssue,
)

#: Tolerance on a finding's cost attribution versus the deterministic candidate.
ATTRIBUTION_TOLERANCE = 0.01
HIGH_CONFIDENCE = 0.8
HIGH_CONFIDENCE_MIN_EVIDENCE = 3
CORROBORATING_SOURCES = ("metric", "audit", "recommendation")

#: A cost export answers "what changed". Naming *why* needs at least one of
#: these. When a dataset contains none of them, no specific mechanism is
#: publishable, however well a playbook's service or SKU pattern matched.
CAUSE_SUPPORTING_SOURCES = ("metric", "audit", "inventory", "recommendation")

#: The honest shape a finding degrades to: measured cost change, confirmed
#: period, cited rule, uncertain. It already exists as ``FALLBACK_PLAYBOOK``.
FALLBACK_CATEGORY = "unexplained_increase"
UNSUPPORTED_CAUSE_MAX_CONFIDENCE = 0.4
UNSUPPORTED_CAUSE_RECOMMENDATION = (
    "Review this resource or service manually, or supply metrics, audit events, inventory, or "
    "provider recommendations for the same period so the mechanism can be confirmed. CloudCause "
    "can measure the cost change from a cost export alone, but not its cause."
)


def missing_cause_sources(available: Iterable[str]) -> list[str]:
    """Which cause-supporting source types are absent from a provider's data.

    An empty list means at least one is present, so a mechanism may be named.
    """

    present = set(available)
    if present.intersection(CAUSE_SUPPORTING_SOURCES):
        return []
    return list(CAUSE_SUPPORTING_SOURCES)


@dataclass
class ValidationResult:
    findings: list[Finding] = field(default_factory=list)
    dropped: list[Finding] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    #: Providers whose data could not support any mechanism, and what was absent.
    missing_source_types: dict[Provider, list[str]] = field(default_factory=dict)

    @property
    def supported_claim_ratio(self) -> float:
        """Share of published findings that carry evidence and a versioned rule."""

        if not self.findings:
            return 1.0
        supported = sum(
            1 for finding in self.findings if finding.evidence and finding.applied_rules
        )
        return round(supported / len(self.findings), 4)

    @property
    def unsupported_claim_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    def issues_for(self, finding_id: str) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.finding_id == finding_id]


def _candidate_index(comparison: PeriodComparison | None) -> dict[str, AnomalyCandidate]:
    if comparison is None:
        return {}
    return {candidate.candidate_id: candidate for candidate in comparison.all_candidates()}


def validate_findings(
    findings: Sequence[Finding],
    *,
    known_resource_ids: Mapping[Provider, set[str]] | None = None,
    comparison: PeriodComparison | None = None,
    available_source_types: Mapping[Provider, set[str]] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    candidates = _candidate_index(comparison)
    known_resource_ids = known_resource_ids or {}
    seen_resources: dict[str, str] = {}

    for finding in findings:
        working = finding.model_copy(deep=True)
        issues: list[ValidationIssue] = []
        drop = False

        def add(
            code: str,
            severity: str,
            detail: str,
            *,
            issues: list[ValidationIssue] = issues,
            finding_id: str = working.finding_id,
            provider: str = working.provider,
        ) -> None:
            issues.append(
                ValidationIssue(
                    code=code,
                    severity=severity,  # type: ignore[arg-type]
                    detail=detail,
                    finding_id=finding_id,
                    provider=provider,
                )
            )

        # 1. Evidence must exist at all.
        if not working.evidence:
            add("missing_evidence", "error", "finding carries no evidence and cannot be published")
            drop = True
        else:
            # 2. Every piece of evidence must be traceable.
            untraceable = [item.evidence_id for item in working.evidence if not item.query_reference]
            if untraceable:
                add(
                    "evidence_without_provenance",
                    "warning",
                    f"evidence without a query reference: {', '.join(untraceable)}",
                )

        # 3. Resource ids must exist in inventory or cost data.
        allowed = known_resource_ids.get(working.provider)
        if allowed:
            unsupported = [
                value
                for value in working.affected_resources
                if value not in allowed and not value.startswith(f"{working.provider}:")
            ]
            if unsupported:
                add(
                    "unsupported_resource_id",
                    "error",
                    f"resource ids not present in provider data: {', '.join(unsupported)}",
                )
                working.affected_resources = [
                    value for value in working.affected_resources if value not in unsupported
                ]
                working.is_uncertain = True
                working.confidence = min(working.confidence, 0.4)

        # 4. Cost attribution must match the deterministic layer.
        candidate = candidates.get(working.candidate_id or "")
        if candidate is not None:
            expected = candidate.absolute_change
            drift = abs(working.actual_cost_increase - expected)
            if drift > max(ATTRIBUTION_TOLERANCE * max(abs(expected), 1.0), 0.02):
                add(
                    "cost_attribution_mismatch",
                    "error",
                    (
                        f"claimed increase {working.actual_cost_increase:,.2f} does not match the "
                        f"measured {expected:,.2f}; the measured value was published instead"
                    ),
                )
                working.actual_cost_increase = round(expected, 2)
                working.is_uncertain = True
        elif working.candidate_id:
            add(
                "unknown_candidate",
                "warning",
                f"candidate {working.candidate_id} is not in the deterministic comparison",
            )

        # 5. Every billing interpretation must cite a versioned rule.
        if not working.applied_rules:
            add(
                "missing_rule_citation",
                "warning",
                "no versioned billing rule was cited; interpretation marked uncertain",
            )
            working.is_uncertain = True
            working.confidence = min(working.confidence, 0.45)
        else:
            for rule in working.applied_rules:
                if rule.is_stale:
                    add(
                        "stale_billing_knowledge",
                        "warning",
                        f"rule {rule.rule_id} was last reviewed {rule.reviewed_at}",
                    )
                    working.is_uncertain = True
                    working.confidence = min(working.confidence, 0.6)
                if rule.valid_from is None:
                    add(
                        "rule_missing_effective_date",
                        "warning",
                        f"rule {rule.rule_id} has no effective date; confidence capped",
                    )
                    working.is_uncertain = True
                    working.confidence = min(working.confidence, 0.6)
                elif (
                    rule.selected_for_date is not None
                    and rule.selected_for_date < rule.valid_from
                ):
                    add(
                        "rule_applied_retroactively",
                        "error",
                        (
                            f"rule {rule.rule_id} starts {rule.valid_from} but was selected for "
                            f"{rule.selected_for_date}"
                        ),
                    )
                    drop = True
                if not rule.source_url:
                    add(
                        "rule_without_source",
                        "error",
                        f"rule {rule.rule_id} has no official source url",
                    )

        # 6. High confidence needs corroboration, not just a cost row.
        kinds = {item.source_type for item in working.evidence}
        if working.confidence >= HIGH_CONFIDENCE and (
            len(working.evidence) < HIGH_CONFIDENCE_MIN_EVIDENCE
            or not kinds.intersection(CORROBORATING_SOURCES)
        ):
            add(
                "insufficient_corroboration",
                "warning",
                "high confidence requires metric, audit, or recommendation evidence; confidence lowered",
            )
            working.confidence = min(working.confidence, 0.7)

        # 7. Untrusted provider text must be visible as such.
        if any(item.contains_untrusted_text for item in working.evidence):
            add(
                "untrusted_text_in_evidence",
                "info",
                "evidence quotes provider-controlled text; it was scrubbed and is treated as data",
            )

        # 8. Two findings must not claim the same resource twice.
        for resource_id in working.affected_resources:
            previous = seen_resources.get(resource_id)
            if previous and previous != working.finding_id:
                add(
                    "duplicate_resource_attribution",
                    "info",
                    f"resource {resource_id} is also attributed to {previous}",
                )
            seen_resources.setdefault(resource_id, working.finding_id)

        # 9. A cause needs data that can show a cause. Cost rows measure the
        #    change; they cannot confirm a mechanism. When the provider supplied
        #    no metric, audit, inventory, or recommendation source at all, the
        #    specific mechanism is rewritten to the honest fallback shape rather
        #    than published at a confidence the data does not earn.
        if available_source_types is not None:
            available = available_source_types.get(working.provider)
            if available is not None:
                missing = missing_cause_sources(available)
                if missing:
                    result.missing_source_types[working.provider] = missing
                    target = (
                        working.affected_resources[0]
                        if working.affected_resources
                        else (working.service_name or working.category)
                    )
                    if working.category != FALLBACK_CATEGORY:
                        add(
                            "cause_unsupported_by_available_sources",
                            "warning",
                            (
                                f"category {working.category!r} names a mechanism, but no "
                                f"{', '.join(missing)} data was supplied for {working.provider}; "
                                "the finding was rewritten as an unexplained increase"
                            ),
                        )
                        working.category = FALLBACK_CATEGORY
                        working.suspected_root_cause = (
                            f"Material cost increase on {target} that the supplied data cannot "
                            "explain; the cost movement is confirmed but the mechanism is not"
                        )
                        working.recommendation = UNSUPPORTED_CAUSE_RECOMMENDATION
                    working.is_uncertain = True
                    working.confidence = min(
                        working.confidence, UNSUPPORTED_CAUSE_MAX_CONFIDENCE
                    )
                    working.warnings.append(
                        f"No {', '.join(missing)} data was supplied for {target}, so the mechanism "
                        "is unconfirmed. Uploading any of those for the same period would raise "
                        "this above an unexplained increase."
                    )

        result.issues.extend(issues)
        if drop:
            result.dropped.append(working)
        else:
            result.findings.append(working)

    return result


def rank_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Rank by cost impact, then by confidence, with uncertain findings last."""

    return sorted(
        findings,
        key=lambda finding: (
            finding.is_uncertain,
            -finding.actual_cost_increase,
            -finding.confidence,
        ),
    )
