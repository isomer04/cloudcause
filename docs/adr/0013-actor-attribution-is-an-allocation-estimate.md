# ADR 0013: Actor attribution is an allocation estimate, not a billed fact

* Status: Accepted
* Date: 2026-09-15
* Scope: `packages/anomaly/actors.py`, `packages/anomaly/comparison.py`,
  `packages/datasets/ingest.py`, `packages/mcp/tools.py`

## Context

"Can it break spend down by principal?" is a reasonable question and the obvious
reading of it is wrong. It sounds like a grouping the bill already supports, in the
way `service` or `region` do, and it is not one.

No cloud's billing export carries the identity that made the call. CUR 2.0 has line
items, resource ids, resource tags, and nothing else that names a caller.
`aws:PrincipalTag` is an IAM/ABAC authorization concept evaluated at request time; it
never reaches the bill. Azure's usage details and GCP's billing export are the same
shape. Resource tags are the closest thing, and they describe the resource, not
whoever drove it this week.

So the only path from a dollar to a principal runs through audit evidence, and that
path is a join plus an apportionment. Both steps are lossy:

* The join is on resource id and day, which is the finest grain the two sides share.
  Cost rows are daily; CloudTrail records are per request.
* The apportionment needs a weight, and the natural weight — how many calls each
  principal made — is a poor proxy for what those calls cost. One Bedrock invocation
  with a long prompt can cost many times another. Invocation count and spend are only
  loosely related.

That is precisely the situation ADR 0006 already legislates: the bill proves the
money moved, and anything it does not prove is published as an estimate with its
evidence, never as a fact. This record applies that rule to a new dimension rather
than inventing a second regime for it.

One more fact shapes the honest framing. The AWS-sanctioned way to attribute shared
Bedrock spend per team is a **tagged application inference profile**: the tag lands in
CUR as an ordinary cost allocation tag, and `group_by="tag_owner"` reads it with none
of this machinery. Anybody who can adopt that should, and the answer to the question
should say so first. This ADR is for the harder case underneath it — spend already
shared across principals, where only the audit trail can apportion it after the fact.

## Decision

**`actor` is a `Dimension`, and it is the only one that is not read off a
`CostRecord`.** Every other dimension is a field lookup. `actor` runs a real
allocation pass first, and `_dimension_key` only reads the result back.

**The allocation is deterministic Python.** For each cost record, the audit events
that name its resource id and fall inside its usage day decide the split. The
record's `effective_cost`, `billed_cost`, and `usage_quantity` are divided across the
principals those events name, by weight. No model computes or adjusts a share —
ADR 0002 applies unchanged.

**Unmatched cost is published as `unattributed`, never distributed.** A record with no
resource id cannot be joined to anything. A record whose resource saw no audit
activity that day has no evidence behind it. Both go whole to an explicit
`unattributed` group, in the same way `tag_owner` already reports `untagged`.
Spreading them across the principals that happen to be known would manufacture
evidence, which is the one thing the dimension must not do.

An audit event that names no principal is weighted *under* `unattributed` rather than
dropped. Dropping it would silently hand its share to the identified principals — the
same guess, arrived at by omission.

**The weighting method is chosen per resource-day and always disclosed.** A token
weight is used when every matched event carries a positive token count, which Bedrock
model invocation logging supplies and plain CloudTrail does not. Otherwise the weight
is invocation count, and a warning saying so is attached to the result. Across a
period the reported method is `token_count`, `invocation_count`, `mixed`, or `none`.

The token weight requires *every* event in the group to carry a count, not most of
them. A partial set ranks an unrecorded event at zero, which understates it rather
than estimating it, and understating one principal overstates every other.

**The estimate travels with its own caveats.** `get_cost_breakdown(group_by="actor")`
returns an `attribution` block carrying the weighting method, the attributed and
unattributed totals, the unattributed share, the audit source's provenance, and the
warnings. A consumer that shows the shares without it is showing a number whose
meaning it cannot see: a split of 12% of the spend and a split of 98% of it are
different claims and look identical otherwise.

**Confidence is not special-cased.** Per ADR 0010 the score is derived from the
evidence present, and an actor breakdown is built on audit evidence, which that
scoring already weights highest because it is the only source that can name an actor
and a moment. Nothing here caps, floors, or overrides a confidence value; the
`unattributed` share is published as its own number instead of being folded into one.

## Rationale

