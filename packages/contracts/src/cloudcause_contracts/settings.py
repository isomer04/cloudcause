"""Runtime configuration read from the environment.

Two independent switches, so data realism and model spend never move together:

``CLOUDCAUSE_DATA_MODE``   fixtures | live   - where provider data comes from
``CLOUDCAUSE_AGENT_MODE``  stub | live       - deterministic stubs or real frameworks

Fixtures + stub is the default: no cloud accounts, no model keys, no network.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .analytics import AnalyticsConfig
from .common import SUPPORTED_FOCUS_VERSION

DataMode = Literal["fixtures", "live"]
AgentMode = Literal["stub", "live"]
LinkMode = Literal["inprocess", "http"]
HistoryBackend = Literal["memory", "sqlite", "postgres"]

_POSTGRES_ALIASES = ("postgres", "postgresql", "psql", "pg")
_SQLITE_ALIASES = ("sqlite", "sqlite3", "file")

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
    # Fall back to the working directory rather than guessing.
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
    if requested in _SQLITE_ALIASES:
        return "sqlite"
    if requested == "memory":
        return "memory"
    url = database_url.strip().lower()
    if url.startswith(("postgres://", "postgresql://")):
        return "postgres"
    if url:
        return "sqlite"
    return "memory"


def _number(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    data_mode: DataMode = "fixtures"
    agent_mode: AgentMode = "stub"
    orchestrator_mode: LinkMode = "inprocess"
    worker_mode: LinkMode = "inprocess"
    # Investigation history. memory keeps the offline default dependency-free.
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
    # gemini-2.0-flash no longer carries a free-tier allowance (429 with limit 0).
    gemini_model: str = "gemini-2.5-flash"
    # Free-tier request quota is per model, and the report summary runs straight
    # after the GCP investigation. A second model keeps it out of that bucket.
    gemini_summary_model: str = "gemini-2.5-flash-lite"
    max_agent_calls: int = 12
    max_agent_seconds: float = 120.0

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

    @property
    def history_sqlite_path(self) -> Path:
        """Where SQLite history lands when no database URL is configured."""

        return self.repo_root / ".cloudcause" / "history.sqlite3"

    @property
    def dataset_sqlite_path(self) -> Path:
        """Where a SQLite dataset store lands when no database URL is configured.

        Only reachable in the http topology, which the same check refuses without
        a DSN, so in practice this is a defensive default rather than a path.
        """

        return self.repo_root / ".cloudcause" / "datasets.sqlite3"

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
            gemini_model=env.get("CLOUDCAUSE_GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_summary_model=env.get(
                "CLOUDCAUSE_GEMINI_SUMMARY_MODEL", "gemini-2.5-flash-lite"
            ),
            max_agent_calls=int(_number(env, "CLOUDCAUSE_MAX_AGENT_CALLS", 12)),
            max_agent_seconds=_number(env, "CLOUDCAUSE_MAX_AGENT_SECONDS", 120.0),
        )


def get_settings() -> Settings:
    """Fresh settings on every call so tests can patch the environment."""

    return Settings.from_env()
