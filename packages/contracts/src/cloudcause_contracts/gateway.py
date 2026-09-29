"""HTTP payloads published by the CloudCause gateway to web clients.

The nested health diagnostics intentionally remain maps: they report transport
and storage implementation details which vary by deployment.  The stable
top-level fields are versioned UI contract fields and therefore stay typed.
"""

from __future__ import annotations

from typing import Any, Literal

from .common import CloudCauseModel, Provider
from .investigation import InvestigationRequest, InvestigationStatus
from .report import InvestigationState
from .settings import AgentMode, DataMode


class ScenarioSummary(CloudCauseModel):
    """One seeded scenario offered by the investigation UI."""

    id: str
    title: str
    providers: list[Provider]
    category: str
    suggested_request: InvestigationRequest


class InvestigationCreated(CloudCauseModel):
    """Immediate response after the gateway has accepted an investigation."""

    investigation_id: str
    status: InvestigationStatus
    headline: str = ""
    state: InvestigationState


class GatewayHealth(CloudCauseModel):
    """Stable gateway health and capability fields consumed by the web app.

    ``orchestrator``, ``history``, and ``datasets`` expose operational detail
    whose exact fields legitimately differ between in-process and HTTP-backed
    deployments.  Keeping them as records preserves the current v1 JSON while
    avoiding a false promise that every provider diagnostic has one fixed shape.
    """

    status: str
    contract_version: str
    data_mode: DataMode
    default_agent_mode: AgentMode
    agent_mode_selection: Literal["per_investigation"]
    supported_agent_modes: list[AgentMode]
    live_agents_available: bool
    orchestrator: dict[str, Any]
    history: dict[str, Any]
    datasets: dict[str, Any]
    rate_limiter: dict[str, Any]
    read_only: bool
