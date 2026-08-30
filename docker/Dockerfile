# One image, four entry points. Each service runs as its own container so the
# frameworks keep separate dependency trees.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    CLOUDCAUSE_HOST=0.0.0.0 \
    CLOUDCAUSE_REPO_ROOT=/app

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# Created before the copies so COPY --chown can own them. A trailing `chown -R`
# instead costs a ~950 MB layer: overlayfs stores each touched file afresh.
RUN useradd --create-home --uid 10001 cloudcause
WORKDIR /app
RUN chown cloudcause:cloudcause /app
USER cloudcause

# Dependency layer: only manifests, so code edits do not re-resolve.
COPY --chown=cloudcause:cloudcause pyproject.toml uv.lock ./
COPY --chown=cloudcause:cloudcause packages/contracts/pyproject.toml packages/contracts/
COPY --chown=cloudcause:cloudcause packages/focus/pyproject.toml packages/focus/
COPY --chown=cloudcause:cloudcause packages/anomaly/pyproject.toml packages/anomaly/
COPY --chown=cloudcause:cloudcause packages/knowledge/pyproject.toml packages/knowledge/
COPY --chown=cloudcause:cloudcause packages/providers/pyproject.toml packages/providers/
COPY --chown=cloudcause:cloudcause packages/evidence/pyproject.toml packages/evidence/
COPY --chown=cloudcause:cloudcause packages/datasets/pyproject.toml packages/datasets/
COPY --chown=cloudcause:cloudcause packages/rate_limit/pyproject.toml packages/rate_limit/
COPY --chown=cloudcause:cloudcause packages/worker_core/pyproject.toml packages/worker_core/
COPY --chown=cloudcause:cloudcause packages/mcp/pyproject.toml packages/mcp/
COPY --chown=cloudcause:cloudcause services/orchestrator_adk/pyproject.toml services/orchestrator_adk/
COPY --chown=cloudcause:cloudcause services/investigator_aws_strands/pyproject.toml services/investigator_aws_strands/
COPY --chown=cloudcause:cloudcause services/investigator_azure_maf/pyproject.toml services/investigator_azure_maf/
COPY --chown=cloudcause:cloudcause api/pyproject.toml api/
# The postgres extra is the psycopg driver for the gateway's investigation
# history. Everything else in the image runs with no optional dependency.
RUN uv sync --frozen --no-install-workspace --no-dev --extra postgres

# Source and data.
COPY --chown=cloudcause:cloudcause packages packages
COPY --chown=cloudcause:cloudcause services services
COPY --chown=cloudcause:cloudcause api api
COPY --chown=cloudcause:cloudcause fixtures fixtures
COPY --chown=cloudcause:cloudcause knowledge knowledge
COPY --chown=cloudcause:cloudcause evaluations evaluations
COPY --chown=cloudcause:cloudcause README.md ./
RUN uv sync --frozen --no-dev --extra postgres

ENV PATH="/app/.venv/bin:${PATH}"

# Not read-only: an upload-backed run writes an MCP snapshot under .cloudcause.

EXPOSE 8000 8100 8101 8102
CMD ["cloudcause-api"]
