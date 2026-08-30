"""Provider investigator base class.

One class, two agent modes. ``stub`` runs the deterministic playbooks and costs
nothing. ``live`` hands the same deterministic evidence pool to the provider's
agent framework. Both produce identical contract objects.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

from cloudcause_contracts import (
    Finding,
    Provider,
    Settings,
    WorkerRequest,
    WorkerResponse,
    get_settings,
)
from cloudcause_knowledge import KnowledgeStore, load_knowledge_store
from cloudcause_providers import get_data_provider
from cloudcause_rate_limit import AIRequestGovernor, build_rate_limiter, run_with_retries

from .context import InvestigationContext
from .live_limits import current_agent_call_budget
from .playbooks import PlaybookSpec, run_playbooks
from .retry_policy import classify_live_agent_error

logger = logging.getLogger("cloudcause.rate_limit")


class LiveAgentUnavailableError(RuntimeError):
    """Raised when live agent mode is requested but the framework cannot run."""


class ProviderInvestigator:
    """Base class for the Strands (AWS), MAF (Azure), and ADK (GCP) specialists."""

    provider: Provider = "aws"
    playbooks: Sequence[PlaybookSpec] = ()
    framework: str = "deterministic"

    def __init__(
        self,
        settings: Settings | None = None,
        knowledge: KnowledgeStore | None = None,
        governor: AIRequestGovernor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.knowledge = knowledge or load_knowledge_store(
            self.settings.knowledge_root,
            review_max_age_days=self.settings.knowledge_review_max_age_days,
            focus_version=self.settings.focus_version,
        )
        # Callers that construct several investigators in one process (the
        # in-process worker topology) should pass a shared governor so, e.g.,
        # the AWS and Azure agents draw from the same OpenAI bucket.
        self.governor = governor or AIRequestGovernor(build_rate_limiter(self.settings), self.settings)

    async def build_context(self, worker_request: WorkerRequest) -> InvestigationContext:
        request = worker_request.request
        run_settings = self.settings.with_overrides(agent_mode=request.agent_mode)
        if request.dataset_id and (
            run_settings.orchestrator_mode == "http" or run_settings.worker_mode == "http"
        ):
            # A distributed upload is SQL-backed. Resolve its sealed snapshot
            # off-loop, but retain the cheap in-memory path for the default
            # single-process topology.
            data_provider = await asyncio.to_thread(
                get_data_provider,
                self.provider,
                run_settings,
                request.scenario_id,
                request.dataset_id,
            )
        else:
            data_provider = get_data_provider(
                self.provider, run_settings, request.scenario_id, request.dataset_id
            )
        bundle = await data_provider.get_bundle([request.current_period, request.baseline_period])
        return InvestigationContext(
            investigation_id=worker_request.investigation_id,
            provider=self.provider,
            request=request,
            task=worker_request.task,
            candidates=list(worker_request.candidates),
            bundle=bundle,
            knowledge=self.knowledge,
            settings=run_settings,
            agent_call_budget=current_agent_call_budget(),
            governor=self.governor,
        )

    async def investigate(self, worker_request: WorkerRequest) -> WorkerResponse:
        started = time.perf_counter()
        try:
            ctx = await self.build_context(worker_request)
        except Exception as error:  # noqa: BLE001 - reported to the orchestrator
            return WorkerResponse(
                investigation_id=worker_request.investigation_id,
                provider=self.provider,
                status="failed",
                message=f"{type(error).__name__}: {error}",
                agent_mode="stub",
                data_mode=self.settings.data_mode,
                duration_seconds=round(time.perf_counter() - started, 3),
            )

        status: str = "ok"
        findings: list[Finding] = []
        if worker_request.request.live_agents_requested:
            loop = asyncio.get_running_loop()
            overall_deadline = loop.time() + self.settings.max_agent_seconds

            async def attempt() -> list[Finding]:
                # Each attempt gets the deadline's *remaining* time, not the
                # full budget again, so N attempts share one bounded window
                # instead of each getting their own max_agent_seconds.
                remaining = max(0.01, overall_deadline - loop.time())
                return await asyncio.wait_for(self.run_live(ctx), timeout=remaining)

            def _log_retry(attempt_number: int, delay: float, error: BaseException) -> None:
                # Never log prompt content or credentials: attempt number,
                # delay, provider, model, and investigation ID only.
                logger.info(
                    "retry provider=%s framework=%s investigation=%s attempt=%d delay=%.2fs error=%s",
                    self.provider,
                    self.framework,
                    ctx.investigation_id,
                    attempt_number,
                    delay,
                    type(error).__name__,
                )

            try:
                findings = await run_with_retries(
                    attempt,
                    classify=classify_live_agent_error,
                    attempts=self.settings.ai_retry_attempts,
                    base_seconds=self.settings.ai_retry_base_seconds,
                    max_seconds=self.settings.ai_retry_max_seconds,
                    deadline_seconds=self.settings.max_agent_seconds,
                    on_retry=_log_retry,
                )
                # Safety net for a retry that succeeds without re-processing
                # every candidate the earlier, failed attempt already found:
                # merge in anything recorded on this shared context that the
                # final attempt's own (freshly rebuilt) toolset doesn't have.
                present = {finding.candidate_id for finding in findings}
                findings = findings + [
                    finding
                    for candidate_id, finding in ctx.recorded_findings.items()
                    if candidate_id not in present
                ]
            except Exception as error:  # noqa: BLE001 - live mode must never break a run
                ctx.warnings.append(
                    f"live {self.framework} agent unavailable ({type(error).__name__}: {error}); "
                    "fell back to the deterministic playbooks"
                )
                status = "partial"
                findings = await self.run_stub(ctx)
        else:
            findings = await self.run_stub(ctx)

        findings.sort(key=lambda finding: finding.actual_cost_increase, reverse=True)
        return WorkerResponse(
            investigation_id=worker_request.investigation_id,
            provider=self.provider,
            status=status,  # type: ignore[arg-type]
            findings=findings,
            sources=ctx.bundle.sources,
            applied_rule_ids=[citation.rule_id for citation in ctx.citations],
            warnings=ctx.warnings,
            available_source_types=sorted(ctx.bundle.available_source_types()),
            agent_mode="live" if worker_request.request.live_agents_requested and status == "ok" else "stub",
            data_mode=self.settings.data_mode,
            data_origin=ctx.bundle.origin(),
            duration_seconds=round(time.perf_counter() - started, 3),
            message=f"{self.framework} investigator completed with {len(findings)} finding(s)",
        )

    async def run_stub(self, ctx: InvestigationContext) -> list[Finding]:
        return run_playbooks(ctx, self.playbooks)

    async def run_live(self, ctx: InvestigationContext) -> list[Finding]:
        raise LiveAgentUnavailableError(
            f"{self.framework} live agent is not implemented for provider {self.provider}"
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "framework": self.framework,
            "default_agent_mode": self.settings.agent_mode,
            "live_agents_available": self.settings.live_agents_available,
            "data_mode": self.settings.data_mode,
            "playbooks": [spec.category for spec in self.playbooks],
            "read_only": True,
            "mutating_tools": [],
            "focus_version": self.settings.focus_version,
        }
