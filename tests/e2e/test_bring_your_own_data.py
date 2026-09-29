"""Tier 1 and Tier 2, end to end over a generated CUR.

Tier 1 is a cost export alone: real period comparison, real materiality, real
reconciliation, real rule citation, and no named cause. Tier 2 adds metrics,
inventory, audit events, and recommendations over the same period, and the same
run then publishes the specific mechanism.

This is the pair that proves the cost-only degradation is honest rather than
decorative.
"""

from __future__ import annotations

import pytest
from cloudcause_api import API_PREFIX, app
from cloudcause_contracts import InvestigationReport, Settings
from cloudcause_datasets import (
    add_source,
    build_dataset_store,
    parse_cost_source,
    parse_evidence_source,
    seal_dataset,
)
from cloudcause_evidence import UNSUPPORTED_CAUSE_MAX_CONFIDENCE
from conftest import (
    NAT_RESOURCE,
    aws_audit_json,
    aws_cur_json,
    aws_inventory_json,
    aws_metrics_json,
    aws_recommendations_json,
)
from fastapi.testclient import TestClient

REQUEST = {
    "providers": ["aws"],
    "start_date": "2026-07-13",
    "end_date": "2026-07-19",
    "comparison_start_date": "2026-07-06",
    "comparison_end_date": "2026-07-12",
    "question": "Why did our AWS spending increase last week?",
    "scenario_id": "",
}

#: The NAT Gateway spike planted in ``conftest.aws_cur_rows``: 2.00/day baseline
#: against 20.00/day current, over seven days each.
EXPECTED_NAT_CHANGE = 126.0

TIER_2_SOURCES = {
    "metrics": aws_metrics_json,
    "inventory": aws_inventory_json,
    "audit": aws_audit_json,
    "recommendations": aws_recommendations_json,
}


def build_dataset(settings: Settings, *, tier: int) -> str:
    store = build_dataset_store(settings)
    dataset = store.create()
    add_source(
        store,
        dataset.dataset_id,
        "aws",
        "cost",
        parse_cost_source("aws", aws_cur_json(), settings),
        4096,
        settings,
    )
    if tier == 2:
        for kind, build in TIER_2_SOURCES.items():
            add_source(
                store,
                dataset.dataset_id,
                "aws",
                kind,  # type: ignore[arg-type]
                parse_evidence_source("aws", kind, build(), settings),  # type: ignore[arg-type]
                512,
                settings,
            )
    seal_dataset(store, dataset.dataset_id)
    return dataset.dataset_id


def investigate(client: TestClient, dataset_id: str) -> InvestigationReport:
    created = client.post(
        f"{API_PREFIX}/investigations?wait=true", json={**REQUEST, "dataset_id": dataset_id}
    )
    assert created.status_code == 200, created.text
    assert created.json()["state"]["status"] == "completed"
    return InvestigationReport.model_validate(
        client.get(
            f"{API_PREFIX}/investigations/{created.json()['investigation_id']}/report"
        ).json()
    )


@pytest.fixture
def tier1(upload_settings: Settings) -> str:
    return build_dataset(upload_settings, tier=1)


@pytest.fixture
def tier2(upload_settings: Settings) -> str:
    return build_dataset(upload_settings, tier=2)




def test_tier_one_measures_the_money_exactly(tier1: str) -> None:
    with TestClient(app) as client:
        report = investigate(client, tier1)

    assert report.data_origin == "upload"
    assert report.currency == "USD"
    assert report.total_absolute_change == pytest.approx(EXPECTED_NAT_CHANGE, abs=0.01)
    assert report.comparison is not None
    candidates = report.comparison.candidates_for("aws")
    assert [candidate.resource_id for candidate in candidates] == [NAT_RESOURCE]
    assert candidates[0].absolute_change == pytest.approx(EXPECTED_NAT_CHANGE, abs=0.01)


def test_tier_one_reconciliation_still_balances(tier1: str) -> None:
    with TestClient(app) as client:
        report = investigate(client, tier1)

    assert report.reconciliation is not None
    assert report.reconciliation.within_tolerance is True
    assert report.reconciliation.attributed_change == pytest.approx(
        report.total_absolute_change, abs=0.05
    )


def test_tier_one_publishes_only_the_honest_cause(tier1: str) -> None:
    with TestClient(app) as client:
        report = investigate(client, tier1)

    assert report.findings, "a measured change is still worth publishing"
    for finding in report.findings:
        assert finding.category == "unexplained_increase"
        assert finding.confidence <= UNSUPPORTED_CAUSE_MAX_CONFIDENCE
        assert finding.is_uncertain is True
        assert finding.applied_rules, "the cited billing rule survives the degradation"
        assert any("mechanism is unconfirmed" in warning for warning in finding.warnings)

    codes = {issue.code for issue in report.validation_issues}
    assert "cause_unsupported_by_available_sources" in codes


