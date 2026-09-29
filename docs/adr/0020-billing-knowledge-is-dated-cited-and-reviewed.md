# ADR 0020: Billing knowledge is dated, cited, and reviewed, never scraped

* Status: Accepted
* Date: 2026-09-16
* Scope: `knowledge/<provider>/*.yaml`, `knowledge/monitored_sources.yaml`,
  `packages/knowledge`, `Settings.knowledge_review_max_age_days`,
  `.github/workflows/docs-change-check.yml`, `docs/billing-knowledge.md`
* Records the design the knowledge store implements. Supersedes nothing.

## Context

Explaining a bill needs facts about how providers bill: what a NAT Gateway charges
for, which Cost Explorer column is amortized, how long each provider's data lags.
Those facts change, on the provider's schedule, and a wrong one produces a confident
wrong explanation. The two easy ways to hold them are both bad: hard-coding them in
the playbooks means they rot invisibly, and letting a model recall them means they
were never verified.

## Decision

**Rules are data, in YAML, one file per topic under `knowledge/<provider>/`.** Each
rule carries `valid_from`, `valid_to`, `reviewed_at`, a `source` with an official
URL and the date it was checked, and a `confidence` of `supported` or `provisional`.
The store is read-only at runtime.

**A rule is selected by the usage date under investigation, never by "now".** The
worker asks with `rule_date(candidate)`, the first spike date or the period end, and a
rule that took effect after the billing period cannot be applied to it. A July rule is
never applied retroactively to a June bill.

**Every rule the report relies on is cited.** `RuleCitation` carries the rule id, the
source URL and the checked date into the finding, and `build_knowledge_provenance`
attaches them to the report, so a reader can follow every claim about billing
behaviour to the document it came from.

**A rule older than `review_max_age_days` is stale, and stale is visible.** The
default is 180 days. A stale rule is still applied - a fact does not become false by
being old - but its citation is flagged and the report says so. Nothing is silently
current.

**Provider documentation is watched, and a change opens a review, never an edit.**
`monitored_sources.yaml` lists the only acceptable inputs for a rule change. The weekly
`docs-change-check` workflow diffs them and files a report for a human. No rule is
updated automatically, and no model writes a rule.

## Rationale

Date-awareness is the property that makes the knowledge safe to keep. Without it,
every rule change is a choice between breaking old investigations and being wrong
about new ones. With it, both versions coexist and the usage date picks.

Citations turn the knowledge from an assertion into an audit trail, which is the same
move ADR 0007 makes for agent findings. A reviewer who doubts a finding can check the
rule; a reviewer who doubts the rule can check the source.

Refusing to scrape is a maintenance decision as much as a correctness one. A scraped
rule has no reviewer, no `reviewed_at`, and no one who can be asked why it says what
it says.

## Alternatives considered

**Facts in the playbooks.** Rejected; they rot invisibly and cannot be cited.

**Facts from the model.** Rejected by ADR 0002's logic extended to text: a model may
phrase a rule, never be the source of one.

**A vector store over provider docs.** Retrieval answers "what does the documentation
say", not "which version applied on this date", and offers no review discipline.

## Consequences

* Adding a provider feature that depends on a billing fact means adding a rule with a
  source before writing code that uses it.
* The report's date-aware citations are part of its contract, and the PDF renders them.
* `review_max_age_days` is a deployment setting; a shorter window means more rules
  flagged stale, not fewer applied.
* Enforced by `tests/knowledge` (date selection, schema versions, stale flagging,
  citation presence) and the scheduled documentation check.
