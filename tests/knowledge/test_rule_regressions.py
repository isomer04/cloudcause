"""Version-aware billing knowledge regressions.

These prove the hard rules of the knowledge layer: rules are selected by usage
date, newer rules
are never applied retroactively, stale or undated knowledge limits confidence,
unsupported schemas fail safely, and every production rule cites an official
source.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from cloudcause_contracts import Settings
from cloudcause_focus import UnsupportedSchemaVersionError
from cloudcause_knowledge import KnowledgeStore, build_knowledge_provenance

EDGE_CASES = Path(__file__).resolve().parents[1] / "data" / "knowledge_edge_cases"


@pytest.fixture
def edge_store() -> KnowledgeStore:
    return KnowledgeStore.from_directory(EDGE_CASES, review_max_age_days=180)


def test_older_bill_uses_the_rule_valid_on_its_usage_date(knowledge: KnowledgeStore) -> None:
    result = knowledge.get_billing_rule(
        "gcp", category="commitment_change", usage_date=date(2025, 6, 1)
    )
    assert result.found
    assert result.rule is not None
    assert result.rule.id == "gcp-committed-use-discount-v1"
    assert result.rule.data["model"] == "single_price"


def test_newer_bill_uses_the_changed_commitment_rule(knowledge: KnowledgeStore) -> None:
    result = knowledge.get_billing_rule(
        "gcp", category="commitment_change", usage_date=date(2026, 3, 1)
    )
    assert result.rule is not None
    assert result.rule.id == "gcp-committed-use-discount-multiprice"
    assert result.citation is not None
    assert result.citation.selected_for_date == date(2026, 3, 1)


def test_a_future_rule_is_never_applied_retroactively(edge_store: KnowledgeStore) -> None:
    result = edge_store.get_billing_rule(
        "aws", category="future_pattern", usage_date=date(2026, 7, 15)
    )
    assert result.found is False
    assert result.rule is None
    assert any("not applied retroactively" in warning for warning in result.warnings)


def test_missing_effective_date_prevents_high_confidence(edge_store: KnowledgeStore) -> None:
    result = edge_store.get_billing_rule(
        "aws", category="mystery_pattern", usage_date=date(2026, 7, 15)
    )
    assert result.found is True
    assert result.rule is not None and result.rule.valid_from is None
    assert any("no effective date" in warning for warning in result.warnings)


def test_stale_knowledge_produces_a_visible_warning(edge_store: KnowledgeStore) -> None:
    result = edge_store.get_billing_rule(
        "aws", category="stale_pattern", usage_date=date(2026, 7, 15)
    )
    assert result.is_stale is True
    assert any("last reviewed" in warning for warning in result.warnings)
    provenance = build_knowledge_provenance(edge_store, [result.citation])  # type: ignore[list-item]
    assert provenance.stale_rule_ids == ["test-aws-stale-rule"]


def test_unknown_focus_version_is_rejected(knowledge: KnowledgeStore) -> None:
    assert knowledge.focus_rule("1.4").found is True
    with pytest.raises(UnsupportedSchemaVersionError):
        knowledge.focus_rule("9.9")


def test_documentation_only_change_does_not_alter_calculations(knowledge: KnowledgeStore) -> None:
    """A billing_change rule is informational: it carries no cost coefficients."""

    changes = knowledge.get_known_billing_change(
        "gcp", start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert [rule.id for rule in changes] == ["gcp-billing-change-2026-multiprice-cuds"]
    assert changes[0].cost_drivers == []


def test_data_delay_is_known_before_reporting_missing_usage(knowledge: KnowledgeStore) -> None:
    for provider, expected in (("aws", 24.0), ("azure", 24.0), ("gcp", 36.0)):
        result = knowledge.get_provider_data_freshness_rules(provider, usage_date=date(2026, 7, 19))
        assert result.found, provider
        assert result.rule is not None
        assert result.rule.data["treat_missing_as_zero"] is False
        assert knowledge.data_delay_hours(provider, date(2026, 7, 19)) == expected


def test_every_production_rule_links_to_an_official_source(knowledge: KnowledgeStore) -> None:
    for rule in knowledge.rules:
        assert rule.source.url.startswith("https://"), rule.id
        assert rule.reviewed_at is not None, rule.id
        assert rule.summary, rule.id


def test_deprecated_apis_are_flagged(knowledge: KnowledgeStore) -> None:
    result = knowledge.get_api_deprecation_status("azure", api="consumption")
    assert result.rule is not None
    assert result.rule.confidence == "deprecated"
    assert any("deprecated" in warning for warning in result.warnings)


def test_pricing_source_is_an_official_api(knowledge: KnowledgeStore) -> None:
    for provider in ("aws", "azure", "gcp"):
        result = knowledge.get_pricing_source(provider)
        assert result.found, provider
        assert result.rule is not None
        assert result.rule.source.type in ("official_api", "official_documentation")


def test_export_schema_versions_are_pinned(knowledge: KnowledgeStore, settings: Settings) -> None:
    result = knowledge.get_export_schema_version("aws", usage_date=date(2026, 7, 19))
    assert result.rule is not None
    assert "2.0" in result.rule.data["supported_versions"]
    assert settings.focus_version == "1.4"
