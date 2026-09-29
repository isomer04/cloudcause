# Plan — Local-only live cost connectors (Azure, AWS, GCP)

> **Status: implemented** on `feat/local-live-connectors`. The record of what was
> built is [ADR 0015](adr/0015-live-connectors-are-local-only.md); the operator
> guide is [docs/usage.md](usage.md#reading-your-own-bill-locally). AWS has been
> run against a real account end to end (cost-only bundle, honest
> `unexplained_increase` report) and its response under
> `tests/data/live_responses/` is recorded from that account. Azure and GCP are
> implemented; their mappers are unit-tested against hand-written wire-shape
> responses and their fetch plumbing against a faked network
> (`tests/unit/test_live_azure_fetch.py`, `tests/unit/test_live_gcp_fetch.py`).
> Azure's not-logged-in path has been exercised against the real CLI. The
> BigQuery dataset `cloudcause-prod.billing` (US multi-region) exists and is
> waiting for the detailed billing export to be enabled in the console. The
> remaining step for each is an `az login` / the enabled export, then
> `make live-cloud` and a re-recording.

Read a real cloud bill directly instead of uploading an export, **without putting a
credential anywhere near the deployed service**.

**Be honest about what this is.** [ADR 0014](adr/0014-cloudcause-never-holds-cloud-credentials.md)
considered exactly this option — *"Run the connector only locally, never deployed"* —
and rejected it. Not on safety: the record calls it *survivable*, because the
credentials are the developer's own, in their own environment. It was rejected on
**maintenance cost**: a code path CI cannot exercise honestly, that every future change
keeps working blind. So this plan does not "narrow" ADR 0014. It takes an alternative
that record turned down, and it has to answer the objection that turned it down.

The answer is in Track C: every connector is split into an SDK call and a pure mapper,
the mapper is unit-tested offline against **recorded, scrubbed responses**, and only the
SDK call is opt-in. That leaves a thin blind seam rather than a blind code path.
ADR 0015 records the reversal of that one rejection explicitly.

Everything else ADR 0014 refuses — customer credentials, a deployed connector,
multi-tenant secret storage — stays refused.

---

## Why this is safe, stated precisely

The property that makes this a small change rather than a security project:

`terraform/gcp/main.tf:118` hardcodes `CLOUDCAUSE_DATA_MODE = "fixtures"` on the Cloud
Run revision, and `settings.py:166` defaults to `fixtures`. `registry.py:48` is the
only place live mode dispatches. `Settings.with_overrides` is called from exactly one
place in the request path (`engine.py:65`) and only ever overrides `agent_mode`. So a
live connector is unreachable in the deployment unless somebody edits Terraform and
redeploys — it is not a flag a visitor can flip, and not a fallback a misconfiguration
can land on.

**That pin is the whole safety argument. Do not remove it, and do not add a way to set
`CLOUDCAUSE_DATA_MODE` from a request, a header, the dataset API, or
`with_overrides`.** If a step in this plan seems to need that, the step is wrong.

The `data_mode` fields on `InvestigationRequest`, `WorkerResponse` and the report are
*reporting* fields. They say what mode a run executed in. They must never become
inputs.

---

## The copy-paste handoff prompt

Paste this into a fresh session at the repo root.

```
You are working in the CloudCause repo (Python 3.11+, uv workspace, packages/ layout).

Read these before writing any code:
  docs/handoff-local-live-connectors.md   (this plan)
  docs/adr/0014-cloudcause-never-holds-cloud-credentials.md
  docs/adr/0002-deterministic-arithmetic.md
  docs/adr/0006-cost-only-data-cannot-name-a-cause.md
  docs/adr/0008-offline-first-evaluation-and-graceful-fallback.md
  docs/testing-strategy.md
  packages/providers/src/cloudcause_providers/protocols.py
  packages/providers/src/cloudcause_providers/live.py
  packages/providers/src/cloudcause_providers/uploads.py     (the -absent provenance pattern)
  packages/providers/src/cloudcause_providers/registry.py
  packages/focus/src/cloudcause_focus/parsers.py
  packages/datasets/src/cloudcause_datasets/ingest.py        (the _*_data_through helpers)
  packages/worker_core/src/cloudcause_worker_core/engine.py  (get_bundle is how a run reads data)
  tests/contract/test_provider_adapters.py

Non-negotiable constraints - do not violate them:
  - Money is deterministic Python. No model computes or adjusts a dollar figure.
  - Every provider method is read-only. Nothing may mutate a cloud resource.
  - A credential never enters a prompt, a log line, an error message, a dataset,
    or the database. Not even redacted.
  - The Cloud Run deployment stays pinned to CLOUDCAUSE_DATA_MODE=fixtures.
    Nothing in this work may make live mode reachable from an HTTP request.
  - Offline CI stays green with no cloud account, no cloud SDK installed, and no
    credentials. Every test that touches a cloud API is opt-in and skipped by default.
  - Provider adapters are interchangeable. Business logic and agents see the
    protocol, never a cloud SDK client.
  - Every connector is an SDK call plus a pure mapper. The mapper takes the raw
    response and returns list[CostRecord], and is tested offline against a
    recorded, scrubbed response checked into tests/data/live_responses/.

Work one step at a time. After each step run the offline suite and stop for review:
  make test      (or: uv run pytest tests -q)
```

---

## The shape every connector shares

Read this before any track. Three things were wrong in the first draft of this plan
and all three are structural, so they are fixed here once.

### 1. The four unimplemented methods must return *absent*, not raise

`engine.py:83` builds a run with `data_provider.get_bundle(...)`, and `get_bundle`
(`protocols.py:65`) calls all five methods. The orchestrator (`orchestrator.py:118`)
calls `get_resources()` right after `get_costs()` and skips the whole provider on any
exception. So if `get_resources` keeps raising `LiveModeNotConfiguredError`, **a live
run does not degrade to `unexplained_increase` — it fails, or the provider is skipped
with "no cost data for this request".** The first draft claimed the opposite.

The upload provider already solves this. `uploads.py:48`, `_absent_provenance`,
returns an empty `SourceResult` whose provenance says `source="<kind>-absent"` and
`query_reference="...#not-supplied"`. Do the same: `get_resources`, `get_metrics`,
`get_audit_events` and `get_recommendations` return an empty result with
`origin="live"`, `source=f"{provider}-{kind}-absent"`, and `data_through` equal to the
cost result's boundary. ADR 0006 then does exactly what it does for a cost-only
upload, and nothing downstream needs to change. **Confirm this with a real bill before
writing the second connector** — it is the cheapest check that the boundary holds.

`LiveModeNotConfiguredError` survives for one purpose: `get_costs` on a provider whose
extra is not installed or whose config is missing. Its message changes from "no
connector, deliberately" to "install `cloudcause[<provider>]` and set
`CLOUDCAUSE_<PROVIDER>_...`". The absent methods never raise it and never import an SDK.

### 2. SDK call and mapper are separate functions

```
_fetch_<provider>(settings, window) -> raw response      # opt-in, never runs in CI
map_<provider>_response(raw) -> list[CostRecord]         # pure, tested offline
```

Where an existing parser already reads the raw shape (Azure Query, GCP export), the
mapper is a thin wrapper that reshapes and delegates. Where it does not (AWS Cost
Explorer), the mapper is the new code. Either way the recorded response in
`tests/data/live_responses/<provider>.json` is the offline contract, and the `cloud`
test re-records nothing — it asserts a real call parses to the same shape.

### 3. Freshness is a clock question, and the connector has a clock

The upload path cannot know what "today" was when a file was exported. A connector
can. So every connector, before calling anything:

* never requests the current UTC day — the window is clipped to yesterday;
* sets `data_through` to the end of the **last day the response actually covers as
  complete**, using each provider's own signal (below), never the requested end date.

`context.py:78` already turns an honest `data_through` into the "data is complete only
through …" warning and `evidence.py:195` into an evidence item, and the
`aws-delayed-billing-data` scenario exists because a stale tail read as a spend drop
produces a confident wrong answer. There is no note field on `Provenance`; the honest
`data_through` **is** the note, and the existing machinery renders it.

### 4. Errors carry the class, never the message

SDK exceptions are chatty. Azure's include the request URL and tenant, boto's include
the ARN and the request id, BigQuery's echo the SQL. None of that is a secret, but the
scrubbing rule this repo already follows for uploads
(`test_a_rejection_names_the_column_and_row_but_never_the_value`) is "name the shape,
never the value". Wrap every SDK failure in `LiveConnectorError(f"{provider}: "
f"{type(exc).__name__}" + optional HTTP status)` and log the same string at INFO.
Never format `str(exc)` into the message or a log line. That makes C3 a rule the
tests can pin rather than a hope.

### 5. Settings carry identifiers, never secrets, and the registry passes them in

`registry.py:50` currently instantiates `_LIVE_PROVIDERS[provider]()` with no
arguments. It has to become `_LIVE_PROVIDERS[provider](settings)`. `Settings` gains
`azure_subscription_id`, `gcp_billing_table`, and nothing for AWS. Each is an
identifier, read by `_flag`-style helpers in `from_env`, and none of them is a
credential. Do not add a setting whose value would be a secret, a key path, or a
token; the ambient credential chain of each SDK is the only source, on purpose.

---

## Track A — Azure

Do this first. It is the smallest connector and it proves the shape before AWS
introduces a mapping problem.

**Why it is easiest, concretely:** the Cost Management Query API returns
`{"properties": {"columns": [...], "rows": [...]}}`, and
`parse_azure_cost_management` (`parsers.py:146`) already reads exactly that envelope —
it does `document.get("properties", document)` then `columns` / `rows`. The response
goes into the existing, already-tested parser. There is no new parsing code to write,
which also means no new place for a number to be got wrong.

### A1. Dependencies as an optional extra

Follow the `postgres` precedent in `pyproject.toml:26` — an extra, never a base
dependency, so the default install stays free of cloud SDKs.

```toml
azure = [
    "azure-identity>=1.17",
]
```

`azure-mgmt-costmanagement` is not needed. `httpx` is already a runtime dependency of
`worker_core`, the Query API is one `POST`, and the raw JSON body is what the parser
wants. The SDK would only wrap the response in a model you then unwrap with
`.as_dict()`. Fewer packages, and the recorded response is the actual wire shape.

Import `azure.identity` **inside** the fetch function, not at module scope. `live.py`
is imported by `registry.py` on every run including offline ones. A missing extra must
raise `LiveModeNotConfiguredError` with the install instruction, not an `ImportError`
traceback.

### A2. Credentials — not `DefaultAzureCredential`

The first draft said `DefaultAzureCredential` and, in the same breath, "never read a
client secret from the environment". Those contradict: `DefaultAzureCredential`
includes `EnvironmentCredential`, which reads `AZURE_CLIENT_SECRET`. The existing
contract test sets that variable to a sentinel precisely to prove nothing reads it.

Use `AzureCliCredential` explicitly (optionally chained with
`AzureDeveloperCliCredential`). Locally that is `az login`; there is nothing to
configure and nothing to store. If it cannot resolve an identity, fail with the
`az login` instruction. Do not add a fallback that takes a secret from anywhere.

Subscription id comes from `CLOUDCAUSE_AZURE_SUBSCRIPTION_ID`. Token scope is
`https://management.azure.com/.default`. Role needed: **Cost Management Reader** on
the subscription.

