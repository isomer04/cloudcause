"""The GCP provider specialist, hosted inside the ADK coordinator service."""

from __future__ import annotations

from cloudcause_contracts import Finding, Provider
from cloudcause_worker_core import InvestigationContext, ProviderInvestigator

from .gcp_playbooks import GCP_PLAYBOOKS


class GcpInvestigator(ProviderInvestigator):
    provider: Provider = "gcp"
    playbooks = GCP_PLAYBOOKS
    framework = "google-adk"

    async def run_live(self, ctx: InvestigationContext) -> list[Finding]:
        from .live_agent import run_adk_investigation

        return await run_adk_investigation(ctx, self.playbooks)
