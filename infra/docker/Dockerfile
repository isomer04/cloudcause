# One image, four entry points. Each service runs as its own container so the
# frameworks keep separate dependency trees.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    CLOUDCAUSE_HOST=0.0.0.0 \
    CLOUDCAUSE_REPO_ROOT=/app

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer: only manifests, so code edits do not re-resolve.
COPY pyproject.toml uv.lock ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY packages/focus_normalizer/pyproject.toml packages/focus_normalizer/
COPY packages/anomaly_detection/pyproject.toml packages/anomaly_detection/
COPY packages/billing_knowledge/pyproject.toml packages/billing_knowledge/
COPY packages/providers/pyproject.toml packages/providers/
COPY packages/evidence_validation/pyproject.toml packages/evidence_validation/
COPY packages/worker_core/pyproject.toml packages/worker_core/
COPY packages/mcp_servers/pyproject.toml packages/mcp_servers/
COPY services/orchestrator_adk/pyproject.toml services/orchestrator_adk/
COPY services/investigator_aws_strands/pyproject.toml services/investigator_aws_strands/
COPY services/investigator_azure_maf/pyproject.toml services/investigator_azure_maf/
COPY apps/api/pyproject.toml apps/api/
# The postgres extra is the psycopg driver for the gateway's investigation
# history. Everything else in the image runs with no optional dependency.
RUN uv sync --frozen --no-install-workspace --no-dev --extra postgres

# Source and data.
COPY packages packages
COPY services services
COPY apps apps
COPY fixtures fixtures
COPY knowledge knowledge
COPY evaluations evaluations
COPY README.md ./
RUN uv sync --frozen --no-dev --extra postgres

ENV PATH="/app/.venv/bin:${PATH}"

# Read-only container by default: nothing here needs to write to the filesystem.
RUN useradd --create-home --uid 10001 cloudcause && chown -R cloudcause:cloudcause /app
USER cloudcause

EXPOSE 8000 8100 8101 8102
CMD ["cloudcause-api"]
