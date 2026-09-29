# ADR 0017: Coverage ends at the last complete day, never at the requested end date

* Status: Accepted
* Date: 2026-09-16
* Scope: `Provenance.data_through`, `packages/datasets/ingest.py` (`_aws_data_through`,
  `_azure_data_through`, `gcp_data_through`), `packages/worker_core/context.py`,
  `packages/providers/live.py`, the `aws-delayed-billing-data` scenario,
  `knowledge/*/data-freshness.yaml`
* Records a decision the code has enforced since the upload path landed. Supersedes
  nothing.

## Context

Every provider reports cost late, and unevenly. A CUR is hourly and its last day can
stop at 05:00. Azure Cost Management returns daily grain with no intraday signal at
all. The GCP export carries `usage_end_time`, so a day that ends at 13:00 is visibly
partial. Cost Explorer flags every day of the unbilled month `Estimated`, which says
nothing about whether the day has been reported.

The failure this produces is specific and confident: a comparison whose current period
ends on a day the provider has only half reported shows spend *falling*, and a tool
that names causes will name one for a drop that does not exist. The
`aws-delayed-billing-data` scenario exists to reproduce exactly that.

## Decision

**Every source carries `data_through`: the reported coverage boundary, derived from the
evidence in the data itself - never the requested end date, and never "now".** It is
the last day the data shows no sign of being incomplete, not proof that it is complete:
where the shape carries no completeness signal, the boundary is provisional and is
reported with that limited confidence. The rule differs per shape because the evidence
differs:

* **AWS CUR:** the last date's hourly buckets are compared against the grain inferred
  from the earlier dates in the same file. A final date whose bucket count is below
  that grain is classified as partial, and coverage stops at the previous day. Measured against
  the file's own grain, not against a constant 24, because a daily-grain export
  carries one bucket a day and would otherwise lose its last day to a caveat about its
  format.
* **Azure Cost Management, from a file:** daily grain, no intraday signal. The last
  day is taken as complete and the summary says so; guessing would be worse.
* **GCP export:** the last day is complete only if its `usage_end_time` reaches
  midnight.
* **Live connectors:** the window never includes today, and the boundary is the last
  day the provider returned anything for. The clock is evidence a file does not have.

**The worker turns the boundary into a warning, not a number.** When `data_through`
falls before the end of the current period, `InvestigationContext` appends the
"complete only through" warning, with the expected provider delay taken from the
date-aware knowledge rules, and every finding stays provisional. Missing days are
unavailable data, never zero usage, and the warning says that in those words.

**The report never fills the gap.** No interpolation, no extrapolation, no "adjusted
for delay" figure. ADR 0002 forbids a model from touching the arithmetic; this record
forbids deterministic code from inventing data the provider has not sent.

## Rationale

A tool whose purpose is to explain a change cannot be allowed to manufacture one.
Reporting through the last complete day costs a day or two of recency and buys the
guarantee that a drop in the report is a drop in the bill.

Deciding per shape rather than with one rule is deliberate. The honest boundary is
whatever the data can prove, and the three exports prove different things. Where a
shape proves nothing, as Azure's daily grain does, the decision is to say so rather
than to pick a conservative number that would make every Azure upload look a day
stale.

## Alternatives considered

**Trim the last N days unconditionally.** Simple, and wrong twice: it throws away
complete days on a fresh export and keeps incomplete ones on a stale one.

**Ask the provider's delay rule instead of the data.** The knowledge store's
`expected_delay_hours` is a typical figure, not a fact about this file. It is used to
say whether an observed delay is *unusual*, never to set the boundary.

**Report the requested end date and add a footnote.** The footnote would be the only
honest thing on the page.

## Consequences

* A comparison over the most recent days is often reported as provisional. That is the
  correct reading of billing data and the UI says so.
* Each new source shape needs its own boundary rule, and the rule must be argued from
  the data. The live connectors' rules are recorded in ADR 0015 and ADR 0016.
* Enforced by `tests/unit/test_dataset_ingest.py` for the three upload rules, by
  `tests/unit/test_live_connectors.py` for the live rules, and by the
  `aws-delayed-billing-data` scenario in the offline evaluation.
