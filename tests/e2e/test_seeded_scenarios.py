"""Seeded scenario evaluation, scored by the evaluation harness."""

from __future__ import annotations

import pytest
from cloudcause_contracts import Settings
from cloudcause_providers import ScenarioSpec, list_scenarios
from harness import evaluate_all, load_expectation, run_scenario


def scenario_ids(settings: Settings) -> list[ScenarioSpec]:
    return list_scenarios(settings.scenario_root)


@pytest.fixture(params=[spec.id for spec in scenario_ids(Settings.from_env({}))])
def scenario(request: pytest.FixtureRequest, settings: Settings) -> ScenarioSpec:
    for spec in scenario_ids(settings):
        if spec.id == request.param:
            return spec
    raise AssertionError(f"scenario {request.param} disappeared")


async def test_scenario_meets_its_expected_finding(scenario: ScenarioSpec, settings: Settings) -> None:
    result = await run_scenario(scenario, settings)
    assert result.passed, "\n".join(result.failures)


async def test_every_scenario_has_an_expectation_file(settings: Settings) -> None:
    for spec in scenario_ids(settings):
        expectation = load_expectation(settings.expected_findings_root, spec.id)
        assert expectation["scenario_id"] == spec.id


async def test_evaluation_metrics_meet_the_mvp_bar(settings: Settings) -> None:
    summary = await evaluate_all(settings)
    assert summary.total >= 12
    assert summary.passed == summary.total, summary.report_text()
    assert summary.top_k_rate == 1.0
    assert summary.attribution_accuracy == 1.0
    assert summary.supported_claim_ratio == 1.0
    assert summary.unsupported_claim_rate == 0.0
    assert "$0.00" in summary.report_text()

    structured = summary.to_dict()
    assert structured["summary"]["passed"] == summary.passed
    assert len(structured["scenarios"]) == summary.total
    assert structured["scenarios"][0]["scenario_id"]

    markdown = summary.report_markdown()
    assert "# CloudCause Evaluation Results" in markdown
    assert f"| Scenarios passed | {summary.passed}/{summary.total} |" in markdown
