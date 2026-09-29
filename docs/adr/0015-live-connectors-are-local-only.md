# ADR 0015: Live connectors are local-only; the deployed service still holds no credentials

* Status: Accepted
* Date: 2026-09-15
* Scope: `packages/providers/live.py`, `packages/contracts/settings.py`, `pyproject.toml` extras,
  `tests/live`, `tests/data/live_responses`
* Relation to [ADR 0014](0014-cloudcause-never-holds-cloud-credentials.md): takes one
  alternative that record considered and rejected. Supersedes nothing else in it.

## Context

ADR 0014 closed the live connector on one argument: the deployment is public,
unauthenticated, and single-tenant, so any credential it held would be reachable by
every visitor. That argument is about the *deployed* service.

Among the alternatives ADR 0014 listed was *"Run the connector only locally, never
deployed."* It called that option survivable on safety - the credentials are the
developer's own, in their own environment - and rejected it on **maintenance cost**: a
code path CI cannot exercise honestly, that every future change keeps working blind.

That objection is real, and it is answerable. This record answers it and takes the
option.

## Decision

**`CLOUDCAUSE_DATA_MODE=live` reads the operator's own bill, on the operator's own
machine, through the credential their cloud CLI already holds.** `az login`, an AWS
profile or SSO session, `gcloud auth application-default login`. CloudCause holds no
credential of its own. The only live settings are identifiers -
`CLOUDCAUSE_AZURE_SUBSCRIPTION_ID`, `CLOUDCAUSE_GCP_BILLING_TABLE` - and there is no
setting whose value is a key, a token, or a path to one. Azure deliberately uses
`AzureCliCredential` rather than `DefaultAzureCredential`, whose chain reads
`AZURE_CLIENT_SECRET` from the environment.

**The application enforces loopback-only live mode, and the Terraform pin is defense
in depth.** `registry.py` refuses live adapters when `CLOUDCAUSE_HOST` is not localhost
or a loopback address. `terraform/gcp/main.tf` additionally hardcodes
`CLOUDCAUSE_DATA_MODE=fixtures` on the Cloud Run revision, the setting defaults to
`fixtures`, and nothing in a request, a header, or the dataset API can change it. The
deployed service cannot reach a cloud account, exactly as before.

**Every connector is an SDK fetch plus a pure mapper, and the mapper is tested
offline.** The fetch imports its SDK lazily, resolves the ambient credential, and
returns the raw wire shape; it runs only under the opt-in `cloud` pytest marker. The
mapper turns that shape into `list[CostRecord]` and is held, in the offline suite,
against a recorded and scrubbed response in `tests/data/live_responses/`. This is the
answer to ADR 0014's maintenance objection: what CI cannot exercise is the network
call, not the code that decides what a number means.

**Only `get_costs` is live.** Inventory, metrics, audit events and recommendations come
back *absent* - an empty result whose provenance says `-absent`, the same shape a
cost-only upload produces - and never import an SDK. A live run therefore degrades to
`unexplained_increase` under [ADR 0006](0006-cost-only-data-cannot-name-a-cause.md)
rather than failing. This matters mechanically: the worker reads data through
`get_bundle`, which calls all five sources, so a source that raised would end the run.

**Errors carry the exception class and an HTTP status, never the SDK's message.** SDK
messages echo tenants, ARNs, request URLs and SQL. The upload path's rule - name the
shape, never the value - is applied by wrapping every SDK failure in
`LiveConnectorError` with the chained cause suppressed, and the security suite asserts
that a sentinel placed in the environment and in a fake SDK exception reaches neither
the message nor a log line.

**Today is never requested, and each provider's own freshness signal sets
`data_through`.** Azure: the last day with a row. AWS: the last day Cost Explorer
returned any group for - not its `Estimated` flag, which a real recording showed set
on every day of the unbilled month and which therefore means "not yet invoiced", not
"not yet reported". GCP: the last day whose `usage_end_time` reaches midnight, the
same rule the upload path applies. The worker already turns an honest `data_through`
into the delayed-data warning; nothing downstream changed.

## What each connector loses against an export

Said here so nobody reads a thinner report as a thinner bill.

| Provider | Source | Lost, and why |
| --- | --- | --- |
| Azure | Cost Management Query API | The API allows two group-by clauses. `ResourceId` and `MeterCategory` are kept; `ChargeType` (every record is `usage`), `Meter` (no `sku_id`) and `ResourceLocation` (no `region_id`) are not. |
| AWS | Cost Explorer `GetCostAndUsage` | No resource ids on this API; every record keys to the service-level aggregate. Two group-by keys leave no room for `RECORD_TYPE`, so one query per record type sets `charge_category`. About $0.01 per call. |
| GCP | BigQuery billing export | Nothing before the export was enabled. A US or EU multi-region dataset backfills to the start of the previous month; a regional one does not. Late usage keeps landing for days. |

The Azure Cost Details report and AWS CUR 2.0 are the full-fidelity alternatives; each
is its own track with its own mapper, and the split above is what makes that swap one
function rather than one connector.

## Rationale

The operator reading their own account is not the case ADR 0014 was protecting
against. The credential never leaves their machine, never reaches a process anyone
else can call, and never becomes something CloudCause stores. What is gained is the
thing ADR 0014 itself said a connector buys - removing an upload step - for the one
user for whom that step is pure friction: the person developing the tool against
their own bill.

The cost is a seam CI cannot see across. Keeping that seam thin - one network call
per provider, everything else pure and recorded - is the whole design, and the
recorded responses are not optional artefacts. A connector without one is the blind
code path ADR 0014 refused.

## Alternatives considered

**Leave ADR 0014 as it stood.** The default, and still right for the deployment. It
leaves the developer uploading their own export to test their own tool, which is the
friction this record removes and nothing more.

**Reverse ADR 0014 for customers.** Not considered here. Authentication, per-tenant
isolation and secret storage remain the preconditions, and remain unmet.

**Implement the other four sources live.** Deferred, not refused. Each is a different
API with a different shape (Resource Graph, CloudWatch, Cloud Asset Inventory), and
cost-only is enough to prove the boundary holds. The absent-provenance shape means
adding one later changes nothing downstream.

**`DefaultAzureCredential` for convenience.** Rejected; see Decision. Reading a client
secret from the environment is the first step toward storing one.

## Consequences

* The three `Live*DataProvider` classes are real adapters. `LiveModeNotConfiguredError`
  now means "this machine is missing the extra, the identifier, or the login", and
  says which.
* Cloud SDKs are optional extras (`azure`, `aws`, `gcp`), imported inside the fetch.
  The default install carries none of them and the offline suite runs with none of
  them installed.
* A new `cloud` pytest marker, separate from `live`, is excluded from CI. A developer
  with no cloud account runs the whole suite; one with a login runs `make live-cloud`.
* Uploads stay the supported route for anyone who is not the operator, and the only
  route the deployment offers.
* Re-recording a response after a provider changes its wire shape is the maintenance
  this record commits to. The recorded files say so in their `_note`.

Enforced by `tests/contract` (the extra is named, absent sources import no SDK, the
wrapper drops the message), `tests/security` (nothing reaches a log line), and
`tests/unit/test_live_connectors.py` (the mappers against recorded responses).
