# Operating CloudCause

Everything needed to run, configure, and deploy the system.
[`architecture.md`](architecture.md) explains what it is and why; this explains how to
work it. Where this document and the code disagree, the code is right.

## Runtime modes

Independent switches, each defaulting to the safe option.

| Variable | Values | Meaning |
| --- | --- | --- |
| `CLOUDCAUSE_DATA_MODE` | `fixtures` (default), `live` | Where provider data comes from. `live` has no connector yet and raises a clear error. |
| `CLOUDCAUSE_AGENT_MODE` | `stub` (default), `live` | Deterministic playbooks, or the real frameworks. |
| `CLOUDCAUSE_ORCHESTRATOR_MODE` | `inprocess` (default), `http` | How the gateway reaches the orchestrator. |
| `CLOUDCAUSE_WORKER_MODE` | `inprocess` (default), `http` | How the orchestrator reaches the AWS and Azure workers. |
| `CLOUDCAUSE_HISTORY_BACKEND` | `memory` (default), `sqlite`, `postgres` | Where investigation history is kept. |
| `CLOUDCAUSE_UPLOADS_ENABLED` | `true` (default), `false` | Whether the gateway accepts a user's own cost export. Still refused, with a reason, when the topology cannot share a dataset. |
| `CLOUDCAUSE_API_PORT` | `8000` (default) | Gateway port. |

Copy `.env.example` to `.env` for the full list. Services read it at startup and
real environment variables always win over the file. Secrets belong there or in a
secret manager, never in prompts, logs, or reports.

## Live agents on fixture data

This proves framework orchestration end to end and still needs no cloud account.

```bash
uv sync --extra live
CLOUDCAUSE_AGENT_MODE=live OPENAI_API_KEY=... GOOGLE_API_KEY=... GOOGLE_GENAI_USE_ENTERPRISE=FALSE \
  uv run cloudcause-api
```

Verified 2026-07-28 against the versions pinned in `uv.lock`: google-adk 2.5.0,
strands-agents 1.50.2, agent-framework 1.12.1.

If a framework errors, that specialist falls back to the deterministic playbooks,
reports `partial`, and records the reason as a report warning. A quota limit or an
SDK change degrades the run instead of losing it.

`/health` reporting `agent_mode: live` only proves the switch was read, not that a
model answered. The per-provider status is the one that settles it: a real live run
shows `status: ok` with `agent_mode: live` for that provider, while `status: partial`
with `agent_mode: stub` means the framework was requested, failed, and fell back —
with the reason in the report warnings. Note also that the specialist determines the
vendor, so a GCP-only scenario produces Gemini traffic and nothing in an OpenAI log.

What the agent returns is prose over evidence IDs the deterministic layer supplied,
not a recomputed figure:

![A live agent response citing AZURE-E006, E004, E005, E001, E002 and E007 for an idle
database, alongside the read-only tool set it was offered](images/cloudcause-live-response.png)

Every tool in that panel that crosses the evidence boundary is a `get_` reader. The two
that are not, `recalculate_attribution` and `record_finding`, stay inside the process:
one asks the deterministic layer to restate a figure, the other writes the finding into
the report. Tool schemas are date-aware, so a rule is selected by the usage date being
explained rather than by today's date:

![The get_billing_rule schema: provider, service, category, usage_date, and rule_type,
with only provider required](images/cloudcause-live-tool-schema.png)

## Bring your own data

The demo fixtures answer "does this work". They cannot answer "does this work on
my bill". A third input path does, without a cloud account and without a
connector: upload the cost export your provider already gives you.

