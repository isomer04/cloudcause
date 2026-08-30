"""Read-only, date-aware billing knowledge store.

Rules live in ``knowledge/<provider>/*.yaml`` and are selected by the usage date
under investigation, never by "now". A rule that took effect after the billing
period cannot be applied to it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from cloudcause_contracts import (
    SUPPORTED_FOCUS_VERSION,
    BillingRule,
    KnowledgeProvenance,
    Provider,
    RuleCitation,
    RuleQueryResult,
    RuleSource,
    RuleType,
    utcnow,
)
from cloudcause_focus import UnsupportedSchemaVersionError

KNOWLEDGE_SCHEMA_VERSION = "1"

KnowledgeProviderKey = Provider | Literal["focus"]


class KnowledgeError(RuntimeError):
    """Raised when the knowledge repository itself is invalid."""


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _rule_from_mapping(payload: dict[str, Any], source_file: Path) -> BillingRule:
    try:
        source_payload = payload["source"]
        matches = payload.get("matches") or {}
        return BillingRule(
            id=payload["id"],
            provider=payload["provider"],
            rule_type=payload["rule_type"],
            title=payload.get("title", payload["id"]),
            service=payload.get("service"),
            valid_from=_as_date(payload.get("valid_from")),
            valid_to=_as_date(payload.get("valid_to")),
            reviewed_at=_as_date(payload.get("reviewed_at")),
            schema_version=str(payload.get("schema_version", KNOWLEDGE_SCHEMA_VERSION)),
            summary=payload.get("summary", ""),
            cost_drivers=list(payload.get("cost_drivers") or []),
            investigation_checks=list(payload.get("investigation_checks") or []),
            matches_services=[str(value) for value in (matches.get("services") or [])],
            matches_categories=[str(value) for value in (matches.get("categories") or [])],
            source=RuleSource(
                type=source_payload["type"],
                url=source_payload["url"],
                updated_at=_as_date(source_payload.get("updated_at")),
            ),
            confidence=payload.get("confidence", "supported"),
            data=dict(payload.get("data") or {}),
            file=source_file.name,
        )
    except KeyError as error:  # pragma: no cover - repository authoring error
        raise KnowledgeError(f"{source_file}: missing required key {error}") from error


class KnowledgeStore:
    """In-memory view over the versioned knowledge repository."""

    def __init__(
        self,
        rules: Sequence[BillingRule],
        *,
        review_max_age_days: int = 180,
        focus_version: str = SUPPORTED_FOCUS_VERSION,
    ) -> None:
        self._rules = list(rules)
        self.review_max_age_days = review_max_age_days
        self.focus_version = focus_version
        duplicates = {rule.id for rule in self._rules if [r.id for r in self._rules].count(rule.id) > 1}
        if duplicates:
            raise KnowledgeError(f"duplicate rule ids in knowledge repository: {sorted(duplicates)}")

    @classmethod
    def from_directory(
        cls,
        root: Path,
        *,
        review_max_age_days: int = 180,
        focus_version: str = SUPPORTED_FOCUS_VERSION,
    ) -> KnowledgeStore:
        root = Path(root)
        if not root.exists():
            raise KnowledgeError(f"knowledge directory not found: {root}")
        rules: list[BillingRule] = []
        # Rules live in per-provider subdirectories; top-level files are configuration
        # (monitored sources, baselines) and are not rules.
        for path in sorted(p for p in root.rglob("*.yaml") if p.parent != root):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entries = document.get("rules") if isinstance(document, dict) else None
            if entries is None:
                entries = [document] if document else []
            for entry in entries:
                rules.append(_rule_from_mapping(entry, path))
        if not rules:
            raise KnowledgeError(f"no knowledge rules found under {root}")
        return cls(rules, review_max_age_days=review_max_age_days, focus_version=focus_version)

    @property
    def rules(self) -> list[BillingRule]:
        return list(self._rules)

    def rule_by_id(self, rule_id: str) -> BillingRule | None:
        for rule in self._rules:
            if rule.id == rule_id:
                return rule
        return None

    def is_stale(self, rule: BillingRule, as_of: date | None = None) -> bool:
        if rule.reviewed_at is None:
            return True
        as_of = as_of or utcnow().date()
        return (as_of - rule.reviewed_at).days > self.review_max_age_days

    def citation(
        self, rule: BillingRule, *, usage_date: date | None = None, as_of: date | None = None
    ) -> RuleCitation:
        return RuleCitation(
            rule_id=rule.id,
            provider=rule.provider,
            rule_type=rule.rule_type,
            service=rule.service,
            schema_version=rule.schema_version,
            valid_from=rule.valid_from,
            valid_to=rule.valid_to,
            reviewed_at=rule.reviewed_at,
            source_url=rule.source.url,
            source_updated_at=rule.source.updated_at,
            confidence=rule.confidence,
            is_stale=self.is_stale(rule, as_of),
            selected_for_date=usage_date,
        )

    def _match_score(
        self,
        rule: BillingRule,
        *,
        service: str | None,
        category: str | None,
    ) -> int:
        """0 means no match. Higher is more specific.

        An investigation category is the strongest signal, then an exact service
        name, then a service-name pattern. Without both filters only generic rules
        match.
        """

        if category and category in rule.matches_categories:
            return 3
        if service:
            lowered = service.lower()
            if rule.service and rule.service.lower() == lowered:
                return 2
            for pattern in rule.matches_services:
                pattern_lower = pattern.lower()
                if pattern_lower in lowered or lowered in pattern_lower:
                    return 1
            return 0
        if category:
            return 0
        return 0 if (rule.matches_services or rule.matches_categories) else 1

    def _select(
        self,
        *,
        provider: KnowledgeProviderKey,
        rule_type: RuleType,
        service: str | None = None,
        category: str | None = None,
        usage_date: date | None = None,
        as_of: date | None = None,
    ) -> RuleQueryResult:
        scored = [
            (self._match_score(rule, service=service, category=category), rule)
            for rule in self._rules
            if rule.provider == provider and rule.rule_type == rule_type
        ]
        best_score = max((score for score, _ in scored if score > 0), default=0)
        pool = [rule for score, rule in scored if score == best_score and score > 0]
        result = RuleQueryResult(
            found=False,
            requested_provider=provider,
            requested_service=service,
            requested_date=usage_date,
        )
        if not pool:
            # Fall back to the provider's generic rule for this rule type, and say so.
            pool = [
                rule
                for rule in self._rules
                if rule.provider == provider
                and rule.rule_type == rule_type
                and not rule.matches_services
                and not rule.matches_categories
            ]
            if pool:
                result.warnings.append(
                    f"no {rule_type} rule specific to service={service} category={category}; "
                    f"using the generic {provider} rule"
                )
            else:
                result.warnings.append(
                    f"no {rule_type} rule for provider={provider} service={service} category={category}"
                )
                return result

        if usage_date is not None:
            effective = [rule for rule in pool if rule.effective_on(usage_date)]
            undated = [rule for rule in pool if rule.valid_from is None]
            if effective:
                # Most recently effective rule wins when several overlap.
                chosen = max(effective, key=lambda rule: rule.valid_from or date.min)
            elif undated:
                chosen = undated[0]
                result.warnings.append(
                    f"rule {chosen.id} has no effective date; confidence must stay limited"
                )
            else:
                nearest = min(pool, key=lambda rule: rule.valid_from or date.max)
                result.warnings.append(
                    f"no rule effective on {usage_date.isoformat()} for provider={provider} "
                    f"service={service}; nearest rule {nearest.id} starts {nearest.valid_from}. "
                    "A newer rule was not applied retroactively."
                )
                return result
        else:
            chosen = max(pool, key=lambda rule: rule.valid_from or date.min)

        citation = self.citation(chosen, usage_date=usage_date, as_of=as_of)
        if citation.is_stale:
            result.warnings.append(
                f"rule {chosen.id} was last reviewed {chosen.reviewed_at}; "
                f"older than {self.review_max_age_days} days"
            )
        if chosen.confidence == "deprecated":
            result.warnings.append(f"rule {chosen.id} is marked deprecated")
        result.found = True
        result.rule = chosen
        result.citation = citation
        return result

    def get_billing_rule(
        self,
        provider: KnowledgeProviderKey,
        *,
        service: str | None = None,
        category: str | None = None,
        usage_date: date | None = None,
        rule_type: RuleType = "cost_driver",
        as_of: date | None = None,
    ) -> RuleQueryResult:
        return self._select(
            provider=provider,
            rule_type=rule_type,
            service=service,
            category=category,
            usage_date=usage_date,
            as_of=as_of,
        )

    def get_cost_driver_definitions(
        self,
        provider: KnowledgeProviderKey,
        *,
        service: str | None = None,
        category: str | None = None,
        usage_date: date | None = None,
    ) -> RuleQueryResult:
        return self._select(
            provider=provider,
            rule_type="cost_driver",
            service=service,
            category=category,
            usage_date=usage_date,
        )

    def get_provider_data_freshness_rules(
        self, provider: KnowledgeProviderKey, *, usage_date: date | None = None
    ) -> RuleQueryResult:
        return self._select(provider=provider, rule_type="data_freshness", usage_date=usage_date)

    def get_export_schema_version(
        self, provider: KnowledgeProviderKey, *, usage_date: date | None = None
    ) -> RuleQueryResult:
        return self._select(provider=provider, rule_type="export_schema", usage_date=usage_date)

    def get_api_deprecation_status(
        self, provider: KnowledgeProviderKey, *, api: str | None = None, usage_date: date | None = None
    ) -> RuleQueryResult:
        return self._select(
            provider=provider, rule_type="api_deprecation", service=api, usage_date=usage_date
        )

    def get_pricing_source(
        self, provider: KnowledgeProviderKey, *, service: str | None = None
    ) -> RuleQueryResult:
        return self._select(provider=provider, rule_type="pricing_source", service=service)

    def get_known_billing_change(
        self,
        provider: KnowledgeProviderKey,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> list[BillingRule]:
        changes = [
            rule
            for rule in self._rules
            if rule.provider == provider and rule.rule_type == "billing_change"
        ]
        if start and end:
            changes = [
                rule
                for rule in changes
                if rule.valid_from is not None and start <= rule.valid_from <= end
            ]
        return sorted(changes, key=lambda rule: rule.valid_from or date.min)

    def focus_rule(self, version: str | None = None) -> RuleQueryResult:
        version = version or self.focus_version
        for rule in self._rules:
            if rule.rule_type == "focus_version" and str(rule.data.get("version")) == version:
                result = RuleQueryResult(found=True, rule=rule, citation=self.citation(rule))
                if result.citation and result.citation.is_stale:
                    result.warnings.append(f"FOCUS rule {rule.id} review is overdue")
                return result
        raise UnsupportedSchemaVersionError("FOCUS", version, frozenset({self.focus_version}))

    def data_delay_hours(self, provider: KnowledgeProviderKey, usage_date: date | None = None) -> float:
        result = self.get_provider_data_freshness_rules(provider, usage_date=usage_date)
        if result.rule is None:
            return 48.0
        return float(result.rule.data.get("expected_delay_hours", 48))


def build_knowledge_provenance(
    store: KnowledgeStore, citations: Iterable[RuleCitation]
) -> KnowledgeProvenance:
    citations = list(citations)
    reviews = [citation.reviewed_at for citation in citations if citation.reviewed_at]
    return KnowledgeProvenance(
        focus_version=store.focus_version,
        knowledge_schema_version=KNOWLEDGE_SCHEMA_VERSION,
        rule_ids=sorted({citation.rule_id for citation in citations}),
        oldest_review_date=min(reviews) if reviews else None,
        newest_review_date=max(reviews) if reviews else None,
        stale_rule_ids=sorted({c.rule_id for c in citations if c.is_stale}),
        review_max_age_days=store.review_max_age_days,
    )


@lru_cache(maxsize=8)
def _cached_store(root: str, review_max_age_days: int, focus_version: str) -> KnowledgeStore:
    return KnowledgeStore.from_directory(
        Path(root), review_max_age_days=review_max_age_days, focus_version=focus_version
    )


def load_knowledge_store(
    root: Path, *, review_max_age_days: int = 180, focus_version: str = SUPPORTED_FOCUS_VERSION
) -> KnowledgeStore:
    """Load (and cache) the knowledge repository from disk."""

    return _cached_store(str(Path(root).resolve()), review_max_age_days, focus_version)