### A3. The fork: the Query API allows two groupings, not five

The first draft grouped by `ResourceId`, `MeterCategory`, `Meter`, `ResourceLocation`
and `ChargeType`. The API reference for `QueryDataset.grouping` says *"Query can have
up to 2 group by clauses"* (and two aggregation clauses). So the same choice AWS faces
in Track B exists here:

| | Query API (`POST .../providers/Microsoft.CostManagement/query`) | Cost Details report (`generateCostDetailsReport`) |
| --- | --- | --- |
| Setup | none | none |
| Shape | `columns`/`rows`, **parser reuses verbatim** | async: submit, poll, download CSV; **new mapper** |
| Detail | 2 groupings; the rest is lost | every column: charge type, meter, location, tags |
| Volume | small, paged via `nextLink` | one CSV per request, can be large |

**Recommendation: Query API, grouped by `ResourceId` and `MeterCategory`.** Those two
are what `resource_key()` and `service_category()` key on, so the comparison and the
playbooks see what they need. What is lost, and must be said in the docstring and the
ADR: `ChargeType` (so every record maps to `charge_category="usage"`, and a tax or
refund line is indistinguishable from usage), `Meter` (`sku_id` and
`charge_description` are empty), `ResourceLocation` (`region_id` is `None`). If that
coarseness turns out to matter, the Cost Details report is its own track with its own
mapper, exactly like CUR for AWS.