```bash
# 1. Mint a dataset
DATASET=$(curl -sX POST localhost:8000/api/v1/datasets | python -c "import json,sys;print(json.load(sys.stdin)['dataset_id'])")

# 2. Stream one file per request. No multipart, no filename.
curl -X PUT "localhost:8000/api/v1/datasets/$DATASET/sources/aws/cost" \
     -H 'content-type: text/csv' --data-binary @my-cur-export.csv

# 3. Seal it, then investigate it
curl -sX POST "localhost:8000/api/v1/datasets/$DATASET/seal"
curl -sX POST 'localhost:8000/api/v1/investigations?wait=true' \
     -H 'content-type: application/json' \
     -d "{\"providers\":[\"aws\"],\"start_date\":\"2026-07-13\",\"end_date\":\"2026-07-19\",
          \"comparison_start_date\":\"2026-07-06\",\"comparison_end_date\":\"2026-07-12\",
          \"scenario_id\":\"\",\"dataset_id\":\"$DATASET\"}"
```

Or drop the files into the Next.js UI, which makes the same three calls.

| Accepted | Shape | Detection |
| --- | --- | --- |
| AWS | Data Exports / CUR 2.0, `.json` with `rows` or `.csv` | `identity_line_item_id`, `line_item_usage_start_date` |
| Azure | Cost Management query result `.json` | `properties.columns` including `UsageDate` |
| GCP | BigQuery detailed usage export `.csv` or `.json` | `service.description`, `usage_start_time` |

`.gz` of any of those is accepted. Detection is by content, not by the URL: an
Azure export dropped in the AWS slot is a `422` naming both formats, never a
silently empty comparison.

### Two tiers, and the difference is honest

A cost export alone measures the change: real period comparison, real materiality,
ranked candidates, reconciliation that balances, and a cited billing rule. It
cannot evidence a *cause*, so findings are published as `unexplained_increase` at
confidence `<= 0.40` with `is_uncertain=true`, and the report names the data that
would raise them.

Add any of metrics, audit events, inventory, or provider recommendations for the
same period, in the four shapes in [`../fixtures/README.md`](../fixtures/README.md),
and the same run publishes the specific mechanism instead. The UI offers a template
download for each.

### What is kept

The raw bytes are parsed from the request stream and discarded: nothing raw is
written to disk, and no row value is ever logged. What is stored is normalized
contract objects, collapsed to the daily grain the analytics layer consumes, for
two hours. `DELETE /api/v1/datasets/{id}` is honoured immediately.

The report outlives the data, so re-running a stored investigation whose dataset
has expired answers `409 dataset_expired` rather than a `404` that reads like a bug.

A dataset is created empty, filled one source at a time, then **sealed**. After
sealing it is immutable, which is what makes it safe for the gateway, the
orchestrator, both workers, and every MCP child process to read concurrently
mid-investigation. An unsealed dataset cannot start a run.

### Limits

| Limit | Default | Variable |
| --- | --- | --- |
| Upload size per file | 25 MB | `CLOUDCAUSE_UPLOAD_MAX_BYTES` |
| Decompressed size per file | 200 MB | `CLOUDCAUSE_UPLOAD_MAX_DECOMPRESSED_BYTES` |
| Rows per cost file | 250,000 | `CLOUDCAUSE_UPLOAD_MAX_ROWS` |
| Sources per dataset | 15 | `CLOUDCAUSE_UPLOAD_MAX_SOURCES` |
| Normalized rows per dataset | 40,000 | `CLOUDCAUSE_DATASET_MAX_RECORDS` |
| Dataset TTL | 2 hours | `CLOUDCAUSE_DATASET_TTL_SECONDS` |
| Total store bytes | 512 MB | `CLOUDCAUSE_DATASET_STORE_MAX_BYTES` |
| Ingest wall clock per file | 30 s | `CLOUDCAUSE_UPLOAD_TIMEOUT_SECONDS` |

### Where the dataset lives

This follows from the topology rather than from preference, because
`get_data_provider` is called independently in every process:

| Topology | Store | Uploads |
| --- | --- | --- |
| Everything in one process (the default) | memory | enabled |
| `http` orchestrator or workers, `CLOUDCAUSE_DATABASE_URL` set | SQL, no memory fallback | enabled |
| `http`, no database | none | refused at ingest, `503` naming the missing configuration |

