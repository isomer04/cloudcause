"""Investigation request, evidence, finding, plan, and worker API contracts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .analytics import AnomalyCandidate
from .common import (
    CloudCauseModel,
    DataOrigin,
    DateRange,
    Provenance,
    Provider,
    Risk,
    reconcile_origin,
    utcnow,
)
from .knowledge import RuleCitation
from .settings import AgentMode

InvestigationStatus = Literal["queued", "running", "completed", "failed"]
WorkerStatus = Literal["ok", "partial", "failed", "skipped"]


class InvestigationRequest(CloudCauseModel):
    providers: list[Provider]
    start_date: date
    end_date: date
    comparison_start_date: date
    comparison_end_date: date
    account_ids: list[str] = Field(default_factory=list)
    # Questions are external text. Keep them bounded before they enter prompts,
    # history, or the report contract.
    question: str = Field(default="Why did our cloud spending increase?", max_length=1000)
    scenario_id: str = "default"
    #: An uploaded, sealed dataset. When present it wins over ``scenario_id``.
    dataset_id: str | None = None
    #: Selected per investigation. The server supports both modes concurrently;
    #: changing this never requires a process restart.
    agent_mode: AgentMode = "stub"

    @field_validator("providers")
    @classmethod
    def _require_provider(cls, value: list[Provider]) -> list[Provider]:
        if not value:
            raise ValueError("at least one provider is required")
        # Preserve order, drop duplicates.
        return list(dict.fromkeys(value))

    @property
    def current_period(self) -> DateRange:
        return DateRange(start=self.start_date, end=self.end_date)

    @property
    def baseline_period(self) -> DateRange:
        return DateRange(start=self.comparison_start_date, end=self.comparison_end_date)

    @property
    def live_agents_requested(self) -> bool:
        """Whether this investigation explicitly permits hosted-model execution."""

        return self.agent_mode == "live"


def resolve_agent_mode(
    request: InvestigationRequest, deployment_default: AgentMode
) -> InvestigationRequest:
    """Settle the mode for one run: the request decides, the deployment fills gaps.

    Both paths are always available in one process, so this never overrides an
    explicit choice. ``CLOUDCAUSE_AGENT_MODE`` only supplies a value for API
    clients that omit the field; what makes a live run actually possible is a
    model key, not a mode setting. See ``Settings.live_agents_available``.
    """

    if "agent_mode" in request.model_fields_set:
        return request
    return request.model_copy(update={"agent_mode": deployment_default})


class Evidence(CloudCauseModel):
    """One verifiable observation. Findings may not make claims without these."""

    evidence_id: str
    provider: Provider
    source_type: str
    source_id: str
    observed_at: datetime
    statement: str
    numeric_value: float | None = None
    numeric_unit: str | None = None
    query_reference: str | None = None
    data_through: datetime | None = None
    origin: DataOrigin = "fixture"
    is_fixture: bool = True
    contains_untrusted_text: bool = False

    @model_validator(mode="before")
    @classmethod
    def _origin_and_flag_agree(cls, data: Any) -> Any:
        return reconcile_origin(data)


class Finding(CloudCauseModel):
    """A ranked, evidence-backed explanation for part of a cost increase."""

    finding_id: str
    provider: Provider
    category: str
    suspected_root_cause: str
    affected_resources: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    actual_cost_increase: float = 0.0
    estimated_monthly_impact: float = 0.0
    recommendation: str = ""
    risk: Risk = "low"
    requires_human_approval: bool = True
    candidate_id: str | None = None
    service_name: str | None = None
    region_id: str | None = None
    applied_rules: list[RuleCitation] = Field(default_factory=list)
    is_uncertain: bool = False
    warnings: list[str] = Field(default_factory=list)
    agent_mode: AgentMode = "stub"

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        return round(value, 3)

    def evidence_ids(self) -> list[str]:
        return [item.evidence_id for item in self.evidence]


class ProviderTask(CloudCauseModel):
    """The orchestrator's instruction to one provider specialist."""

    provider: Provider
    # A task can be supplied over the worker transport, so it needs the same
    # bound as the originating investigation question.
    question: str = Field(max_length=1000)
    candidate_ids: list[str] = Field(default_factory=list)
    focus_areas: list[str] = Field(default_factory=list)
    must_explain: list[str] = Field(default_factory=list)
    max_findings: int = 5


class InvestigationPlan(CloudCauseModel):
    investigation_id: str
    question: str
    created_at: datetime = Field(default_factory=utcnow)
    current_period: DateRange
    baseline_period: DateRange
    tasks: list[ProviderTask] = Field(default_factory=list)
    deterministic_summary: str = ""
    rationale: str = ""
    planner_mode: Literal["deterministic", "live"] = "deterministic"


class WorkerRequest(CloudCauseModel):
    """Gateway/orchestrator to provider specialist, over HTTP."""

    investigation_id: str
    provider: Provider
    request: InvestigationRequest
    task: ProviderTask
    candidates: list[AnomalyCandidate] = Field(default_factory=list)
    contract_version: str = "v1"


class WorkerResponse(CloudCauseModel):
    """Provider specialist back to the orchestrator."""

    investigation_id: str
    provider: Provider
    status: WorkerStatus = "ok"
    findings: list[Finding] = Field(default_factory=list)
    sources: list[Provenance] = Field(default_factory=list)
    applied_rule_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: Evidence source types this provider's data could actually supply, in the
    #: ``Evidence.source_type`` vocabulary. The orchestrator forwards these to
    #: ``validate_findings`` so a cost-only dataset cannot publish a mechanism.
    available_source_types: list[str] = Field(default_factory=list)
    agent_mode: AgentMode = "stub"
    data_mode: Literal["fixtures", "live"] = "fixtures"
    data_origin: DataOrigin = "fixture"
    duration_seconds: float = 0.0
    message: str = ""
    contract_version: str = "v1"

    @property
    def executed_in_live_mode(self) -> bool:
        return self.agent_mode == "live"


class ProviderStatus(CloudCauseModel):
    provider: Provider
    status: WorkerStatus
    message: str = ""
    data_through: datetime | None = None
    origin: DataOrigin = "fixture"
    is_fixture: bool = True
    finding_count: int = 0
    duration_seconds: float = 0.0
    agent_mode: AgentMode = "stub"

    @property
    def executed_in_live_mode(self) -> bool:
        return self.agent_mode == "live"

    @model_validator(mode="before")
    @classmethod
    def _origin_and_flag_agree(cls, data: Any) -> Any:
        return reconcile_origin(data)


def determine_effective_agent_mode(
    requested_mode: AgentMode, statuses: Sequence[ProviderStatus]
) -> AgentMode:
    """Return live only when a requested live run actually executed live everywhere."""

    executed = [status for status in statuses if status.status != "skipped"]
    if requested_mode == "live" and executed and all(status.executed_in_live_mode for status in executed):
        return "live"
    return "stub"


class ProgressEvent(CloudCauseModel):
    """Streamed to the UI over SSE."""

    investigation_id: str
    sequence: int
    at: datetime = Field(default_factory=utcnow)
    stage: str
    status: Literal["started", "progress", "completed", "failed"] = "progress"
    provider: Provider | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(CloudCauseModel):
    """Common error envelope for every CloudCause HTTP service."""

    error: str
    detail: str = ""
    investigation_id: str | None = None
    provider: Provider | None = None
    retryable: bool = False