Putting the allocation in `packages/anomaly` rather than in the MCP tool keeps it
where the rest of the deterministic arithmetic lives, testable without a server, and
reachable identically by an agent through MCP and by internal analytics.

Splitting money invites rounding drift, and drift here would be visible: the system
reconciles attributed change against total change and publishes the residual
(ADR 0009). A split whose parts did not sum back to the source record would show up
as an unattributed remainder the allocator had manufactured, indistinguishable from a
real one. So the largest share absorbs the rounding residual and the parts sum to the
record exactly. The residual goes to the largest share because that is where it is
proportionally smallest.

**The two sides do not spell a resource the same way, and the join absorbs that.**
CloudTrail always writes a full ARN. CUR writes `i-0abc…` for EC2, a bare bucket
name for S3, and a full ARN for a Bedrock inference profile. An exact-match join
therefore attributes *nothing* for the most common resource type on AWS, and the
output of that failure is indistinguishable from "no principal touched it" — the
worst possible failure mode for this feature, because it is silent and looks like
data. So an ARN also matches on its final segment. Only an ARN gains an alias; two
bare ids never match each other on a suffix, which would be a guess rather than a
reconciliation of two spellings of one name. This was found by building the upload
fixture and watching a correct-looking allocation attribute 100% of the spend to
`unattributed`.

The join key — resource id and usage day — is the coarsest part of the design and is
chosen for a reason. Cost rows are daily after aggregation, so an hourly join would
need intraday cost the dataset no longer holds. Being explicit about a daily join is
better than an hour-level one that quietly assumes a distribution nobody measured.

Carrying the allocated actor on a copied record under a reserved tag key, rather than
in a parallel structure, means the whole existing aggregation path — materiality,
candidate building, reconciliation — works on allocated records unchanged. The copies
are fresh; the sealed originals are never mutated, which ADR 0005 requires.

## Alternatives considered

**Read the principal from a cost allocation tag and call it attribution.** This is the
right answer when it is available, and it is already supported: tag the application
inference profile per team and group by `tag_owner`, which A3 widened to accept any
activated tag key. Rejected as *the* answer because it only works where spend was
separated in advance. It cannot apportion spend that is already shared, which is the
case that prompts the question.

**Distribute unmatched cost proportionally across known actors.** Rejected. It makes
the breakdown look complete and the completeness is fabricated. The `unattributed`
bucket is less satisfying and is the true shape of the evidence.

**Use invocation count always, for one consistent method.** Rejected: consistency is
not worth being predictably wrong where better evidence exists. Token counts, where
present, are far closer to what drives model spend.

**Use token counts always and refuse to allocate without them.** Rejected the other
way: most audit exports carry no token counts, and refusing would turn the feature
off for nearly every real dataset. The fallback plus a warning says less than the
user hoped without saying more than the data carries.

**Infer the principal from `aws:PrincipalTag` in the cost export.** Not an
alternative, a misconception, and it is recorded here because it is the first thing
anyone proposes. That key does not exist in CUR.

**Let an agent attribute spend by reading audit events and cost rows together.**
Rejected under ADR 0002 and ADR 0007. This is arithmetic over money; a model neither
computes nor adjusts it.

## Consequences

Accepted:

* An actor breakdown on a dataset with no audit source returns one `unattributed`
  group covering everything. That is correct and will look broken. The
  `attribution.weight_method` of `none` and the unattributed share of `1.0` are what
  distinguish "no evidence" from "one principal did everything".
* The daily join grain means a resource used by several principals on one day is
  apportioned rather than measured, even when per-request cost would in principle be
  derivable from a finer export.
* Matching an ARN on its final segment can in principle join two different
  resources that share a trailing name — a bucket and an inference profile both
  called `summariser`, say. Judged far less likely than the naming mismatch it
  fixes, and constrained by the same-day requirement, but it is a real way for this
  dimension to be wrong.
* The `unattributed` and `untagged` group keys collide with a principal or tag value
  literally called that. Accepted as the existing convention rather than introducing a
  sentinel that would have to be explained everywhere it surfaced.
* CloudTrail ingest now maps provider-native records, so the audit path has two
  accepted shapes to keep working instead of one.

Enforced by `tests/unit` on the parser mapping and the allocation arithmetic — the
unattributed path, the weight fallback, and the requirement that shares sum back to
the source record — by `tests/contract` on the widened `Dimension`, by `tests/mcp` on
the new `group_by` and its `attribution` block, and by `tests/security` confirming no
actor identity reaches a log line.
