# Operating CloudCause

Everything needed to run, configure, and deploy the system.
[`architecture.md`](architecture.md) explains what it is and why; this explains how to
work it. Where this document and the code disagree, the code is right.

## Runtime modes

Data source is a deployment setting. Investigation mode is selected per request and
needs no configuration: a model key is what makes the live path possible.

| Variable | Values | Meaning |
| --- | --- | --- |
| `CLOUDCAUSE_DATA_MODE` | `fixtures` (default), `live` | Where provider data comes from. `live` has no connector yet and raises a clear error. |
| `OPENAI_API_KEY` | key | Enables live AWS (Strands) and Azure (Agent Framework) agents. |
| `GOOGLE_API_KEY` | key | Enables the live GCP (ADK) agent. The published report summary is deterministic. |
| Request `agent_mode` | `live`, `stub` | Per-investigation choice: hosted AI agents or deterministic playbooks. Always honoured; a provider with no key falls back and reports `partial`. |
| `CLOUDCAUSE_AGENT_MODE` | `stub` (default), `live` | Only the default for API clients that omit `agent_mode`. Gates nothing. |
| `CLOUDCAUSE_ORCHESTRATOR_MODE` | `inprocess` (default), `http` | How the gateway reaches the orchestrator. |
| `CLOUDCAUSE_WORKER_MODE` | `inprocess` (default), `http` | How the orchestrator reaches the AWS and Azure workers. |
| `CLOUDCAUSE_HISTORY_BACKEND` | `memory` (default), `postgres` | Where investigation history is kept. `memory` loses it on exit. |
| `CLOUDCAUSE_UPLOADS_ENABLED` | `true` (default), `false` | Whether the gateway accepts a user's own cost export. Still refused, with a reason, when the topology cannot share a dataset. |
| `CLOUDCAUSE_API_PORT` | `8000` (default) | Gateway port. |

Copy `.env.example` to `.env` for the full list. Services read it at startup and
real environment variables always win over the file. Secrets belong there or in a
secret manager, never in prompts, logs, or reports.

## Live agents on fixture data

This proves framework orchestration end to end and still needs no cloud account.

```bash
uv sync
OPENAI_API_KEY=... GOOGLE_API_KEY=... uv run cloudcause-api
```

Verified 2026-07-28 against the versions pinned in `uv.lock`: google-adk 2.5.0,
strands-agents 1.50.2, agent-framework 1.12.1.

If a framework errors, that specialist falls back to the deterministic playbooks,
reports `partial`, and records the reason as a report warning. A quota limit or an
SDK change degrades the run instead of losing it.

`/health` reports that both modes are supported, but it cannot predict which one a
user will select for the next investigation. The per-provider status settles whether
a model answered: a real live run
shows `status: ok` with `agent_mode: live` for that provider, while `status: partial`
with `agent_mode: stub` means the framework was requested, failed, and fell back —
with the reason in the report warnings. Note also that the specialist determines the
vendor, so a GCP-only scenario produces Gemini traffic and nothing in an OpenAI log.

What the agent returns is prose over evidence IDs the deterministic layer supplied,
not a recomputed figure. The published executive summary is generated only from the
validated, reconciled deterministic report data, in both live and stub modes.

Every tool in that panel that crosses the evidence boundary is a `get_` reader. The two
that are not, `recalculate_attribution` and `record_finding`, stay inside the process:
one asks the deterministic layer to restate a figure, the other writes the finding into
the report. Tool schemas are date-aware, so a rule is selected by the usage date being
explained rather than by today's date.

## Bring your own data

The demo fixtures answer "does this work". They cannot answer "does this work on
my bill". A third input path does, without a cloud account and without a
connector: upload the cost export your provider already gives you.

![CloudCause upload walkthrough: choose your data, upload an accepted provider cost
export, seal the dataset, and continue to the investigation brief](images/cloudcause-upload.gif)

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

Ready-to-upload synthetic cost exports for AWS, Azure, and Google Cloud are in
[`fixtures/uploads`](../fixtures/uploads/README.md). Each includes matching baseline and
investigation periods so a new user can exercise the complete upload flow without
using billing data from a real account.

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

Completed cases stay available in **History**, where the same evidence-backed dossier
can be reopened without rerunning the investigation.

![CloudCause history walkthrough: review completed cases, reopen a saved dossier, and
return to a new investigation](images/cloudcause-history.gif)

The gateway keeps live jobs in memory because SSE streaming needs a queue, and
writes every state change through to SQL when a history backend is configured.
State, reports, and the progress log then survive a restart, served by the same
endpoints. `GET /health` reports which backend is live.

