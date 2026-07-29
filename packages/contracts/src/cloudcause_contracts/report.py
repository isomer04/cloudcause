"""The final investigation report contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .analytics import PeriodComparison, Reconciliation
from .common import CloudCauseModel, DataOrigin, DateRange, Provenance, utcnow
from .investigation import Finding, InvestigationPlan, InvestigationRequest, ProviderStatus

REPORT_CONTRACT_VERSION = "v1"

ValidationSeverity = Literal["info", "warning", "error"]


class ValidationIssue(CloudCauseModel):
    code: str
    severity: ValidationSeverity
    detail: str
    finding_id: str | None = None
    provider: str | None = None


class InvestigationReport(CloudCauseModel):
    investigation_id: str
    contract_version: str = REPORT_CONTRACT_VERSION
    question: str
    request: InvestigationRequest
    plan: InvestigationPlan | None = None
    generated_at: datetime = Field(default_factory=utcnow)
    current_period: DateRange
    baseline_period: DateRange
    total_current_cost: float = 0.0
    total_baseline_cost: float = 0.0
    total_absolute_change: float = 0.0
    total_percent_change: float | None = None
    currency: str = "USD"
    comparison: PeriodComparison | None = None
    findings: list[Finding] = Field(default_factory=list)
    provider_statuses: list[ProviderStatus] = Field(default_factory=list)
    reconciliation: Reconciliation | None = None
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sources: list[Provenance] = Field(default_factory=list)
    knowledge: KnowledgeProvenanceRef | None = None
    data_mode: Literal["fixtures", "live"] = "fixtures"
    #: Finer than ``data_mode``: fixtures, a user upload, or a live connector.
    data_origin: DataOrigin = "fixture"
    agent_mode: Literal["stub", "live"] = "stub"
    summary: str = ""

    def data_through(self) -> datetime | None:
        stamps = [source.data_through for source in self.sources]
        return min(stamps) if stamps else None

    def evidence_count(self) -> int:
        return sum(len(finding.evidence) for finding in self.findings)


# Imported late to keep the knowledge module independent of the report module.
from .knowledge import KnowledgeProvenance as KnowledgeProvenanceRef  # noqa: E402

InvestigationReport.model_rebuild()


class InvestigationState(CloudCauseModel):
    """What the gateway exposes while an investigation runs."""

    investigation_id: str
    status: Literal["queued", "running", "completed", "failed"]
    question: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    request: InvestigationRequest
    provider_statuses: list[ProviderStatus] = Field(default_factory=list)
    stage: str = "queued"
    message: str = ""
    report: InvestigationReport | None = None
    error: str | None = None
