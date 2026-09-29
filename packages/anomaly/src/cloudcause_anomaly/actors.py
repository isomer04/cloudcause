"""Actor (principal) cost attribution.

No cloud's billing export carries the identity that made the call. CUR 2.0 has
line items, resource ids, and resource tags; ``aws:PrincipalTag`` is an IAM/ABAC
authorization concept that never reaches the bill, and Azure and GCP are the
same. A breakdown by actor is therefore a *join* against audit evidence and an
*allocation estimate*, never a billed fact - exactly the shape ADR 0006
describes, and the reason ADR 0013 permits it at all.

Everything here is deterministic Python. No model computes or adjusts a share.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date
from typing import Literal

from cloudcause_contracts import AuditEvent, CostRecord

#: Cost that no audit event can speak for. Reported as its own group rather than
#: spread across the actors that happen to be known, which would invent evidence.
#: The same discipline as ``untagged`` in the ``tag_owner`` dimension.
UNATTRIBUTED_ACTOR = "unattributed"

#: Where an allocated record carries the actor it was split to. Reserved: an
#: upload cannot set it, because allocation runs on records the ingest already
#: sealed and writes a fresh copy.
ACTOR_TAG_KEY = "cloudcause:actor"

#: Six places is the rounding the rest of the analytics layer uses.
_PLACES = 6

#: ``token_count`` is the honest weight for model spend; ``invocation_count`` is
#: the fallback and says so in a warning. ``mixed`` means both were used within
#: one period, ``none`` that nothing was attributable at all.
WeightMethod = Literal["token_count", "invocation_count", "mixed", "none"]

_INVOCATION_WARNING = (
    "Actor shares are weighted by invocation count because the audit evidence carries no "
    "token counts. Invocation count is a poor proxy for model spend: one call with a long "
    "prompt can cost many times another. Supply Bedrock model invocation logging to get a "
    "token weight."
)

_MIXED_WARNING = (
    "Actor shares use a token weight where token counts were present and an invocation-count "
    "weight where they were not, so shares are not comparable across all resources in this "
    "period."
)


@dataclass(frozen=True)
class ActorAllocation:
    """One period's cost, split across the principals the audit trail names.

    ``records`` are copies: each carries one actor's share of a source record's
    money and quantity, and the actor itself in :data:`ACTOR_TAG_KEY`. The
    originals are untouched, and the shares of one source record sum back to it
    exactly.
    """

    records: list[CostRecord] = field(default_factory=list)
    weight_method: WeightMethod = "none"
    attributed_cost: float = 0.0
    unattributed_cost: float = 0.0
    attributed_magnitude: float = 0.0
    unattributed_magnitude: float = 0.0
    actors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return round(self.attributed_cost + self.unattributed_cost, _PLACES)

    @property
    def unattributed_share(self) -> float:
        """The unattributed fraction of total cost, ``0.0`` through ``1.0``.

        Published beside every actor breakdown: a split of 12% of the spend is a
        different claim from a split of 98%, and the consumer cannot tell which
        it has without this number.
        """

        total_magnitude = self.attributed_magnitude + self.unattributed_magnitude
        if total_magnitude <= 0.0:
            return 0.0
        return round(self.unattributed_magnitude / total_magnitude, _PLACES)


def _resource_aliases(resource_id: str) -> tuple[str, ...]:
    """The forms one resource can take across a bill and an audit trail.

    The two sides do not agree on how to name a resource. CloudTrail always
    writes a full ARN; CUR writes ``i-0abc...`` for EC2, a bare bucket name for
    S3, and a full ARN for a Bedrock inference profile. An exact-match join would
    therefore attribute nothing at all for the most common resource type on AWS,
    which reads as "no principal touched it" rather than as the naming mismatch
    it is.

    So an ARN also matches on its final segment - after the last ``/`` when it
    has one, otherwise after the last ``:``. Only an ARN gains an alias: two bare
    ids never match each other on a suffix, which would be a guess.
    """

    if not resource_id.startswith("arn:"):
        return (resource_id,)
    # Suffix reconciliation is safe only for identifiers whose abbreviated form
    # is well-defined: EC2 instance ids and globally unique S3 bucket names.
    is_ec2_instance = ":ec2:" in resource_id and ":instance/" in resource_id
    is_s3_bucket = ":s3:::" in resource_id
    if not (is_ec2_instance or is_s3_bucket):
        return (resource_id,)
    separator = "/" if is_ec2_instance else ":"
    tail = resource_id.rsplit(separator, 1)[-1]
    if not tail or tail == resource_id:
        return (resource_id,)
    return (resource_id, tail)


def _actor_weights(events: Sequence[AuditEvent]) -> tuple[dict[str, float], WeightMethod]:
    """Weight one day's events for one resource, by tokens where they exist.

    An event that names no principal is weighted under :data:`UNATTRIBUTED_ACTOR`
    rather than dropped. Dropping it would silently hand its share to whichever
    principals happened to be identified, which is the guess this module exists
    to avoid.

    The token weight is used only when *every* event in the group carries a
    positive token count. A partial set would rank an event with no recorded
    tokens at zero, which understates it rather than merely estimating it.
    """

    if not events:
        return {}, "none"
    counts = [event.token_count() for event in events]
    weights: dict[str, float] = defaultdict(float)
    if all(count is not None and count > 0.0 for count in counts):
        for event, count in zip(events, counts, strict=True):
            weights[event.actor or UNATTRIBUTED_ACTOR] += float(count or 0.0)
        return dict(weights), "token_count"
    for event in events:
        weights[event.actor or UNATTRIBUTED_ACTOR] += 1.0
    return dict(weights), "invocation_count"


def _actor_record(record: CostRecord, actor: str, share: float) -> CostRecord:
    return record.model_copy(
        update={
            "billed_cost": round(record.billed_cost * share, _PLACES),
            "effective_cost": round(record.effective_cost * share, _PLACES),
            "usage_quantity": round(record.usage_quantity * share, _PLACES),
            "tags": {**record.tags, ACTOR_TAG_KEY: actor},
        }
    )


def _split_record(record: CostRecord, weights: dict[str, float]) -> list[CostRecord]:
    """Split one cost record across actors by weight, losing nothing.

    Rounding each share independently would drop or invent fractions of a cent,
    and the reconciliation the rest of the system performs would then report an
    unattributed remainder this function manufactured. The largest share absorbs
    the residual instead, so the parts sum to the source record exactly.
    """

    total = sum(weights.values())
    if total <= 0.0:  # pragma: no cover - callers filter empty weights first
        return [_actor_record(record, UNATTRIBUTED_ACTOR, 1.0)]
    ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    parts = [_actor_record(record, actor, weight / total) for actor, weight in ordered]
    update: dict[str, float] = {}
    for name in ("billed_cost", "effective_cost", "usage_quantity"):
        source = getattr(record, name)
        residual = round(source - sum(getattr(part, name) for part in parts), _PLACES)
        if residual:
            update[name] = round(getattr(parts[0], name) + residual, _PLACES)
    if update:
        parts[0] = parts[0].model_copy(update=update)
    return parts


def _matching_events(
    index: dict[tuple[str, date], list[AuditEvent]], record: CostRecord
) -> list[AuditEvent]:
    """Every event naming this record's resource on its usage day, once each.

    A record and an event can meet under more than one alias, so events are
    de-duplicated by id: counting one twice would double its weight and quietly
    overstate the principal behind it.
    """

    matches: list[AuditEvent] = []
    seen: set[str] = set()
    for alias in _resource_aliases(record.resource_id or ""):
        for event in index.get((alias, record.usage_date), []):
            if event.event_id not in seen:
                seen.add(event.event_id)
                matches.append(event)
    return matches


def allocate_actor_costs(
    records: Iterable[CostRecord], audit_events: Iterable[AuditEvent]
) -> ActorAllocation:
    """Apportion cost across the principals the audit trail names.

    For each cost record, the audit events that name its resource and fall inside
    its usage day decide the split. Cost with no matching event - including every
    record that carries no resource id at all, because nothing can be joined to
    it - goes whole to :data:`UNATTRIBUTED_ACTOR`. It is never distributed and
    never guessed.
    """

    events_by_resource_day: dict[tuple[str, date], list[AuditEvent]] = defaultdict(list)
    for event in audit_events:
        day = event.event_time.astimezone(UTC).date()
        for resource_id in event.resource_ids:
            for alias in _resource_aliases(resource_id):
                events_by_resource_day[(alias, day)].append(event)

    allocated: list[CostRecord] = []
    methods: set[WeightMethod] = set()
    for record in records:
        weights, method = _actor_weights(
            _matching_events(events_by_resource_day, record) if record.resource_id else []
        )
        if not weights:
            allocated.append(_actor_record(record, UNATTRIBUTED_ACTOR, 1.0))
            continue
        if set(weights) != {UNATTRIBUTED_ACTOR}:
            methods.add(method)
        allocated.extend(_split_record(record, weights))

    attributed = 0.0
    unattributed = 0.0
    attributed_magnitude = 0.0
    unattributed_magnitude = 0.0
    actors: set[str] = set()
    for record in allocated:
        actor = record.tags.get(ACTOR_TAG_KEY, UNATTRIBUTED_ACTOR)
        actors.add(actor)
        if actor == UNATTRIBUTED_ACTOR:
            unattributed += record.effective_cost
            unattributed_magnitude += abs(record.effective_cost)
        else:
            attributed += record.effective_cost
            attributed_magnitude += abs(record.effective_cost)

    weight_method: WeightMethod
    if not methods:
        weight_method = "none"
    elif len(methods) == 1:
        weight_method = methods.pop()
    else:
        weight_method = "mixed"

    warnings: list[str] = []
    if weight_method == "invocation_count":
        warnings.append(_INVOCATION_WARNING)
    elif weight_method == "mixed":
        warnings.append(_MIXED_WARNING)

    allocation = ActorAllocation(
        records=allocated,
        weight_method=weight_method,
        attributed_cost=round(attributed, _PLACES),
        unattributed_cost=round(unattributed, _PLACES),
        attributed_magnitude=round(attributed_magnitude, _PLACES),
        unattributed_magnitude=round(unattributed_magnitude, _PLACES),
        actors=sorted(actors),
        warnings=warnings,
    )
    if allocation.unattributed_magnitude > 0.0:
        warnings.append(
            f"{allocation.unattributed_share * 100:.1f}% of cost in this period matched no audit "
            "event and is reported as 'unattributed' rather than spread across the actors that "
            "are known."
        )
    return allocation
