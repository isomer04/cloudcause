"""Govern OpenAI requests at the transport layer, not the whole agent run.

Strands and MAF each run a multi-turn tool-calling loop (``agent.invoke_async``
/ ``agent.run``) that can issue many real OpenAI requests per investigation.
Acquiring the outbound permit around that whole call would only bound
concurrent *investigations*, not concurrent *requests* -- both frameworks
accept a pre-built ``openai.AsyncOpenAI`` client, so wrapping its HTTP
transport is what actually makes every request (including ones the agent
issues internally per tool round-trip) acquire its own permit.
"""

from __future__ import annotations

import httpx
from openai import AsyncOpenAI

from .context import InvestigationContext


class _GovernedTransport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped: httpx.AsyncBaseTransport, ctx: InvestigationContext, model: str) -> None:
        self._wrapped = wrapped
        self._ctx = ctx
        self._model = model

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with self._ctx.acquire_model_permit("openai", self._model):
            return await self._wrapped.handle_async_request(request)

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def build_governed_openai_client(ctx: InvestigationContext, api_key: str, model: str) -> AsyncOpenAI:
    """An ``AsyncOpenAI`` client whose every HTTP request acquires an outbound permit.

    Callers are responsible for closing the returned client (``await
    client.close()``) once the agent run finishes, mirroring the "caller
    manages the client lifecycle" contract both frameworks document for a
    pre-built client.
    """

    transport = _GovernedTransport(httpx.AsyncHTTPTransport(), ctx, model)
    return AsyncOpenAI(
        api_key=api_key,
        http_client=httpx.AsyncClient(transport=transport),
        # The SDK retries twice by default, which would multiply against
        # `run_with_retries` (up to 3x the attempts it thinks it is making) and
        # burn deadline time in backoff it never accounted for. Retry
        # classification, backoff, and attempt counting all belong to the one
        # policy in `retry_policy` / `run_with_retries`.
        max_retries=0,
    )