Request body: `type: "ActualCost"`, `timeframe: "Custom"`, `timePeriod` = the union
of the requested `DateRange`s clipped to yesterday, `dataset.granularity: "Daily"`,
`aggregation: {"totalCost": {"name": "Cost", "function": "Sum"}}`, and the two
groupings. Follow `nextLink` until absent and **concatenate `rows` across pages
before parsing** — `source_record_id` is the row index, so parsing page by page would
mint duplicate ids. Handle `429` with the `Retry-After` header; the Query API rate
limits aggressively.

Hand the merged document to `parse_azure_cost_management`. Return
`SourceResult[CostRecord]` with `origin="live"`, `source="azure-cost-management"`.

### A4. Freshness

`_azure_data_through` (`ingest.py:658`) does **not** solve the partial-day problem;
read it. It says, correctly for a file, that daily grain carries no intraday signal,
takes the last date as complete, and attaches a caveat. The first draft pointed at it
as the rule to reuse. It is the rule to *improve on*, because a connector knows the
clock. Clip the window to yesterday, then set `data_through` to the end of the last
day that has at least one row. If the last requested day came back empty, that is
Azure's lag, and `data_through` says so.

### A5. The other four methods

Return absent, per the shared shape above. Do not raise.

---

## Track B — AWS

**Correction to a claim made in ADR 0014 and worth stating plainly here:** that record
says a connector should "reuse the FOCUS normalizer, not the upload code". That is
true for the CUR path and **false for the Cost Explorer path**. Cost Explorer returns
`ResultsByTime[].Groups[].Keys/Metrics` — nothing like CUR's flat columns, and
`parse_aws_rows` (`parsers.py:107`) cannot read it. Choose knowingly:

