"""Provider investigator base class.

One class, two agent modes. ``stub`` runs the deterministic playbooks and costs
nothing. ``live`` hands the same deterministic evidence pool to the provider's
agent framework. Both produce identical contract objects.
"""

from __future__ import annotations

import asyncio
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

from .context import InvestigationContext
from .playbooks import PlaybookSpec, run_playbooks


class LiveAgentUnavailableError(RuntimeError):
    """Raised when live agent mode is requested but the framework cannot run."""


class ProviderInvestigator:
    """Base class for the Strands (AWS), MAF (Azure), and ADK (GCP) specialists."""

    provider: Provider = "aws"
    playbooks: Sequence[PlaybookSpec] = ()
    framework: str = "deterministic"

    def __init__(self, settings: Settings | None = None, knowledge: KnowledgeStore | None = None) -> None:
        self.settings = settings or get_settings()
        self.knowledge = knowledge or load_knowledge_store(
            self.settings.knowledge_root,
            review_max_age_days=self.settings.knowledge_review_max_age_days,
            focus_version=self.settings.focus_version,
        )

    # ------------------------------------------------------------------ context
    async def build_context(self, worker_request: WorkerRequest) -> InvestigationContext:
        request = worker_request.request
        data_provider = get_data_provider(
            self.provider, self.settings, request.scenario_id, request.dataset_id
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
            settings=self.settings,
        )

    # ------------------------------------------------------------------ running
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
                agent_mode=self.settings.agent_mode,
                data_mode=self.settings.data_mode,
                duration_seconds=round(time.perf_counter() - started, 3),
            )

        status: str = "ok"
        findings: list[Finding] = []
        if self.settings.agent_mode == "live":
            try:
                findings = await asyncio.wait_for(
                    self.run_live(ctx), timeout=self.settings.max_agent_seconds
                )
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
            agent_mode="live" if self.settings.agent_mode == "live" and status == "ok" else "stub",
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

    # ------------------------------------------------------------- introspection
    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "framework": self.framework,
            "agent_mode": self.settings.agent_mode,
            "data_mode": self.settings.data_mode,
            "playbooks": [spec.category for spec in self.playbooks],
            "read_only": True,
            "mutating_tools": [],
            "focus_version": self.settings.focus_version,
        }
