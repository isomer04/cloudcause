# Handoff — Actor Cost Attribution & the Live AWS Connector

Two features a CACI panel probed in Sept 2026 that CloudCause did not have yet.
Both were real gaps with a clean path through the existing architecture. Track A is
self-contained. Track B was gated on an auth decision and should not start first.

> **Status, 2026-09-15 — both tracks are resolved. This document is now history.**
>
> * **Track A: built.** Actor attribution ships as a disclosed allocation estimate.
>   See [ADR 0013](adr/0013-actor-attribution-is-an-allocation-estimate.md) and
>   `packages/anomaly/src/cloudcause_anomaly/actors.py`.
> * **Track B: superseded.** The deployed service still holds no cloud credentials
>   ([ADR 0014](adr/0014-cloudcause-never-holds-cloud-credentials.md)). Local-only,
>   cost-only live connectors now exist under
>   [ADR 0015](adr/0015-live-connectors-are-local-only.md): they read the operator's
>   own bill through their own CLI login, on their own machine. The B1–B4 notes below
>   are the original design and are retained as history.

---

## The copy-paste handoff prompt

Paste this into a fresh session at the repo root.

```
You are working in the CloudCause repo (Python 3.11+, uv workspace, packages/ layout).

Read these before writing any code:
  docs/architecture.md
  docs/adr/0005-uploads-are-sealed-server-parsed-datasets.md
  docs/adr/0006-cost-only-data-cannot-name-a-cause.md
  docs/adr/0010-confidence-is-derived-not-capped.md
  docs/testing-strategy.md
  packages/providers/src/cloudcause_providers/protocols.py
  packages/contracts/src/cloudcause_contracts/provider_data.py
  packages/contracts/src/cloudcause_contracts/datasets.py

Non-negotiable constraints already established in this codebase - do not violate them:
  - Money is deterministic Python. No model computes or adjusts a dollar figure.
  - Every provider method is read-only. Nothing may mutate a cloud resource.
  - Uploads are parsed from the request stream into a sealed, immutable dataset.
    Nothing raw touches disk. No log line ever contains a row value.
  - Per ADR 0006, a cost export alone cannot name a cause. Anything the bill does
    not prove is published as an estimate with its evidence, never as a fact.
  - Provider adapters are interchangeable. Business logic and agents see the
    protocol, never a cloud SDK client.

Implement Track A only (see docs/handoff-actor-attribution-and-live-aws.md).
Work one step at a time. After each step, run the offline suite and stop for review:
  make test      (or: uv run pytest tests/unit tests/contract tests/mcp)

Do not start Track B. It is blocked on an auth decision recorded in that same doc.
```

---

## Track A — Actor (principal) cost attribution

**The question this answers:** "can it break spend down by cost allocation tags, or
by principal tags?" e.g. attributing shared Bedrock spend to the team or identity
that drove it.

**The fact that shapes the design:** no cloud's billing export carries the IAM
principal that made the call. CUR 2.0 has line items, resource IDs, and resource
tags. `aws:PrincipalTag` is an IAM/ABAC authorization concept that never reaches the
bill. Azure and GCP are the same. So actor attribution is necessarily a **join**
against audit evidence and an **allocation estimate**, never a billed fact. That is
exactly the shape ADR 0006 already describes, which is why this fits.

### A1. A CloudTrail-native audit parser

`packages/datasets/src/cloudcause_datasets/ingest.py` -> `parse_evidence_source`
currently accepts only CloudCause's own shape, `{"items": [...]}` validated against
`AuditEvent`, and says so explicitly: *"Provider-native shapes are not accepted yet."*
A raw CloudTrail export is rejected today.

- Add provider-native detection for CloudTrail's `{"Records": [...]}` envelope,
  alongside the existing `items` path. Detect by content, matching how
  `parse_cost_source` already detects CUR vs Azure vs GCP.
- Map each record to `AuditEvent` (`packages/contracts/src/cloudcause_contracts/provider_data.py:117`):
  `eventID`->`event_id`, `eventName`->`event_name`, `eventTime`->`event_time`,
  `eventSource`->`source`, `awsRegion`->`region_id`, `sourceIPAddress`->`source_ip`,
  and resource ARNs->`resource_ids`.
- **Populate `actor` and `actor_type` from `userIdentity`.** These two fields are
  already declared on the model but nothing in the codebase writes them - confirm
  with `grep -rn "actor=" --include="*.py" packages`. Handle the `type` variants
  (`IAMUser`, `AssumedRole`, `AWSService`, `Root`), and prefer
  `sessionContext.sessionIssuer.userName` for assumed roles so every request in a
  session does not collapse to a single opaque session name.
- Keep the existing per-row rejection discipline: name the offending field, never
  echo its value.

### A2. The `actor` dimension and the allocation function

- Add `"actor"` to `Dimension` in `packages/contracts/src/cloudcause_contracts/analytics.py:16`.
- Extend `_dimension_key` in `packages/anomaly/src/cloudcause_anomaly/comparison.py:100`.
  Unlike the other dimensions, the key cannot be read off a `CostRecord`, so this
  needs a real allocation pass rather than a one-line branch.
- **The allocation, deterministic Python only:** for each cost record, find audit
  events whose `resource_ids` include that record's `resource_id` and whose
  `event_time` falls inside the record's usage day. Split that record's
  `effective_cost` across the distinct actors by usage share. Cost with no matching
  audit event goes to an explicit `unattributed` bucket - never distributed, never
  guessed. This mirrors how `tag_owner` already reports `untagged`.
- **Choosing the weight matters.** Invocation count is a poor proxy for Bedrock
  spend because token counts vary per call. Prefer a token-count weight from Bedrock
  model invocation logging when that evidence is present, and fall back to
  invocation count with a warning attached to the result when it is not.

