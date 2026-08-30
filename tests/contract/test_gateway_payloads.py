"""Shared gateway payload models must preserve the public v1 envelope."""

from __future__ import annotations

import pytest
from cloudcause_contracts import GatewayHealth
from pydantic import ValidationError


def test_gateway_health_keeps_stable_capabilities_typed_and_diagnostics_flexible() -> None:
    health = GatewayHealth.model_validate(
        {
            "status": "ok",
            "contract_version": "v1",
            "data_mode": "fixtures",
            "default_agent_mode": "stub",
            "agent_mode_selection": "per_investigation",
            "supported_agent_modes": ["live", "stub"],
            "live_agents_available": False,
            "orchestrator": {"transport": "inprocess", "workers": {"aws": {"status": "ok"}}},
            "history": {"backend": "memory", "durable": False, "retention": 50},
            "datasets": {"enabled": False, "reason": "uploads disabled"},
            "rate_limiter": {"backend": "memory", "admission_enabled": True},
            "read_only": True,
        }
    )

    assert health.agent_mode_selection == "per_investigation"
    assert health.orchestrator["workers"]["aws"] == {"status": "ok"}


def test_gateway_health_requires_the_per_investigation_selection_signal() -> None:
    with pytest.raises(ValidationError, match="agent_mode_selection"):
        GatewayHealth.model_validate(
            {
                "status": "ok",
                "contract_version": "v1",
                "data_mode": "fixtures",
                "default_agent_mode": "stub",
                "supported_agent_modes": ["live", "stub"],
                "live_agents_available": False,
                "orchestrator": {},
                "history": {},
                "datasets": {},
                "read_only": True,
            }
        )
