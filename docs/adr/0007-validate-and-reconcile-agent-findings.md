# ADR 0007: Agent findings are untrusted until validated and reconciled

* Status: Accepted
* Date: 2026-07-30
* Scope: `packages/worker_core`, `packages/evidence`,
  `packages/anomaly`, `services/orchestrator_adk`

## Context

An AWS, Azure, or GCP specialist returns a claim: a resource caused a cost
increase, the increase has a certain value, and a billing rule explains the
charge. The claim may come from a live model or from a deterministic playbook.
Neither source should be trusted merely because it produced valid JSON.

Prompt instructions are useful guidance, but they cannot guarantee that a finding
cites real evidence, names a known resource, uses the measured cost value, or
applies a billing rule that was valid during the investigated period. Financial
reports need controls outside the component that writes the explanation.

## Decision

**A specialist may submit a finding, but it may not publish one directly.** Every
submission passes through deterministic controls before it enters
`InvestigationReport.findings`.

**Evidence references are capability-limited.** The worker prepares an evidence
pool with stable IDs. `record_finding` accepts only IDs from that pool, rejects an
unknown ID, and requires at least one citation. The agent can select evidence; it
cannot create a new evidence record through the finding tool.

**Validation runs outside every agent framework.** The shared validator checks:

- Evidence exists and carries a source reference.
- Affected resource IDs occur in cost or inventory data.
- Claimed cost attribution matches the deterministic anomaly candidate.
- Billing rules have an official source and were valid on the usage date.
- High confidence has corroborating metric, audit, or recommendation evidence.
- The available data can support a cause rather than only a measured change.

The validator may drop a finding, replace its number with the measured value,
remove an unsupported resource ID, lower confidence, or rewrite a named mechanism
as `unexplained_increase`. Every correction produces a structured validation issue
rather than happening silently.

**Cost reconciliation is reported after validation.** The deterministic analytics
layer sums the published findings and compares them with the total measured change.
The report records attributed and unattributed amounts. A mismatch outside tolerance
is visible as a warning; it is never hidden by changing the total or inventing a
balancing finding.

**Published findings are ranked only after validation.** Supported and certain
findings appear before uncertain ones, then by cost impact and confidence.

## Rationale

The agent is good at correlating evidence and explaining ambiguity. It is not the
right place to enforce the validity of its own answer. A shared post-agent gate
applies the same rules to Google ADK, AWS Strands, Microsoft Agent Framework, and
the offline playbooks.

Evidence IDs make a statement traceable to a particular source row, metric, event,
or recommendation. Versioned billing citations make the interpretation traceable
to the rule and official documentation used for that date. Reconciliation makes
the financial completeness of the report measurable.

Together, these controls change the trust model from "the prompt told the model not
to guess" to "unsupported output cannot pass unchanged into the report."

## Alternatives considered

**Rely on prompt instructions.** Rejected. Prompts guide behaviour but are not an
enforcement boundary, and three frameworks would not fail in identical ways.

**Let each specialist validate its own findings.** Rejected. Rules would drift
between clouds, and a framework adapter would own financial policy that belongs in
the shared core.

**Drop every imperfect finding.** Rejected. A measured increase with weak causal
evidence is still useful when labelled `unexplained_increase` with low confidence.
Discarding it would hide true cost information.

**Silently repair invalid output.** Rejected. Replacing an incorrect number may be
safe, but an auditor and the UI still need to know that a correction occurred.

## Consequences

Accepted:

* The report may contain warnings or an unattributed amount instead of presenting
  false completeness.
* Adding a new finding field or evidence type may require a matching validator rule
  and regression test.
* Some useful-sounding agent answers are deliberately downgraded. Accuracy takes
  priority over confidence or presentation.
* The validator is part of the financial control surface and must remain
  deterministic, framework-independent, and well tested.

Enforced by `tests/unit/test_evidence_validation.py`,
`tests/unit/test_cause_support.py`, `tests/e2e/test_offline_end_to_end.py`, and the
semantic checks in `evaluations/`.

