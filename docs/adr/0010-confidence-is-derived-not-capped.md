# ADR 0010: Confidence is derived from evidence, never reported from a ceiling

* Status: Accepted
* Date: 2026-08-14
* Scope: `packages/worker_core/playbooks.py`

## Context

`PlaybookSpec.max_confidence` exists to stop a playbook claiming more certainty than
its pattern deserves. `untagged_resources` caps at `0.70`, the fallback caps at `0.40`
to satisfy ADR 0006, and the default is `0.92`.

The original `_score_confidence` summed presence bonuses per evidence kind:
`0.30` base, plus `0.10` usage, `0.15` metric, `0.20` audit, `0.10` inventory,
`0.10` recommendation, `+0.05` for new spend. Maximum `1.00`, and `0.95` without the
new-spend bonus.

That total sits above the default ceiling, so any finding with a full evidence set
returned `min(0.95, 0.92)` — the ceiling. Every well-supported finding in the demo
reported `0.92`, identically, and did so no matter how much evidence was behind it or
how sharp the change was. Four causes of very different strength — a compromised API
key with three audit events and a +3,847% move, and a forgotten sandbox instance with
one audit event — displayed the same number.

The defect is not that `0.92` is too high. It is that the displayed value was a
constant, and the display was labelled as a measurement: a meter with ticks, an
`aria-valuenow`, a percentage. A reviewer who works with models notices a
never-varying score immediately, and correctly concludes the score is decorative.

This also made the ceiling untestable. A cap that binds on every input cannot be
distinguished from a cap that is never consulted, and neither can the scoring beneath
it.

## Decision

**The derived score must be able to move across its whole range without touching the
ceiling.** The four axes below sum to at most `0.865`, strictly under the `0.92`
default. `max_confidence` returns to being a safety limit for the playbooks that set
a lower one, and stops being the number on screen.

**Confidence is the sum of four independent axes:**

1. **Evidence kinds present** — `usage 0.07`, `inventory 0.05`, `metric 0.12`,
   `audit 0.18`, `recommendation 0.05`. Audit carries the most because it is the only
   source that can name an actor and a moment; a provider recommendation carries the
   least because it is a third party's opinion, not an observation.
2. **Corroboration** — `0.025` per additional item of a kind already counted, capped
   at `0.075`. Three audit events naming one resource are stronger than one.
3. **Separation** — up to `0.10`, reaching full value at a `+300%` change. A resource
   that quadrupled is easier to attribute than one that moved 25%, because ordinary
   week-to-week noise cannot account for it.
4. **Rate coherence** — up to `0.06`, and directional. A quantity-growth playbook
   claims the workload grew, so a stable effective unit cost corroborates it and drift
   means a rate or commitment change is mixed in that the named mechanism does not
   explain. A `requires_rate_change` playbook claims the opposite, so for it drift *is*
   the mechanism and a stable rate would contradict the claim. Full value at 25% drift
   in whichever direction the playbook requires.

Base is `0.16`. The existing penalties survive unchanged: `-0.10` for incomplete
provider data, `-0.05` for flagged untrusted text, and the downstream caps in
`build_finding` for a missing, stale, or undated billing rule.

**Cost evidence is deliberately unweighted.** It proves the money moved, never why.
It sets the floor rather than earning a bonus, which is ADR 0006 expressed in the
scoring function instead of only in the validator.

## Rationale

The four axes were chosen because each is independently observable in data the
deterministic layer already computes, and because each fails differently. Evidence
kinds answer *what sources agree*; corroboration answers *how many times*; separation
answers *could this be noise*; rate coherence answers *is the named mechanism even
the right kind of mechanism*. A finding strong on all four is genuinely better
supported than one strong on two, and the score now says so.

Weighting audit highest is the one judgement worth defending. Metrics show that
something changed; an audit event shows *who did what, when*. In every planted
scenario in `evaluations/`, the audit event is the fact that turns a correlation into
a mechanism, which is why `match_score` already scores audit patterns higher than
metric names.

The ceiling headroom is the load-bearing constraint, and it is stated against one
specific cap: the **sum of the four axes' maxima must stay below the default
`max_confidence`**. Without that, any future weight increase silently reintroduces
the original defect, and no test would notice because the output would still look
plausible. `tests/unit` asserts the inequality directly rather than asserting a
specific score, so the property survives retuning.

The lower per-playbook caps are deliberately outside that constraint. `untagged_resources`
at `0.70` and the fallback at `0.40` are *meant* to bind — a fallback finding that
scored above `0.40` would violate ADR 0006 — so requiring headroom against the lowest
cap in the set would be requiring the scoring never to reach the answer it exists to
produce.

Concretely, the demo's four findings now score `0.865 / 0.840 / 0.833 / 0.785`,
ordered as the evidence orders them, and the low-evidence
`aws-cost-only-unexplained` scenario lands at `0.39` under the fallback cap.

## Alternatives considered

**Raise `max_confidence` above the sum so the cap never binds.** Rejected. It fixes
the symptom by removing the safety limit, and leaves the scoring untested. The
correct move is to make the score fit under the cap, not to move the cap out of the
way.

**Drop the number and show only the risk label.** Genuinely tempting, and the
reviewer's own suggested option. Rejected because the number is used for ranking in
`rank_findings` and is asserted in `evaluations/expected_findings`; removing it from
display while keeping it internally would leave the evaluation grading something the
user never sees. A visible score that varies is more honest than a hidden one.

**Let the live agent set its own confidence.** Rejected under ADR 0007: agent output
is untrusted. `confidence_override` remains available for a live agent, but it passes
through the same validation and the same caps.

**Calibrate the weights empirically against labelled outcomes.** The right answer,
and not available — there is no corpus of confirmed cost root causes to calibrate
against. The weights are declared judgement, documented here so they can be argued
with, rather than presented as derived from data that does not exist.

## Consequences

Accepted:

* Published confidence drops for well-evidenced findings, `0.92` to roughly
  `0.79–0.87`. This looks like a regression and is not: the previous value was the
  ceiling, not a measurement.
* Documentation quoting `0.92` needed updating, and any future weight change moves
  the demo's published numbers. The evaluation thresholds are ranges rather than
  points precisely so retuning does not force a scenario rewrite.
* The weights are defensible but not calibrated, and this record says so rather than
  implying an accuracy the method does not have.

Enforced by `tests/unit/test_confidence_scoring.py`: the axis maximum stays under
`PlaybookSpec.max_confidence`, every evidence kind moves the score, a second item of a
kind corroborates the first, separation and rate coherence each change it in the
expected direction, the fallback ceiling still holds a cost-only finding at `<= 0.40`,
and the demo's findings do not all report one value.
