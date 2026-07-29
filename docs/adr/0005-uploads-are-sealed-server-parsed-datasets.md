# ADR 0005: An upload is a sealed, server-parsed dataset addressed by id

* Status: Accepted
* Date: 2026-07-28
* Deciders: Rashed Khan
* Scope: `packages/datasets`, `packages/focus_normalizer`, `apps/api`, `apps/web`

## Context

The investigation engine was general but its inputs were not reachable: a stranger
could run the demo fixtures or the twelve seeded scenarios and nothing else. Letting
someone bring their own cost export is what turns a demo into a tool.

Two questions had to be answered before any code: where the file is parsed, and how
the parsed result reaches the processes that need it.

The second question is not a preference. `scenario_id` is the only data selector
that crosses a process boundary, and it is resolved *independently* in the
orchestrator, in the worker engine, and in `packages/mcp_servers/tools.py` — where
the call site is a stdio subprocess that receives its selector as an environment
variable. Under Compose those are three containers plus a child process. **Data does
not travel in the request; each process rebuilds it from an id.** Any upload design
that assumes a single process is wrong on arrival.

## Decision

**Parsing is server-side.** The uploaded bytes are parsed by the same
`packages/focus_normalizer` parsers a live provider connector will use —
`parse_aws_rows`, `parse_azure_cost_management`, `parse_gcp_billing_export`.

**An upload becomes a dataset:** created empty, filled one source at a time, then
**sealed**. Sealing makes it immutable, which is what makes it safe for three
processes and a subprocess to read concurrently mid-investigation. An unsealed
dataset cannot start an investigation, and `dataset_id` joins `scenario_id` as a
selector every reader resolves for itself, including via `CLOUDCAUSE_DATASET_ID`
injected into the MCP child.

**Nothing raw is persisted.** Bytes are read from `Request.stream()`, parsed,
aggregated to the daily grain `packages/anomaly_detection` consumes, and discarded.
What is stored is normalized contract objects plus one `Provenance` per source.

**There is no multipart.** Starlette's `UploadFile` is a `SpooledTemporaryFile` that
flushes to a real temp file above 1 MB, so the obvious FastAPI signature would break
"nothing raw on disk" for every file large enough to matter. One file per request,
raw body, addressed by `{provider}/{kind}` in the path — so no filename is ever
accepted, and there is none to echo back or use as a path.

**The dataset store never degrades.** History deliberately falls back to memory when
its database is unreachable, because losing history must not fail an investigation.
A dataset must do the opposite: memory when everything is in-process, SQL when the
orchestrator or workers are over HTTP, and a `503` naming the missing DSN when
neither is possible.

## Rationale

Server-side parsing keeps one source of truth for the number the product's
credibility rests on. Reimplementing three provider parsers in TypeScript would
create a second implementation of the arithmetic that ADR 0002 exists to protect,
and it would be the copy nobody unit-tests. Browser parsing also buys less privacy
than it appears to: the normalized rows still have to reach the server for the
orchestrator to compare and the workers to build evidence, and the raw file is never
persisted anyway.

Refusing to let the dataset store degrade is the same instinct as the reconciliation
gate. A dataset only the gateway can see would produce a mid-run "unknown dataset"
or — far worse — a silent fall-through to the demo fixtures under an uploaded label.
A run that reports demo numbers against somebody's own filename is the exact class
of quiet wrongness this project exists to refuse. Failing loudly at ingest is
strictly better than succeeding with fiction.

One contract trap is worth recording because it is not obvious. `Provenance` gained a
third origin (`fixture`, `upload`, `live`) and the temptation was to make the old
`is_fixture` flag a computed property. That breaks the worker HTTP contract:
`Provenance` inherits a model with `extra="forbid"`, pydantic serializes computed
fields on output, and `WorkerResponse.sources` crosses HTTP when
`worker_mode=http` — so serialize-then-validate would fail with "extra inputs are
not permitted". `is_fixture` therefore stays a declared field, defaulted from
`origin` by a validator.

## Alternatives considered

**Parse in the browser.** Rejected for the two-sources-of-truth problem above. A 200 MB
CUR parsed in a tab also freezes the tab or needs a web worker plus a streaming CSV
library, where the same work in Python is a bounded, cancellable, measurable request.
Browser *pre-aggregation* is deferred rather than rejected: it is a pure transport
optimization that can land later without changing the server contract.

**Send the parsed data in the investigation request.** Rejected because it contradicts
the topology. Four independent readers resolve the selector themselves, one of them
through a subprocess environment variable, so payload-in-request would have to be
replaced by an id anyway the moment anything ran over HTTP.

**Multipart upload with `UploadFile`.** Rejected: it writes temp files, adds a
`python-multipart` dependency, accepts a filename the system has no use for, and puts
fifteen files inside one request's time budget instead of giving each its own.

**Mirror the history store exactly, degradation included.** Rejected. The two have
opposite failure requirements, which is the whole point of the ADR.

## Consequences

Accepted:

* A dataset lives two hours and is not extendable by use. The report it produced is
  kept in history; the data behind it is not. Re-running a stored investigation whose
  dataset has expired answers `409 dataset_expired` rather than a `404` that reads
  like a bug.
* Uploads in the Compose topology require the same `CLOUDCAUSE_DATABASE_URL` on the
  orchestrator and both workers. This is wiring the feature cannot work without, so
  it is enforced with a refusal rather than documented as a caveat.
* One currency per dataset, or the file is refused. `comparison.py` assigns currency
  last-row-wins, so a mixed-currency upload would sum unlike money under one
  arbitrary label.
* The gateway is unauthenticated, and uploads make that matter more.
  `CLOUDCAUSE_UPLOADS_ENABLED=false` turns the surface off; dataset ids are
  unguessable but that is the only separation between two users' data. A public
  deployment needs real multi-tenancy first, which the README states outright.

Enforced by `tests/unit` (per-provider parsing, aggregation, `data_through`,
mixed-currency rejection), `tests/gateway` (every refusal path), `tests/persistence`
(restart, TTL, store caps, SQL failure raising rather than degrading), and
`tests/security` (no temp file created during ingest, no row values in logs).
