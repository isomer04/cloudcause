"""Billing knowledge contracts.

Billing knowledge explains how to interpret provider billing behaviour. It is
versioned, human reviewed, and always selected by the usage date being
investigated.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field

from .common import CloudCauseModel, Provider, SupportStatus

RuleType = Literal[
    "cost_driver",
    "data_freshness",
    "export_schema",
    "api_deprecation",
    "pricing_source",
    "billing_change",
    "focus_version",
]


class RuleSource(CloudCauseModel):
    type: Literal["official_documentation", "official_api", "release_notes", "specification"]
    url: str
    updated_at: date | None = None


class BillingRule(CloudCauseModel):
    id: str
    provider: Provider | Literal["focus"]
    rule_type: RuleType
    title: str
    service: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    reviewed_at: date | None = None
    schema_version: str = "1"
    summary: str = ""
    cost_drivers: list[str] = Field(default_factory=list)
    investigation_checks: list[str] = Field(default_factory=list)
    matches_services: list[str] = Field(default_factory=list)
    matches_categories: list[str] = Field(default_factory=list)
    source: RuleSource
    confidence: SupportStatus = "supported"
    data: dict[str, Any] = Field(default_factory=dict)
    file: str | None = None

    def effective_on(self, usage_date: date) -> bool:
        if self.valid_from is None:
            return False
        if usage_date < self.valid_from:
            return False
        return self.valid_to is None or usage_date <= self.valid_to


class RuleCitation(CloudCauseModel):
    """The provenance stamp attached to every billing interpretation."""

    rule_id: str
    provider: Provider | Literal["focus"]
    rule_type: RuleType
    service: str | None = None
    schema_version: str = "1"
    valid_from: date | None = None
    valid_to: date | None = None
    reviewed_at: date | None = None
    source_url: str = ""
    source_updated_at: date | None = None
    confidence: SupportStatus = "supported"
    is_stale: bool = False
    selected_for_date: date | None = None


class RuleQueryResult(CloudCauseModel):
    """A knowledge lookup answer: the rule, its citation, and any warnings."""

    found: bool
    rule: BillingRule | None = None
    citation: RuleCitation | None = None
    warnings: list[str] = Field(default_factory=list)
    requested_provider: Provider | Literal["focus"] | None = None
    requested_service: str | None = None
    requested_date: date | None = None

    @property
    def is_stale(self) -> bool:
        return bool(self.citation and self.citation.is_stale)


class KnowledgeProvenance(CloudCauseModel):
    """Knowledge-side provenance for the final report header."""

    focus_version: str
    knowledge_schema_version: str = "1"
    rule_ids: list[str] = Field(default_factory=list)
    oldest_review_date: date | None = None
    newest_review_date: date | None = None
    stale_rule_ids: list[str] = Field(default_factory=list)
    review_max_age_days: int = 180
