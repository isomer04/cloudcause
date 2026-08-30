"""Offline regression tests for local live-AI capacity and call budgets."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from cloudcause_api import main
from cloudcause_contracts import InvestigationRequest, Settings
from cloudcause_worker_core import AgentCallBudget, AgentCallLimitExceeded


def request(*, agent_mode: str = "live") -> InvestigationRequest:
    return InvestigationRequest.model_validate(
        {
            "providers": ["aws"],
            "start_date": "2026-07-13",
            "end_date": "2026-07-19",
            "comparison_start_date": "2026-07-06",
            "comparison_end_date": "2026-07-12",
            "agent_mode": agent_mode,
        }
    )


def test_agent_call_budget_fails_before_an_extra_boundary_is_started() -> None:
    budget = AgentCallBudget(maximum=2)

    budget.reserve("model:openai:strands")
    budget.reserve("native_tool:get_candidate_evidence")

    with pytest.raises(AgentCallLimitExceeded, match="2/2"):
        budget.reserve("native_tool:record_finding")


@pytest.mark.parametrize(
    "name",
    [
        "CLOUDCAUSE_MAX_AGENT_CALLS",
        "CLOUDCAUSE_MAX_AGENT_SECONDS",
        "CLOUDCAUSE_MAX_CONCURRENT_LIVE_INVESTIGATIONS",
        "CLOUDCAUSE_LIVE_QUEUE_TIMEOUT_SECONDS",
    ],
)
def test_live_limits_fail_closed_for_non_positive_configuration(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings.from_env({name: "0"})


async def test_live_jobs_wait_for_capacity_then_fail_with_typed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = main.settings
    main.configure(
        Settings.from_env({}).with_overrides(
            max_concurrent_live_investigations=1,
            live_queue_timeout_seconds=0.01,
        )
    )
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingLink:
        async def run(self, _request, _investigation_id, _emit):
            started.set()
            await release.wait()
            return SimpleNamespace(provider_statuses=[])

    monkeypatch.setattr(main, "link", BlockingLink())
    first = main.jobs.create("inv-live-first", request())
    second = main.jobs.create("inv-live-second", request())
    first_task = asyncio.create_task(main._run_job(first.investigation_id, first.state.request))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await main._run_job(second.investigation_id, second.state.request)

        assert second.state.status == "failed"
        assert second.state.error and second.state.error.startswith("live_capacity_timeout:")
        assert second.events[-1].stage == "queue"
        assert second.events[-1].data["capacity_status"] == "timed_out"
        assert second.events[-1].data["retryable"] is True
        assert first.state.status == "running"
    finally:
        release.set()
        await first_task
        main.configure(original)


async def test_stub_jobs_bypass_live_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    original = main.settings
    main.configure(
        Settings.from_env({}).with_overrides(
            max_concurrent_live_investigations=1,
            live_queue_timeout_seconds=0.01,
        )
    )

    class ImmediateLink:
        async def run(self, _request, _investigation_id, _emit):
            return SimpleNamespace(provider_statuses=[])

    monkeypatch.setattr(main, "link", ImmediateLink())
    job = main.jobs.create("inv-stub", request(agent_mode="stub"))
    try:
        async with main.live_capacity.reserve():
            await main._run_job(job.investigation_id, job.state.request)
        assert job.state.status == "completed"
        assert not any(event.stage == "queue" for event in job.events)
    finally:
        main.configure(original)
