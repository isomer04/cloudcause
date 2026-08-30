"""The governed OpenAI client leaves retry policy to run_with_retries."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from cloudcause_worker_core import governed_openai
from openai import InternalServerError


def _ctx() -> SimpleNamespace:
    @asynccontextmanager
    async def acquire_model_permit(_provider: str, _model: str):
        yield

    return SimpleNamespace(acquire_model_permit=acquire_model_permit)


async def test_one_failed_request_makes_exactly_one_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's own retries would multiply against the outer retry policy."""

    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500, json={"error": {"message": "server error"}})

    monkeypatch.setattr(
        governed_openai.httpx, "AsyncHTTPTransport", lambda *_a, **_kw: httpx.MockTransport(handler)
    )
    client = governed_openai.build_governed_openai_client(_ctx(), "test-key", "gpt-4.1-mini")
    try:
        with pytest.raises(InternalServerError):
            await client.chat.completions.create(
                model="gpt-4.1-mini", messages=[{"role": "user", "content": "hi"}]
            )
    finally:
        await client.close()

    assert requests == 1
