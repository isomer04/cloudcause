"""Native tool calling surface.

These are the in-process, deterministic helpers each framework registers with its
own idiom (``@tool`` in Strands, plain typed functions in MAF, function tools in
ADK). They are mechanical on purpose: read prepared analytics, read the prepared
evidence pool, and record a finding whose numbers come from the deterministic
layer rather than from the model.

External provider data and billing knowledge are not exposed here. They come
through MCP (see ``cloudcause_mcp``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from cloudcause_contracts import Evidence, Finding

from .context import InvestigationContext
from .playbooks import PlaybookSpec, build_finding, gather_evidence, select_playbook


class NativeToolset:
    """Deterministic helpers bound to one investigation context."""

    def __init__(self, ctx: InvestigationContext, playbooks: Sequence[PlaybookSpec]) -> None:
        self.ctx = ctx
        self.playbooks = list(playbooks)
        self.findings: list[Finding] = []
        self._evidence_pool: dict[str, Evidence] = {}
        self._candidate_evidence: dict[str, list[Evidence]] = {}
        self._selected: dict[str, PlaybookSpec] = {}
        self._prepare()

    def _prepare(self) -> None:
        for candidate in self.ctx.candidates:
            spec = select_playbook(candidate, self.playbooks, self.ctx)
            self._selected[candidate.candidate_id] = spec
            evidence = gather_evidence(candidate, spec, self.ctx)
            self._candidate_evidence[candidate.candidate_id] = evidence
            for item in evidence:
                self._evidence_pool[item.evidence_id] = item

    # ----------------------------------------------------------------- read side
    def get_anomaly_candidates(self) -> list[dict[str, Any]]:
        """List the deterministic cost-increase candidates assigned to this provider."""

        return [
            {
                "candidate_id": candidate.candidate_id,
                "key": candidate.key,
                "service_name": candidate.service_name,
                "region_id": candidate.region_id,
                "resource_id": candidate.resource_id,
                "absolute_change": candidate.absolute_change,
                "percent_change": candidate.percent_change,
                "quantity_percent_change": candidate.quantity_percent_change,
                "is_new": candidate.is_new,
                "first_spike_date": (
                    candidate.first_spike_date.isoformat() if candidate.first_spike_date else None
                ),
                "currency": candidate.currency,
                "suggested_category": self._selected[candidate.candidate_id].category,
            }
            for candidate in self.ctx.candidates
        ]

    def get_investigation_plan(self) -> dict[str, Any]:
        """Read the orchestrator's task for this provider."""

        return {
            "investigation_id": self.ctx.investigation_id,
            "provider": self.ctx.provider,
            "question": self.ctx.task.question,
            "focus_areas": list(self.ctx.task.focus_areas),
            "must_explain": list(self.ctx.task.must_explain),
            "candidate_ids": list(self.ctx.task.candidate_ids),
            "current_period": self.ctx.current_period.label(),
            "baseline_period": self.ctx.baseline_period.label(),
            "max_findings": self.ctx.task.max_findings,
        }

    def get_candidate_evidence(self, candidate_id: str) -> list[dict[str, Any]]:
        """Read the prepared evidence for one candidate. Only these ids may be cited."""

        evidence = self._candidate_evidence.get(candidate_id)
        if evidence is None:
            return [{"error": f"unknown candidate_id {candidate_id!r}"}]
        return [
            {
                "evidence_id": item.evidence_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "observed_at": item.observed_at.isoformat(),
                "statement": item.statement,
                "numeric_value": item.numeric_value,
                "numeric_unit": item.numeric_unit,
                "contains_untrusted_text": item.contains_untrusted_text,
            }
            for item in evidence
        ]

    def recalculate_attribution(self, candidate_id: str) -> dict[str, Any]:
        """Ask the deterministic layer to restate the cost attribution for a candidate."""

        candidate = self.ctx.candidate(candidate_id)
        if candidate is None:
            return {"error": f"unknown candidate_id {candidate_id!r}"}
        days = max(self.ctx.current_period.days, 1)
        return {
            "candidate_id": candidate_id,
            "baseline_cost": candidate.baseline_cost,
            "expected_baseline_cost": candidate.expected_baseline_cost,
            "current_cost": candidate.current_cost,
            "absolute_change": candidate.absolute_change,
            "daily_delta": round(candidate.absolute_change / days, 4),
            "estimated_monthly_impact": round(candidate.absolute_change / days * 30.4, 2),
            "currency": candidate.currency,
        }

    # ---------------------------------------------------------------- write side
    def record_finding(
        self,
        candidate_id: str,
        category: str,
        suspected_root_cause: str,
        recommendation: str,
        evidence_ids: list[str],
        risk: Literal["low", "medium", "high"] = "low",
    ) -> dict[str, Any]:
        """Record one finding. Cost figures are taken from the deterministic layer.

        Only evidence ids returned by ``get_candidate_evidence`` may be cited; an
        unknown id is rejected rather than accepted as a claim.
        """

        candidate = self.ctx.candidate(candidate_id)
        if candidate is None:
            return {"accepted": False, "error": f"unknown candidate_id {candidate_id!r}"}
        unknown = [value for value in evidence_ids if value not in self._evidence_pool]
        if unknown:
            return {
                "accepted": False,
                "error": f"unknown evidence ids {unknown}; cite ids from get_candidate_evidence",
            }
        cited = [self._evidence_pool[value] for value in evidence_ids]
        if not cited:
            return {"accepted": False, "error": "at least one evidence id is required"}

        spec = self._selected[candidate_id]
        if category and category != spec.category:
            spec = PlaybookSpec(
                category=category,
                root_cause=spec.root_cause,
                recommendation=spec.recommendation,
                risk=risk,
                rule_type=spec.rule_type,
                max_confidence=min(spec.max_confidence, 0.8),
            )
        finding = build_finding(
            candidate,
            spec,
            self.ctx,
            index=len(self.findings) + 1,
            evidence=cited,
            agent_mode="live",
            root_cause=suspected_root_cause.strip() or None,
            recommendation=recommendation.strip() or None,
        )
        self.findings.append(finding)
        return {
            "accepted": True,
            "finding_id": finding.finding_id,
            "confidence": finding.confidence,
            "actual_cost_increase": finding.actual_cost_increase,
            "cited_evidence": finding.evidence_ids(),
        }

    def as_functions(self) -> list[Any]:
        """The callables to register with a framework's native tool calling."""

        return [
            self.get_investigation_plan,
            self.get_anomaly_candidates,
            self.get_candidate_evidence,
            self.recalculate_attribution,
            self.record_finding,
        ]
