"""Shared investigation scaffolding for the three framework services."""

from .context import InvestigationContext
from .engine import LiveAgentUnavailableError, ProviderInvestigator
from .evidence import EvidenceFactory
from .history import (
    DatabaseTarget,
    DatabaseUnavailableError,
    InvestigationHistory,
    SqlJobStore,
    build_job_store,
    hash_identifier,
    parse_database_url,
    redact_state,
)
from .http_app import CONTRACT_VERSION, create_worker_app
from .jobs import InvestigationJob, JobStore
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
from .sanitize import looks_like_injection, scrub, scrub_tags

__all__ = [
    "CONTRACT_VERSION",
    "DatabaseTarget",
    "DatabaseUnavailableError",
    "EvidenceFactory",
    "FALLBACK_PLAYBOOK",
    "InvestigationContext",
    "InvestigationHistory",
    "InvestigationJob",
    "JobStore",
    "LiveAgentUnavailableError",
    "NativeToolset",
    "PlaybookSpec",
    "ProviderInvestigator",
    "SqlJobStore",
    "build_finding",
    "build_job_store",
    "create_worker_app",
    "gather_evidence",
    "hash_identifier",
    "looks_like_injection",
    "match_score",
    "parse_database_url",
    "redact_state",
    "run_playbooks",
    "scrub",
    "scrub_tags",
    "select_playbook",
]
