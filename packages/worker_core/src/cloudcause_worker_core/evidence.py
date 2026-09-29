"""Evidence construction.

Every number in an ``Evidence`` object comes from provider data or deterministic
analytics. Statements are templated, and any untrusted provider text is scrubbed
and flagged first.
"""

from __future__ import annotations

from cloudcause_contracts import (
    AnomalyCandidate,
    AuditEvent,
    CloudResource,
    DateRange,
    Evidence,
    MetricSeries,
    Provenance,
    Provider,
    ProviderDataBundle,
    Recommendation,
)

from .sanitize import scrub, scrub_tags


class EvidenceFactory:
    """Builds evidence with stable, citable ids for one provider run."""

    def __init__(self, provider: Provider, bundle: ProviderDataBundle) -> None:
        self.provider = provider
        self.bundle = bundle
        self._counter = 0
        self.suspicious_text_seen = False

    def _next_id(self) -> str:
        self._counter += 1
        return f"{self.provider.upper()}-E{self._counter:03d}"

    def _build(
        self,
        *,
        provenance: Provenance,
        source_type: str,
        source_id: str,
        statement: str,
        numeric_value: float | None = None,
        numeric_unit: str | None = None,
        untrusted: bool = False,
    ) -> Evidence:
        return Evidence(
            evidence_id=self._next_id(),
            provider=self.provider,
            source_type=source_type,
            source_id=source_id,
            observed_at=provenance.observed_at,
            statement=statement,
            numeric_value=numeric_value,
            numeric_unit=numeric_unit,
            query_reference=provenance.reference(),
            data_through=provenance.data_through,
            origin=provenance.origin,
            contains_untrusted_text=untrusted,
        )

    def cost_change(self, candidate: AnomalyCandidate) -> Evidence:
        percent = (
            "new spend in this period"
            if candidate.percent_change is None
            else f"{candidate.percent_change:+.1f}% versus the length-adjusted baseline"
        )
        statement = (
            f"Effective cost for {candidate.key} moved from "
            f"{candidate.expected_baseline_cost:,.2f} to {candidate.current_cost:,.2f} "
            f"{candidate.currency} ({percent})."
        )
        return self._build(
            provenance=self.bundle.costs.provenance,
            source_type="cost",
            source_id=candidate.key,
            statement=statement,
            numeric_value=candidate.absolute_change,
            numeric_unit=candidate.currency,
        )

    def usage_change(self, candidate: AnomalyCandidate) -> Evidence | None:
        if candidate.current_quantity <= 0 and candidate.baseline_quantity <= 0:
            return None
        quantity_percent = (
            "new usage"
            if candidate.quantity_percent_change is None
            else f"{candidate.quantity_percent_change:+.1f}%"
        )
        unit_note = ""
        if candidate.unit_cost_baseline is not None and candidate.unit_cost_current is not None:
            unit_note = (
                f" Effective unit cost moved from {candidate.unit_cost_baseline:,.6f} to "
                f"{candidate.unit_cost_current:,.6f} {candidate.currency} per unit."
            )
        statement = (
            f"Usage quantity for {candidate.key} moved from {candidate.baseline_quantity:,.2f} to "
            f"{candidate.current_quantity:,.2f} ({quantity_percent}).{unit_note}"
        )
        return self._build(
            provenance=self.bundle.costs.provenance,
            source_type="usage",
            source_id=candidate.key,
            statement=statement,
            numeric_value=candidate.current_quantity - candidate.baseline_quantity,
            numeric_unit="usage units",
        )

    def inventory(self, resource: CloudResource) -> Evidence:
        tag_text, tag_flag = scrub_tags(resource.tags)
        name_text, name_flag = scrub(resource.resource_name or resource.resource_id, 120)
        created = resource.created_at.isoformat() if resource.created_at else "unknown"
        statement = (
            f"Inventory shows {resource.resource_type} {name_text} in "
            f"{resource.region_id or 'unknown region'}, state {resource.state}, created {created}, "
            f"tags: {tag_text}."
        )
        return self._build(
            provenance=self.bundle.resources.provenance,
            source_type="inventory",
            source_id=resource.resource_id,
            statement=statement,
            untrusted=tag_flag or name_flag,
        )

    def metric(self, series: MetricSeries, current: DateRange, baseline: DateRange) -> Evidence:
        baseline_average = series.window_average(baseline.start, baseline.end)
        current_average = series.window_average(current.start, current.end)
        current_peak = series.window_max(current.start, current.end)
        if baseline_average > 0:
            change = f"{((current_average - baseline_average) / baseline_average) * 100:+.1f}%"
        else:
            change = "no baseline activity"
        statement = (
            f"{series.metric_name} ({series.statistic}, {series.unit}) averaged "
            f"{baseline_average:,.2f} per day in the baseline and {current_average:,.2f} in the "
            f"current period ({change}), peaking at {current_peak:,.2f}."
        )
        return self._build(
            provenance=self.bundle.metrics.provenance,
            source_type="metric",
            source_id=f"{series.resource_id}:{series.metric_name}",
            statement=statement,
            numeric_value=round(current_average, 4),
            numeric_unit=series.unit,
        )

    def audit(self, event: AuditEvent) -> Evidence:
        summary, summary_flag = scrub(event.summary, 240)
        actor, actor_flag = scrub(event.actor or "unknown", 120)
        origin = ""
        if event.source_ip:
            origin = f" from {event.source_ip}"
            if event.source_location:
                location, location_flag = scrub(event.source_location, 60)
                summary_flag = summary_flag or location_flag
                origin += f" ({location})"
        statement = (
            f"{event.source} recorded {event.event_name} at {event.event_time.isoformat()} "
            f"by {actor}{origin}. Reported detail: {summary or 'none'}"
        )
        evidence = self._build(
            provenance=self.bundle.audit_events.provenance,
            source_type="audit",
            source_id=event.event_id,
            statement=statement,
            untrusted=bool(summary_flag or actor_flag),
        )
        # An audit event is observed when it happened, not when the log was read.
        return evidence.model_copy(update={"observed_at": event.event_time})

    def recommendation(self, recommendation: Recommendation) -> Evidence:
        description, flagged = scrub(recommendation.description, 240)
        statement = (
            f"{recommendation.source} reports a {recommendation.category} recommendation: "
            f"{description} Estimated monthly saving "
            f"{recommendation.estimated_monthly_savings:,.2f} {recommendation.currency}."
        )
        return self._build(
            provenance=self.bundle.recommendations.provenance,
            source_type="recommendation",
            source_id=recommendation.recommendation_id,
            statement=statement,
            numeric_value=recommendation.estimated_monthly_savings,
            numeric_unit=recommendation.currency,
            untrusted=flagged,
        )

    def freshness(self, period_end_iso: str, expected_delay_hours: float) -> Evidence:
        provenance = self.bundle.costs.provenance
        statement = (
            f"Cost data is complete only through {provenance.data_through.isoformat()} while the "
            f"requested period ends {period_end_iso}. The documented provider delay is about "
            f"{expected_delay_hours:.0f}h, so the missing days are unavailable data rather than "
            "zero usage."
        )
        return self._build(
            provenance=provenance,
            source_type="freshness",
            source_id=f"{self.provider}:data_through",
            statement=statement,
        )
