"""Snapshot files are removed when live-agent setup fails before a session starts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from cloudcause_aws import live_agent as aws_live
from cloudcause_orchestrator import live_agent as adk_live


def snapshot_payload(path: Path) -> dict[str, object]:
    path.write_text("sensitive billing data", encoding="utf-8")
    return {"command": "python", "args": [], "env": {"CLOUDCAUSE_DATASET_SNAPSHOT": str(path)}}


def context() -> SimpleNamespace:
    period = SimpleNamespace(label=lambda: "2026-07-13..2026-07-19")
    return SimpleNamespace(
        investigation_id="inv-cleanup",
        request=SimpleNamespace(
            scenario_id="default",
            dataset_id="dataset-1",
            question="Why did spending increase?",
        ),
        task=SimpleNamespace(question="Explain the measured spending increase."),
        current_period=period,
        baseline_period=period,
        settings=SimpleNamespace(openai_model="test-model", gemini_model="test-model"),
    )


async def test_strands_client_construction_failure_removes_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "aws-snapshot.json"
    payload = snapshot_payload(snapshot)

    class Toolset:
        findings: list[object] = []

        def __init__(self, *_args):
            pass

        def as_functions(self) -> list[object]:
            return []

    class FailingMCPClient:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("MCP setup failed")

    import strands.tools.mcp

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(aws_live, "NativeToolset", Toolset)
    monkeypatch.setattr(aws_live, "operational_server_params", lambda *_args, **_kwargs: payload)
    monkeypatch.setattr(strands.tools.mcp, "MCPClient", FailingMCPClient)

    with pytest.raises(RuntimeError, match="MCP setup failed"):
        await aws_live.run_strands_investigation(context(), [])
    assert not snapshot.exists()


def test_adk_toolset_assembly_failure_removes_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "adk-assembly-snapshot.json"
    payload = snapshot_payload(snapshot)
    monkeypatch.setattr(adk_live, "operational_server_params", lambda *_args, **_kwargs: payload)

    def fail_knowledge() -> dict[str, object]:
        raise RuntimeError("knowledge setup failed")

    monkeypatch.setattr(adk_live, "knowledge_server_params", fail_knowledge)

    with pytest.raises(RuntimeError, match="knowledge setup failed"):
        adk_live._mcp_toolsets("default", "dataset-1")
    assert not snapshot.exists()


async def test_adk_agent_construction_failure_removes_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "adk-agent-snapshot.json"
    payload = snapshot_payload(snapshot)

    class FailingAgent:
        def __init__(self, **_kwargs):
            raise RuntimeError("agent setup failed")

    class Toolset:
        findings: list[object] = []

        def __init__(self, *_args):
            pass

        def as_functions(self) -> list[object]:
            return []

    monkeypatch.setattr(adk_live, "_require_adk", lambda: (FailingAgent, object, lambda **_kwargs: None))
    monkeypatch.setattr(adk_live, "NativeToolset", Toolset)
    monkeypatch.setattr(adk_live, "_mcp_toolsets", lambda *_args: ([], [payload]))

    with pytest.raises(RuntimeError, match="agent setup failed"):
        await adk_live.run_adk_investigation(context(), [])
    assert not snapshot.exists()


async def test_adk_releases_the_model_permit_when_a_call_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled Gemini call reaches neither the after nor the error callback."""

    active = 0

    @asynccontextmanager
    async def acquire_model_permit(_provider: str, _model: str):
        nonlocal active
        active += 1
        try:
            yield
        finally:
            active -= 1

    ctx = context()
    ctx.acquire_model_permit = acquire_model_permit
    ctx.reserve_agent_call = lambda _label: None

    captured: dict[str, object] = {}

    class Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class Runner:
        def __init__(self, **_kwargs):
            pass

        async def run_debug(self, *_args, **_kwargs):
            # ADK's own boundary: the permit is taken, then the engine's
            # `asyncio.wait_for` cancels us mid-request. `CancelledError` is a
            # BaseException, so `on_model_error_callback` never sees it.
            await captured["before_model_callback"](None, None)
            assert active == 1
            raise asyncio.CancelledError

    class Toolset:
        findings: list[object] = []

        def __init__(self, *_args):
            pass

        def as_functions(self) -> list[object]:
            return []

    monkeypatch.setattr(adk_live, "_require_adk", lambda: (Agent, Runner, lambda **_kwargs: None))
    monkeypatch.setattr(adk_live, "NativeToolset", Toolset)
    monkeypatch.setattr(adk_live, "_mcp_toolsets", lambda *_args: ([], []))

    with pytest.raises(asyncio.CancelledError):
        await adk_live.run_adk_investigation(ctx, [])
    # Deterministically, on the way out -- not whenever the callback closure
    # happens to be garbage collected.
    assert active == 0
