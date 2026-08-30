# ADR 0009: Materiality leaves a residual, and the residual is published

* Status: Accepted
* Date: 2026-08-14
* Scope: `packages/anomaly`, `fixtures/generate_fixtures.py`, `web/components/report`

## Context

`AnalyticsConfig` sets `min_absolute_change = 5.00` and `min_percent_change = 20.0`.
Anything smaller is not promoted to a candidate, so no playbook investigates it and
no finding claims it. That threshold is not an implementation detail; it is the
reason a report reads as four ranked causes instead of forty line items, one of
which happens to be "CloudWatch metrics rose $2.89".

A materiality threshold and a complete decomposition are mutually exclusive by
construction. If the tool declines to explain movement below a floor, the explained
findings cannot sum to the measured total unless nothing in the dataset fell below
that floor. Which means a report that reconciles to exactly `0.00` is reporting one
of two things: that the threshold did no work, or that the input was arranged so it
would not have to.

The demo fixture was the second case. Every planted cause cleared materiality and
nothing else moved, so `unattributed_change` was `0.00` on all three providers
simultaneously and `within_tolerance` was true for a reason unrelated to tolerance.
The reconciler's band — 5% of the measured change — was never exercised in the path
a reviewer actually looks at. A reviewer with FinOps experience reads
"100% attributed, $0.00 residual" as evidence the fixture was written backwards from
the answer, which it was.

Industry practice is consistent on this point, and it is worth recording because the
instinct to show a perfect decomposition is strong and keeps returning.

* AWS Cost Anomaly Detection publishes "up to 10 root causes, with estimated dollar
  attributions" and states plainly that "some anomalies result from numerous small
  changes rather than a few large ones… some minor contributors may not be captured
  within the top 10". The sum of shown causes is not asserted to equal the anomaly.
* Google Cloud and Azure anomaly detection both present *top contributors* — projects,
  services, regions, SKUs — never a closed decomposition.
* Datadog's cost-change monitor is explicitly two-threshold: alert above 5% *and*
  above a dollar floor. That is the same materiality construct, and it produces the
  same residual.
* The FinOps Foundation Allocation capability names as a success metric the "ability
  to surface the percentage of cost that cannot be categorized and allocated
  directly". Even at Run maturity the wording is "few scenarios where all cost is not
  allocated" — not none.
* Kubecost surfaces `__unallocated__` and `__idle__` as first-class buckets rather
  than distributing them, on the principle that the gap is real money and should not
  be "silently smeared across tenants to make the books balance".
* Where a vendor does market 100% coverage — CloudZero's CostFormation — the claim is
  about *allocation* (assigning spend an owner), not about *attribution* (proving a
  cause). Those are different problems, and only the first is completable in
  principle.

The distinction matters for this repository: CloudCause attributes a change to a
mechanism. Nobody in the field claims that is exhaustively solvable, and ADR 0006
already commits the system to saying less than a user hoped rather than more than
the evidence carries.

## Decision

**The residual is a published figure, not an error state.** `reconcile()` already
computed `unattributed_change`; the verdict strip now shows it next to the attributed
total with its tolerance verdict, at the same weight as the money it sits beside.
Reconciliation guarantees the residual is *measured and declared*, never that it is
zero.

**The demo fixture plants a residual on every provider.** One untagged, resource-less
SKU per cloud drifts upward across the current period at an unchanged unit rate:
AWS `CW:MetricMonitorUsage` +2.89, Azure `Data Transfer Out - Zone 1` +3.85, GCP
`Network Internet Egress` +3.92. Each is below `min_absolute_change`, so none becomes
a candidate. The demo reads +419.60 attributed of +430.26 measured, +10.66
unattributed, within tolerance.

**Planted drift must stay under the materiality floor and the total under tolerance.**
Documented in `fixtures/README.md`. Drift that clears materiality would become a
finding and defeat the purpose; a residual outside 5% would make the demo publish a
warning.

**Every figure remains computed, never authored.** The generator changes three input
line items. `total_absolute_change`, `attributed_change`, `unattributed_change`, and
the per-provider splits are derived by `packages/anomaly` from those inputs, exactly
as they will be for a user's own export.

## Rationale

The reconciler exists to catch a report whose parts do not balance. A demo in which
the residual is structurally always zero cannot demonstrate that, and worse, it
trains the reader to expect zero — so the first real run, where a residual of a few
percent is normal, reads as the tool malfunctioning. Planting the residual makes the
demo match the shape of the thing it is demonstrating.

It also closes an honesty gap that is easy to miss. The materiality threshold is a
deliberate editorial choice about what deserves a human's attention. Presenting a
decomposition that appears complete conceals that a choice was made at all. Showing
`+10.66 unattributed, within tolerance` states the choice and its cost in one line.

This is ADR 0002 read in the other direction. That record establishes that the system
may not overstate a number. This one establishes that it may not overstate its own
coverage: exactness about how much is explained is worth more than the appearance of
having explained everything.

## Alternatives considered

**Keep the fixture tidy and rely on the tolerance logic being unit-tested.** Rejected.
The unit tests were already there and passing; the gap was that the artefact a
reviewer reads never exercised the behaviour. A guarantee nobody can observe in the
product is indistinguishable from one that is not implemented.

**Lower `min_absolute_change` to zero so everything is attributed.** Rejected. It
would produce a literal 100% decomposition and destroy the report: dozens of
sub-dollar findings ranked alongside a compromised API key, with no signal about
which deserves attention. The threshold is the feature.

**Distribute the residual proportionally across the findings so they sum to the
total.** Rejected outright, and it is the alternative most worth naming because it is
the one that looks tidiest. It would attribute money to causes the evidence does not
support, which is the exact failure ADR 0007 exists to prevent, and it is what
Kubecost's design notes call smearing the gap to make the books balance.

**Show the residual only in the provenance section.** Rejected as a half-measure. If
the residual is credible it belongs beside the attributed total where the reader
forms their judgement; burying it in an appendix concedes that it looks like a defect.

## Consequences

Accepted:

* The demo's headline moves from +419.60 to +430.26 and its summary reads "98% of
  the change" rather than "100%". A reader who equates completeness with quality will
  read that as worse. That reader is the one this record disagrees with.
* Documentation carrying demo figures — `README.md`, `fixtures/README.md` — must be
  updated whenever the drift changes. ADR 0002 quotes the older 419.60/0.00 example
  and is left as written, per the immutability rule; this record supersedes that
  illustration, not its decision.
* Anyone editing `generate_fixtures.py` now has a constraint to respect: drift stays
  under materiality, residual stays under tolerance.

Enforced by `tests/e2e/test_offline_end_to_end.py`: the total and every provider
must reconcile with a strictly positive residual inside tolerance, and no finding may
name one of the drifting services. `make evaluate` remains 13/13 with 100% cost
attribution accuracy, which is what shows the seeded scenarios were not disturbed.