Investigation history degrades to memory when its database disappears, because
losing history must never fail an investigation. The dataset store does the
opposite and refuses uploads, because a dataset only the gateway can see produces
a mid-run "unknown dataset" or, worse, a fall-through to the demo fixtures under
an uploaded label. `GET /health` reports the two separately.

> **A public deployment that accepts uploads needs authentication and per-user
> isolation first.** The gateway is unauthenticated: anyone who can reach it can
> start an investigation, read any investigation, and upload. Dataset ids are
> `secrets.token_urlsafe(16)` so they are not enumerable, and that is the only
> thing separating two users' data. Real multi-tenancy is out of scope for the MVP,
> and the UI must not imply otherwise. `CLOUDCAUSE_UPLOADS_ENABLED=false` turns the
> feature off entirely.

## Investigation history

The gateway keeps live jobs in memory because SSE streaming needs a queue, and
writes every state change through to SQL when a history backend is configured.
State, reports, and the progress log then survive a restart, served by the same
endpoints. `GET /health` reports which backend is live.

```bash
# A local file, no extra dependency:
CLOUDCAUSE_DATABASE_URL=sqlite:///.cloudcause/history.sqlite3 uv run cloudcause-api

# PostgreSQL, the Compose default for the api service:
uv sync --extra postgres
CLOUDCAUSE_DATABASE_URL=postgresql://cloudcause:pw@127.0.0.1:5432/cloudcause uv run cloudcause-api
```

Both backends run the same portable migrations from
`packages/worker_core/src/cloudcause_worker_core/migrations/`, applied on startup
and recorded in `cloudcause_schema_migrations`. Account and subscription
identifiers are hashed before they are stored, so the durable copy is not the raw
request. An unreachable database degrades to memory with a warning instead of
failing the gateway or an investigation.

To exercise the PostgreSQL path locally:

```bash
docker compose -f infra/docker/docker-compose.yml up -d postgres
uv sync --extra postgres
CLOUDCAUSE_TEST_DATABASE_URL=postgresql://cloudcause:cloudcause-local-only@127.0.0.1:5432/cloudcause \
  uv run pytest tests/persistence -q
```

Without that variable the PostgreSQL test skips itself and the SQLite tests carry
the coverage, which is why CI stays offline.

## The full test suite

```bash
uv run pytest tests -q                        # everything offline, $0, no keys
uv run pytest tests/unit -q                   # deterministic analytics
uv run pytest tests/contract -q               # provider adapter contract
uv run pytest tests/worker_api -q             # worker HTTP contracts, both transports
uv run pytest tests/gateway tests/ui -q       # gateway contract, thin-client rule
uv run pytest tests/knowledge -q              # date-aware rule regressions
uv run pytest tests/persistence -q            # history and datasets survive a restart
uv run pytest tests/security -q               # nothing raw on disk, no row in a log
uv run pytest tests/e2e -q                    # offline end to end + 12 scenarios + both upload tiers
uv run pytest tests/live -m live              # opt-in, needs model keys
uv run python evaluations/run_evaluation.py   # scored metrics report
```

The Next.js UI has its own two gates, run from `apps/web`:

```bash
npm run typecheck                             # contract types match the gateway
npm run build                                 # production build
```

`tests/ui` enforces the thin-client rule: `apps/web/lib/types.ts` must mirror the
gateway's Pydantic models field for field, so a contract change cannot land in
Python and go missing in TypeScript.

The offline suite covers five test layers plus the evidence and
knowledge regressions: rules selected by usage date, no retroactive rule
application, undated or stale knowledge capping confidence, unknown schema versions
failing safely, delayed data never read as zero usage, a partial trailing day never
read as a quiet one, mixed currencies refused rather than summed, and no mutating
tool anywhere in the system.

## Containers

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

Each service runs in its own process, which is what exercises the `http`
orchestrator and worker transports rather than the in-process default. The image
runs as an unprivileged user and needs no write access to the filesystem.
