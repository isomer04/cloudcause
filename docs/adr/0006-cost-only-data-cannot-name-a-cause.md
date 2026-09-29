# ADR 0006: A cost export alone may never name a cause

* Status: Accepted
* Date: 2026-07-28
* Scope: `packages/evidence`, `packages/worker_core`, `services/orchestrator_adk`

## Context

A billing export answers *what changed*. It cannot answer *why*. The distinction is
the entire product: anyone can render a bar chart of a spike, and the reason CloudCause
is worth running is that it says which mechanism caused it and shows the evidence.

Uploads made this urgent. A user who supplies only a cost export — the common case,
because a CUR is one download and metrics are not — must not receive a
confident-looking mechanism the data cannot support.

Reviewing the code rather than trusting the plan turned up three facts that made the
intended safeguard a no-op:

* `validate_findings` never dropped or downgraded a finding for missing metric, audit,
  inventory, or recommendation evidence. Its confidence cap only engaged when
  confidence was already high.
* `playbooks.match_score` gates on utilization metrics and audit patterns, but treats
  metric names as a *bonus only*. Any playbook keyed on service or SKU patterns plus
  quantity growth matches on cost rows alone — so a cost-only run could publish a
  specific named mechanism.
* A confidence cap would have been close to a no-op anyway: cost-plus-usage evidence
  already scores `0.40`, and the fallback playbook already caps itself at `0.40`.

The honest degradation already existed in the codebase. It simply was not reachable
from the condition that should trigger it.

## Decision

**Availability of source types is an input to validation.** `validate_findings` takes
`available_source_types` per provider, and the orchestrator passes what the data
bundle actually returned rather than what was requested.

**If the dataset contains no metric, audit, inventory, or recommendation source at
all, no specific mechanism is publishable.** Such a finding is rewritten to the
`unexplained_increase` shape — the measured cost change, the confirmed period, the
cited billing rule, `is_uncertain=True`, confidence `<= 0.40` — and an issue is
recorded with the code `cause_unsupported_by_available_sources`.

**The rewrite says what would fix it.** Each degraded finding carries a warning naming
the data that would raise it, and the report carries one top-level warning listing the
missing source types. "No metric series supplied for `nat-0ab…`; the mechanism is
unconfirmed" is useful; a silent 0.40 is not.

**The check is per dataset and all-or-nothing, deliberately.** It guarantees that cost
rows alone never name a cause. It does not claim the source type present is the one
that particular mechanism needed — matching a playbook's own required evidence stays
the job of `match_score`.

## Rationale

Placing the rule in `validate_findings` rather than in the worker means it covers live
agents and deterministic playbooks identically. A model that free-associates a cause
from cost rows and a playbook that pattern-matches one hit the same gate, and the gate
is testable in `tests/unit` without running an investigation.

The all-or-nothing framing was chosen over a per-mechanism requirements matrix because
it is a guarantee rather than a heuristic. A matrix mapping each playbook to its
required source types would be more precise and would rot: every new playbook becomes
an opportunity to forget an entry, and the failure mode of a forgotten entry is a
confident wrong answer. The coarse rule cannot be forgotten.

This is the same principle as ADR 0002 applied to causation instead of arithmetic:
the system is allowed to say less than a user hoped, and is never allowed to say more
than the evidence carries.

## Alternatives considered

**Cap confidence at 0.5 for cost-only runs.** Rejected as very close to a no-op —
cost-only findings already land at or below 0.40 — and it treats the problem as
presentational. The issue is not that the number is too high, it is that a mechanism
is being named at all.

**Refuse to publish anything without evidence sources.** Rejected: the measured change,
the period, the reconciliation, and the cited billing rule are all real and useful. A
user who uploads a CUR should learn that spend rose 67.8% and which service moved,
labelled honestly as unexplained. Returning nothing throws away true information to
avoid stating a false one.

**Per-playbook required-source matrix.** Better precision, rejected for the
maintenance failure mode above. It remains the natural upgrade if playbook count grows
enough to justify the bookkeeping.

**Let the model decide whether its evidence is sufficient.** Rejected. Asking the
component with an incentive to answer whether it is allowed to answer is not a
control.

## Consequences

Accepted:

* The most common upload — a cost export by itself — produces deliberately unsatisfying
  output. That is the correct result and the UI says so before the run starts, not
  only afterwards.
* Adding any single evidence source lifts the floor for the whole dataset, which is
  coarser than reality. A dataset with inventory but no metrics can publish a
  metric-driven mechanism if its playbook matched. Accepted knowingly: `match_score`
  is the finer-grained control, and the guarantee this ADR buys is the one that matters.
* Two paths through the same code now need fixture coverage, so the seeded scenarios
  and the demo fixtures must keep supplying full evidence or the evaluation score
  would move for the wrong reason.

Enforced by `tests/unit` on the validator in isolation, and by the paired `tests/e2e`
case: a cost-only upload yields `unexplained_increase` at `<= 0.40`, and the same
period with metrics, inventory, audit events, and recommendations added yields the
specific mechanism. The unchanged evaluation score is what shows the demo path was not
disturbed.
