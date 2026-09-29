# ADR 0019: Durable history stores hashed identifiers, and persistence never fails a run

* Status: Accepted
* Date: 2026-09-16
* Scope: `packages/worker_core/history.py`, `packages/datasets/sql.py`
  (`hash_identifier`), `Settings.history_backend`, `Settings.id_hash_salt`,
  `GET /health`
* Records the design the history store implements. Supersedes nothing.

## Context

Live jobs stay in memory because SSE streaming needs a queue (ADR 0001). Without a
durable copy, a gateway restart loses every report, and the Cloud Run demo restarts
whenever it scales to zero. The obvious fix is to write every state change to SQL.

The obvious fix has a security consequence. An investigation request names account
ids and subscription ids, the findings name resources whose ids embed them, and a
durable table of who investigated which account is a more valuable target than any
single report. ADR 0014 says the deployment holds no credentials; a history table
must not become the next best thing.

## Decision

**Every state change is written to SQL when a DSN is configured; otherwise history
lives in the process and is lost on exit.** Memory is the default and is what the
demo runs. PostgreSQL is the only persisted backend, imported lazily so an offline run
never needs the driver.

**Account and subscription identifiers are hashed on the way in.** `redact_request`
and `redact_state` replace every `account_ids` entry with `hash_identifier(value,
salt)` before the row is written. The durable copy is deliberately not the raw request.
Without `CLOUDCAUSE_ID_HASH_SALT` the digest is an unkeyed SHA-256 of the identifier: it
lets two runs over the same account be correlated, and anyone holding a candidate
account id can confirm it by hashing it, so it does not resist enumeration. The gateway
therefore refuses to start durable PostgreSQL history without a salt; with one, the
digest is a secret only the deployment can reproduce. The same salt keys rate-limit
client identity (ADR 0018).

**Persistence never fails an investigation.** A write error degrades the store to
memory-only for the rest of the process, is logged as a class name, and is reported by
`SqlJobStore.describe()`, which the gateway exposes on `/health`. The investigation
that triggered it completes and streams normally.

## Rationale

History is a convenience; the report is the product. A persistence failure that
aborted a run would trade a lost report for a lost history entry, which is the wrong
way round, and would make database availability a precondition for a feature that
works without one.

Hashing at the boundary rather than encrypting at rest is the right level for what is
being protected. The threat is enumeration - which accounts has this deployment seen -
not confidentiality of the findings themselves, which the operator chose to run. A
pseudonym keyed by the secret salt defeats enumeration, which is why durable history
requires the salt; encryption would add key management ADR 0014 refuses.

## Alternatives considered

**Store nothing durable.** The demo did this until the restart problem became a
support burden. Rejected as a permanent answer; kept as the default.

**Store the raw request.** Rejected; see Context.

**Fail the run on a write error.** Rejected; see Rationale.

**SQLite for local durability.** Not chosen because the split topology
(`CLOUDCAUSE_ORCHESTRATOR_MODE=http`) needs a store two processes can share, and one
backend is easier to keep correct than two.

## Consequences

* History cannot be searched by raw account id. A deployment that needs that has to
  hash the query with the same salt, and the API does not offer it.
* `/health` reports whether the durable store is healthy or degraded, and whether
  identifiers are hashed.
* Resource ids inside findings are not hashed. For Azure they embed the subscription
  id. This is a known limit, recorded here rather than hidden: hashing them would
  break the report's citations, and the mitigation is the memory default plus the
  operator's choice to configure a DSN.
* Enforced by `tests/persistence` (survives a restart, degrades on write error,
  identifiers hashed in the stored row), skipped without a PostgreSQL and run in CI.
