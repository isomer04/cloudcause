# ADR 0004: Native tool calling inside, MCP at the evidence boundary

* Status: Accepted
* Date: 2026-07-28
* Scope: `packages/worker_core/native_tools.py`, `packages/mcp`

## Context

There are two ways to give an agent a capability in this codebase, and the easy
mistake is to treat them as interchangeable and pick by fashion. MCP is the
newer, more impressive-sounding option; native tool calling is what every framework
gives you for free.

The tools this system needs fall into two groups that happen to differ in more than
implementation. Some are pure in-process reads of data the orchestrator already
computed. Others cross a boundary into operational data and versioned billing
knowledge, where the question "where did this claim come from" has to be answerable
after the fact.

## Decision

**Native tool calling for in-process deterministic helpers.** Five tools, in
`packages/worker_core/src/cloudcause_worker_core/native_tools.py`. Four read objects
already in memory and involve no I/O: `get_anomaly_candidates`,
`get_investigation_plan`, `get_candidate_evidence`, and `recalculate_attribution`,
which asks the deterministic layer to restate an attribution rather than computing one.
The fifth, `record_finding`, is the write side: it is how an agent submits a finding for
validation, and it reaches nothing outside the process.

**MCP for the external evidence boundary.** Two read-only servers under
`packages/mcp` expose 13 tools. Six operational: cost breakdown, resource
inventory, resource metrics, audit events, provider recommendations, and data freshness.
Seven from the versioned knowledge store: billing rule, cost-driver definitions,
provider data-freshness rules, export schema version, API deprecation status, pricing
source, and known billing change.

**Every MCP tool name starts with `get_`,** and the servers expose no other verb.
`tests/mcp/test_mcp_tools.py` and `tests/e2e/test_offline_end_to_end.py` assert the
prefix over both MCP allowlists and reject any name containing a mutating word, so a
mutating tool cannot be added to the evidence boundary without a test failing. The
prefix rule is deliberately scoped to that boundary: `recalculate_attribution` and
`record_finding` are named for what they do, because a `get_` name on a write would be
the misleading option.

**MCP transport is stdio,** because these servers are local subprocesses rather than
remote services. See [ADR 0001](0001-streaming-transport.md).

## Rationale

The split follows a property, not a preference: crossing a process boundary is
exactly where an allowlist and provenance are worth their cost, and in-process
reads are exactly where they are pure overhead.

Wrapping the three native helpers in MCP would add a subprocess, a serialization
round trip, and a schema to maintain, in exchange for isolating the agent from data
the same process already holds. That is ceremony, not security.

Conversely, exposing operational data as native functions would put the read
surface inside the agent process, where "which tools exist" is a property of Python
imports rather than of a server that can be enumerated and asserted over. The MCP
server is what makes the read-only claim checkable from outside: the boundary
publishes its own tool list, and a test reads it.

## Alternatives considered

**Everything native.** Fastest, least machinery. Rejected: it dissolves the
read-only boundary into the call graph, and the security posture becomes "we did not
write a mutating function" rather than "the boundary cannot express one."

**Everything MCP.** Uniform, and a defensible answer if the tools were ever to be
consumed by an external client. Rejected: three subprocess hops per investigation
for data already in memory, and the schema duplication that comes with it.

**MCP with a write surface plus a confirmation prompt.** Rejected outright, and it
is the reason the boundary exists. Remediation, deletion, shutdown, IAM changes,
and key rotation are out of scope for this system. A tool that *could* mutate is a
different product with a different risk review.

## Consequences

Accepted:

* Two mechanisms means a contributor must know which side of the boundary a new
  tool belongs on. The rule is I/O: if it crosses a process or reads the knowledge
  store, it is MCP.
* The MCP servers are launched per investigation, so their startup cost is on the
  critical path. Acceptable at the current tool-call volume.
* Because the boundary is read-only by construction, acting on a finding is a human
  step outside the system. Every finding carries
  `requires_human_approval`, and the report says so.
