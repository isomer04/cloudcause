# ADR 0003: One agent framework per cloud, meeting at an HTTP contract

* Status: Accepted
* Date: 2026-07-28
* Deciders: Rashed Khan
* Scope: `services/orchestrator_adk`, `services/investigator_aws_strands`,
  `services/investigator_azure_maf`, `packages/worker_core`

## Context

Three agent frameworks were in scope to learn: Google ADK, AWS Strands Agents, and
Microsoft Agent Framework. The naive way to use three frameworks in one project is
to pick one and mention the others, or to wire all three into a single process and
let their event loops, tracing, and dependency pins fight.

There is also a real architectural question underneath the learning goal. Each
cloud's investigation differs in its evidence sources and its failure modes, and
provider SDK surfaces change independently of each other.

## Decision

**One framework owns one cloud, and each runs as its own service.** Google ADK is
the coordinator and also owns GCP; AWS Strands owns AWS; Microsoft Agent Framework
owns Azure.

**They communicate over an HTTP contract, not over library calls.** The orchestrator
reaches each worker through the same request and response models regardless of
which framework is behind it. `CLOUDCAUSE_WORKER_MODE` selects `inprocess` for a
single-process run or `http` for separate services, and both paths are tested by
`tests/worker_api`.

**Framework-agnostic logic lives in `packages/worker_core`.** Playbooks, evidence
assembly, native tools, and the worker app are shared. A framework integration is
the adapter around that core, not the home of the investigation logic.

**Every specialist degrades rather than fails.** If its SDK errors, times out, or
hits a quota, the specialist falls back to the deterministic playbooks, returns
`partial`, and records the reason as a report warning.

## Rationale

The HTTP boundary is what turns "three frameworks" from a liability into a
demonstration. Each service pins its own SDK versions, so an incompatible release
in one framework cannot block the others. Swapping a framework means rewriting one
adapter against a contract that already has tests, which is the claim the topology
is making: the framework is an implementation detail.

Isolating the frameworks in separate processes also means their conflicting async
and telemetry assumptions never have to be reconciled, which is the failure mode of
the single-process alternative.

Keeping the shared logic in `worker_core` is what prevents three divergent
investigators. Because the playbooks are shared and deterministic, the fallback path
is not a degraded stub written for the occasion; it is the same logic the framework
path is decorating.

## Alternatives considered

**One framework for everything.** Simplest and probably right for a product with a
single-cloud scope. Rejected here because it forfeits the portability claim, and
because it makes the framework the architecture: a breaking release in it becomes a
rewrite of the system rather than of an adapter.

**All three frameworks in one process.** Rejected: conflicting transitive pins, three
sets of telemetry and event-loop assumptions, and a single dependency resolution
that must satisfy all three at once. The coupling buys nothing, since the workers
never share in-process state.

**A message queue between orchestrator and workers.** Better for long fan-out work
and retries. Rejected for the MVP: an investigation is a synchronous
request-response with a bounded number of workers, and the queue would add a broker
to operate plus correlation logic to write. The SSE progress feed already covers the
"tell the user what is happening" requirement that queues are often introduced for.

**gRPC instead of HTTP + JSON.** Rejected: the same Pydantic models already serve
the gateway contract and the UI's TypeScript mirror, so JSON keeps one schema
source. Revisit if worker payload size becomes the bottleneck.

## Consequences

Accepted:

* Three services is more to run than one. Mitigated by `inprocess` being the
  default for local work and CI, so the multi-process topology is opt-in and the
  offline suite stays fast.
* The contract is now a versioned artifact. A change to it touches the orchestrator,
  both workers, the gateway, and `apps/web/lib/types.ts` in the same commit, which
  `tests/ui` enforces.
* Two transports means both need testing. `tests/worker_api` runs the worker
  contract over `inprocess` and `http`, so the default path cannot drift from the
  deployed one.
* Per-provider `status` values (`ok`, `partial`) are part of the report, and the UI
  has to render partial results honestly rather than hiding them.