PostgreSQL is the only persisted backend. There is no file-backed option: a run
either has a database and says so with a DSN, or keeps nothing.

```bash
docker compose -f docker/docker-compose.yml up -d postgres
uv sync --extra postgres
CLOUDCAUSE_HISTORY_BACKEND=postgres CLOUDCAUSE_DATABASE_URL=postgresql://cloudcause:cloudcause-local-only@127.0.0.1:5432/cloudcause   uv run cloudcause-api
```

The backend is inferred from the URL, so naming it is belt and braces — but an
explicit `CLOUDCAUSE_HISTORY_BACKEND=memory` left in a `.env` outranks the URL,
and this makes that visible rather than silently keeping nothing.

Migrations from `packages/worker_core/src/cloudcause_worker_core/migrations/` are
applied on startup and recorded in `cloudcause_schema_migrations`. Account and
subscription identifiers are hashed before they are stored, so the durable copy is
not the raw request. An unreachable database degrades to memory with a warning
instead of failing the gateway or an investigation; a DSN naming a backend that
does not exist is refused by name rather than quietly resolved to something else.

To exercise the PostgreSQL path locally:

```bash
docker compose -f docker/docker-compose.yml up -d postgres
uv sync --extra postgres
CLOUDCAUSE_TEST_DATABASE_URL=postgresql://cloudcause:cloudcause-local-only@127.0.0.1:5432/cloudcause \
  uv run pytest tests/persistence -q
```

Without that variable `tests/persistence` skips itself, which is what keeps
`pytest tests` runnable with no Docker and no database. CI supplies it in the
`postgres-storage` job, so the schema is covered on every push.

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
uv run pytest tests/e2e -q                    # offline end to end + 13 scenarios + both upload tiers
uv run pytest tests/live -m live              # opt-in, needs model keys
uv run python evaluations/run_evaluation.py   # scored metrics report
```

The Next.js UI has its own two gates, run from `web`:

```bash
npm run typecheck                             # contract types match the gateway
npm run build                                 # production build
```

`tests/ui` enforces the thin-client rule: `web/lib/types.ts` must mirror the
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
docker compose -f docker/docker-compose.yml up --build
```

Each service runs in its own process, which is what exercises the `http`
orchestrator and worker transports rather than the in-process default. The image
runs as an unprivileged user and needs no write access to the filesystem.

The stack brings up PostgreSQL and Redis alongside the five application
containers, because splitting into processes is exactly what makes both
necessary:

| Service | What it shares between processes |
| --- | --- |
| **postgres** | Uploaded datasets. Each process rebuilds provider data from a dataset id, so without a shared DSN the ingest endpoints answer 503. It also backs investigation history here. |
| **redis** | The outbound model quota. The limiter is per-process, so on `memory` the orchestrator and both workers would each hold a private OpenAI allowance and the combined rate would be a multiple of the configured one. |

Neither is needed by `make dev` or by the Cloud Run deployment, which run
everything in one process and keep the `memory` default for both.

Redis also requires `CLOUDCAUSE_ID_HASH_SALT`, which Compose defaults to a
local-only value. Admission bucket keys leave the process once the backend is
shared, and an unsalted digest of a client IP is reversible, so the gateway
refuses that combination at startup rather than storing them.

## Deploying to Cloud Run

`terraform/gcp/` deploys two services: the gateway and the web app. The gateway
runs the orchestrator and both workers in process, so the distributed topology
above stays a Compose and local concern rather than five cloud services
([ADR 0012](adr/0012-cloud-deployment-is-single-process.md)). Three GCP APIs are
used and no others — Cloud Run, Artifact Registry, Cloud Build.

Terraform describes infrastructure only; Cloud Build builds the images. Nothing
here needs a local Docker daemon.

This section is first-time setup. To change something already deployed, see
[docs/deployment-runbook.md](deployment-runbook.md).

```bash
cd terraform/gcp
export PROJECT=cloudcause-prod REGION=us-central1 TAG=v1

# 1. Create the registry first, or the push below has nowhere to land.
terraform init
terraform apply -target=google_artifact_registry_repository.containers -var project_id=$PROJECT

# 2. Build both images inside GCP. Source upload is megabytes; the images
#    themselves never cross your connection. --config rather than --tag,
#    because --tag cannot point at a Dockerfile outside the source root.
cd ../..
REPO=$REGION-docker.pkg.dev/$PROJECT/cloudcause

gcloud builds submit --config cloudbuild.yaml . \
  --substitutions=_DOCKERFILE=docker/Dockerfile,_IMAGE=$REPO/api:$TAG

gcloud builds submit --config cloudbuild.yaml . \
  --substitutions=_DOCKERFILE=docker/Dockerfile.web,_IMAGE=$REPO/web:$TAG

# 3. Deploy. The web_url output is the one to open.
cd terraform/gcp
terraform apply -var project_id=$PROJECT -var image_tag=$TAG
```

