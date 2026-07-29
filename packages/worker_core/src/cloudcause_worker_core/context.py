"""Per-investigation context handed to playbooks, stub agents, and live agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from cloudcause_contracts import (
    AnomalyCandidate,
    DateRange,
    InvestigationRequest,
    Provider,
    ProviderDataBundle,
    ProviderTask,
    RuleCitation,
    Settings,
)
from cloudcause_knowledge import KnowledgeStore

from .evidence import EvidenceFactory


@dataclass
class InvestigationContext:
    investigation_id: str
    provider: Provider
    request: InvestigationRequest
    task: ProviderTask
    candidates: list[AnomalyCandidate]
    bundle: ProviderDataBundle
    knowledge: KnowledgeStore
    settings: Settings
    evidence: EvidenceFactory = field(init=False)
    warnings: list[str] = field(default_factory=list)
    citations: list[RuleCitation] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.evidence = EvidenceFactory(self.provider, self.bundle)
        self._check_freshness()

    @property
    def current_period(self) -> DateRange:
        return self.request.current_period

    @property
    def baseline_period(self) -> DateRange:
        return self.request.baseline_period

    def candidate(self, candidate_id: str) -> AnomalyCandidate | None:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        return None

    def rule_date(self, candidate: AnomalyCandidate) -> date:
        """The usage date a billing rule must be selected for."""

        return candidate.first_spike_date or self.current_period.end

    def period_end_datetime(self) -> datetime:
        return datetime.combine(self.current_period.end, time(23, 59, 59), tzinfo=UTC)

    def _check_freshness(self) -> None:
        data_through = self.bundle.data_through()
        period_end = self.period_end_datetime()
        if data_through >= period_end:
            return
        delay_hours = round((period_end - data_through).total_seconds() / 3600, 1)
        expected = self.knowledge.data_delay_hours(self.provider, self.current_period.end)
        detail = (
            f"{self.provider} data is complete only through {data_through.isoformat()}, "
            f"{delay_hours}h before the end of the requested period"
        )
        if delay_hours > expected:
            detail += f" (expected provider delay is about {expected}h)"
        self.warnings.append(
            f"{detail}. Missing days are unavailable data, not zero usage; "
            "conclusions about the most recent days stay provisional."
        )

    def data_is_incomplete(self) -> bool:
        return self.bundle.data_through() < self.period_end_datetime()

    def add_citation(self, citation: RuleCitation | None) -> None:
        if citation is None:
            return
        if all(existing.rule_id != citation.rule_id for existing in self.citations):
            self.citations.append(citation)