| | Cost Explorer `GetCostAndUsage` | CUR 2.0 → S3 |
| --- | --- | --- |
| Setup | none, works immediately | configure export, wait ~24h for first delivery |
| Cost | ~$0.01 per request | S3 storage only |
| Detail | 2 group-by keys; no resource ids | resource-level plus tags natively |
| Parsing | **new mapper needed** | `parse_aws_rows` reuses verbatim |
| Format | JSON | partitioned Parquet |

**Recommendation: start with Cost Explorer.** It works today with no account changes,
which is the entire point of this plan; the per-request cost is irrelevant for
personal use. Write the mapper as a separate, unit-testable function taking the API
response and returning `list[CostRecord]`, so swapping to CUR later replaces one
function rather than the connector.

If you later want CUR, it needs `pyarrow` and an S3 read path, and that is a bigger
change than it looks — treat it as its own track.

### B1. Dependencies

```toml
aws = [
    "boto3>=1.34",
]
```

Same rules as A1: optional extra, imported inside the fetch function.

### B2. Credentials

Let `boto3` resolve the default chain — profile, SSO, environment, instance role.
Respect `AWS_PROFILE` and `AWS_REGION` and add nothing of your own. Do not introduce a
`CLOUDCAUSE_AWS_ACCESS_KEY_ID` setting; the whole point is that CloudCause never holds
the credential, only borrows the caller's ambient one.

