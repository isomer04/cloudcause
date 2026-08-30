"""Deterministic investigation playbooks.

A playbook is a declarative description of one waste pattern: which candidates it
explains, which evidence must be gathered, which billing rule interprets it, and
what a human should consider doing. The engine below does the matching, evidence
assembly, rule citation, and confidence scoring, so each provider service only
declares its own patterns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from cloudcause_contracts import AnomalyCandidate, Evidence, Finding, MetricSeries, Risk, RuleType

from .context import InvestigationContext

DAYS_PER_MONTH = 30.4
MAX_METRIC_EVIDENCE = 3
MAX_AUDIT_EVIDENCE = 3
MAX_RECOMMENDATION_EVIDENCE = 2
QUANTITY_GROWTH_THRESHOLD = 10.0

# Confidence scoring. Weights are per evidence *kind*, not per item: audit data
# is the only source that can name an actor and a moment, so it carries the most;
# a provider recommendation is a third-party opinion, so it carries the least.
# The four axes below sum to at most 0.865, which keeps the derived score clear
# of `PlaybookSpec.max_confidence` - a ceiling that binds on every finding is a
# ceiling being reported, not a score being measured.
CONFIDENCE_FLOOR = 0.16
EVIDENCE_WEIGHTS: dict[str, float] = {
    "usage": 0.07,
    "inventory": 0.05,
    "metric": 0.12,
    "audit": 0.18,
    "recommendation": 0.05,
}
CORROBORATION_STEP = 0.025
MAX_CORROBORATION = 0.075
MAX_SEPARATION = 0.10
MAX_RATE_COHERENCE = 0.06
SEPARATION_FULL_PERCENT = 300.0
RATE_DRIFT_FULL = 0.25

_OWNER_TAG_KEYS = ("owner", "Owner", "team", "Team", "cost-center", "costCenter")


@dataclass(frozen=True)
class PlaybookSpec:
    """One declarative waste pattern."""

    category: str
    root_cause: str
    recommendation: str
    risk: Risk = "low"
    rule_type: RuleType = "cost_driver"
    service_patterns: tuple[str, ...] = ()
    sku_patterns: tuple[str, ...] = ()
    resource_type_patterns: tuple[str, ...] = ()
    metric_names: tuple[str, ...] = ()
    audit_event_patterns: tuple[str, ...] = ()
    low_utilization_metrics: tuple[str, ...] = ()
    low_utilization_threshold: float = 5.0
    requires_new_spend: bool = False
    requires_quantity_growth: bool = True
    requires_rate_change: bool = False
    requires_missing_owner: bool = False
    priority: int = 50
    is_fallback: bool = False
    max_confidence: float = 0.92
    checks: tuple[str, ...] = field(default=())


FALLBACK_PLAYBOOK = PlaybookSpec(
    category="unexplained_increase",
    root_cause=(
        "Material cost increase on {key} that no known waste pattern explains; "
        "the cost movement is confirmed but the mechanism is not"
    ),
    recommendation=(
        "Review this resource or service manually. CloudCause can confirm the cost change and "
        "the surrounding activity, but not the root cause."
    ),
    risk="low",
    requires_quantity_growth=False,
    priority=99,
    is_fallback=True,
    max_confidence=0.4,
)


def _matches_any(value: str | None, patterns: Sequence[str]) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _has_owner(candidate: AnomalyCandidate, ctx: InvestigationContext) -> bool:
    tags = dict(candidate.tags)
    if candidate.resource_id:
        resource = ctx.bundle.resource(candidate.resource_id)
        if resource:
            tags.update(resource.tags)
    return any(tags.get(key) for key in _OWNER_TAG_KEYS)


def _series_for(candidate: AnomalyCandidate, ctx: InvestigationContext) -> list[MetricSeries]:
    if not candidate.resource_id:
        return []
    return ctx.bundle.metrics_for(candidate.resource_id)


def _low_utilization(spec: PlaybookSpec, candidate: AnomalyCandidate, ctx: InvestigationContext) -> bool:
    current = ctx.current_period
    for series in _series_for(candidate, ctx):
        if not _matches_any(series.metric_name, spec.low_utilization_metrics):
            continue
        if series.window_average(current.start, current.end) <= spec.low_utilization_threshold:
            return True
    return False


def match_score(spec: PlaybookSpec, candidate: AnomalyCandidate, ctx: InvestigationContext) -> int | None:
    """Return a specificity score, or ``None`` when the playbook does not apply."""

    if spec.is_fallback:
        return 0
    score = 0

    if spec.service_patterns:
        if _matches_any(candidate.service_name, spec.service_patterns) or _matches_any(
            candidate.service_category, spec.service_patterns
        ):
            score += 3
        else:
            return None

    if spec.sku_patterns:
        if any(_matches_any(sku, spec.sku_patterns) for sku in candidate.sku_ids):
            score += 3
        else:
            return None

    if spec.resource_type_patterns:
        resource = ctx.bundle.resource(candidate.resource_id) if candidate.resource_id else None
        if resource and _matches_any(resource.resource_type, spec.resource_type_patterns):
            score += 2
        else:
            return None

    if spec.requires_new_spend:
        if not candidate.is_new:
            return None
        score += 1

    if spec.requires_rate_change:
        if candidate.is_quantity_driven:
            return None
        score += 3
    elif spec.requires_quantity_growth and not candidate.is_quantity_driven:
        return None

    if spec.requires_missing_owner:
        if _has_owner(candidate, ctx):
            return None
        score += 2

    if spec.low_utilization_metrics:
        if not _low_utilization(spec, candidate, ctx):
            return None
        score += 2

    if spec.metric_names and any(
        _matches_any(series.metric_name, spec.metric_names) for series in _series_for(candidate, ctx)
    ):
        score += 1

    if spec.audit_event_patterns and _matching_events(spec, candidate, ctx):
        score += 2

    return score


def select_playbook(
    candidate: AnomalyCandidate, specs: Sequence[PlaybookSpec], ctx: InvestigationContext
) -> PlaybookSpec:
    best: tuple[int, int, PlaybookSpec] | None = None
    for spec in specs:
        score = match_score(spec, candidate, ctx)
        if score is None:
            continue
        ranked = (score, -spec.priority, spec)
        if best is None or (ranked[0], ranked[1]) > (best[0], best[1]):
            best = ranked
    return best[2] if best else FALLBACK_PLAYBOOK


def _matching_events(spec: PlaybookSpec, candidate: AnomalyCandidate, ctx: InvestigationContext):
    events = []
    for event in ctx.bundle.audit_events.items:
        in_scope = bool(candidate.resource_id) and candidate.resource_id in event.resource_ids
        if not in_scope and candidate.resource_id:
            continue
        if spec.audit_event_patterns and not _matches_any(event.event_name, spec.audit_event_patterns):
            continue
        if not ctx.current_period.contains(event.event_time.date()):
            continue
        events.append(event)
    return sorted(events, key=lambda event: event.event_time)


def gather_evidence(
    candidate: AnomalyCandidate, spec: PlaybookSpec, ctx: InvestigationContext
) -> list[Evidence]:
    """Assemble every piece of evidence available for one candidate."""

    factory = ctx.evidence
    evidence: list[Evidence] = [factory.cost_change(candidate)]

    usage = factory.usage_change(candidate)
    if usage is not None:
        evidence.append(usage)

    if candidate.resource_id:
        resource = ctx.bundle.resource(candidate.resource_id)
        if resource is not None:
            evidence.append(factory.inventory(resource))

    wanted_metrics = [
        series
        for series in _series_for(candidate, ctx)
        if not spec.metric_names or _matches_any(series.metric_name, spec.metric_names)
    ] or _series_for(candidate, ctx)
    for series in wanted_metrics[:MAX_METRIC_EVIDENCE]:
        evidence.append(factory.metric(series, ctx.current_period, ctx.baseline_period))

    for event in _matching_events(spec, candidate, ctx)[:MAX_AUDIT_EVIDENCE]:
        evidence.append(factory.audit(event))

    if candidate.resource_id:
        for recommendation in ctx.bundle.recommendations_for(candidate.resource_id)[
            :MAX_RECOMMENDATION_EVIDENCE
        ]:
            evidence.append(factory.recommendation(recommendation))

    if ctx.data_is_incomplete():
        evidence.append(
            factory.freshness(
                ctx.period_end_datetime().isoformat(),
                ctx.knowledge.data_delay_hours(ctx.provider, ctx.current_period.end),
            )
        )
    return evidence


def _separation(candidate: AnomalyCandidate) -> float:
    """How far the change stands clear of the baseline, 0.0 to 1.0.

    A resource that quadrupled is easier to attribute than one that moved 25%,
    because ordinary week-to-week noise cannot account for it. New spend has no
    baseline to clear, so it separates completely.
    """

    if candidate.percent_change is None:
        return 1.0
    return max(0.0, min(candidate.percent_change / SEPARATION_FULL_PERCENT, 1.0))


def _rate_coherence(spec: PlaybookSpec, candidate: AnomalyCandidate) -> float:
    """Whether the rate behaviour agrees with the mechanism this playbook claims.

    The axis is directional, because the two families of playbook want opposite
    evidence. A quantity-growth playbook says the workload grew, so a steady
    effective unit cost corroborates it and drift means a rate, discount, or
    commitment change is mixed in that the named mechanism does not explain. A
    ``requires_rate_change`` playbook says the opposite - that the rate moved
    while usage held - so for it drift *is* the mechanism and a steady rate would
    contradict the claim.

    Scoring drift as a penalty in both directions marked every `pricing_change`
    finding down for showing exactly the signature its own playbook selected on.
    """

    baseline = candidate.unit_cost_baseline
    current = candidate.unit_cost_current
    if not baseline or current is None:
        # No comparable rate (new spend, or usage not reported): neither
        # corroborated nor contradicted.
        return 0.5
    drift = min(abs(current - baseline) / baseline / RATE_DRIFT_FULL, 1.0)
    return drift if spec.requires_rate_change else 1.0 - drift


def _score_confidence(
    spec: PlaybookSpec, evidence: Sequence[Evidence], candidate: AnomalyCandidate
) -> float:
    """Derive confidence from the evidence actually gathered for this candidate.

    Four independent axes, so two findings only score alike when they really are
    supported alike: which kinds of evidence exist, how many items corroborate
    each kind, how far the change stands clear of the baseline, and whether the
    effective unit cost behaved the way this playbook's mechanism requires. Cost
    evidence is deliberately unweighted -
    it proves the money moved, never why - so it sets the floor instead.
    """

    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.source_type] = counts.get(item.source_type, 0) + 1

    confidence = CONFIDENCE_FLOOR
    corroboration = 0.0
    for kind, weight in EVIDENCE_WEIGHTS.items():
        count = counts.get(kind, 0)
        if count == 0:
            continue
        confidence += weight
        corroboration += CORROBORATION_STEP * (count - 1)
    confidence += min(corroboration, MAX_CORROBORATION)
    confidence += MAX_SEPARATION * _separation(candidate)
    confidence += MAX_RATE_COHERENCE * _rate_coherence(spec, candidate)

    if "freshness" in counts:
        confidence -= 0.10
    if any(item.contains_untrusted_text for item in evidence):
        confidence -= 0.05
    return max(0.05, min(confidence, spec.max_confidence))


def _format(template: str, candidate: AnomalyCandidate, ctx: InvestigationContext) -> str:
    spike = candidate.first_spike_date.isoformat() if candidate.first_spike_date else "the period start"
    return template.format(
        key=candidate.key,
        resource=candidate.resource_name or candidate.resource_id or candidate.key,
        resource_id=candidate.resource_id or candidate.key,
        service=candidate.service_name or "the service",
        region=candidate.region_id or "an unknown region",
        spike_date=spike,
        increase=f"{candidate.absolute_change:,.2f} {candidate.currency}",
        provider=ctx.provider.upper(),
    )


def build_finding(
    candidate: AnomalyCandidate,
    spec: PlaybookSpec,
    ctx: InvestigationContext,
    *,
    index: int,
    evidence: Sequence[Evidence] | None = None,
    agent_mode: Literal["stub", "live"] = "stub",
    root_cause: str | None = None,
    recommendation: str | None = None,
    confidence_override: float | None = None,
) -> Finding:
    """Assemble a finding. Cost numbers always come from the deterministic layer."""

    evidence = list(evidence if evidence is not None else gather_evidence(candidate, spec, ctx))
    warnings: list[str] = []

    rule_result = ctx.knowledge.get_billing_rule(
        ctx.provider,
        service=candidate.service_name,
        category=spec.category,
        usage_date=ctx.rule_date(candidate),
        rule_type=spec.rule_type,
    )
    citations = []
    if rule_result.citation is not None:
        citations.append(rule_result.citation)
        ctx.add_citation(rule_result.citation)
    warnings.extend(rule_result.warnings)

    confidence = (
        confidence_override
        if confidence_override is not None
        else _score_confidence(spec, evidence, candidate)
    )
    uncertain = spec.is_fallback

    if not rule_result.found:
        confidence = min(confidence, 0.45)
        uncertain = True
        warnings.append(
            "No versioned billing rule was effective for this usage date, so the interpretation "
            "stays provisional."
        )
    elif rule_result.is_stale:
        confidence = min(confidence, 0.6)
        uncertain = True
    elif any("no effective date" in warning for warning in rule_result.warnings):
        confidence = min(confidence, 0.6)
        uncertain = True

    if not candidate.is_quantity_driven and spec.category != "pricing_change":
        warnings.append(
            "Usage quantity did not grow with cost, so a rate or discount change may explain part "
            "of this increase."
        )
        confidence = min(confidence, 0.7)

    if ctx.data_is_incomplete():
        uncertain = True
        warnings.append("Provider data for the end of the period is incomplete.")

    daily_delta = candidate.absolute_change / max(ctx.current_period.days, 1)
    return Finding(
        finding_id=f"{ctx.provider.upper()}-F{index:02d}",
        provider=ctx.provider,
        category=spec.category,
        suspected_root_cause=root_cause or _format(spec.root_cause, candidate, ctx),
        # Service-level candidates carry no resource id; claiming one would be a fabrication.
        affected_resources=[candidate.resource_id] if candidate.resource_id else [],
        evidence=evidence,
        confidence=round(min(max(confidence, 0.0), 1.0), 3),
        actual_cost_increase=round(candidate.absolute_change, 2),
        estimated_monthly_impact=round(daily_delta * DAYS_PER_MONTH, 2),
        recommendation=recommendation or _format(spec.recommendation, candidate, ctx),
        risk=spec.risk,
        requires_human_approval=True,
        candidate_id=candidate.candidate_id,
        service_name=candidate.service_name,
        region_id=candidate.region_id,
        applied_rules=citations,
        is_uncertain=uncertain,
        warnings=warnings,
        agent_mode=agent_mode,
    )


def run_playbooks(
    ctx: InvestigationContext, specs: Sequence[PlaybookSpec], max_findings: int | None = None
) -> list[Finding]:
    """Deterministic investigation of every candidate assigned to this provider."""

    limit = max_findings or ctx.task.max_findings
    wanted = set(ctx.task.candidate_ids)
    findings: list[Finding] = []
    for candidate in ctx.candidates:
        if wanted and candidate.candidate_id not in wanted:
            continue
        spec = select_playbook(candidate, specs, ctx)
        findings.append(build_finding(candidate, spec, ctx, index=len(findings) + 1))
        if len(findings) >= limit:
            break
    return findings