Live agents are opt-in and cost money outside any GCP credit, since both keys are
billed by their own provider:

Pass them as `TF_VAR_` environment variables rather than `-var`, so the values stay
out of your shell history and out of the process arguments any other user on the
machine can read:

```bash
read -rs TF_VAR_openai_api_key && export TF_VAR_openai_api_key
read -rs TF_VAR_google_api_key && export TF_VAR_google_api_key
terraform apply -var project_id=$PROJECT -var image_tag=$TAG
```

Even so, the keys land in the Cloud Run revision as plain environment variables and
in `terraform.tfstate` in cleartext. That is the accepted trade of keeping this
deployment to three GCP APIs
([ADR 0012](adr/0012-cloud-deployment-is-single-process.md)): fine while the project
is yours alone, not fine once anyone else can reach the project or the state file.
Move to Secret Manager before that point.

Three defaults worth knowing, all changeable with `-var`:

* `api_min_instances = 0` scales the gateway fully to zero when idle. With
  `cpu_idle = false` a warm instance bills 2 vCPU and 2 GiB continuously — around
  $100/month for something idle almost all of the time — so zero is the honest
  default for a demo. The cost is a cold start on the first visit after a quiet
  period: Cloud Run pulls the image, then the gateway reaches `/health` in about
  4 seconds. Set `-var api_min_instances=1` while actively sharing the link.
* `uploads_enabled = true`, because a cost investigator that cannot read a bill
  demonstrates nothing. The gateway is unauthenticated, so anyone reaching it can
  post a file; what bounds that is [ADR 0005](adr/0005-uploads-are-sealed-server-parsed-datasets.md) —
  bytes are parsed from the request stream and discarded, nothing raw reaches
  disk, and no row value is logged. Set `-var uploads_enabled=false` for a
  deployment where even that is too much.
* `image_tag` is what triggers a new revision. Cloud Run compares the image
  string, so re-pushing the same tag deploys nothing — use `v2` or a git sha.

## Running with live agents

The offline default is a complete system run, but not an AI-quality test:
deterministic playbooks produce the findings, no key is read, and no request leaves
the machine. Live mode swaps those playbooks for the three hosted AI frameworks on
the same fixture data, while arithmetic, validation, and reconciliation remain
deterministic in both modes.

**There is no mode to configure.** Add a model key and both paths are live in the
same process; choose **Live AI agents** or **Deterministic playbooks** in the brief
for each run — no mode switch, no restart between runs, no second deployment.

```bash
# .env — this is the entire setup
OPENAI_API_KEY=sk-...      # AWS (Strands) and Azure (Agent Framework)
GOOGLE_API_KEY=...         # GCP (ADK)
```

Which models those keys drive:

| Specialist | Framework | Model |
| --- | --- | --- |
| Google Cloud | Google ADK | `gemini-3.5-flash-lite` |
| AWS | AWS Strands | `gpt-4.1-mini` |
| Azure | Microsoft Agent Framework | `gpt-4.1-mini` |

Override either with `CLOUDCAUSE_GEMINI_MODEL` or `CLOUDCAUSE_OPENAI_MODEL`. The
Gemini default is picked for free-tier headroom rather than peak capability.
Quotas vary by account tier, project, and model; check the active project quota
in Google AI Studio / GCP console to avoid 429 RESOURCE_EXHAUSTED errors. On a
paid key, `gemini-3.6-flash` reasons better.

```bash
uv sync
uv run cloudcause-api
```

Real environment variables always win over the file. Deterministic playbooks stay
the default for each new brief, so a run costs money only when someone picks the
live path for it.

The brief offers the live option exactly when the gateway reports it can serve one,
so the choice never silently turns into something else:

```bash
curl -s localhost:8000/health     # live_agents_available: true
```

Every run records which path it took, so a finished report answers the same question
after the fact: findings and the report carry `agent_mode`, and the exported Markdown
prints it in the header. Confidence is not the tell — it is derived from the evidence
gathered and the rule that was matched, and the deterministic run above reaches 0.87
without a model.

The provider's own request log is the outside view of the same run, and it shows the
division of labour from [ADR 0002](adr/0002-deterministic-arithmetic.md) as
executed calls: the agent asks for the plan, the candidates, and the evidence, then
records a finding. It never asks for a total, because the total was already computed in
Python before the agent was invoked.

