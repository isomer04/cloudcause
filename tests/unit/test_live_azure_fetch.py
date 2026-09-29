"""The Azure fetch plumbing, with the network faked (ADR 0015).

The mapper is covered by ``test_live_connectors.py`` against a recorded
response. This file covers what sits between the SDK and the mapper: the token
scope, the request body, ``nextLink`` paging, ``429`` retries, and the shape
of the failures. ``httpx.MockTransport`` plays the Query API and a stub module
plays ``azure.identity``, so nothing here needs an ``az login``.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from cloudcause_contracts import DateRange, Settings
from cloudcause_providers import LiveAzureDataProvider, LiveConnectorError, LiveModeNotConfiguredError
from cloudcause_providers.live import (
    AZURE_MANAGEMENT_SCOPE,
    AZURE_MAX_RETRY_WAIT_SECONDS,
    AZURE_QUERY_URL,
    azure_query_body,
)
from conftest import recorded

SUBSCRIPTION = "8f3c2b71-9d4e-4a5f-8c21-7b6e5d4c3a2b"
WINDOW = DateRange(start=date(2026, 9, 1), end=date(2026, 9, 3))
TOKEN = "eyJ-should-never-be-logged"


class _Token:
    token = TOKEN


class _CliCredential:
    """Stands in for ``AzureCliCredential``; records the scope it was asked for."""

    scopes: list[str] = []

    def get_token(self, scope: str) -> _Token:
        _CliCredential.scopes.append(scope)
        return _Token()


class _MissingCliCredential:
    def get_token(self, scope: str) -> _Token:
        raise RuntimeError(f"Please run 'az login' to set up an account; tenant {TOKEN}")


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("azure.identity")
    module.AzureCliCredential = _CliCredential  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "azure.identity", module)
    _CliCredential.scopes = []
    return module


def _adapter(settings: Settings, handler, subscription: str = SUBSCRIPTION) -> LiveAzureDataProvider:
    adapter = LiveAzureDataProvider(settings.with_overrides(data_mode="live", azure_subscription_id=subscription))
    adapter._http_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return adapter


def _pages() -> list[dict]:
    return recorded("azure_cost_management_query.json")["pages"]


async def test_fetch_follows_next_link_and_sends_the_recorded_request(identity, settings: Settings) -> None:
    pages = _pages()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = pages[0] if "skiptoken" not in str(request.url) else pages[1]
        return httpx.Response(200, json=page)

    adapter = _adapter(settings, handler)
    result = await adapter.get_costs([WINDOW])

    assert [r.method for r in seen] == ["POST", "POST"]
    assert str(seen[0].url) == AZURE_QUERY_URL.format(subscription=SUBSCRIPTION)
    assert "skiptoken" in str(seen[1].url), "the second call is the nextLink verbatim"
    assert json.loads(seen[0].content) == azure_query_body(WINDOW)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert _CliCredential.scopes == [AZURE_MANAGEMENT_SCOPE]
    assert len(result.items) == 4, "rows from both pages, parsed once"
    assert result.provenance.source == "azure-cost-management"
    assert result.provenance.data_through.date() == date(2026, 9, 2)


async def test_a_429_is_retried_after_the_header_says_so(identity, settings: Settings, monkeypatch) -> None:
    pages = _pages()
    pages[0]["properties"]["nextLink"] = None
    attempts: list[int] = []
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("cloudcause_providers.live.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {"code": "429"}})
        return httpx.Response(200, json=pages[0])

    result = await _adapter(settings, handler).get_costs([WINDOW])
    assert len(attempts) == 2
    assert slept == [3.0]
    assert len(result.items) == 3


async def test_a_retry_after_http_date_is_honoured(identity, settings: Settings, monkeypatch) -> None:
    pages = _pages()
    pages[0]["properties"]["nextLink"] = None
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("cloudcause_providers.live.asyncio.sleep", fake_sleep)
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    responses = [httpx.Response(429, headers={"Retry-After": when}), httpx.Response(200, json=pages[0])]

    result = await _adapter(settings, lambda request: responses.pop(0)).get_costs([WINDOW])

    assert len(slept) == 1 and 10.0 <= slept[0] <= 20.0, "the date is read, not replaced by backoff"
    assert len(result.items) == 3


async def test_a_retry_after_beyond_the_cap_fails_instead_of_stalling(
    identity, settings: Settings, monkeypatch
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("cloudcause_providers.live.asyncio.sleep", fake_sleep)
    too_long = str(int(AZURE_MAX_RETRY_WAIT_SECONDS) + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": too_long})

    with pytest.raises(LiveConnectorError) as error:
        await _adapter(settings, handler).get_costs([WINDOW])
    assert "HTTP 429" in str(error.value)
    assert slept == []


async def test_an_http_error_is_wrapped_to_status_and_class_only(identity, settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {"code": "AuthorizationFailed", "message": f"The client {TOKEN} does not have authorization"}
            },
        )

    with pytest.raises(LiveConnectorError) as error:
        await _adapter(settings, handler).get_costs([WINDOW])
    detail = str(error.value)
    assert "HTTPStatusError" in detail and "HTTP 403" in detail
    assert TOKEN not in detail and "AuthorizationFailed" not in detail
    assert error.value.__cause__ is None


async def test_a_non_object_body_is_refused(identity, settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(LiveConnectorError):
        await _adapter(settings, handler).get_costs([WINDOW])


async def test_a_204_is_an_empty_cost_window(identity, settings: Settings) -> None:
    result = await _adapter(settings, lambda request: httpx.Response(204)).get_costs([WINDOW])

    assert result.items == []
    assert result.provenance.data_through.date() == date(2026, 8, 31)


async def test_transient_server_errors_are_retried(identity, settings: Settings, monkeypatch) -> None:
    attempts: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("cloudcause_providers.live.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503)
        page = _pages()[0]
        page["properties"]["nextLink"] = None
        return httpx.Response(200, json=page)

    result = await _adapter(settings, handler).get_costs([WINDOW])

    assert len(attempts) == 2
    assert result.items


async def test_cross_origin_next_link_is_rejected_before_token_forwarding(identity, settings: Settings) -> None:
    seen: list[str] = []
    page = _pages()[0]
    page["properties"]["nextLink"] = "https://attacker.example/steal"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=page)

    with pytest.raises(LiveConnectorError, match="invalid nextLink"):
        await _adapter(settings, handler).get_costs([WINDOW])
    assert len(seen) == 1


async def test_pagination_loops_are_rejected(identity, settings: Settings) -> None:
    first_url = AZURE_QUERY_URL.format(subscription=SUBSCRIPTION)
    page = _pages()[0]
    page["properties"]["nextLink"] = first_url

    with pytest.raises(LiveConnectorError, match="pagination loop"):
        await _adapter(settings, lambda request: httpx.Response(200, json=page)).get_costs([WINDOW])


async def test_no_cli_login_names_the_command_and_nothing_else(settings: Settings, monkeypatch) -> None:
    module = types.ModuleType("azure.identity")
    module.AzureCliCredential = _MissingCliCredential  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "azure.identity", module)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must never be reached
        raise AssertionError("no request may be made without a token")

    with pytest.raises(LiveModeNotConfiguredError) as error:
        await _adapter(settings, handler).get_costs([WINDOW])
    detail = str(error.value)
    assert "az login" in detail
    assert TOKEN not in detail
    assert error.value.__cause__ is None
