# ADR 0008: Offline evaluation is the default; live agents degrade visibly

* Status: Accepted
* Date: 2026-07-30
* Scope: runtime settings, provider adapters, worker services, `tests/`,
  `evaluations/`

## Context

A multi-agent cloud investigation normally depends on two unstable external
systems: cloud-provider accounts and hosted models. Tests built directly on both
are slow, cost money, require secrets, and can fail because of quotas, network
conditions, SDK changes, or model wording rather than because the application is
wrong.

Avoiding live integrations entirely would create the opposite problem: the system
could have a fast test suite while the real framework adapters remain unexercised.
The project needs reproducible proof for the deterministic pipeline and a separate
place to test live framework behaviour.

## Decision

**Data source and agent execution are independent settings.**
`CLOUDCAUSE_DATA_MODE` selects fixture or live provider data, while each
`InvestigationRequest.agent_mode` selects deterministic playbooks or live frameworks.
The same running server accepts both modes; changing the investigation path never
requires editing an environment variable or restarting a process. A model key never
changes the request implicitly.

**The offline evaluation path is fixtures plus deterministic playbooks.** It runs the real
normalization, analytics, orchestration, worker contracts, evidence validation,
reconciliation, persistence, API, and report generation without a cloud account,
model key, or network request.

**Correctness is scored against seeded scenarios.** Twelve scenarios define known
cost changes, operational evidence, and expected conclusions. Assertions compare
provider, category, resource, cost tolerance, evidence coverage, confidence, and
billing-rule citations. Exact generated prose is never snapshot-tested.

**Live framework tests are opt-in.** They use the same fixture-backed tools but run
Google ADK, AWS Strands, and Microsoft Agent Framework with hosted models. They
assert semantic properties rather than exact sentences.

**A requested live specialist degrades visibly when it fails.** An SDK error,
timeout, missing key, or quota failure invokes the same deterministic playbook used
by the offline path. The worker returns `partial`, records the reason as a warning,
and reports that the completed finding used `agent_mode: stub`. It must not claim a
successful live run.

**A provider failure does not erase other providers' results.** Specialists run
independently, and the report carries a status for each provider.

## Rationale

Most of the system's risk is not model wording. It is incorrect normalization,
date handling, arithmetic, evidence binding, billing-rule selection, persistence,
or security. Those behaviours can and should be tested deterministically on every
change.

Separating fixture data from agent mode also makes failures easier to diagnose. A
developer can run a live framework against known data without also debugging cloud
permissions, or test a future live connector with deterministic reasoning.

Semantic evaluation acknowledges that two correct explanations may use different
words while still requiring the same provider, resource, evidence, and cost impact.

## Alternatives considered

**Run live models and live cloud accounts in every CI job.** Rejected because it
introduces secrets, cost, quota failures, network failures, and non-reproducible
results into the required test path.

**Mock only the final agent response.** Rejected. That would skip planning, worker
contracts, evidence assembly, validation, and the failure paths where most
integration bugs occur.

**Snapshot exact generated text.** Rejected. Wording changes are not correctness
failures; missing evidence, a wrong resource, or incorrect attribution is.

**Fail the entire investigation when one framework fails.** Rejected. A partial
evidence-backed report is more useful than no report, provided the fallback and its
reason are shown honestly.

**Fall back silently.** Rejected. A user must be able to tell whether a hosted model
or a deterministic playbook produced each provider result.

## Consequences

Accepted:

* Offline evaluation proves the application pipeline, not the quality of every
  future live-model response. Opt-in live tests remain necessary.
* Fixtures and expected findings are versioned product assets and must be reviewed
  when contracts or playbooks change.
* Every report and provider status must preserve execution-mode and partial-failure
  information.
* The required CI path remains fast, reproducible, secret-free, and free of model
  cost.

Enforced by the offline test suite, `evaluations/run_evaluation.py`,
`tests/live/test_framework_integration.py`, and worker fallback tests.