Note for C2: the default chain *does* read `AWS_ACCESS_KEY_ID` from the environment.
That is the ambient credential, and it is fine. What the test pins is that the value
never appears in a message or a log, not that the SDK refuses to look at it. Cost
Explorer is a global service; call it against `us-east-1`.

Permissions: `ce:GetCostAndUsage` and `sts:GetCallerIdentity` (B3 says why). Add the
latter to `LiveAwsDataProvider.required_roles`.

### B3. `LiveAwsDataProvider.get_costs`

`ce:GetCostAndUsage`, `Granularity="DAILY"`,
`Metrics=["UnblendedCost", "AmortizedCost", "UsageQuantity"]`, grouped by `SERVICE` and
`USAGE_TYPE`. Follow `NextPageToken`.

Three things the first draft got wrong or left out:

* **`End` is exclusive.** `DateRange.end` is inclusive. Pass `end + 1 day`, and clip
  to yesterday first. An off-by-one here silently drops the last day of every window.
* **`RESOURCE_ID` is not a `GroupBy` key on this API.** The valid `DIMENSION` keys are
  listed in the reference and it is not among them. Resource-level data is a separate
  operation, `GetCostAndUsageWithResources`, gated on the paid hourly/resource opt-in
  and limited to the last 14 days. So on this path every `CostRecord` has
  `resource_id=None` and `resource_key()` falls back to the service-level aggregate.
  That is not a bug — it is Cost Explorer being coarser than CUR, and it must be
  visible in the docstring and the ADR rather than silently producing a thinner
  report.
* **Two group-by keys means no `LINKED_ACCOUNT` and no `RECORD_TYPE`.**
  `billing_account_id` is required on `CostRecord`: get it from one
  `sts:GetCallerIdentity` call, which is read-only and free. `RECORD_TYPE` is what
  separates usage from tax, credit and refund; without it the mapper would label a
  refund as usage, the exact confusion `_AWS_CHARGE_TYPES` exists to prevent. Issue one
  query per record type via `Filter.Dimensions.RECORD_TYPE` — `Usage`, `Tax`, `Credit`,
  `Refund`, `Fee`, plus whatever the account has — and let the mapper set
  `charge_category` from the filter it was issued under. A handful of $0.01 calls.

Map `UnblendedCost` → `billed_cost` and `AmortizedCost` → `effective_cost`, matching
what `parse_aws_rows` does for CUR, so the two AWS paths agree. `UsageQuantity.Unit`
is the `usage_unit`; the AWS docs warn that usage summed across usage types is
meaningless, which is why `USAGE_TYPE` is one of the two groupings.

### B4. Freshness

**Corrected after recording a real account.** The first draft said to use
`ResultsByTime[].Estimated`. The recording showed it `true` for every day of the
unbilled month, three days back included: it means "not yet invoiced", not "not yet
reported", and using it would declare the whole current month incomplete. Set
`data_through` to the end of the **last day with any group**, the same rule Azure's
daily grain gets. Days Cost Explorer has not reported yet come back with no groups.

### B5. The other four methods

Return absent. Do not raise.

---

## Track C — Cross-cutting work

### C1. A new pytest marker, and recorded responses

`pyproject.toml:68` sets `addopts = "-m 'not live' --strict-markers"`, and the existing
`live` marker means *"requires model API keys and real agent frameworks"*. Cloud
credentials are a different axis — a machine can have `az login` and no `OPENAI_API_KEY`
or the reverse. **Add a separate `cloud` marker** rather than overloading `live`:

```toml
markers = [
    "live: requires model API keys and real agent frameworks (excluded from offline CI)",
    "cloud: requires real cloud credentials and reads a real bill (excluded from offline CI)",
]
addopts = "-m 'not live and not cloud' --strict-markers"
```

Opt-in tests go in `tests/live/test_cloud_connectors.py` and **skip**, not fail, when
the credential, the config, or the extra is absent. A developer without an Azure
account must be able to run the whole suite.

