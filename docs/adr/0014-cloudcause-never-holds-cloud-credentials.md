# ADR 0014: CloudCause never holds a customer's cloud credentials

* Status: Accepted
* Date: 2026-09-15
* Scope: `packages/providers/live.py`, `packages/contracts/settings.py`, deployment
* Supersedes nothing. Closes the live-connector track opened in
  [`../handoff-actor-attribution-and-live-aws.md`](../handoff-actor-attribution-and-live-aws.md).

## Context

`CLOUDCAUSE_DATA_MODE=live` has been a declared but unimplemented mode since the
beginning. `LiveAwsDataProvider`, `LiveAzureDataProvider`, and `LiveGcpDataProvider`
each name the read-only access they would need and raise `LiveModeNotConfiguredError`
rather than silently falling back to fixtures. The adapter boundary is complete, the
protocol is async, the FOCUS 1.4 normalizer exists, and a connector would only have to
emit `CostRecord`. The question was whether to write one.

Writing one is not the hard part. Holding the credentials is.

Today the deployment's strongest security property is that **it holds no cloud
credentials at all**. A cost export is uploaded, parsed from the request stream into a
sealed dataset, and read back by id. There is nothing in the system that could be
stolen to reach somebody's cloud account, because nothing in the system can reach one.

A live connector inverts that, and the deployment as it stands cannot carry the
inversion:

* **The gateway is unauthenticated.** `terraform/gcp/main.tf` grants
  `roles/run.invoker` to `allUsers`. The only separation between users is an
  unguessable dataset id — adequate for a cost export somebody chose to upload,
  nowhere near adequate for standing read access to a cloud account.
* **Secrets are plain environment variables.** `OPENAI_API_KEY` and `GOOGLE_API_KEY`
  sit on the Cloud Run revision and in `terraform.tfstate` in cleartext. Secret
  Manager was deferred as a demo trade under ADR 0012 and the runbook says so.
* **There is no tenancy.** Postgres and Redis exist in code and in
  `docker-compose.yml`, but the deployment runs neither. There is no per-tenant
  isolation to attach a credential to.

So the real prerequisite was never connector code. It was real identity, per-tenant
isolation, and secret storage — a security-critical project in its own right, whose
failure mode is not a wrong number on a dashboard but unauthorized access to someone
else's cloud account.

## Decision

**CloudCause does not hold cloud credentials, and the live connector is a non-goal.**
Not deferred, not "when auth lands" — closed. Upload is the permanent data boundary
for customer data.

**`CLOUDCAUSE_DATA_MODE=live` stays, and stays unimplemented.** It is not removed,
because removing it would delete the honest statement the codebase currently makes.
`LiveModeNotConfiguredError` keeps naming the read-only access a connector would
require. What changes is the message: it stops implying a connector is coming and
points at the upload path instead. A mode that fails loudly with the reason is better
documentation than a mode that was quietly deleted.

**The three `Live*DataProvider` classes stay.** They are what makes the adapter
boundary demonstrably complete: they prove the protocol is implementable by something
other than a fixture reader, and their `required_roles` remain the specification
anybody would build against. Deleting them would make the boundary look like an
accident of having only one implementation.

**Reversing this is a new ADR, and it may not be written before the three gaps above
are closed.** Authentication, per-tenant isolation, and Secret Manager are the
preconditions, and they are preconditions rather than parallel work.

## Rationale

The decisive argument is what a live connector would actually buy, weighed against
what it would risk.

It buys *removing an upload step*. That is all. Everything downstream — the
comparison, the playbooks, the evidence validation, the reconciliation, the report —
already runs identically on uploaded data and would run identically on live data,
which is the entire point of the adapter boundary. The analytical work is not gated on
the connector and never was.

It risks standing read access to a stranger's cloud account, held by an
unauthenticated public service. Cost and billing data is not low-sensitivity: a CUR
enumerates every resource, every account, every region, and the shape of a company's
infrastructure. Read-only is not harmless.

