# ADR 0011: LangSmith is not adopted; agent tracing, if added, arrives over OTLP

* Status: Accepted
* Date: 2026-08-14
* Scope: dependencies of `services/investigator_aws_strands`,
  `services/investigator_azure_maf`, `services/orchestrator_adk`

## Context

The three investigators run on three different agent frameworks: `strands-agents`
for AWS, `agent-framework` (Microsoft Agent Framework) for Azure, and `google-adk`
for the orchestrator. Beneath them, `packages/worker_core` depends on `openai`.

There is no LangChain or LangGraph anywhere in the tree, and none is planned.

LangSmith was evaluated as a way to see inside a live investigation: which tools an
agent called, in what order, with what intermediate reasoning, before a finding
reaches the validator. That visibility is genuinely missing today. When a live run
produces a finding that ADR 0007's validation rejects, the rejection is recorded but
the path that produced it is not.

The evaluation surfaced a mismatch. LangSmith's primary Python integration is driven
by `LANGSMITH_TRACING=true`, which instruments LangChain's callback system. Against
this codebase that flag traces nothing, because there are no LangChain callbacks to
instrument. Capturing the three frameworks through the vendor SDK instead means
either decorating call sites in all three services with `@traceable`, or adopting
LangChain as a wrapper layer.

Separately, the inputs here are customer cloud billing exports. A trace of an
investigation carries resource identifiers, account numbers, and spend figures.

## Decision

**LangSmith is not adopted, and no LangChain-family dependency enters the tree.**

**If agent tracing is added later, it arrives as OpenTelemetry over OTLP** — a
neutral exporter configured by environment variable, with no vendor SDK imported by
any service. All three frameworks already emit OpenTelemetry spans. LangSmith
remains reachable under this decision, since it accepts OTLP ingest, but so do
Phoenix, Braintrust, and a self-hosted collector, and the choice becomes one
endpoint variable rather than a dependency in three `pyproject.toml` files.

Two constraints bind any such future work:

1. **Tracing is opt-in and absent by default.** A missing API key must no-op
   silently. It may never block a request, fail a test, or alter the fallback path
   that ADR 0008 depends on.
2. **Billing identifiers are redacted before export, or tracing is confined to the
   offline fixtures.** Deciding which is a prerequisite of enabling tracing, not a
   detail to settle afterward.

## Rationale

The load-bearing reason is ADR 0003. One agent framework per cloud is isolated
behind an HTTP contract precisely so that a problem in one investigator cannot reach
the other two. A tracing SDK imported by all three services is a shared dependency
spanning exactly the boundary that ADR exists to hold — the first thing since the
contract was drawn that all three would have to agree on, and it would be a vendor's
release schedule they were agreeing on. OpenTelemetry inverts this: the frameworks
already speak it natively, so the integration point is configuration each service
reads independently, and no service learns anything about the others.

The offline guarantee is the second reason. The suite runs 438 tests at no cost with
no keys and no network. A tracing library that initializes an exporter on import, or
that treats a missing key as an error, puts that guarantee at the mercy of an
upstream default. An OTLP exporter that is simply never constructed when the
endpoint variable is unset cannot regress it.

The third reason is that the alternative costs nothing to keep open. Rejecting the
SDK does not reject the product. If LangSmith's evaluation tooling later proves
worth having, OTLP ingest reaches it without revisiting this decision — and if it
disappoints, repointing one variable is the whole migration.

The data-egress constraint is stated as a prerequisite rather than a caution because
`tests/security` already treats provider text as untrusted input. A codebase that is
careful about what untrusted text may *do* should be equally explicit about what
customer data may *leave*, and no such rule exists yet.

## Alternatives considered

**Adopt LangSmith via the vendor SDK, decorating call sites with `@traceable`.**
Rejected. It works, and it is the coupling ADR 0003 forbids: three services taking a
common dependency, with per-framework decoration to maintain in each. The visibility
gained is available over OTLP without any of that.

**Adopt LangChain or LangGraph so the native integration applies.** Rejected
decisively. This inverts the cost — rewriting three working investigators to suit an
observability tool — and abandons ADR 0003's deliberate demonstration that three
frameworks can meet at one contract.

**Enable OTLP export to LangSmith now.** Deferred, not rejected. It is the shape any
future tracing takes, but the redaction question above is unanswered, and enabling
export of billing identifiers to a third-party service before answering it would
settle a data-governance question by accident.

**Use LangSmith's evaluation tooling for `evaluations/` while leaving tracing
alone.** The most attractive rejected option. The offline scenarios, with their
expected findings, are already a labelled dataset in a local format. Rejected for now
because ADR 0008 makes offline evaluation the default and it must keep running with
no keys; a hosted evaluator would either duplicate that harness or weaken it. Worth
reopening if the scenario count grows past what local assertions can express.

## Consequences

Accepted:

* Live agent runs remain hard to introspect. When a live finding is rejected in
  validation, the reasoning path that produced it is still not recoverable, and this
  decision does not fix that — it declines one particular fix.
* The `LANGSMITH_*` credentials already held go unused. This is the intended outcome,
  not an oversight.
* Any future tracing work carries the added cost of wiring OTLP exporters explicitly
  rather than importing an SDK that self-configures.

Not enforced by a test. Nothing currently prevents a LangChain-family dependency from
being added, and the absence of one in the tree is the only thing keeping this record
true.
