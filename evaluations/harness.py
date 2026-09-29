"""Scenario evaluation harness.

Scores every seeded scenario against its expected finding file and reports the
scored metrics: anomaly detection, root-cause ranking, cost attribution
accuracy, evidence support, unsupported-claim rate, latency, and model cost.

Semantic assertions only. Model wording is never compared.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from cloudcause_contracts import (
    Finding,
    InvestigationReport,
    Settings,
    get_settings,
    resolve_agent_mode,
)
from cloudcause_orchestrator import Orchestrator
from cloudcause_providers import ScenarioSpec, list_scenarios

TOP_K = 3


def load_expectation(root: Path, scenario_id: str) -> dict[str, Any]:
    path = Path(root) / f"{scenario_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"missing expected findings file: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@dataclass
class ScenarioResult:
    scenario_id: str
    provider: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    report: InvestigationReport | None = None
    root_cause_rank: int | None = None
    cost_error: float | None = None
    supported_claim_ratio: float = 1.0
    unsupported_claims: int = 0

    @property
    def status(self) -> str:
        return "pass" if self.passed else "FAIL"


def _match_finding(findings: list[Finding], expected: dict[str, Any]) -> int | None:
    """Index of the first finding matching provider, category, and resource."""

    for index, finding in enumerate(findings):
        if finding.provider != expected.get("provider"):
            continue
        if finding.category != expected.get("category"):
            continue
        resource_id = expected.get("resource_id")
        if resource_id and resource_id not in finding.affected_resources:
            continue
        return index
    return None


def score_report(report: InvestigationReport, expectation: dict[str, Any]) -> ScenarioResult:
    scenario_id = expectation["scenario_id"]
    result = ScenarioResult(
        scenario_id=scenario_id,
        provider=report.request.providers[0],
        passed=True,
        report=report,
    )
    findings = report.findings

    for phrase in expectation.get("expect_warnings_containing", []) or []:
        if not any(phrase.lower() in warning.lower() for warning in report.warnings):
            result.failures.append(f"expected a warning containing {phrase!r}")

    for category in expectation.get("forbidden_categories", []) or []:
        if any(finding.category == category for finding in findings[:TOP_K]):
            result.failures.append(f"forbidden category {category!r} appeared in the top {TOP_K}")

    supported = sum(1 for finding in findings if finding.evidence and finding.applied_rules)
    result.supported_claim_ratio = round(supported / len(findings), 4) if findings else 1.0
    result.unsupported_claims = sum(
        1 for issue in report.validation_issues if issue.severity == "error"
    )
    if result.unsupported_claims:
        result.failures.append(f"{result.unsupported_claims} validation error(s) were raised")

    if not expectation.get("expect_findings", True):
        if findings:
            result.failures.append(
                f"expected no findings, got {len(findings)}: "
                + ", ".join(f"{f.provider}/{f.category}" for f in findings)
            )
        result.passed = not result.failures
        return result

    expected_top = expectation.get("top_finding") or {}
    index = _match_finding(findings, expected_top)
    if index is None:
        result.failures.append(
            f"expected {expected_top.get('provider')}/{expected_top.get('category')} on "
            f"{expected_top.get('resource_id')}; got "
            + (", ".join(f"{f.provider}/{f.category}" for f in findings) or "no findings")
        )
        result.passed = False
        return result

    result.root_cause_rank = index + 1
    if index >= TOP_K:
        result.failures.append(f"expected cause ranked {index + 1}, outside the top {TOP_K}")

    finding = findings[index]
    expected_cost = float(expected_top.get("cost_increase", finding.actual_cost_increase))
    tolerance = float(expected_top.get("cost_tolerance", 0.02))
    error = abs(finding.actual_cost_increase - expected_cost)
    result.cost_error = round(error, 4)
    if error > max(tolerance * abs(expected_cost), 0.01):
        result.failures.append(
            f"cost attribution {finding.actual_cost_increase:,.2f} differs from expected "
            f"{expected_cost:,.2f} by more than {tolerance:.0%}"
        )

    min_confidence = float(expected_top.get("min_confidence", 0.0))
    if finding.confidence < min_confidence:
        result.failures.append(f"confidence {finding.confidence} below minimum {min_confidence}")
    max_confidence = expected_top.get("max_confidence")
    if max_confidence is not None and finding.confidence > float(max_confidence):
        result.failures.append(f"confidence {finding.confidence} above maximum {max_confidence}")
    if not 0.0 <= finding.confidence <= 1.0:
        result.failures.append("confidence outside [0, 1]")

    min_evidence = int(expected_top.get("min_evidence", 1))
    if len(finding.evidence) < min_evidence:
        result.failures.append(
            f"{len(finding.evidence)} evidence item(s), expected at least {min_evidence}"
        )

    sources = {item.source_type for item in finding.evidence}
    missing = [
        source
        for source in expected_top.get("required_evidence_sources", []) or []
        if source not in sources
    ]
    if missing:
        result.failures.append(f"missing evidence sources: {', '.join(missing)}")

    rule_ids = {rule.rule_id for rule in finding.applied_rules}
    missing_rules = [
        rule_id
        for rule_id in expected_top.get("required_rule_ids", []) or []
        if rule_id not in rule_ids
    ]
    if missing_rules:
        result.failures.append(f"missing rule citations: {', '.join(missing_rules)}")

    expected_risk = expected_top.get("expect_risk")
    if expected_risk and finding.risk != expected_risk:
        result.failures.append(f"risk {finding.risk!r}, expected {expected_risk!r}")

    if not finding.requires_human_approval:
        result.failures.append("finding does not require human approval")

    known_resources: set[str] = set()
    for status in report.provider_statuses:
        known_resources.add(status.provider)
    if finding.affected_resources and expected_top.get("resource_id"):
        if expected_top["resource_id"] not in finding.affected_resources:
            result.failures.append("expected resource id missing from affected resources")

    if expectation.get("expect_reconciled", True):
        if report.reconciliation is None or not report.reconciliation.within_tolerance:
            result.failures.append("cost totals did not reconcile within tolerance")

    result.passed = not result.failures
    return result


async def run_scenario(spec: ScenarioSpec, settings: Settings) -> ScenarioResult:
    expectation = load_expectation(settings.expected_findings_root, spec.id)
    orchestrator = Orchestrator(settings)
    started = time.perf_counter()
    # The harness drives the orchestrator directly, so it settles the mode the way
    # the gateway would rather than inheriting the request default.
    request = resolve_agent_mode(spec.to_request(), settings.agent_mode)
    report = await orchestrator.run(request)
    duration = time.perf_counter() - started
    result = score_report(report, expectation)
    result.duration_seconds = round(duration, 3)
    return result


@dataclass
class EvaluationSummary:
    results: list[ScenarioResult] = field(default_factory=list)
    agent_mode: str = "stub"
    data_mode: str = "fixtures"

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def top_k_rate(self) -> float:
        ranked = [r for r in self.results if r.root_cause_rank is not None]
        if not ranked:
            return 1.0
        return round(sum(1 for r in ranked if r.root_cause_rank <= TOP_K) / len(ranked), 4)

    @property
    def attribution_accuracy(self) -> float:
        scored = [r for r in self.results if r.cost_error is not None]
        if not scored:
            return 1.0
        return round(sum(1 for r in scored if r.cost_error <= 0.02) / len(scored), 4)

    @property
    def supported_claim_ratio(self) -> float:
        if not self.results:
            return 1.0
        return round(sum(r.supported_claim_ratio for r in self.results) / len(self.results), 4)

    @property
    def unsupported_claim_rate(self) -> float:
        if not self.results:
            return 0.0
        return round(sum(r.unsupported_claims for r in self.results) / len(self.results), 4)

    @property
    def average_latency(self) -> float:
        if not self.results:
            return 0.0
        return round(sum(r.duration_seconds for r in self.results) / len(self.results), 3)

    def report_text(self) -> str:
        lines = [
            "CloudCause scenario evaluation",
            f"  agent mode:                {self.agent_mode}",
            f"  data mode:                 {self.data_mode}",
            f"  scenarios passed:          {self.passed}/{self.total}",
            f"  root cause in top {TOP_K}:      {self.top_k_rate:.0%}",
            f"  cost attribution accuracy: {self.attribution_accuracy:.0%}",
            f"  claims backed by evidence: {self.supported_claim_ratio:.0%}",
            f"  unsupported claims/run:    {self.unsupported_claim_rate:.2f}",
            f"  average latency:           {self.average_latency:.2f}s",
            f"  model cost:                {'$0.00 (stub mode)' if self.agent_mode == 'stub' else 'see provider billing'}",
            "",
        ]
        for result in self.results:
            rank = result.root_cause_rank or "-"
            lines.append(
                f"  [{result.status:>4}] {result.scenario_id:<32} provider={result.provider:<5} "
                f"rank={rank} cost_error={result.cost_error if result.cost_error is not None else '-'} "
                f"({result.duration_seconds:.2f}s)"
            )
            for failure in result.failures:
                lines.append(f"           - {failure}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "agent_mode": self.agent_mode,
                "data_mode": self.data_mode,
                "passed": self.passed,
                "total": self.total,
                "root_cause_top_k": TOP_K,
                "root_cause_top_k_rate": self.top_k_rate,
                "cost_attribution_accuracy": self.attribution_accuracy,
                "supported_claim_ratio": self.supported_claim_ratio,
                "unsupported_claims_per_run": self.unsupported_claim_rate,
                "average_latency_seconds": self.average_latency,
                "model_cost_usd": 0.0 if self.agent_mode == "stub" else None,
            },
            "scenarios": [
                {
                    "scenario_id": result.scenario_id,
                    "provider": result.provider,
                    "status": result.status,
                    "passed": result.passed,
                    "failures": result.failures,
                    "notes": result.notes,
                    "duration_seconds": result.duration_seconds,
                    "root_cause_rank": result.root_cause_rank,
                    "cost_error": result.cost_error,
                    "supported_claim_ratio": result.supported_claim_ratio,
                    "unsupported_claims": result.unsupported_claims,
                }
                for result in self.results
            ],
        }

    def report_markdown(self) -> str:
        model_cost = "$0.00 (stub mode)" if self.agent_mode == "stub" else "See provider billing"
        lines = [
            "# CloudCause Evaluation Results",
            "",
            "| Metric | Result |",
            "| --- | ---: |",
            f"| Scenarios passed | {self.passed}/{self.total} |",
            f"| Root cause in top {TOP_K} | {self.top_k_rate:.0%} |",
            f"| Cost attribution accuracy | {self.attribution_accuracy:.0%} |",
            f"| Claims backed by evidence | {self.supported_claim_ratio:.0%} |",
            f"| Unsupported claims per run | {self.unsupported_claim_rate:.2f} |",
            f"| Average latency | {self.average_latency:.2f}s |",
            f"| Model cost | {model_cost} |",
            "",
            "## Scenarios",
            "",
            "| Scenario | Provider | Status | Rank | Cost error | Latency |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
        for result in self.results:
            rank = result.root_cause_rank or "-"
            cost_error = result.cost_error if result.cost_error is not None else "-"
            lines.append(
                f"| `{result.scenario_id}` | {result.provider} | {result.status} | "
                f"{rank} | {cost_error} | {result.duration_seconds:.2f}s |"
            )
            for failure in result.failures:
                lines.append(f"\n> **{result.scenario_id}:** {failure}")
        return "\n".join(lines) + "\n"


async def evaluate_all(settings: Settings | None = None) -> EvaluationSummary:
    settings = settings or get_settings()
    summary = EvaluationSummary(agent_mode=settings.agent_mode, data_mode=settings.data_mode)
    for spec in list_scenarios(settings.scenario_root):
        summary.results.append(await run_scenario(spec, settings))
    return summary
