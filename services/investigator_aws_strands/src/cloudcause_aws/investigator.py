"""The AWS provider specialist."""

from __future__ import annotations

from cloudcause_contracts import Finding, Provider
from cloudcause_worker_core import InvestigationContext, ProviderInvestigator

from .playbooks import AWS_PLAYBOOKS


class AwsInvestigator(ProviderInvestigator):
    provider: Provider = "aws"
    playbooks = AWS_PLAYBOOKS
    framework = "aws-strands"

    async def run_live(self, ctx: InvestigationContext) -> list[Finding]:
        from .live_agent import run_strands_investigation

        return await run_strands_investigation(ctx, self.playbooks)
