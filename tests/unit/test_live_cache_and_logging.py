"""One fetch per investigation, and no SDK debug chatter (ADR 0016).

The orchestrator, the worker and the MCP cost tool each ask the adapter for
costs. Without a cache one AWS investigation is three Cost Explorer round
trips. And the cloud SDKs log request detail at DEBUG, which this module
cannot scrub because it does not emit it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

import pytest
from cloudcause_contracts import DateRange, Settings
from cloudcause_providers import (
    LiveAwsDataProvider,
    LiveAzureDataProvider,
    LiveConnectorError,
    clear_live_cache,
    mute_sdk_debug_logging,
)
from cloudcause_providers import live as live_module
from cloudcause_providers.live import SDK_LOGGERS
from conftest import recorded

WINDOW = DateRange(start=date(2026, 9, 1), end=date(2026, 9, 3))
SUBSCRIPTION = "8f3c2b71-9d4e-4a5f-8c21-7b6e5d4c3a2b"


def _azure(settings: Settings, subscription: str = SUBSCRIPTION) -> LiveAzureDataProvider:
    return LiveAzureDataProvider(settings.with_overrides(data_mode="live", azure_subscription_id=subscription))


def _document() -> dict:
    pages = recorded("azure_cost_management_query.json")["pages"]
    return live_module.merge_azure_pages(pages)


def _counting_fetch(adapter, document: dict, calls: list[str]):
    async def fetch(window: DateRange) -> dict:
        calls.append(window.label())
        return document

    adapter._fetch = fetch  # type: ignore[method-assign]


async def test_one_window_is_fetched_once_per_process(settings: Settings) -> None:
    calls: list[str] = []
    first = _azure(settings)
    _counting_fetch(first, _document(), calls)
    second = _azure(settings)  # a different adapter instance, as the worker and orchestrator build
    _counting_fetch(second, _document(), calls)

    a = await first.get_costs([WINDOW])
    b = await second.get_costs([WINDOW])
    c = await second.get_bundle([WINDOW])

    assert calls == [WINDOW.label()], "orchestrator, worker and bundle shared one fetch"
    assert len(a.items) == len(b.items) == len(c.costs.items) == 4


async def test_the_cache_is_keyed_by_window_and_identifier(settings: Settings) -> None:
    calls: list[str] = []
    adapter = _azure(settings)
    _counting_fetch(adapter, _document(), calls)
    other = _azure(settings, subscription="11111111-2222-3333-4444-555555555555")
    _counting_fetch(other, _document(), calls)

    await adapter.get_costs([WINDOW])
    await adapter.get_costs([DateRange(start=date(2026, 9, 1), end=date(2026, 9, 2))])
    await other.get_costs([WINDOW])

    assert len(calls) == 3


async def test_an_expired_entry_is_fetched_again(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    adapter = _azure(settings)
    _counting_fetch(adapter, _document(), calls)
    now = [1000.0]
    monkeypatch.setattr(live_module.clock, "monotonic", lambda: now[0])

    await adapter.get_costs([WINDOW])
    now[0] += live_module.LIVE_FETCH_TTL_SECONDS - 1
    await adapter.get_costs([WINDOW])
    now[0] += 2
    await adapter.get_costs([WINDOW])

    assert len(calls) == 2


async def test_concurrent_callers_share_one_fetch(settings: Settings) -> None:
    calls: list[str] = []
    adapter = _azure(settings)
    document = _document()

    async def slow_fetch(window: DateRange) -> dict:
        calls.append(window.label())
        await asyncio.sleep(0.01)
        return document

    adapter._fetch = slow_fetch  # type: ignore[method-assign]
    results = await asyncio.gather(*(adapter.get_costs([WINDOW]) for _ in range(5)))

    assert len(calls) == 1
    assert all(len(result.items) == 4 for result in results)


async def test_a_failure_is_not_cached(settings: Settings) -> None:
    attempts: list[int] = []
    adapter = _azure(settings)

    async def flaky(window: DateRange) -> dict:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return _document()

    adapter._fetch = flaky  # type: ignore[method-assign]
    with pytest.raises(LiveConnectorError):
        await adapter.get_costs([WINDOW])
    result = await adapter.get_costs([WINDOW])
    assert len(attempts) == 2 and len(result.items) == 4


def test_clear_live_cache_forgets_everything() -> None:
    live_module._fetch_cache[("aws", "", "x")] = (float("inf"), {})
    clear_live_cache()
    assert live_module._fetch_cache == {} and live_module._fetch_locks == {}


async def test_sdk_loggers_are_raised_to_info_on_the_first_live_fetch(
    settings: Settings, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_module, "_sdk_loggers_muted", False)
    # A developer's DEBUG root: every SDK logger inherits DEBUG until muted.
    monkeypatch.setattr(logging.getLogger(), "level", logging.DEBUG)
    for name in SDK_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    adapter = LiveAwsDataProvider(settings.with_overrides(data_mode="live"))
    raw = recorded("aws_cost_explorer.json")

    async def fetch(window: DateRange) -> dict:
        logging.getLogger("botocore").debug(
            "Making request for OperationModel(GetCostAndUsage) with params: SECRET-PARAMS"
        )
        return raw

    adapter._fetch = fetch  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG):
        await adapter.get_costs([DateRange(start=date(2026, 9, 13), end=date(2026, 9, 15))])

    assert "SECRET-PARAMS" not in caplog.text
    assert all(logging.getLogger(name).getEffectiveLevel() == logging.INFO for name in SDK_LOGGERS)


def test_muting_does_not_switch_on_loggers_that_inherit_a_quiet_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_module, "_sdk_loggers_muted", False)
    monkeypatch.setattr(logging.getLogger(), "level", logging.WARNING)
    monkeypatch.setattr(logging.getLogger("httpx"), "level", logging.NOTSET)

    mute_sdk_debug_logging()

    # Pinning httpx to INFO here would start logging every request URL.
    assert logging.getLogger("httpx").level == logging.NOTSET
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING


def test_muting_leaves_a_stricter_developer_setting_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_module, "_sdk_loggers_muted", False)
    monkeypatch.setattr(logging.getLogger("botocore"), "level", logging.ERROR)
    monkeypatch.setattr(logging.getLogger("azure"), "level", logging.DEBUG)

    mute_sdk_debug_logging()

    assert logging.getLogger("botocore").level == logging.ERROR
    assert logging.getLogger("azure").level == logging.INFO
