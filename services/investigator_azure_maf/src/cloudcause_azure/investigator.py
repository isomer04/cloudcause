"""The Azure provider specialist."""

from __future__ import annotations

from cloudcause_contracts import Finding, Provider
from cloudcause_worker_core import InvestigationContext, ProviderInvestigator

from .playbooks import AZURE_PLAYBOOKS


class AzureInvestigator(ProviderInvestigator):
    provider: Provider = "azure"
    playbooks = AZURE_PLAYBOOKS
    framework = "microsoft-agent-framework"

    async def run_live(self, ctx: InvestigationContext) -> list[Finding]:
        from .live_agent import run_maf_investigation

        return await run_maf_investigation(ctx, self.playbooks)