That is a bad trade for a demo, and it stays a bad trade at small scale. The right
moment to take it is when there is a real tenant boundary to attach it to, and at that
point the decision should be made with that context rather than inherited from a
handoff note.

**The honest framing is a feature, not an apology.** "This system deliberately cannot
reach your cloud account; hand it an export instead" is a stronger security posture
than "this system can reach your cloud account, and here is how we protect the
credentials." The first has no credential-handling failure mode. The second has
several, and each one has to be got right permanently.

There is also a consistency argument with the rest of these records. ADR 0006 refuses
to name a cause the data cannot support. ADR 0009 publishes the residual rather than
hiding it. ADR 0013 publishes an unattributed share rather than distributing it. The
pattern is the same each time: say less, and say it accurately. Declining to hold
credentials is that pattern applied to data access instead of to analysis.

## Alternatives considered

**Build authentication, then the connector.** The option this record exists to reject.
Not rejected as bad engineering — it is what a product with customers would do — but
rejected as the wrong next investment here. It is a large amount of security-critical
work whose payoff is removing an upload step, and it would be built without a real
tenant to validate it against.

**Ship a connector with a single set of credentials, for one owner's own account.**
Tempting, and genuinely useful for a demo that reads its author's real bill. Rejected
because the deployment is public: a single-tenant connector on an unauthenticated
endpoint means every visitor reads that account's cost data. Making it safe requires
exactly the authentication work this ADR declines, so it is the same project wearing a
smaller hat.

**Run the connector only locally, never deployed.** Survivable — credentials would be
the developer's own, in their own environment — but it creates a code path that CI
cannot exercise honestly and that every future change has to keep working blind.
`tests/live/` already sets the precedent for opt-in tests, but a connector is a much
larger surface to maintain untested than a model call. Rejected on maintenance cost,
not on safety.

**Delete `CLOUDCAUSE_DATA_MODE=live` and the `Live*DataProvider` classes.** Rejected.
The mode failing with a specific, actionable message is a documented decision; an
absent mode is an unanswered question. The classes are also the proof that the adapter
boundary is real.

**STS `AssumeRole` with an external ID, rolled out by StackSets.** This was the
intended design and it is the correct design — it is recorded here so that whoever
reverses this ADR starts from it rather than rediscovering it. Cross-account role
assumption from a tooling account, external id per tenant, scoped to the reads
`LiveAwsDataProvider.required_roles` already names, credentials never entering a
prompt, a log, or the database. Also recorded: `get_costs` should read **CUR 2.0
Parquet from S3**, not the Cost Explorer API — CUR carries resource-level detail and
tags, while Cost Explorer is coarser and bills roughly $0.01 per request, which is
indefensible inside a cost tool. Expect ~24h delivery latency and partitioned Parquet,
so it is a different read path from the upload parser and should reuse the FOCUS
normalizer rather than the upload code. `get_audit_events` should reuse the CloudTrail
mapping added for ADR 0013, so uploaded and live audit events produce identical
`AuditEvent` values.

## Consequences

Accepted:

* CloudCause cannot answer "why did my bill move" without somebody handing it an
  export. That is a genuine product limitation and the README should read as though it
  is a choice, because it is one.
* Every future provider feature has to be reachable through the upload path or it is
  not reachable at all. ADR 0013's CloudTrail parser is the model: provider-native
  shapes are accepted at ingest rather than fetched.
* The `live` data mode will keep looking unfinished to a reader who does not find this
  record, which is why `LiveModeNotConfiguredError` now points at it directly.
* Work already spent on the live adapter boundary is not recovered as a running
  connector. It is retained as the thing that makes fixtures, scenarios, and uploads
  interchangeable, which is load-bearing for ADR 0008's offline-first evaluation.

Enforced by `tests/contract`, which holds every adapter to one behavioural contract
and asserts that live mode raises rather than silently degrading to fixtures.
