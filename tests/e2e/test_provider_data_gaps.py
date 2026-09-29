"""A provider with no data must not fail the whole investigation.

Reproduces a real bug: an AWS-only scenario selected with both aws and
azure ticked. CloudCause must run with one connected provider
and surface partial failures as warnings, so azure is skipped and the AWS
investigation still reports.
"""

from __future__ import annotations

import pytest
from cloudcause_api import API_PREFIX, app
from cloudcause_contracts import InvestigationRequest, Settings
from cloudcause_orchestrator import Orchestrator, ProviderDataUnavailableError
from fastapi.testclient import TestClient

SCENARIO = "aws-delayed-billing-data"
PERIODS = {
    "start_date": "2026-07-13",
    "end_date": "2026-07-19",
    "comparison_start_date": "2026-07-06",
    "comparison_end_date": "2026-07-12",
}


def mismatched_request(*providers: str) -> InvestigationRequest:
    return InvestigationRequest(
        providers=list(providers),  # type: ignore[arg-type]
        start_date="2026-07-13",  # type: ignore[arg-type]
        end_date="2026-07-19",  # type: ignore[arg-type]
        comparison_start_date="2026-07-06",  # type: ignore[arg-type]
        comparison_end_date="2026-07-12",  # type: ignore[arg-type]
        question="Did our AWS spending change last week?",
        scenario_id=SCENARIO,
    )


async def test_a_provider_without_data_is_skipped_not_fatal(settings: Settings) -> None:
    report = await Orchestrator(settings).run(mismatched_request("aws", "azure"))

    statuses = {status.provider: status for status in report.provider_statuses}
    assert set(statuses) == {"aws", "azure"}
    assert statuses["azure"].status == "skipped"
    assert "UnknownScenarioError" in statuses["azure"].message
    assert statuses["aws"].status in ("ok", "partial")

    assert any("[azure] skipped" in warning for warning in report.warnings)
    assert [task.provider for task in report.plan.tasks] == ["aws"], (
        "a specialist must not be asked to investigate data nobody could load"
    )
    assert all(finding.provider == "aws" for finding in report.findings)
    assert report.comparison is not None
    assert [entry.provider for entry in report.comparison.providers] == ["aws"]


async def test_no_usable_provider_fails_with_one_clear_reason(settings: Settings) -> None:
    with pytest.raises(ProviderDataUnavailableError) as error:
        await Orchestrator(settings).run(mismatched_request("azure", "gcp"))
    message = str(error.value)
    assert "azure" in message and "gcp" in message
    assert SCENARIO in message


def test_the_gateway_reports_the_skip_instead_of_failing() -> None:
    with TestClient(app) as client:
        created = client.post(
            f"{API_PREFIX}/investigations?wait=true",
            json={
                "providers": ["aws", "azure"],
                **PERIODS,
                "question": "Did our AWS spending change last week?",
                "scenario_id": SCENARIO,
            },
        ).json()
        investigation_id = created["investigation_id"]
        assert created["state"]["status"] == "completed"

        report = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()
        markdown = client.get(f"{API_PREFIX}/investigations/{investigation_id}/report.md").text

    skipped = [status for status in report["provider_statuses"] if status["status"] == "skipped"]
    assert [status["provider"] for status in skipped] == ["azure"]
    assert any("[azure] skipped" in warning for warning in report["warnings"])
    assert "# CloudCause investigation" in markdown
