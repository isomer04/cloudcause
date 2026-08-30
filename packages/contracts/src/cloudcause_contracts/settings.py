"""Runtime configuration read from the environment.

Data realism and model spend never move together:

``CLOUDCAUSE_DATA_MODE``   fixtures | live   - where provider data comes from
``InvestigationRequest.agent_mode`` stub | live - selected for each run

There is no mode to configure. A deployment can run hosted-model agents exactly
when it has a model key, so putting ``OPENAI_API_KEY`` or ``GOOGLE_API_KEY`` in
``.env`` is the whole setup, and both investigation paths are then live in the
same process with the choice made per run. ``live_agents_available`` reports that
capability; the UI reads it so it never offers a path this deployment cannot walk.

``CLOUDCAUSE_AGENT_MODE`` survives only as the default for API clients that omit
the request field. It gates nothing.

Fixtures + stub is the default: no cloud accounts, no model keys, no network.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Literal

from .analytics import AnalyticsConfig
from .common import SUPPORTED_FOCUS_VERSION

DataMode = Literal["fixtures", "live"]
AgentMode = Literal["stub", "live"]
LinkMode = Literal["inprocess", "http"]
HistoryBackend = Literal["memory", "postgres"]
RateLimitBackend = Literal["memory", "redis"]
DispatchMode = Literal["background", "cloud_tasks"]

_POSTGRES_ALIASES = ("postgres", "postgresql", "psql", "pg")

#: The repository root is the only directory holding *both* the workspace manifest
#: and the versioned rule store the runtime reads. Every package directory has its
#: own ``pyproject.toml``, so one marker alone would match a subdirectory, and a
#: documentation filename would tie process startup to where a doc happens to live.
_ROOT_MARKERS = ("pyproject.toml", "knowledge")

#: Everything else counts as "on", so a typo never silently disables a guard.
_FALSE_FLAGS = ("0", "false", "no", "off")


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from this file (or ``start``) until the repository root is found."""

    env_root = os.environ.get("CLOUDCAUSE_REPO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if all((parent / marker).exists() for marker in _ROOT_MARKERS):
            return parent
    return Path.cwd().resolve()


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a mapping. Missing file means an empty mapping.

    Deliberately minimal: ``KEY=value`` lines, ``#`` comments, optional ``export``
    prefix, optional surrounding quotes. No interpolation, no shell semantics.
    """

    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.removeprefix("export ").partition("=")
        key = key.strip()
        if not key:
            continue
        value = raw.strip().strip('"').strip("'")
        if value and value != "replace-me":
            values[key] = value
    return values


def _flag(env: Mapping[str, str], key: str, default: str) -> str:
    return (env.get(key) or default).strip().lower()


def _history_backend(requested: str, database_url: str) -> HistoryBackend:
    """Explicit backend wins; otherwise infer it from the database URL scheme."""

    if requested in _POSTGRES_ALIASES:
        return "postgres"
    if requested == "memory":
        return "memory"
    # Any non-empty URL, including an unsupported one, so parse_database_url can
    # refuse it by name rather than it becoming a silent downgrade to memory.
    return "postgres" if database_url.strip() else "memory"


def _key(env: Mapping[str, str], name: str) -> str:
    """A placeholder key is the same as no key; ``.env.example`` ships ``replace-me``."""

    value = (env.get(name) or "").strip()
    return "" if value == "replace-me" else value


def _number(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _positive_number(env: Mapping[str, str], key: str, default: float) -> float:
    """Read a required positive limit without silently weakening a guard."""

    raw = env.get(key)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a positive number") from error
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return value


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = _positive_number(env, key, float(default))
    if not value.is_integer():
        raise ValueError(f"{key} must be a positive integer")
    return int(value)


def _non_negative_int(env: Mapping[str, str], key: str, default: int) -> int:
    """Read a limit that may legitimately be zero (e.g. no burst allowance)."""

    raw = env.get(key)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a non-negative integer") from error
    if not isfinite(value) or value < 0 or not value.is_integer():
        raise ValueError(f"{key} must be a non-negative integer")
    return int(value)


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    data_mode: DataMode = "fixtures"
    agent_mode: AgentMode = "stub"
    orchestrator_mode: LinkMode = "inprocess"
    worker_mode: LinkMode = "inprocess"
    # Investigation history. postgres is the only persisted store, and needs a DSN.
    history_backend: HistoryBackend = "memory"
    database_url: str = ""
    history_keep: int = 50
    history_connect_timeout_seconds: float = 5.0
    id_hash_salt: str = ""
    # Bring-your-own-data. Every limit is enforced while streaming or parsing, so
    # a large or hostile upload is refused rather than absorbed.
    uploads_enabled: bool = True
    upload_max_bytes: int = 25 * 1024 * 1024
    upload_max_decompressed_bytes: int = 200 * 1024 * 1024
    upload_max_rows: int = 250_000
    upload_max_sources: int = 15
    upload_timeout_seconds: float = 30.0
    dataset_max_records: int = 40_000
    dataset_ttl_seconds: float = 7200.0
    dataset_store_max_bytes: int = 512 * 1024 * 1024
    orchestrator_url: str = "http://127.0.0.1:8100"
    aws_worker_url: str = "http://127.0.0.1:8101"
    azure_worker_url: str = "http://127.0.0.1:8102"
    api_url: str = "http://127.0.0.1:8000"
    worker_timeout_seconds: float = 90.0
    focus_version: str = SUPPORTED_FOCUS_VERSION
    knowledge_review_max_age_days: int = 180
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    openai_model: str = "gpt-4.1-mini"
    # Configurable deployment-specific model target. Default chosen for free-tier
    # headroom. Quotas vary by account tier, project, and model; check the active
    # project quota, model, and account tier to prevent 429 RESOURCE_EXHAUSTED
    # errors.
    gemini_model: str = "gemini-3.5-flash-lite"
    # Budget for one whole investigation, shared by every provider's agent
    # (Orchestrator.run binds a single AgentCallBudget). A multi-cloud run
    # therefore divides this across three concurrently racing agents, so a
    # per-provider-sized number starves two of them: at 12 the AWS agent
    # consumed the budget and the ADK and MAF agents fell back to playbooks.
    # Sized for the three-provider default with retry headroom.
    max_agent_calls: int = 48
    # A throttled agent spends most of this waiting for its next permit rather
    # than thinking, so this bounds queueing as much as reasoning: the ADK
    # investigator timed out at exactly 120.023s once the governor was
    # correctly capped. Cloud Run allows 600s, so this stays well inside it.
    max_agent_seconds: float = 180.0
    # Single-process defense in depth. This retains excess live jobs in their
    # existing queued state and must not be treated as a distributed quota.
    max_concurrent_live_investigations: int = 2
    live_queue_timeout_seconds: float = 60.0
    # Read here as well as by the SDKs, so the gateway can answer "can this
    # deployment run live at all?" without attempting a model call to find out.
    openai_api_key: str = ""
    google_api_key: str = ""

    # Layer 1: gateway admission control for POST /investigations (live only).
    live_rate_limit_enabled: bool = True
    live_investigations_per_hour: int = 3
    live_investigation_burst: int = 0
    global_live_starts_per_minute: int = 20
    trust_proxy_headers: bool = False

    # Layer 3: outbound per-provider/model quota, enforced around every model call.
    openai_max_concurrency: int = 2
    openai_requests_per_minute: int = 20
    # These two buckets are independent, so their sum is what actually reaches
    # the API. Sized here as configurable deployment-specific targets. Check the
    # active project quota, model, and account tier to prevent 429
    # RESOURCE_EXHAUSTED errors, and adjust both settings accordingly.
    gemini_max_concurrency: int = 1
    gemini_requests_per_minute: int = 8
    gemini_summary_max_concurrency: int = 1
    gemini_summary_requests_per_minute: int = 4

    # Layer 4: bounded, jittered retries for provider throttling/transient errors.
    ai_retry_attempts: int = 3
    ai_retry_base_seconds: float = 1.0
    ai_retry_max_seconds: float = 20.0

    # Rate-limit storage backend. memory works single-process; redis is required
    # before scaling the gateway or live workers past one replica.
    rate_limit_backend: RateLimitBackend = "memory"
    rate_limit_redis_url: str = ""

    # Live-investigation dispatch. background keeps today's FastAPI BackgroundTasks
    # path; cloud_tasks enqueues onto Cloud Tasks and requires the CLOUDCAUSE_TASKS_*
    # settings below plus a reachable private worker endpoint.
    dispatch_mode: DispatchMode = "background"
    tasks_queue: str = ""
    tasks_location: str = ""
    tasks_project_id: str = ""
    tasks_worker_url: str = ""
    tasks_service_account: str = ""

    @property
    def live_agents_available(self) -> bool:
        """Whether a hosted-model run is possible here at all.

        Configuring a key is the act that opts a deployment into model spend, so
        it is also what decides whether the UI offers the live path. A run that
        reaches a provider whose key is missing still degrades to the
        deterministic playbooks and reports ``partial`` rather than failing.
        """

        return bool(self.openai_api_key or self.google_api_key)

    @property
    def fixture_root(self) -> Path:
        return self.repo_root / "fixtures"

    @property
    def knowledge_root(self) -> Path:
        return self.repo_root / "knowledge"

    @property
    def scenario_root(self) -> Path:
        return self.repo_root / "evaluations" / "scenarios"

    @property
    def expected_findings_root(self) -> Path:
        return self.repo_root / "evaluations" / "expected_findings"

    def worker_url(self, provider: str) -> str:
        return {"aws": self.aws_worker_url, "azure": self.azure_worker_url}.get(
            provider, self.orchestrator_url
        )

    def with_overrides(self, **changes: object) -> Settings:
        from dataclasses import replace

        return replace(self, **changes)  # type: ignore[arg-type]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read settings from the environment, with ``.env`` as the lower layer.

        Real environment variables always win over the file, so a shell export or
        a container's env overrides ``.env`` rather than fighting it. Passing an
        explicit ``env`` mapping skips the file entirely, which is what the tests
        do to stay hermetic.
        """

        if env is None:
            env_file = Path(os.environ.get("CLOUDCAUSE_ENV_FILE") or find_repo_root() / ".env")
            # setdefault, not assignment: a real environment variable always wins.
            # These land in os.environ because the model SDKs read their keys from
            # there, not from Settings, so parsing the file alone would not be enough.
            for key, value in load_env_file(env_file).items():
                os.environ.setdefault(key, value)
            env = os.environ
        analytics = AnalyticsConfig(
            min_absolute_change=_number(env, "CLOUDCAUSE_MIN_ABSOLUTE_CHANGE", 5.0),
            min_percent_change=_number(env, "CLOUDCAUSE_MIN_PERCENT_CHANGE", 20.0),
            reconciliation_tolerance=_number(env, "CLOUDCAUSE_RECONCILIATION_TOLERANCE", 0.05),
        )
        data_mode = _flag(env, "CLOUDCAUSE_DATA_MODE", "fixtures")
        agent_mode = _flag(env, "CLOUDCAUSE_AGENT_MODE", "stub")
        database_url = (env.get("CLOUDCAUSE_DATABASE_URL") or "").strip()
        return cls(
            repo_root=find_repo_root(),
            data_mode="live" if data_mode == "live" else "fixtures",
            agent_mode="live" if agent_mode == "live" else "stub",
            orchestrator_mode=(
                "http" if _flag(env, "CLOUDCAUSE_ORCHESTRATOR_MODE", "inprocess") == "http" else "inprocess"
            ),
            worker_mode="http" if _flag(env, "CLOUDCAUSE_WORKER_MODE", "inprocess") == "http" else "inprocess",
            history_backend=_history_backend(_flag(env, "CLOUDCAUSE_HISTORY_BACKEND", ""), database_url),
            database_url=database_url,
            history_keep=int(_number(env, "CLOUDCAUSE_HISTORY_KEEP", 50)),
            history_connect_timeout_seconds=_number(
                env, "CLOUDCAUSE_HISTORY_CONNECT_TIMEOUT_SECONDS", 5.0
            ),
            id_hash_salt=env.get("CLOUDCAUSE_ID_HASH_SALT", ""),
            uploads_enabled=_flag(env, "CLOUDCAUSE_UPLOADS_ENABLED", "true") not in _FALSE_FLAGS,
            upload_max_bytes=int(_number(env, "CLOUDCAUSE_UPLOAD_MAX_BYTES", 25 * 1024 * 1024)),
            upload_max_decompressed_bytes=int(
                _number(env, "CLOUDCAUSE_UPLOAD_MAX_DECOMPRESSED_BYTES", 200 * 1024 * 1024)
            ),
            upload_max_rows=int(_number(env, "CLOUDCAUSE_UPLOAD_MAX_ROWS", 250_000)),
            upload_max_sources=int(_number(env, "CLOUDCAUSE_UPLOAD_MAX_SOURCES", 15)),
            upload_timeout_seconds=_number(env, "CLOUDCAUSE_UPLOAD_TIMEOUT_SECONDS", 30.0),
            dataset_max_records=int(_number(env, "CLOUDCAUSE_DATASET_MAX_RECORDS", 40_000)),
            dataset_ttl_seconds=_number(env, "CLOUDCAUSE_DATASET_TTL_SECONDS", 7200.0),
            dataset_store_max_bytes=int(
                _number(env, "CLOUDCAUSE_DATASET_STORE_MAX_BYTES", 512 * 1024 * 1024)
            ),
            orchestrator_url=env.get("CLOUDCAUSE_ORCHESTRATOR_URL", "http://127.0.0.1:8100"),
            aws_worker_url=env.get("CLOUDCAUSE_AWS_WORKER_URL", "http://127.0.0.1:8101"),
            azure_worker_url=env.get("CLOUDCAUSE_AZURE_WORKER_URL", "http://127.0.0.1:8102"),
            api_url=env.get("CLOUDCAUSE_API_URL", "http://127.0.0.1:8000"),
            worker_timeout_seconds=_number(env, "CLOUDCAUSE_WORKER_TIMEOUT_SECONDS", 90.0),
            focus_version=env.get("CLOUDCAUSE_FOCUS_VERSION", SUPPORTED_FOCUS_VERSION),
            knowledge_review_max_age_days=int(_number(env, "CLOUDCAUSE_KNOWLEDGE_REVIEW_MAX_AGE_DAYS", 180)),
            analytics=analytics,
            openai_model=env.get("CLOUDCAUSE_OPENAI_MODEL", "gpt-4.1-mini"),
            gemini_model=env.get("CLOUDCAUSE_GEMINI_MODEL", "gemini-3.5-flash-lite"),
            max_agent_calls=_positive_int(env, "CLOUDCAUSE_MAX_AGENT_CALLS", 48),
            max_agent_seconds=_positive_number(env, "CLOUDCAUSE_MAX_AGENT_SECONDS", 180.0),
            max_concurrent_live_investigations=_positive_int(
                env, "CLOUDCAUSE_MAX_CONCURRENT_LIVE_INVESTIGATIONS", 2
            ),
            live_queue_timeout_seconds=_positive_number(
                env, "CLOUDCAUSE_LIVE_QUEUE_TIMEOUT_SECONDS", 60.0
            ),
            openai_api_key=_key(env, "OPENAI_API_KEY"),
            google_api_key=_key(env, "GOOGLE_API_KEY"),
            live_rate_limit_enabled=_flag(env, "CLOUDCAUSE_LIVE_RATE_LIMIT_ENABLED", "true")
            not in _FALSE_FLAGS,
            live_investigations_per_hour=_positive_int(
                env, "CLOUDCAUSE_LIVE_INVESTIGATIONS_PER_HOUR", 3
            ),
            live_investigation_burst=_non_negative_int(env, "CLOUDCAUSE_LIVE_INVESTIGATION_BURST", 0),
            global_live_starts_per_minute=_positive_int(
                env, "CLOUDCAUSE_GLOBAL_LIVE_STARTS_PER_MINUTE", 20
            ),
            trust_proxy_headers=_flag(env, "CLOUDCAUSE_TRUST_PROXY_HEADERS", "false") not in _FALSE_FLAGS,
            openai_max_concurrency=_positive_int(env, "CLOUDCAUSE_OPENAI_MAX_CONCURRENCY", 2),
            openai_requests_per_minute=_positive_int(env, "CLOUDCAUSE_OPENAI_REQUESTS_PER_MINUTE", 20),
            gemini_max_concurrency=_positive_int(env, "CLOUDCAUSE_GEMINI_MAX_CONCURRENCY", 1),
            gemini_requests_per_minute=_positive_int(env, "CLOUDCAUSE_GEMINI_REQUESTS_PER_MINUTE", 8),
            gemini_summary_max_concurrency=_positive_int(
                env, "CLOUDCAUSE_GEMINI_SUMMARY_MAX_CONCURRENCY", 1
            ),
            gemini_summary_requests_per_minute=_positive_int(
                env, "CLOUDCAUSE_GEMINI_SUMMARY_REQUESTS_PER_MINUTE", 4
            ),
            ai_retry_attempts=_positive_int(env, "CLOUDCAUSE_AI_RETRY_ATTEMPTS", 3),
            ai_retry_base_seconds=_positive_number(env, "CLOUDCAUSE_AI_RETRY_BASE_SECONDS", 1.0),
            ai_retry_max_seconds=_positive_number(env, "CLOUDCAUSE_AI_RETRY_MAX_SECONDS", 20.0),
            rate_limit_backend=(
                "redis" if _flag(env, "CLOUDCAUSE_RATE_LIMIT_BACKEND", "memory") == "redis" else "memory"
            ),
            rate_limit_redis_url=(env.get("CLOUDCAUSE_RATE_LIMIT_REDIS_URL") or "").strip(),
            dispatch_mode=(
                "cloud_tasks"
                if _flag(env, "CLOUDCAUSE_DISPATCH_MODE", "background") == "cloud_tasks"
                else "background"
            ),
            tasks_queue=env.get("CLOUDCAUSE_TASKS_QUEUE", ""),
            tasks_location=env.get("CLOUDCAUSE_TASKS_LOCATION", ""),
            tasks_project_id=env.get("CLOUDCAUSE_TASKS_PROJECT_ID", ""),
            tasks_worker_url=env.get("CLOUDCAUSE_TASKS_WORKER_URL", ""),
            tasks_service_account=env.get("CLOUDCAUSE_TASKS_SERVICE_ACCOUNT", ""),
        )


def get_settings() -> Settings:
    """Fresh settings on every call so tests can patch the environment."""

    return Settings.from_env()
