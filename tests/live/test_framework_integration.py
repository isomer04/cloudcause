"""Framework integration tests (opt-in).

    uv sync
    uv run pytest tests/live -m live

Runs the real Google ADK, Microsoft Agent Framework, and AWS Strands agents
against fixture data with small hosted models. Needs OPENAI_API_KEY and
GOOGLE_API_KEY, needs no cloud account, and is excluded from offline CI.

Assertions are semantic: provider, category, resource, evidence count, confidence
range, and cost attribution tolerance. Model wording is never compared.
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from cloudcause_contracts import InvestigationRequest, Settings
from cloudcause_orchestrator import Orchestrator

pytestmark = pytest.mark.live

EXPECTED = {
    "aws": ("nat_gateway_misroute", "nat-0ab12cd34ef56789a", 126.0),
    "azure": (
        "functions_retry_loop",
        "/subscriptions/8f3c2b71-9d4e-4a5f-8c21-7b6e5d4c3a2b/resourceGroups/rg-prod/providers/Microsoft.Web/sites/orders-processor",
        103.2,
    ),
    "gcp": (
        "api_key_abuse",
        "//serviceusage.googleapis.com/projects/cloudcause-demo/services/translate.googleapis.com",
        161.6,
    ),
}


def _require_keys() -> None:
    missing = [
        name
        for name in ("OPENAI_API_KEY", "GOOGLE_API_KEY")
        if not os.environ.get(name) or os.environ.get(name) == "replace-me"
    ]
    if missing:
        pytest.skip(f"live agent mode needs {', '.join(missing)}")


@pytest.fixture
def live_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("CLOUDCAUSE_AGENT_MODE", "live")
    monkeypatch.setenv("CLOUDCAUSE_DATA_MODE", "fixtures")
    monkeypatch.setenv("CLOUDCAUSE_ORCHESTRATOR_MODE", "inprocess")
    monkeypatch.setenv("CLOUDCAUSE_WORKER_MODE", "inprocess")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "FALSE")
    settings = Settings.from_env()
    _require_keys()
    return settings


async def test_all_three_frameworks_participate_in_one_investigation(
    live_settings: Settings,
) -> None:
    request = InvestigationRequest(
        providers=["aws", "azure", "gcp"],
        start_date=date(2026, 7, 13),
        end_date=date(2026, 7, 19),
        comparison_start_date=date(2026, 7, 6),
        comparison_end_date=date(2026, 7, 12),
        question="Why did our cloud spending increase last week?",
        agent_mode="live",
    )
    report = await Orchestrator(live_settings).run(request)

    statuses = {status.provider: status for status in report.provider_statuses}
    assert set(statuses) == {"aws", "azure", "gcp"}
    for provider, status in statuses.items():
        assert status.status in ("ok", "partial"), f"{provider}: {status.message}"

    live_providers = {
        status.provider for status in report.provider_statuses if status.agent_mode == "live"
    }
    assert live_providers == set(EXPECTED), (
        f"not every framework ran in live mode: {sorted(live_providers)}"
    )

    for provider, (category, resource_id, expected_cost) in EXPECTED.items():
        matches = [
            finding
            for finding in report.findings
            if finding.provider == provider and finding.category == category
        ]
        assert matches, f"{provider} did not report {category}"
        finding = matches[0]
        assert resource_id in finding.affected_resources
        assert len(finding.evidence) >= 2
        assert 0.0 <= finding.confidence <= 1.0
        assert finding.actual_cost_increase == pytest.approx(expected_cost, rel=0.02)
        assert finding.applied_rules
        assert finding.requires_human_approval is True

    assert report.reconciliation is not None and report.reconciliation.within_tolerance
    assert not [issue for issue in report.validation_issues if issue.severity == "error"]


async def test_live_investigation_publishes_the_deterministic_summary(live_settings: Settings) -> None:
    request = InvestigationRequest(
        providers=["aws"],
        start_date=date(2026, 7, 13),
        end_date=date(2026, 7, 19),
        comparison_start_date=date(2026, 7, 6),
        comparison_end_date=date(2026, 7, 12),
        question="Why did AWS spending increase?",
        scenario_id="aws-nat-gateway-misroute",
        agent_mode="live",
    )
    report = await Orchestrator(live_settings).run(request)
    assert report.summary.startswith("Spending rose +126.00 USD")
    assert "Every recommendation needs human approval; CloudCause changed nothing." in report.summary
