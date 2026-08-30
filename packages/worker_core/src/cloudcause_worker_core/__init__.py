"""Shared investigation scaffolding for the three framework services."""

from .context import InvestigationContext
from .engine import LiveAgentUnavailableError, ProviderInvestigator
from .evidence import EvidenceFactory
from .governed_openai import build_governed_openai_client
from .history import (
    DatabaseTarget,
    DatabaseUnavailableError,
    InvestigationHistory,
    SqlJobStore,
    UnsupportedDatabaseUrlError,
    build_job_store,
    hash_identifier,
    parse_database_url,
    redact_state,
)
from .http_app import CONTRACT_VERSION, create_worker_app
from .jobs import InvestigationJob, JobStore
from .live_limits import (
    AgentCallBudget,
    AgentCallLimitExceeded,
    LiveCapacityTimeoutError,
    LiveInvestigationCapacity,
    bind_agent_call_budget,
    current_agent_call_budget,
    reset_agent_call_budget,
)
from .native_tools import NativeToolset
from .playbooks import (
    FALLBACK_PLAYBOOK,
    PlaybookSpec,
    build_finding,
    gather_evidence,
    match_score,
    run_playbooks,
    select_playbook,
)
from .sanitize import looks_like_injection, render_untrusted_literal, scrub, scrub_tags

__all__ = [
    "CONTRACT_VERSION",
    "AgentCallBudget",
    "AgentCallLimitExceeded",
    "DatabaseTarget",
    "DatabaseUnavailableError",
    "UnsupportedDatabaseUrlError",
    "EvidenceFactory",
    "FALLBACK_PLAYBOOK",
    "InvestigationContext",
    "InvestigationHistory",
    "InvestigationJob",
    "JobStore",
    "LiveAgentUnavailableError",
    "LiveCapacityTimeoutError",
    "LiveInvestigationCapacity",
    "NativeToolset",
    "PlaybookSpec",
    "ProviderInvestigator",
    "SqlJobStore",
    "build_finding",
    "bind_agent_call_budget",
    "build_governed_openai_client",
    "build_job_store",
    "create_worker_app",
    "gather_evidence",
    "hash_identifier",
    "looks_like_injection",
    "match_score",
    "current_agent_call_budget",
    "parse_database_url",
    "redact_state",
    "render_untrusted_literal",
    "reset_agent_call_budget",
    "run_playbooks",
    "scrub",
    "scrub_tags",
    "select_playbook",
]