def test_tier_one_names_the_data_that_would_improve_it(tier1: str) -> None:
    with TestClient(app) as client:
        report = investigate(client, tier1)

    banner = [warning for warning in report.warnings if "no metric" in warning]
    assert banner, "the report carries one top-level warning listing the missing sources"
    for source_type in ("metric", "audit", "inventory", "recommendation"):
        assert source_type in banner[0]


def test_tier_one_evidence_is_cost_and_usage_only(tier1: str) -> None:
    with TestClient(app) as client:
        report = investigate(client, tier1)

    kinds = {item.source_type for finding in report.findings for item in finding.evidence}
    assert kinds <= {"cost", "usage"}
    assert all(
        item.origin == "upload" for finding in report.findings for item in finding.evidence
    )




def test_tier_two_publishes_the_specific_mechanism(tier2: str) -> None:
    with TestClient(app) as client:
        report = investigate(client, tier2)

    categories = {finding.category for finding in report.findings}
    assert "nat_gateway_misroute" in categories, (
        "the same cost rows plus metrics, inventory, and an audit event should name the cause"
    )
    named = next(f for f in report.findings if f.category == "nat_gateway_misroute")
    assert named.confidence > UNSUPPORTED_CAUSE_MAX_CONFIDENCE
    assert NAT_RESOURCE in named.affected_resources
    assert named.applied_rules


def test_tier_two_measures_the_same_money_as_tier_one(tier1: str, tier2: str) -> None:
    with TestClient(app) as client:
        one = investigate(client, tier1)
        two = investigate(client, tier2)

    assert one.total_absolute_change == pytest.approx(two.total_absolute_change, abs=0.01)
    assert one.total_current_cost == pytest.approx(two.total_current_cost, abs=0.01)
    assert (
        one.findings[0].actual_cost_increase
        == pytest.approx(two.findings[0].actual_cost_increase, abs=0.01)
    ), "adding evidence changes the explanation, never the arithmetic"


def test_evidence_files_do_not_widen_the_period_the_gateway_suggests(
    upload_settings: Settings, tier1: str, tier2: str
) -> None:
    """Caught in a browser pass: inventory `created_at` moved the comparison.

    A resource's birthday is not a window the file covers, and letting it widen the
    dataset's period silently changed which days were compared, which changed the
    total. The suggested brief comes from the cost rows only.
    """

    with TestClient(app) as client:
        one = client.get(f"{API_PREFIX}/datasets/{tier1}").json()
        two = client.get(f"{API_PREFIX}/datasets/{tier2}").json()

    assert one["period_start"] == two["period_start"] == "2026-07-06"
    assert one["period_end"] == two["period_end"] == "2026-07-19"
    assert one["suggested_request"]["start_date"] == two["suggested_request"]["start_date"]
    assert two["suggested_request"]["comparison_start_date"] == "2026-07-06"

    inventory = next(source for source in two["sources"] if source["kind"] == "inventory")
    assert inventory["period_start"] is None, "an inventory snapshot covers no period"
    assert "no period of its own" in inventory["data_through_note"]


def test_tier_two_cites_every_kind_of_evidence(tier2: str) -> None:
    with TestClient(app) as client:
        report = investigate(client, tier2)

    kinds = {item.source_type for finding in report.findings for item in finding.evidence}
    for expected in ("cost", "usage", "metric", "audit", "inventory", "recommendation"):
        assert expected in kinds, f"{expected} evidence should be reachable in tier 2"

    codes = {issue.code for issue in report.validation_issues}
    assert "cause_unsupported_by_available_sources" not in codes
    assert not [warning for warning in report.warnings if "no metric" in warning]


def test_tier_two_report_renders_with_the_upload_marker(tier2: str) -> None:
    with TestClient(app) as client:
        created = client.post(
            f"{API_PREFIX}/investigations?wait=true", json={**REQUEST, "dataset_id": tier2}
        ).json()
        markdown = client.get(
            f"{API_PREFIX}/investigations/{created['investigation_id']}/report.md"
        ).text

    assert "**data origin:** upload" in markdown
    assert "cost export you supplied" in markdown
    assert "did not verify them against a cloud account" in markdown
    assert "Evidence ID" in markdown
