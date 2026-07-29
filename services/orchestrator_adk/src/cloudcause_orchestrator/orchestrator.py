"""The ADK coordinator.

Fixed order of operations: normalize, measure deterministically,
plan, run the provider specialists concurrently, validate the evidence, reconcile
the money, then rank and report. Read-only throughout.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Protocol

from cloudcause_anomaly import compare_periods, reconcile_findings
from cloudcause_contracts import (
    CostRecord,
    DataOrigin,
    Finding,
    InvestigationReport,
    InvestigationRequest,
    PeriodComparison,
    Provenance,
    Provider,
    ProviderStatus,
    RuleCitation,
    Settings,
    WorkerRequest,
    WorkerResponse,
    get_settings,
)
from cloudcause_evidence import missing_cause_sources, rank_findings, validate_findings
from cloudcause_focus import filter_records, to_focus_records
from cloudcause_knowledge import KnowledgeStore, build_knowledge_provenance, load_knowledge_store
from cloudcause_providers import get_data_provider

from .planner import build_plan
from .workers import WorkerClient, build_worker_clients


class ProviderDataUnavailableError(RuntimeError):
    """No selected provider could supply cost data, so there is nothing to compare."""


class Emitter(Protocol):
    def __call__(
        self, stage: str, message: str = "", *, provider: Provider | None = None, **data: Any
    ) -> Any: ...


def _noop(stage: str, message: str = "", *, provider: Provider | None = None, **data: Any) -> None:
    return None


class Orchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        knowledge: KnowledgeStore | None = None,
        workers: dict[Provider, WorkerClient] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.knowledge = knowledge or load_knowledge_store(
            self.settings.knowledge_root,
            review_max_age_days=self.settings.knowledge_review_max_age_days,
            focus_version=self.settings.focus_version,
        )
        self.workers = workers or build_worker_clients(self.settings, self.knowledge)

    # ------------------------------------------------------------------ health
    async def health(self) -> dict[str, Any]:
        provider_health = {
            provider: await client.health() for provider, client in self.workers.items()
        }
        return {
            "status": "ok",
            "role": "adk-coordinator+gcp-specialist",
            "agent_mode": self.settings.agent_mode,
            "data_mode": self.settings.data_mode,
            "worker_mode": self.settings.worker_mode,
            "focus_version": self.settings.focus_version,
            "knowledge_rules": len(self.knowledge.rules),
            "workers": provider_health,
        }

    # --------------------------------------------------------------- normalize
    async def _load_costs(
        self, request: InvestigationRequest, emit: Emitter
    ) -> tuple[list[CostRecord], list[Provenance], dict[Provider, set[str]], list[ProviderStatus]]:
        """Normalize every selected provider, skipping the ones with no data source.

        A provider whose adapter cannot serve this request (a scenario that does
        not describe it, live mode without a connector) is reported as skipped and
        left out of the investigation plan. One unavailable provider must not fail
        the run, the same rule the worker layer already follows.
        """

        records: list[CostRecord] = []
        sources: list[Provenance] = []
        known_ids: dict[Provider, set[str]] = {}
        unavailable: list[ProviderStatus] = []
        periods = [request.current_period, request.baseline_period]
        for provider in request.providers:
            try:
                adapter = get_data_provider(
                    provider, self.settings, request.scenario_id, request.dataset_id
                )
                costs = await adapter.get_costs(periods)
                resources = await adapter.get_resources()
            except Exception as error:  # noqa: BLE001 - one provider must not fail the run
                reason = f"{type(error).__name__}: {error}"
                unavailable.append(
                    ProviderStatus(provider=provider, status="skipped", message=reason)
                )
                emit(
                    "normalize",
                    f"{provider}: skipped, no cost data for this request ({reason})",
                    provider=provider,
                    skipped=True,
                )
                continue
            selected = filter_records(
                costs.items,
                periods=periods,
                providers=[provider],
                account_ids=request.account_ids,
            )
            records.extend(selected)
            sources.append(costs.provenance)
            ids = {record.resource_id for record in costs.items if record.resource_id}
            ids.update(resource.resource_id for resource in resources.items)
            known_ids[provider] = ids
            emit(
                "normalize",
                f"{provider}: {len(selected)} cost rows normalized to FOCUS "
                f"{self.settings.focus_version}",
                provider=provider,
                rows=len(selected),
                data_through=costs.provenance.data_through.isoformat(),
                origin=costs.provenance.origin,
            )
        return records, sources, known_ids, unavailable

    # ----------------------------------------------------------------- running
    async def run(
        self,
        request: InvestigationRequest,
        investigation_id: str | None = None,
        emit: Emitter | None = None,
    ) -> InvestigationReport:
        emit = emit or _noop
        investigation_id = investigation_id or f"inv-{uuid.uuid4().hex[:12]}"

        emit("normalize", "Loading and normalizing provider cost data", status="started")
        records, sources, known_ids, unavailable = await self._load_costs(request, emit)
        skipped_providers = {status.provider for status in unavailable}
        active_providers: list[Provider] = [
            provider for provider in request.providers if provider not in skipped_providers
        ]
        if not active_providers:
            raise ProviderDataUnavailableError(
                "no cost data for any selected provider: "
                + "; ".join(f"{status.provider}: {status.message}" for status in unavailable)
            )
        focus_records = to_focus_records(records, self.settings.focus_version)

        emit(
            "analyze",
            "Comparing periods deterministically",
            focus_rows=len(focus_records),
        )
        comparison: PeriodComparison = compare_periods(
            records,
            active_providers,
            request.current_period,
            request.baseline_period,
            self.settings.analytics,
        )
        emit(
            "analyze",
            f"Total change {comparison.total_absolute_change:+,.2f} across "
            f"{len(active_providers)} provider(s); "
            f"{len(comparison.all_candidates())} material candidate(s)",
            total_change=comparison.total_absolute_change,
            candidates=len(comparison.all_candidates()),
        )

        plan = build_plan(investigation_id, request, comparison, providers=active_providers)
        emit("plan", plan.deterministic_summary, tasks=[task.provider for task in plan.tasks])

        responses = await self._investigate(investigation_id, request, plan, comparison, emit)

        findings: list[Finding] = []
        statuses: list[ProviderStatus] = list(unavailable)
        warnings: list[str] = [
            f"[{status.provider}] skipped: {status.message}" for status in unavailable
        ]
        available_source_types: dict[Provider, set[str]] = {}
        for response in responses:
            findings.extend(response.findings)
            warnings.extend(f"[{response.provider}] {warning}" for warning in response.warnings)
            sources.extend(response.sources)
            if response.status in ("ok", "partial"):
                available_source_types[response.provider] = set(response.available_source_types)
            data_through = (
                min(source.data_through for source in response.sources) if response.sources else None
            )
            statuses.append(
                ProviderStatus(
                    provider=response.provider,
                    status=response.status,
                    message=response.message,
                    data_through=data_through,
                    origin=response.data_origin,
                    finding_count=len(response.findings),
                    duration_seconds=response.duration_seconds,
                    agent_mode=response.agent_mode,
                )
            )

        emit("validate", f"Validating {len(findings)} finding(s) against the evidence")
        validation = validate_findings(
            findings,
            known_resource_ids=known_ids,
            comparison=comparison,
            available_source_types=available_source_types,
        )
        published = rank_findings(validation.findings)
        if validation.dropped:
            warnings.append(
                f"{len(validation.dropped)} finding(s) were dropped because evidence did not "
                "support them"
            )
        for provider in sorted(available_source_types):
            missing = missing_cause_sources(available_source_types[provider])
            if missing:
                warnings.append(
                    f"[{provider}] no {', '.join(missing)} data was supplied, so a cost change can "
                    "be measured but its mechanism cannot be confirmed. Any one of those sources "
                    "for the same period would raise a finding above an unexplained increase."
                )

        reconciliation = reconcile_findings(
            published,
            comparison.total_absolute_change,
            self.settings.analytics.reconciliation_tolerance,
        )
        if not reconciliation.within_tolerance:
            warnings.append(
                f"{reconciliation.unattributed_change:+,.2f} of the total change is unattributed, "
                f"outside the {reconciliation.tolerance:.0%} tolerance"
            )
        emit(
            "reconcile",
            f"Attributed {reconciliation.attributed_change:+,.2f} of "
            f"{reconciliation.total_change:+,.2f}",
            within_tolerance=reconciliation.within_tolerance,
        )

        citations: list[RuleCitation] = [
            rule for finding in published for rule in finding.applied_rules
        ]
        knowledge_provenance = build_knowledge_provenance(self.knowledge, citations)
        if knowledge_provenance.stale_rule_ids:
            warnings.append(
                "Billing knowledge review is overdue for: "
                + ", ".join(knowledge_provenance.stale_rule_ids)
            )

        currency = comparison.providers[0].currency if comparison.providers else "USD"
        unique_sources = _dedupe_sources(sources)
        report = InvestigationReport(
            investigation_id=investigation_id,
            question=request.question,
            request=request,
            plan=plan,
            current_period=request.current_period,
            baseline_period=request.baseline_period,
            total_current_cost=comparison.total_current_cost,
            total_baseline_cost=comparison.total_baseline_cost,
            total_absolute_change=comparison.total_absolute_change,
            total_percent_change=comparison.total_percent_change,
            currency=currency,
            comparison=comparison,
            findings=published,
            provider_statuses=statuses,
            reconciliation=reconciliation,
            validation_issues=validation.issues,
            warnings=warnings,
            sources=unique_sources,
            knowledge=knowledge_provenance,
            data_mode=self.settings.data_mode,
            data_origin=_report_origin(unique_sources),
            agent_mode=self.settings.agent_mode,
        )
        report.summary = await self._summarize(report, validation.supported_claim_ratio)
        emit(
            "report",
            "Investigation complete",
            status="completed",
            findings=len(published),
            supported_claim_ratio=validation.supported_claim_ratio,
        )
        return report

    async def _investigate(
        self,
        investigation_id: str,
        request: InvestigationRequest,
        plan,
        comparison: PeriodComparison,
        emit: Emitter,
    ) -> list[WorkerResponse]:
        async def call(task) -> WorkerResponse:
            client = self.workers.get(task.provider)
            if client is None:
                return WorkerResponse(
                    investigation_id=investigation_id,
                    provider=task.provider,
                    status="skipped",
                    message=f"no worker configured for {task.provider}",
                )
            emit("investigate", task.question, provider=task.provider, status="started")
            worker_request = WorkerRequest(
                investigation_id=investigation_id,
                provider=task.provider,
                request=request,
                task=task,
                candidates=comparison.candidates_for(task.provider),
            )
            try:
                response = await client.investigate(worker_request)
            except Exception as error:  # noqa: BLE001 - one worker must not fail the run
                response = WorkerResponse(
                    investigation_id=investigation_id,
                    provider=task.provider,
                    status="failed",
                    message=f"{type(error).__name__}: {error}",
                )
            emit(
                "investigate",
                f"{task.provider}: {response.status}, {len(response.findings)} finding(s)",
                provider=task.provider,
                status="completed" if response.status in ("ok", "partial") else "failed",
                findings=len(response.findings),
            )
            return response

        return list(await asyncio.gather(*(call(task) for task in plan.tasks)))

    async def _summarize(self, report: InvestigationReport, supported_ratio: float) -> str:
        deterministic = _deterministic_summary(report, supported_ratio)
        if self.settings.agent_mode != "live":
            return deterministic
        try:
            from .live_agent import synthesize_summary

            return await asyncio.wait_for(
                synthesize_summary(report, deterministic, self.settings),
                timeout=self.settings.max_agent_seconds,
            )
        except Exception as error:  # noqa: BLE001 - summary is never worth failing a run
            report.warnings.append(
                f"live ADK summary unavailable ({type(error).__name__}: {error}); "
                "the deterministic summary was used"
            )
            return deterministic


def _dedupe_sources(sources: list[Provenance]) -> list[Provenance]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Provenance] = []
    for source in sources:
        key = (source.provider, source.source, source.data_through.isoformat())
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    return unique


def _report_origin(sources: list[Provenance]) -> DataOrigin:
    """The least-verified origin in the report, so a mixed run never reads as live."""

    origins = {source.origin for source in sources}
    for candidate in ("upload", "fixture", "live"):
        if candidate in origins:
            return candidate  # type: ignore[return-value]
    return "fixture"


def _deterministic_summary(report: InvestigationReport, supported_ratio: float) -> str:
    if not report.findings:
        lines = [
            f"No evidence-backed cause was found for the {report.total_absolute_change:+,.2f} "
            f"{report.currency} change between {report.baseline_period.label()} and "
            f"{report.current_period.label()}."
        ]
        if report.warnings:
            lines.append(f"{len(report.warnings)} warning(s) were raised, including data coverage.")
        return " ".join(lines)

    top = report.findings[:3]
    causes = "; ".join(
        f"{finding.provider} {finding.category} ({finding.actual_cost_increase:+,.2f} "
        f"{report.currency}, confidence {finding.confidence:.2f})"
        for finding in top
    )
    attributed = report.reconciliation.attributed_change if report.reconciliation else 0.0
    share = (
        f"{(attributed / report.total_absolute_change) * 100:.0f}%"
        if report.total_absolute_change
        else "0%"
    )
    return (
        f"Spending rose {report.total_absolute_change:+,.2f} {report.currency} "
        f"({report.total_percent_change:+.1f}% versus the length-adjusted baseline). "
        f"Ranked causes: {causes}. Evidence-backed findings account for {share} of the change, "
        f"and {supported_ratio:.0%} of published findings cite both evidence and a versioned "
        "billing rule. Every recommendation needs human approval; CloudCause changed nothing."
    ) if report.total_percent_change is not None else (
        f"Spending rose {report.total_absolute_change:+,.2f} {report.currency}. "
        f"Ranked causes: {causes}. Evidence-backed findings account for {share} of the change."
    )