Offline mapper tests go in `tests/unit/` and read
`tests/data/live_responses/<provider>.json`. Record each file once from a real call,
then scrub it: replace account, subscription and project ids with the fixture ids
already used in `fixtures/`, round nothing, and drop nothing structural. The file is
the contract; the `cloud` test asserts a fresh call still maps to the same
`CostRecord` field set. This is the answer to ADR 0014's maintenance objection, so it
is not optional.

### C2. Two existing tests break, by design

Not one. `tests/contract/test_provider_adapters.py` has:

* `test_live_mode_fails_loudly_instead_of_silently_using_fixtures` (line 130) — asserts
  `get_costs` raises with "deliberately" and "ADR 0014" for every provider.
* `test_no_live_adapter_reads_a_credential_from_the_environment` (line 152) — asserts
  *every* live method raises `LiveModeNotConfiguredError`.

Do not delete them — they pin ADR 0014's property. Replace them with what is true now:

* Without the extra installed, `get_costs` raises `LiveModeNotConfiguredError` naming
  the extra, and the sentinel set in `AZURE_CLIENT_SECRET` / `AWS_SECRET_ACCESS_KEY` /
  `GOOGLE_APPLICATION_CREDENTIALS` is absent from `str(error)`.
* The four absent methods return an empty `SourceResult` whose `source` ends in
  `-absent`, **and no cloud SDK module appears in `sys.modules` afterwards**. That is
  the offline proof that those methods cannot touch a credential.
* The error wrapper: given a fake SDK exception whose message contains a sentinel, the
  `LiveConnectorError` it produces does not contain it. Pure function, no SDK needed.

### C3. The credential must not reach a log line

`tests/security/test_upload_safety.py` already establishes the pattern for this
(`test_no_actor_identity_reaches_a_log_line`). Add the equivalent: with `caplog` at
DEBUG, drive the error wrapper and the absent methods, and assert the sentinel is not
in `caplog.text`. The SDKs log at DEBUG and are easy to misconfigure; the rule in
"Errors carry the class" above is what makes this pass.

### C4. ADR 0015

Short record: *live connectors are local-only; the deployed service still holds no
credentials*. It must say, in this order:

1. ADR 0014 considered and rejected the local-only connector on maintenance cost; this
   record takes that option and answers the objection with the mapper split and
   recorded responses (C1). It does not reopen anything else ADR 0014 closed.
2. The Terraform `fixtures` pin is the enforcement.
3. Only `get_costs` is implemented; the other four return absent, so a live run
   degrades to `unexplained_increase` under ADR 0006.
4. What each connector loses relative to its export: Azure two groupings, AWS no
   resource ids, GCP nothing before the export was enabled.
5. Customer credentials and a deployed connector remain refused.

Add it to `docs/adr/README.md`.

### C5. Docs

- `docs/usage.md:14` — the `CLOUDCAUSE_DATA_MODE` row says live "has no connector by
  design". That becomes: local-only, cost only, per-provider extras and env vars.
- `docs/architecture.md:168` — the MCP tree line noting no live connector.
- `docs/architecture.md:321` already lists "BigQuery Data Viewer/Job User" for GCP;
  `live.py` lists only Data Viewer. Make them agree (Job User is required, see D2).
- `packages/contracts/src/cloudcause_contracts/settings.py:19-22` — the module
  docstring says live "will not get one". It was missed in the first draft.
- `packages/providers/src/cloudcause_providers/live.py` module docstring and
  `required_roles` (add `sts:GetCallerIdentity`, add BigQuery Job User).
- `README.md` if it claims the system cannot reach a cloud account.

---

## Track D — GCP

The first draft put GCP out of scope because there is no direct cost API. That is
true and it is not a reason to skip it; it is a reason to do it last and to say
plainly what it costs the operator. Everything in the shared shape applies.

### What GCP actually offers

* **The Cloud Billing API does not return spend.** It covers billing accounts and SKU
  prices. There is no equivalent of Cost Explorer or the Query API.
