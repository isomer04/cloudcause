"""Per-investigation context handed to playbooks, stub agents, and live agents."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from cloudcause_contracts import (
    AnomalyCandidate,
    DateRange,
    Finding,
    InvestigationRequest,
    Provider,
    ProviderDataBundle,
    ProviderTask,
    RuleCitation,
    Settings,
)
from cloudcause_knowledge import KnowledgeStore
from cloudcause_rate_limit import AIRequestGovernor

from .evidence import EvidenceFactory
from .live_limits import AgentCallBudget, current_agent_call_budget


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
    agent_call_budget: AgentCallBudget | None = None
    governor: AIRequestGovernor | None = None
    evidence: EvidenceFactory = field(init=False)
    warnings: list[str] = field(default_factory=list)
    citations: list[RuleCitation] = field(default_factory=list)
    #: candidate_id -> Finding, accumulated across every attempt on this
    #: context. Lives here, not on NativeToolset, because a whole-agent retry
    #: rebuilds a fresh (empty-``findings``) NativeToolset but reuses this
    #: context -- without this, a finding recorded by an attempt that later
    #: failed on a *different* candidate would be silently discarded rather
    #: than merged into the retry's result.
    recorded_findings: dict[str, Finding] = field(default_factory=dict)

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

    def reserve_agent_call(self, boundary: str) -> None:
        """Spend one live-call unit at a framework or native-tool boundary."""

        budget = self.agent_call_budget or current_agent_call_budget()
        if budget is None:
            budget = AgentCallBudget(self.settings.max_agent_calls)
            self.agent_call_budget = budget
        budget.reserve(boundary)

    @asynccontextmanager
    async def acquire_model_permit(self, family: str, model: str) -> AsyncIterator[None]:
        """Hold an outbound provider/model rate-limit permit for one model call.

        A no-op when no governor is bound, so contexts built directly in tests
        without a governor keep working.
        """

        if self.governor is None:
            yield
            return
        async with self.governor.permit(family, model):
            yield