### A3. Widen the tag breakdown while you are here

`_dimension_key`'s `tag_owner` branch hardcodes `("owner", "Owner", "team", "Team")`.
Accept an arbitrary tag key so a caller can group by any activated cost allocation
tag. Keep the current four as the default.

### A4. Expose it

- `packages/mcp/src/cloudcause_mcp/tools.py:124` -> `get_cost_breakdown` validates
  `group_by` against a hardcoded set at ~line 145. Add `"actor"` there, and derive
  that set from `Dimension` instead of repeating it, so the two cannot drift.
- Attach the weighting method and the unattributed share to the tool's response, so
  a consumer can see how the estimate was produced.
- Update `docs/mcp-tools-guide.md`.

### A5. Write the ADR

New ADR: *actor attribution is an allocation estimate, not a billed fact*. State
that billing exports carry no principal, that attribution therefore requires an
audit join, that unmatched cost is published as unattributed rather than
distributed, and how the weighting choice is disclosed. Read ADR 0006 and ADR 0010
first and express confidence the way they already prescribe.

### A6. Fixtures, scenario, tests

- A synthetic CloudTrail file in `fixtures/uploads/` matching the shape the new
  parser accepts, so the upload path has an end-to-end case like the cost exports do.
- Extend the existing `aws-unexpected-ai-inference` scenario so Bedrock spend on a
  tagged application inference profile also resolves to an actor.
- Tests: `tests/unit` for the parser mapping and the allocation math (including the
  unattributed path and the weight fallback), `tests/contract` for the widened
  `Dimension`, `tests/mcp` for the new `group_by`, `tests/security` to confirm no
  actor identity reaches a log line.

**Worth knowing:** the AWS-sanctioned path for per-team Bedrock attribution is a
tagged **application inference profile**, which lands in CUR as an ordinary cost
allocation tag and needs none of this machinery. Track A is for the harder case
where spend is shared and only the audit trail can apportion it. Say that plainly in
the ADR; it is the honest framing.

---

## Track B — The live AWS connector

**Closed by [ADR 0014](adr/0014-cloudcause-never-holds-cloud-credentials.md).** The
gate below was resolved against building it: authentication was judged the wrong next
investment, because a connector buys only the removal of an upload step while risking
standing read access to a stranger's cloud account from an unauthenticated public
service. Everything from here down is preserved as the design to start from if that
decision is ever reversed — the preconditions in B0 have to be closed first.

The original note follows.

**Blocked.** Not on connector code. On auth.

`packages/providers/src/cloudcause_providers/live.py` is already a complete adapter
boundary: `LiveAwsDataProvider`, `LiveAzureDataProvider`, and `LiveGcpDataProvider`
each declare the read-only access they require, and `CLOUDCAUSE_DATA_MODE=live`
raises `LiveModeNotConfiguredError` naming the missing access rather than silently
falling back to fixtures. The protocol is async and already returns
`SourceResult[CostRecord]`; the FOCUS 1.4 normalizer already exists. A connector only
has to emit `CostRecord`.

### B0. The gate — decide this before writing a connector

Today the app's strongest security property is that it holds **no cloud credentials
at all**, so there is nothing to steal. Holding a customer's credentials inverts
that, and the current deployment cannot carry it:

- The gateway is unauthenticated. The only separation between users is an
  unguessable dataset ID.
- Model API keys are plain env vars on the Cloud Run revision; Secret Manager was
  deferred as a demo trade.
- Postgres and Redis exist in code but are not deployed.

So the prerequisite is an ADR and the work behind it: real identity, per-tenant
isolation, and secret storage. **Do not ship a live connector before that lands.**

### B1-B4. Once B0 is resolved

- **Credentials:** STS `AssumeRole` with an external ID from a tooling account,
  rolled out org-wide via StackSets. Scope to Cost Explorer / Data Exports,
  CloudWatch, CloudTrail lookup, tagging, and Compute Optimizer reads - the roles
  `LiveAwsDataProvider.required_roles` already names. Credentials never enter a
  prompt, a log, or the database.
- **`get_costs`:** read **CUR 2.0 Parquet from S3**, not the Cost Explorer API. CUR
  carries resource-level detail and tags; Cost Explorer is coarser and bills about
  $0.01 per request, which is indefensible inside a cost tool. Expect roughly 24h
  delivery latency and partitioned Parquet, so this is a different read path from the
  upload parser - reuse the FOCUS normalizer, not the upload code.
- **`get_audit_events`:** CloudTrail lookup, reusing the A1 mapping so uploaded and
  live audit events produce identical `AuditEvent` values. This is where Track A pays
  off a second time.
- **Tests:** `tests/live/` already exists. Keep live tests opt-in and offline CI
  green - recorded fixtures or moto, never real credentials in CI.
- **Docs:** `docs/architecture.md`, `docs/deployment-runbook.md`, and the
  `CLOUDCAUSE_DATA_MODE` notes in `packages/contracts/src/cloudcause_contracts/settings.py`.

---

## Suggested order

1. A1 -> A2 -> A3 -> A4 -> A5 -> A6. Self-contained, no auth dependency, and it
   closes the gap that actually got probed.
2. B0 as a written ADR. Decide whether multi-tenant auth is worth building at all,
   or whether upload-only is the right permanent answer for a public demo.
3. B1-B4 only if B0 says yes.

**What happened:** step 1 was built as described, with one design change found while
building the fixture — CUR and CloudTrail spell a resource id differently, so an
exact-match join silently attributed everything to `unattributed`; ADR 0013 records
the alias join that fixes it. Step 2 was written and says **no**, so step 3 does not
happen.