* **The sanctioned path is the BigQuery billing export.** You enable it once in the
  billing console; Cloud Billing then writes rows into a dataset you own. There are
  two useful exports:
  * *Standard usage cost* → table `gcp_billing_export_v1_<BILLING_ACCOUNT_ID>`.
  * *Detailed usage cost* → table `gcp_billing_export_resource_v1_<BILLING_ACCOUNT_ID>`,
    which adds `resource.name` and `resource.global_name`.

  `parse_gcp_billing_export` (`parsers.py:185`) reads `resource.global_name` /
  `resource.name` for `resource_id`, so **enable the detailed export**. On the standard
  export every record has `resource_id=None`, the same coarseness as AWS Cost Explorer.
* **Backfill depends on where the dataset lives.** Google's docs: a US or EU
  multi-region dataset gets data *"exported retroactively from the start of the
  previous month"*; a regional dataset gets data only *"from the date you enable Cloud
  Billing export, and after"*. Retroactive export can take *"up to five days"* to
  finish, and there are *"no delivery or latency guarantees"*. So: create the dataset
  in the US multi-region, enable the export today, and expect a usable baseline
  within a week. There is no way to read last quarter if the export was not running
  last quarter.

This is the one connector where the operator has to change their account first, and
the plan says so rather than pretending otherwise.

### D1. Dependencies

```toml
gcp = [
    "google-cloud-bigquery>=3.25",
]
```

Same rules as A1. `google-auth` comes with it.

### D2. Credentials — Application Default Credentials, and the trap

The BigQuery client uses ADC. **`gcloud auth login` does not set ADC.** The command is

```
gcloud auth application-default login
```

and forgetting the difference is the single most common GCP local-auth failure. The
`LiveModeNotConfiguredError` message for a missing credential must name that exact
command. Do not add a setting for a service-account key path; if the operator wants
one, `GOOGLE_APPLICATION_CREDENTIALS` is the SDK's own variable and none of
CloudCause's business.

Config: `CLOUDCAUSE_GCP_BILLING_TABLE` as a fully qualified
`project.dataset.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX`. Validate it
against a strict pattern before it goes anywhere near SQL — a table name cannot be a
query parameter, so this is the one string interpolated into the query and it must be
proven safe first:

```
^[a-z][a-z0-9-]{4,28}[a-z0-9]\.[A-Za-z0-9_]{1,1024}\.gcp_billing_export(_resource)?_v1_[0-9A-F_]+$
```

Roles: **BigQuery Data Viewer** on the dataset and **BigQuery Job User** on the
project the query runs in. The second one is what people forget; `live.py`'s
`required_roles` lists only the first.

### D3. `LiveGcpDataProvider.get_costs`

One parameterised query, aggregated in SQL so the rows are small and the parser reads
them unchanged:

```sql
SELECT
  billing_account_id,
  DATE(usage_start_time)                        AS usage_start_time,
  MAX(usage_end_time)                           AS usage_end_time,
  service.description                           AS service_description,
  sku.id                                        AS sku_id,
  sku.description                               AS sku_description,
  location.location                             AS location_location,
  resource.name                                 AS resource_name,
  resource.global_name                          AS resource_global_name,
  currency,
  ANY_VALUE(usage.unit)                         AS usage_unit,
  SUM(usage.amount)                             AS usage_amount,
  SUM(cost)                                     AS cost,
  SUM((SELECT IFNULL(SUM(c.amount), 0) FROM UNNEST(credits) c)) AS credits_amount,
  ANY_VALUE(TO_JSON_STRING(labels))             AS labels
FROM `<validated table>`
WHERE _PARTITIONTIME >= TIMESTAMP(@partition_floor)
  AND usage_start_time >= TIMESTAMP(@window_start)
  AND usage_start_time <  TIMESTAMP(@window_end_exclusive)
GROUP BY 1, 2, 4, 5, 6, 7, 8, 9, 10
```

Points that are easy to get wrong:

