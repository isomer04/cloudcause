"""Actor attribution: the CloudTrail mapping and the allocation arithmetic.

The money assertions are the point of this file. Per ADR 0013 an actor breakdown
is an allocation estimate, so what has to hold is that the estimate never invents
a dollar, never loses one, and never quietly presents a guess as a measurement.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from cloudcause_anomaly import (
    ACTOR_TAG_KEY,
    UNATTRIBUTED_ACTOR,
    allocate_actor_costs,
    group_changes,
    group_changes_by_actor,
)
from cloudcause_contracts import AuditEvent, CostRecord, DateRange, Settings
from cloudcause_datasets import FormatMismatchError, IngestError, parse_evidence_source
from conftest import (
    AWS_ACCOUNT,
    BEDROCK_PROFILE,
    assumed_role_identity,
    aws_cloudtrail_json,
    cloudtrail_record,
)

CURRENT = DateRange(start=date(2026, 7, 13), end=date(2026, 7, 19))
BASELINE = DateRange(start=date(2026, 7, 6), end=date(2026, 7, 12))
SPIKE_DAY = date(2026, 7, 15)


def audit_event(
    event_id: str,
    actor: str | None,
    *,
    day: date = SPIKE_DAY,
    hour: int = 9,
    resource: str = BEDROCK_PROFILE,
    tokens: float | None = None,
) -> AuditEvent:
    attributes = {"input_token_count": str(tokens)} if tokens is not None else {}
    return AuditEvent(
        provider="aws",
        event_id=event_id,
        event_name="InvokeModel",
        event_time=datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
        source="cloudtrail",
        actor=actor,
        resource_ids=[resource],
        attributes=attributes,
    )


def cost(
    day: date, amount: float, *, resource: str | None = BEDROCK_PROFILE, quantity: float = 4.0
) -> CostRecord:
    return CostRecord(
        provider="aws",
        billing_account_id=AWS_ACCOUNT,
        usage_date=day,
        service_name="Amazon Bedrock",
        resource_id=resource,
        usage_quantity=quantity,
        billed_cost=amount,
        effective_cost=amount,
    )


#
# A1 - the CloudTrail-native parser
#


def test_a_cloudtrail_export_is_detected_by_content(settings: Settings) -> None:
    result = parse_evidence_source("aws", "audit", aws_cloudtrail_json(), settings)

    assert result.detected_format == "aws-cloudtrail-json"
    assert result.accepted_rows == 1


def test_cloudtrail_fields_map_onto_the_audit_event_contract(settings: Settings) -> None:
    result = parse_evidence_source("aws", "audit", aws_cloudtrail_json(), settings)

    (event,) = result.audit_events
    assert event.event_id == "ct-0001"
    assert event.event_name == "InvokeModel"
    assert event.source == "bedrock.amazonaws.com"
    assert event.event_time.date() == CURRENT.start
    assert event.region_id == "us-east-1"
    assert event.source_ip == "10.0.31.7"
    assert event.resource_ids == [BEDROCK_PROFILE]
    assert event.provider == "aws"


@pytest.mark.parametrize(
    ("identity", "expected_actor", "expected_type"),
    [
        (
            {"type": "IAMUser", "arn": f"arn:aws:iam::{AWS_ACCOUNT}:user/analyst"},
            f"arn:aws:iam::{AWS_ACCOUNT}:user/analyst",
            "IAMUser",
        ),
        (
            assumed_role_identity("search-api", "i-0abc"),
            f"arn:aws:iam::{AWS_ACCOUNT}:role/search-api",
            "AssumedRole",
        ),
        (
            {"type": "AWSService", "invokedBy": "lambda.amazonaws.com"},
            "lambda.amazonaws.com",
            "AWSService",
        ),
        (
            {"type": "Root", "arn": f"arn:aws:iam::{AWS_ACCOUNT}:root"},
            f"arn:aws:iam::{AWS_ACCOUNT}:root",
            "Root",
        ),
    ],
    ids=["iam-user", "assumed-role", "aws-service", "root"],
)
def test_every_user_identity_variant_names_an_actor(
    settings: Settings, identity: dict[str, object], expected_actor: str, expected_type: str
) -> None:
    payload = aws_cloudtrail_json([cloudtrail_record(identity=identity)])

    (event,) = parse_evidence_source("aws", "audit", payload, settings).audit_events

    assert event.actor == expected_actor
    assert event.actor_type == expected_type


def test_one_role_across_many_sessions_is_one_actor(settings: Settings) -> None:
    """The stable issuer ARN groups sessions without losing account identity."""

    payload = aws_cloudtrail_json(
        [
            cloudtrail_record(
                event_id=f"ct-{index}", identity=assumed_role_identity("search-api", f"sess-{index}")
            )
            for index in range(4)
        ]
    )

    events = parse_evidence_source("aws", "audit", payload, settings).audit_events

    assert len({event.actor for event in events}) == 1
    assert events[0].actor == f"arn:aws:iam::{AWS_ACCOUNT}:role/search-api"


def test_an_identity_naming_nobody_yields_no_actor(settings: Settings) -> None:
    payload = aws_cloudtrail_json([cloudtrail_record(identity={"type": "Unknown"})])

    (event,) = parse_evidence_source("aws", "audit", payload, settings).audit_events

    assert event.actor is None
    assert event.actor_type == "Unknown"


def test_token_counts_are_read_from_a_merged_export(settings: Settings) -> None:
    payload = aws_cloudtrail_json([cloudtrail_record(tokens=(18_000, 2_000))])

    (event,) = parse_evidence_source("aws", "audit", payload, settings).audit_events

    assert event.token_count() == pytest.approx(20_000.0)


def test_plain_cloudtrail_records_carry_no_token_count(settings: Settings) -> None:
    (event,) = parse_evidence_source("aws", "audit", aws_cloudtrail_json(), settings).audit_events

    assert event.token_count() is None


def test_arbitrary_request_parameters_are_not_copied_into_attributes(
    settings: Settings,
) -> None:
    """``requestParameters`` is caller-supplied. An allowlist keeps it out."""

    payload = aws_cloudtrail_json(
        [cloudtrail_record(requestParameters={"prompt": "a customer's private prompt text"})]
    )

    (event,) = parse_evidence_source("aws", "audit", payload, settings).audit_events

    assert "a customer's private prompt text" not in json.dumps(event.attributes)
    assert set(event.attributes) <= {"requestID", "eventCategory", "eventType", "errorCode"}


def test_a_record_missing_a_required_field_is_rejected_by_name_not_value(
    settings: Settings,
) -> None:
    good = cloudtrail_record(event_id="ct-ok")
    bad = cloudtrail_record(event_id="ct-bad")
    del bad["eventID"]
    bad["accountName"] = "acme-holdings"

    result = parse_evidence_source("aws", "audit", aws_cloudtrail_json([good, bad]), settings)

    assert result.accepted_rows == 1
    (rejection,) = result.rejections
    assert rejection.row_number == 2
    assert "eventID" in rejection.detail
    assert "acme-holdings" not in rejection.detail


def test_the_cloudcause_items_shape_still_parses(settings: Settings) -> None:
    payload = json.dumps(
        {
            "items": [
                {
                    "event_id": "cc-01",
                    "event_name": "ReplaceRoute",
                    "event_time": "2026-07-15T08:14:00Z",
                    "source": "cloudtrail",
                    "actor": "deploy-pipeline",
                }
            ]
        }
    ).encode()

    result = parse_evidence_source("aws", "audit", payload, settings)

    assert result.detected_format == "cloudcause-audit-json"
    assert result.audit_events[0].actor == "deploy-pipeline"


def test_a_cloudtrail_file_uploaded_as_another_provider_is_refused(settings: Settings) -> None:
    with pytest.raises(FormatMismatchError) as error:
        parse_evidence_source("gcp", "audit", aws_cloudtrail_json(), settings)

    assert "/sources/aws/audit" in str(error.value)


def test_a_cloudtrail_file_uploaded_as_another_kind_is_refused(settings: Settings) -> None:
    with pytest.raises(FormatMismatchError):
        parse_evidence_source("aws", "inventory", aws_cloudtrail_json(), settings)


def test_the_refusal_for_an_unknown_shape_names_both_accepted_shapes(
    settings: Settings,
) -> None:
    with pytest.raises(IngestError) as error:
        parse_evidence_source("aws", "audit", json.dumps({"events": []}).encode(), settings)

    assert "items" in str(error.value)
    assert "Records" in str(error.value)


#
# A2 - the allocation
#


def test_cost_is_split_by_token_share() -> None:
    records = [cost(SPIKE_DAY, 100.0)]
    events = [
        audit_event("e1", "search-api", tokens=90_000),
        audit_event("e2", "batch-jobs", tokens=10_000),
    ]

    allocation = allocate_actor_costs(records, events)

    by_actor = {record.tags[ACTOR_TAG_KEY]: record.effective_cost for record in allocation.records}
    assert allocation.weight_method == "token_count"
    assert by_actor == {"search-api": pytest.approx(90.0), "batch-jobs": pytest.approx(10.0)}


def test_the_split_sums_back_to_the_source_record_exactly() -> None:
    """Three-way splits do not divide evenly; the residual may not be lost.

    A split that does not sum back would surface downstream as an unattributed
    remainder the allocator invented, indistinguishable from a real one.
    """

    records = [cost(SPIKE_DAY, 10.0, quantity=1.0)]
    events = [audit_event(f"e{index}", f"actor-{index}") for index in range(3)]

    allocation = allocate_actor_costs(records, events)

    assert sum(record.effective_cost for record in allocation.records) == pytest.approx(10.0)
    assert sum(record.billed_cost for record in allocation.records) == pytest.approx(10.0)
    assert sum(record.usage_quantity for record in allocation.records) == pytest.approx(1.0)


def test_the_source_records_are_not_mutated() -> None:
    source = cost(SPIKE_DAY, 100.0)

    allocate_actor_costs([source], [audit_event("e1", "search-api")])

    assert source.effective_cost == pytest.approx(100.0)
    assert ACTOR_TAG_KEY not in source.tags


def test_cost_with_no_matching_event_is_unattributed_not_distributed() -> None:
    records = [cost(SPIKE_DAY, 100.0), cost(SPIKE_DAY, 40.0, resource="i-unwatched")]
    events = [audit_event("e1", "search-api")]

    allocation = allocate_actor_costs(records, events)

    by_actor: dict[str, float] = {}
    for record in allocation.records:
        by_actor[record.tags[ACTOR_TAG_KEY]] = (
            by_actor.get(record.tags[ACTOR_TAG_KEY], 0.0) + record.effective_cost
        )
    assert by_actor["search-api"] == pytest.approx(100.0)
    assert by_actor[UNATTRIBUTED_ACTOR] == pytest.approx(40.0)
    assert allocation.unattributed_share == pytest.approx(40.0 / 140.0, abs=1e-6)


def test_a_record_with_no_resource_id_cannot_be_joined() -> None:
    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 25.0, resource=None)], [audit_event("e1", "search-api")]
    )

    (record,) = allocation.records
    assert record.tags[ACTOR_TAG_KEY] == UNATTRIBUTED_ACTOR
    assert allocation.unattributed_share == pytest.approx(1.0)


def test_an_event_on_another_day_does_not_attribute_this_day() -> None:
    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 50.0)], [audit_event("e1", "search-api", day=date(2026, 7, 16))]
    )

    assert allocation.records[0].tags[ACTOR_TAG_KEY] == UNATTRIBUTED_ACTOR


def test_event_days_are_normalized_to_utc_before_joining() -> None:
    event = audit_event("e1", "search-api").model_copy(
        update={"event_time": datetime.fromisoformat("2026-07-14T23:30:00-05:00")}
    )

    allocation = allocate_actor_costs([cost(SPIKE_DAY, 50.0)], [event])

    assert allocation.records[0].tags[ACTOR_TAG_KEY] == "search-api"


def test_an_event_naming_no_principal_weighs_toward_unattributed() -> None:
    """Dropping it would hand its share to the named actors - the same guess."""

    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 100.0)],
        [audit_event("e1", "search-api"), audit_event("e2", None)],
    )

    by_actor = {record.tags[ACTOR_TAG_KEY]: record.effective_cost for record in allocation.records}
    assert by_actor["search-api"] == pytest.approx(50.0)
    assert by_actor[UNATTRIBUTED_ACTOR] == pytest.approx(50.0)


def test_without_token_counts_the_weight_falls_back_and_says_so() -> None:
    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 100.0)],
        [audit_event("e1", "search-api"), audit_event("e2", "batch-jobs")],
    )

    by_actor = {record.tags[ACTOR_TAG_KEY]: record.effective_cost for record in allocation.records}
    assert allocation.weight_method == "invocation_count"
    assert by_actor == {"search-api": pytest.approx(50.0), "batch-jobs": pytest.approx(50.0)}
    assert any("invocation count" in warning for warning in allocation.warnings)


def test_a_partial_token_set_does_not_earn_a_token_weight() -> None:
    """One unrecorded count would rank that event at zero, not merely estimate it."""

    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 100.0)],
        [audit_event("e1", "search-api", tokens=90_000), audit_event("e2", "batch-jobs")],
    )

    assert allocation.weight_method == "invocation_count"


def test_non_finite_token_counts_fall_back_without_poisoning_money() -> None:
    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 100.0)],
        [audit_event("e1", "search-api", tokens=float("inf")), audit_event("e2", "batch-jobs")],
    )

    assert allocation.weight_method == "invocation_count"
    assert all(record.effective_cost == pytest.approx(50.0) for record in allocation.records)


def test_unattributed_share_uses_magnitude_for_credits() -> None:
    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 100.0), cost(SPIKE_DAY, -50.0, resource="unmatched")],
        [audit_event("e1", "search-api")],
    )

    assert allocation.unattributed_cost == pytest.approx(-50.0)
    assert allocation.unattributed_share == pytest.approx(1 / 3)


def test_token_and_invocation_weights_in_one_period_report_as_mixed() -> None:
    other = "arn:aws:bedrock:us-east-1:111122223333:application-inference-profile/classifier"
    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 100.0), cost(SPIKE_DAY, 50.0, resource=other)],
        [
            audit_event("e1", "search-api", tokens=90_000),
            audit_event("e2", "batch-jobs", resource=other),
        ],
    )

    assert allocation.weight_method == "mixed"
    assert any("not comparable" in warning for warning in allocation.warnings)


def test_nothing_attributable_reports_no_weight_method() -> None:
    allocation = allocate_actor_costs([cost(SPIKE_DAY, 100.0)], [])

    assert allocation.weight_method == "none"
    assert allocation.unattributed_share == pytest.approx(1.0)


def test_a_bare_cost_resource_id_joins_a_cloudtrail_arn() -> None:
    """CUR writes ``i-0abc...``; CloudTrail writes the full ARN for the same thing.

    An exact-match join attributes nothing for the commonest resource type on
    AWS, and reads as "no principal touched it" rather than as a spelling
    mismatch.
    """

    arn = f"arn:aws:ec2:us-east-1:{AWS_ACCOUNT}:instance/i-0a1b2c3d4e5f67890"
    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 30.0, resource="i-0a1b2c3d4e5f67890")],
        [audit_event("e1", "batch-etl", resource=arn)],
    )

    assert allocation.records[0].tags[ACTOR_TAG_KEY] == "batch-etl"
    assert allocation.unattributed_share == pytest.approx(0.0)


def test_one_event_matching_under_two_aliases_is_counted_once() -> None:
    arn = f"arn:aws:ec2:us-east-1:{AWS_ACCOUNT}:instance/i-0a1b2c3d4e5f67890"
    event = AuditEvent(
        provider="aws",
        event_id="e1",
        event_name="StartInstances",
        event_time=audit_event("x", "a").event_time,
        source="cloudtrail",
        actor="batch-etl",
        resource_ids=[arn, "i-0a1b2c3d4e5f67890"],
    )

    allocation = allocate_actor_costs(
        [cost(SPIKE_DAY, 30.0, resource="i-0a1b2c3d4e5f67890")],
        [event, audit_event("e2", "ml-training", resource=arn)],
    )

    by_actor = {record.tags[ACTOR_TAG_KEY]: record.effective_cost for record in allocation.records}
    assert by_actor["batch-etl"] == pytest.approx(15.0)
    assert by_actor["ml-training"] == pytest.approx(15.0)


#
# A2 - the grouping built on it
#


def test_grouping_by_actor_compares_the_two_periods() -> None:
    records = [
        *[cost(day, 2.0) for day in BASELINE.dates()],
        *[cost(day, 2.0) for day in CURRENT.dates()[:2]],
        *[cost(day, 30.0) for day in CURRENT.dates()[2:]],
    ]
    events = [
        audit_event(f"b{index}", "nightly-batch", day=day, tokens=1_000)
        for index, day in enumerate(BASELINE.dates())
    ]
    events += [
        audit_event(f"c{index}", "nightly-batch", day=day, tokens=1_000)
        for index, day in enumerate(CURRENT.dates()[:2])
    ]
    for index, day in enumerate(CURRENT.dates()[2:]):
        events.append(audit_event(f"n{index}", "nightly-batch", day=day, tokens=1_000))
        events.append(audit_event(f"s{index}", "search-page", day=day, hour=11, tokens=9_000))

    changes, allocation = group_changes_by_actor(records, events, CURRENT, BASELINE, "aws")

    by_key = {change.key: change for change in changes}
    assert allocation.weight_method == "token_count"
    assert allocation.unattributed_share == pytest.approx(0.0)
    assert by_key["search-page"].baseline_cost == pytest.approx(0.0)
    assert by_key["search-page"].current_cost == pytest.approx(135.0)
    assert by_key["nightly-batch"].baseline_cost == pytest.approx(14.0)
    assert changes[0].key == "search-page"


def test_grouping_by_actor_covers_every_dollar_in_the_window() -> None:
    records = [*[cost(day, 2.0) for day in BASELINE.dates()], *[cost(day, 30.0) for day in CURRENT.dates()]]
    events = [audit_event(f"e{index}", "search-page", day=day) for index, day in enumerate(CURRENT.dates())]

    changes, allocation = group_changes_by_actor(records, events, CURRENT, BASELINE, "aws")

    assert sum(change.current_cost for change in changes) == pytest.approx(7 * 30.0)
    assert sum(change.baseline_cost for change in changes) == pytest.approx(7 * 2.0)
    assert allocation.total_cost == pytest.approx(7 * 32.0)


def test_group_changes_routes_the_actor_dimension_through_the_allocation() -> None:
    records = [cost(day, 10.0) for day in CURRENT.dates()]
    events = [audit_event(f"e{index}", "search-page", day=day) for index, day in enumerate(CURRENT.dates())]

    changes = group_changes(records, "actor", CURRENT, BASELINE, "aws", audit_events=events)

    assert [change.key for change in changes] == ["search-page"]


def test_grouping_by_actor_ignores_another_providers_audit_events() -> None:
    records = [cost(day, 10.0) for day in CURRENT.dates()]
    foreign = audit_event("gcp-1", "search-page").model_copy(update={"provider": "gcp"})

    changes, allocation = group_changes_by_actor(records, [foreign], CURRENT, BASELINE, "aws")

    assert [change.key for change in changes] == [UNATTRIBUTED_ACTOR]
    assert allocation.weight_method == "none"


def test_grouping_by_actor_without_audit_evidence_says_unattributed() -> None:
    """Not "one principal did everything" - an empty result would imply that."""

    records = [cost(day, 10.0) for day in CURRENT.dates()]

    changes, allocation = group_changes_by_actor(records, [], CURRENT, BASELINE, "aws")

    assert [change.key for change in changes] == [UNATTRIBUTED_ACTOR]
    assert allocation.weight_method == "none"
    assert allocation.unattributed_share == pytest.approx(1.0)


#
# A3 - the widened tag breakdown
#


def test_tag_owner_still_defaults_to_the_owner_keys() -> None:
    records = [
        *[cost(day, 2.0) for day in BASELINE.dates()],
        *[cost(day, 20.0) for day in CURRENT.dates()],
    ]
    tagged = [record.model_copy(update={"tags": {"Team": "search"}}) for record in records]

    changes = group_changes(tagged, "tag_owner", CURRENT, BASELINE, "aws")

    assert [change.key for change in changes] == ["search"]


def test_any_activated_cost_allocation_tag_can_be_grouped_on() -> None:
    records = [
        *[cost(day, 2.0) for day in BASELINE.dates()],
        *[cost(day, 20.0) for day in CURRENT.dates()],
    ]
    tagged = [
        record.model_copy(update={"tags": {"cost-center": "cc-4471", "owner": "platform"}})
        for record in records
    ]

    changes = group_changes(tagged, "tag_owner", CURRENT, BASELINE, "aws", tag_key="cost-center")

    assert [change.key for change in changes] == ["cc-4471"]


def test_a_record_without_the_named_tag_reports_untagged() -> None:
    records = [cost(day, 20.0) for day in CURRENT.dates()]

    changes = group_changes(records, "tag_owner", CURRENT, BASELINE, "aws", tag_key="cost-center")

    assert [change.key for change in changes] == ["untagged"]
