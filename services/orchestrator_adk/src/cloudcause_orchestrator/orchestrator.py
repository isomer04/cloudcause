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
    determine_effective_agent_mode,
    get_settings,
)
from cloudcause_evidence import missing_cause_sources, rank_findings, validate_findings
from cloudcause_focus import filter_records, to_focus_records
from cloudcause_knowledge import KnowledgeStore, build_knowledge_provenance, load_knowledge_store
from cloudcause_providers import get_data_provider
from cloudcause_worker_core import AgentCallBudget, bind_agent_call_budget, reset_agent_call_budget

from .planner import build_plan
from .workers import WorkerClient, build_worker_clients


class ProviderDataUnavailableError(RuntimeError):
    """No selected provider could supply cost data, so there is nothing to compare."""


class Emitter(Protocol):
    def __call__(self, stage: str, message: str = "", *, provider: Provider | None = None, **data: Any) -> Any: ...


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

    async def health(self) -> dict[str, Any]:
        provider_health = {provider: await client.health() for provider, client in self.workers.items()}
        return {
            "status": "ok",
            "role": "adk-coordinator+gcp-specialist",
            "default_agent_mode": self.settings.agent_mode,
            "agent_mode_selection": "per_investigation",
            "supported_agent_modes": ["live", "stub"],
            "live_agents_available": self.settings.live_agents_available,
            "data_mode": self.settings.data_mode,
            "worker_mode": self.settings.worker_mode,
            "focus_version": self.settings.focus_version,
            "knowledge_rules": len(self.knowledge.rules),
            "workers": provider_health,
        }

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
                if request.dataset_id and (
                    self.settings.orchestrator_mode == "http" or self.settings.worker_mode == "http"
                ):
                    # SQL-backed uploads are assembled synchronously.  Keep that
                    # short database read out of the coordinator's event loop;
                    # the all-in-process topology retains its in-memory path.
                    adapter = await asyncio.to_thread(
                        get_data_provider,
                        provider,
                        self.settings,
                        request.scenario_id,
                        request.dataset_id,
                    )
                else:
                    adapter = get_data_provider(provider, self.settings, request.scenario_id, request.dataset_id)
                costs = await adapter.get_costs(periods)
                resources = await adapter.get_resources()
            except Exception as error:  # noqa: BLE001 - one provider must not fail the run
                reason = f"{type(error).__name__}: {error}"
                unavailable.append(ProviderStatus(provider=provider, status="skipped", message=reason))
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
                f"{provider}: {len(selected)} cost rows normalized to FOCUS {self.settings.focus_version}",
                provider=provider,
                rows=len(selected),
                data_through=costs.provenance.data_through.isoformat(),
                origin=costs.provenance.origin,
            )
        return records, sources, known_ids, unavailable

    async def run(
        self,
        request: InvestigationRequest,
        investigation_id: str | None = None,
        emit: Emitter | None = None,
    ) -> InvestigationReport:
        budget_token = None
        if request.live_agents_requested:
            budget_token = bind_agent_call_budget(AgentCallBudget(self.settings.max_agent_calls))
        try:
            return await self._run(request, investigation_id=investigation_id, emit=emit)
        finally:
            if budget_token is not None:
                reset_agent_call_budget(budget_token)

    async def _run(
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
        warnings: list[str] = [f"[{status.provider}] skipped: {status.message}" for status in unavailable]
        available_source_types: dict[Provider, set[str]] = {}
        for response in responses:
            findings.extend(response.findings)
            warnings.extend(f"[{response.provider}] {warning}" for warning in response.warnings)
            sources.extend(response.sources)
            if response.status in ("ok", "partial"):
                available_source_types[response.provider] = set(response.available_source_types)
            data_through = min(source.data_through for source in response.sources) if response.sources else None
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
            warnings.append(f"{len(validation.dropped)} finding(s) were dropped because evidence did not support them")
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
            f"Attributed {reconciliation.attributed_change:+,.2f} of {reconciliation.total_change:+,.2f}",
            within_tolerance=reconciliation.within_tolerance,
        )

        citations: list[RuleCitation] = [rule for finding in published for rule in finding.applied_rules]
        knowledge_provenance = build_knowledge_provenance(self.knowledge, citations)
        if knowledge_provenance.stale_rule_ids:
            warnings.append(
                "Billing knowledge review is overdue for: " + ", ".join(knowledge_provenance.stale_rule_ids)
            )

        currency = comparison.providers[0].currency if comparison.providers else "USD"
        unique_sources = _dedupe_sources(sources)
        effective_agent_mode = determine_effective_agent_mode(request.agent_mode, statuses)
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
            agent_mode=effective_agent_mode,
        )
        # The published executive summary is deterministic evidence-backed output.
        # Hosted agents may investigate candidates, but never synthesize report prose.
        report.summary = _deterministic_summary(report, validation.supported_claim_ratio)
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


def _movement_sentence(report: InvestigationReport) -> str:
    """State the direction of the change in a word, then the size.

    The old wording was ``f"Spending rose {change:+,.2f}"``, which printed
    "Spending rose -50.00 USD" whenever spending had actually fallen. The verb
    is chosen from the sign now, and the figure is printed unsigned because
    the verb already carries the direction.
    """

    change = report.total_absolute_change
    percent_change = report.total_percent_change
    if percent_change is None:
        return f"Spending of {abs(change):,.2f} {report.currency} was new this period."
    if change == 0:
        return "Spending held level against the length-adjusted baseline."
    verb = "rose" if change > 0 else "fell"
    return (
        f"Spending {verb} {abs(change):,.2f} {report.currency} "
        f"({abs(percent_change):.1f}%) against the length-adjusted baseline."
    )


def _deterministic_summary(report: InvestigationReport, supported_ratio: float) -> str:
    """The executive summary: what moved, and how far to trust the answer.

    It deliberately does not rank the causes. Every surface that shows this
    summary - the web report, the PDF export, the markdown export - renders
    the findings immediately beneath it, ranked, with the same figures in the
    product's own vocabulary. Repeating them here said the same thing twice
    and said it worse, in raw contract slugs ("gcp api_key_abuse
    (+161.60 USD, confidence 0.86)").
    """

    if not report.findings:
        lines = [
            f"No evidence-backed cause was found for the {report.total_absolute_change:+,.2f} "
            f"{report.currency} change between {report.baseline_period.label()} and "
            f"{report.current_period.label()}."
        ]
        if report.warnings:
            lines.append(f"{len(report.warnings)} warning(s) were raised, including data coverage.")
        return " ".join(lines)

    attributed = report.reconciliation.attributed_change if report.reconciliation else 0.0
    share = f"{(attributed / report.total_absolute_change) * 100:.0f}%" if report.total_absolute_change else "0%"
    count = len(report.findings)
    return (
        f"{_movement_sentence(report)} "
        f"{count} evidence-backed finding{'s' if count != 1 else ''} "
        f"account{'s' if count == 1 else ''} for {share} of the change, and "
        f"{supported_ratio:.0%} of published findings cite both evidence and a versioned "
        "billing rule. Every recommendation needs human approval; CloudCause changed nothing."
    )