* **Filter on `_PARTITIONTIME`, not only on `usage_start_time`.** The export tables
  are ingestion-time partitioned and Google's own query examples filter on
  `_PARTITIONTIME`. Filtering only on `usage_start_time` scans the whole table on
  every run. Usage for a day can land in a partition several days later, so the
  partition floor is `window_start - 7 days`, and the `usage_start_time` predicate
  does the precise cut.
* **Aliases cannot contain dots.** The parser wants `service.description`,
  `sku.id`, `usage.amount`, `credits.amount`, `location.location`,
  `resource.global_name`. BigQuery column names cannot. The mapper renames
  `service_description` → `service.description` and so on — one dict comprehension —
  then calls `parse_gcp_billing_export`. That rename is the whole GCP mapper.
* **`credits` is a repeated record.** The parser reads a scalar `credits.amount`; the
  subquery above flattens it. `labels` is also repeated; `TO_JSON_STRING` yields the
  `[{"key":…,"value":…}]` form `_as_tags` already understands.
* **Window end is exclusive** in the query; `DateRange.end` is inclusive. Same
  off-by-one as AWS. Clip to yesterday first.
* Use `QueryJobConfig(query_parameters=[...])` for the three timestamps. Never format
  a date into the SQL string.

Return `SourceResult[CostRecord]` with `origin="live"`,
`source="gcp-billing-export-bigquery"`.

### D4. Freshness

The export carries `usage_end_time`, which is why `_gcp_data_through`
(`ingest.py:629`) can check whether the last day reaches midnight. The aggregated
query keeps `MAX(usage_end_time)` per day for exactly that reason. Move the midnight
check out of `ingest.py` into a small shared helper so upload and live use one rule,
then apply it. Because late usage keeps arriving for days, say in the ADR that the
last two or three days of a live GCP number may still move; the report's provisional
wording already exists for that.

### D5. Cost of running it

BigQuery on-demand pricing bills bytes scanned; the partition filter keeps that to the
window, and the first terabyte per month is free. Storage for the export is the
operator's, and small.

### D6. The other four methods

Return absent. Do not raise.

### Not in this track

Google also ships a FOCUS-format variant of the BigQuery export. The repo already pins
FOCUS 1.4, so it may be a better long-term source than the native columns. It was not
verified while writing this plan, so it is noted, not planned.

---

## Suggested order

1. **The shared shape, then C1.** Absent-provenance for the four methods, the error
   wrapper, the registry passing settings, the `cloud` marker, and the recorded-response
   directory — before any connector exists, or the first one lands with no honest way
   to test it.
2. **A1 → A5.** Azure, reusing the existing parser. Smallest change, highest
   confidence, and it validates the whole shape.
3. **C2 → C3.** Rewrite the two contract tests and add the log-line test while the
   Azure connector is the only thing to check them against.
4. **B1 → B5.** AWS via Cost Explorer, with the response mapper as its own function.
5. **D1 → D6.** GCP, after enabling the detailed export and waiting for backfill. Start
   the export on day one so the wait overlaps the earlier tracks.
6. **C4 → C5.** ADR and docs last, describing what was actually built rather than what
   was planned.

Stop after step 2 and confirm a real Azure bill produces a report, including the
`unexplained_increase` degradation with the four absent sources, before writing the
AWS connector. If the adapter boundary holds, everything downstream — comparison,
materiality, playbooks, reconciliation, the report — works with no further changes,
and that is the claim worth testing before tripling the surface.

## What this plan does not do

* Does not deploy a connector. The Terraform `fixtures` pin stays.
* Does not hold anyone's credentials but the operator's own ambient ones.
* Does not add authentication, tenancy, or secret storage. Those remain the
  preconditions ADR 0014 set for reading a *customer's* account, and nothing here
  brings that closer.
* Does not implement metrics, inventory, audit, or recommendations live. A live run
  is cost-only and degrades honestly under ADR 0006.
* Does not touch the upload path, which stays the supported route for anyone who is
  not the operator.
* Does not claim parity with an export. Each connector's docstring and ADR 0015 say
  what it loses.
