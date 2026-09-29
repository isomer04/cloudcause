from datetime import date

import pytest
from cloudcause_contracts import (
    InvestigationRequest,
    ProviderStatus,
    ProviderTask,
    Settings,
    determine_effective_agent_mode,
    resolve_agent_mode,
)


def request(**overrides) -> InvestigationRequest:
    values = {
        "providers": ["aws"],
        "start_date": date(2026, 7, 13),
        "end_date": date(2026, 7, 19),
        "comparison_start_date": date(2026, 7, 6),
        "comparison_end_date": date(2026, 7, 12),
    }
    values.update(overrides)
    return InvestigationRequest(**values)


def test_an_explicit_request_mode_always_wins_over_the_deployment_default() -> None:
    """Both paths run in one process, so no environment setting overrides a choice."""

    assert resolve_agent_mode(request(agent_mode="live"), "stub").agent_mode == "live"
    assert resolve_agent_mode(request(agent_mode="stub"), "live").agent_mode == "stub"


def test_the_deployment_default_only_fills_in_an_omitted_mode() -> None:
    assert resolve_agent_mode(request(), "live").agent_mode == "live"
    assert resolve_agent_mode(request(), "stub").agent_mode == "stub"


def test_effective_mode_requires_every_executed_provider_to_run_live() -> None:
    assert determine_effective_agent_mode("live", []) == "stub"
    assert (
        determine_effective_agent_mode(
            "live", [ProviderStatus(provider="aws", status="skipped", agent_mode="live")]
        )
        == "stub"
    )
    assert (
        determine_effective_agent_mode(
            "live", [ProviderStatus(provider="aws", status="ok", agent_mode="live")]
        )
        == "live"
    )
    assert (
        determine_effective_agent_mode(
            "live",
            [
                ProviderStatus(provider="aws", status="ok", agent_mode="live"),
                ProviderStatus(provider="gcp", status="partial", agent_mode="stub"),
            ],
        )
        == "stub"
    )


def test_a_model_key_is_what_makes_live_available_not_a_mode_setting() -> None:
    """The UI reads this to decide whether to offer the live path, so it must not
    depend on CLOUDCAUSE_AGENT_MODE."""

    base = {"CLOUDCAUSE_REPO_ROOT": "."}
    assert Settings.from_env(base).live_agents_available is False
    # A stub default with a key present still permits live runs.
    assert Settings.from_env({**base, "OPENAI_API_KEY": "sk-test"}).live_agents_available is True
    assert Settings.from_env({**base, "GOOGLE_API_KEY": "ai-test"}).live_agents_available is True
    # The placeholder shipped in .env.example is not a key.
    assert (
        Settings.from_env({**base, "OPENAI_API_KEY": "replace-me"}).live_agents_available is False
    )
    # A live default without any key cannot promise a live run.
    assert (
        Settings.from_env({**base, "CLOUDCAUSE_AGENT_MODE": "live"}).live_agents_available is False
    )


def test_a_stub_request_never_reports_live_however_the_providers_ran() -> None:
    assert (
        determine_effective_agent_mode(
            "stub", [ProviderStatus(provider="aws", status="ok", agent_mode="live")]
        )
        == "stub"
    )


def test_questions_are_bounded_before_they_cross_worker_or_prompt_boundaries() -> None:
    with pytest.raises(ValueError):
        request(question="x" * 1001)
    with pytest.raises(ValueError):
        ProviderTask(provider="aws", question="x" * 1001)
