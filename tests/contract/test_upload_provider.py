"""The upload adapter is held to the same contract as the fixture ones.

If an uploaded dataset produced a different bundle shape, every playbook, the
evidence factory, and both live agents would need a second code path. They do not:
the only difference a reader can observe is ``Provenance.origin``, plus which
sources are empty.
"""

from __future__ import annotations

from datetime import date

import pytest
from cloudcause_contracts import (
    DateRange,
    Finding,
    Provenance,
    Settings,
    WorkerResponse,
)
from cloudcause_datasets import (
    DatasetNotSealedError,
    DatasetProviderMissingError,
    UnknownDatasetError,
    add_source,
    build_dataset_store,
    parse_cost_source,
    parse_evidence_source,
    seal_dataset,
)
from cloudcause_providers import (
    FixtureDataProvider,
    UnknownScenarioError,
    UploadDataProvider,
    get_data_provider,
)
from conftest import (
    NAT_RESOURCE,
    aws_audit_json,
    aws_cur_json,
    aws_inventory_json,
    aws_metrics_json,
    aws_recommendations_json,
    azure_cost_management_json,
)

CURRENT = DateRange(start=date(2026, 7, 13), end=date(2026, 7, 19))
BASELINE = DateRange(start=date(2026, 7, 6), end=date(2026, 7, 12))
PERIODS = [CURRENT, BASELINE]


def seal_cost_only(settings: Settings) -> str:
    store = build_dataset_store(settings)
    dataset = store.create()
    parsed = parse_cost_source("aws", aws_cur_json(), settings)
    add_source(store, dataset.dataset_id, "aws", "cost", parsed, 512, settings)
    seal_dataset(store, dataset.dataset_id)
    return dataset.dataset_id


def seal_full(settings: Settings) -> str:
    store = build_dataset_store(settings)
    dataset = store.create()
    add_source(
        store,
        dataset.dataset_id,
        "aws",
        "cost",
        parse_cost_source("aws", aws_cur_json(), settings),
        512,
        settings,
    )
    for kind, payload in (
        ("metrics", aws_metrics_json()),
        ("audit", aws_audit_json()),
        ("inventory", aws_inventory_json()),
        ("recommendations", aws_recommendations_json()),
    ):
        add_source(
            store,
            dataset.dataset_id,
            "aws",
            kind,  # type: ignore[arg-type]
            parse_evidence_source("aws", kind, payload, settings),  # type: ignore[arg-type]
            256,
            settings,
        )
    seal_dataset(store, dataset.dataset_id)
    return dataset.dataset_id


# --------------------------------------------------------------------- resolution


def test_a_dataset_id_wins_over_the_scenario_id(upload_settings: Settings) -> None:
    dataset_id = seal_cost_only(upload_settings)
    adapter = get_data_provider("aws", upload_settings, "default", dataset_id)
    assert isinstance(adapter, UploadDataProvider)

    without = get_data_provider("aws", upload_settings, "default")
    assert isinstance(without, FixtureDataProvider), "the demo path is untouched"


def test_an_unknown_dataset_never_falls_through_to_fixtures(
    upload_settings: Settings,
) -> None:
    with pytest.raises(UnknownDatasetError):
        get_data_provider("aws", upload_settings, "default", "no-such-dataset")


def test_an_unsealed_dataset_cannot_be_resolved(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    dataset = store.create()
    add_source(
        store,
        dataset.dataset_id,
        "aws",
        "cost",
        parse_cost_source("aws", aws_cur_json(), upload_settings),
        512,
        upload_settings,
    )
    with pytest.raises(DatasetNotSealedError):
        get_data_provider("aws", upload_settings, "default", dataset.dataset_id)


def test_a_provider_absent_from_the_dataset_is_named_not_guessed(
    upload_settings: Settings,
) -> None:
    dataset_id = seal_cost_only(upload_settings)
    with pytest.raises(DatasetProviderMissingError) as error:
        get_data_provider("gcp", upload_settings, "default", dataset_id)
    assert "no gcp data" in str(error.value)


def test_a_dataset_id_wins_over_live_mode_too(upload_settings: Settings) -> None:
    """An upload is real data, so it must not be shadowed by an absent connector."""

    dataset_id = seal_cost_only(upload_settings)
    live = upload_settings.with_overrides(data_mode="live")
    assert isinstance(get_data_provider("aws", live, "default", dataset_id), UploadDataProvider)


def test_scenario_resolution_still_behaves(upload_settings: Settings) -> None:
    with pytest.raises(UnknownScenarioError):
        get_data_provider("aws", upload_settings, "no-such-scenario")


# ------------------------------------------------------------------ the contract


async def test_a_full_upload_satisfies_the_bundle_contract(
    upload_settings: Settings,
) -> None:
    adapter = get_data_provider("aws", upload_settings, "", seal_full(upload_settings))
    bundle = await adapter.get_bundle(PERIODS)

    assert bundle.provider == "aws"
    assert bundle.costs.items
    assert bundle.resources.items
    assert bundle.metrics.items
    assert bundle.audit_events.items
    assert bundle.recommendations.items

    for source in bundle.sources:
        assert source.provider == "aws"
        assert source.origin == "upload"
        assert source.is_fixture is False
        assert source.schema_version
        assert source.query_reference.startswith("upload:aws/")
        assert source.retrieved_at >= source.data_through
        assert source.observed_at.tzinfo is not None

    assert bundle.origin() == "upload"
    assert bundle.available_source_types() == {
        "cost",
        "usage",
        "inventory",
        "metric",
        "audit",
        "recommendation",
    }


async def test_cost_records_from_an_upload_are_normalized_consistently(
    upload_settings: Settings,
) -> None:
    adapter = get_data_provider("aws", upload_settings, "", seal_cost_only(upload_settings))
    result = await adapter.get_costs(PERIODS)
    assert result.items
    for record in result.items:
        assert record.provider == "aws"
        assert record.currency == "USD"
        assert record.billing_account_id
        assert record.service_name and record.service_category
        assert record.charge_category in (
            "usage",
            "purchase",
            "tax",
            "credit",
            "adjustment",
            "unknown",
        )
        assert any(period.contains(record.usage_date) for period in PERIODS)
        assert isinstance(record.tags, dict)


async def test_period_filtering_is_honoured_for_uploads(upload_settings: Settings) -> None:
    adapter = get_data_provider("aws", upload_settings, "", seal_cost_only(upload_settings))
    current_only = await adapter.get_costs([CURRENT])
    assert current_only.items
    assert all(CURRENT.contains(record.usage_date) for record in current_only.items)


async def test_a_cost_only_upload_reports_empty_evidence_not_borrowed_evidence(
    upload_settings: Settings,
) -> None:
    adapter = get_data_provider("aws", upload_settings, "", seal_cost_only(upload_settings))
    bundle = await adapter.get_bundle(PERIODS)

    assert bundle.costs.items
    assert bundle.metrics.items == []
    assert bundle.audit_events.items == []
    assert bundle.resources.items == []
    assert bundle.recommendations.items == []
    assert bundle.available_source_types() == {"cost", "usage"}
    for source in bundle.sources:
        assert source.origin == "upload", (
            "an absent source still says the dataset has none, never that it came "
            "from somewhere else"
        )
    assert any("not-supplied" in source.query_reference for source in bundle.sources)


async def test_metric_filtering_by_resource_id_works_for_uploads(
    upload_settings: Settings,
) -> None:
    adapter = get_data_provider("aws", upload_settings, "", seal_full(upload_settings))
    wanted = await adapter.get_metrics([NAT_RESOURCE])
    assert [series.resource_id for series in wanted.items] == [NAT_RESOURCE]
    assert (await adapter.get_metrics(["i-does-not-exist"])).items == []


async def test_azure_and_aws_can_share_one_dataset(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    dataset = store.create()
    add_source(
        store,
        dataset.dataset_id,
        "aws",
        "cost",
        parse_cost_source("aws", aws_cur_json(), upload_settings),
        512,
        upload_settings,
    )
    add_source(
        store,
        dataset.dataset_id,
        "azure",
        "cost",
        parse_cost_source("azure", azure_cost_management_json(), upload_settings),
        512,
        upload_settings,
    )
    seal_dataset(store, dataset.dataset_id)

    for provider in ("aws", "azure"):
        adapter = get_data_provider(provider, upload_settings, "", dataset.dataset_id)
        bundle = await adapter.get_bundle(PERIODS)
        assert bundle.costs.items
        assert all(record.provider == provider for record in bundle.costs.items)


# ------------------------------------------------------- serialization over HTTP


def test_upload_provenance_survives_a_worker_response_round_trip() -> None:
    """``is_fixture`` stays a declared field precisely so this keeps working.

    ``Provenance`` forbids extra keys and ``WorkerResponse.sources`` crosses HTTP
    when ``worker_mode=http``. A computed ``is_fixture`` would serialize and then
    fail to validate with "extra inputs are not permitted".
    """

    response = WorkerResponse(
        investigation_id="inv-round-trip",
        provider="aws",
        sources=[
            Provenance(
                provider="aws",
                source="uploaded-cost-export",
                observed_at="2026-07-19T23:59:59Z",  # type: ignore[arg-type]
                retrieved_at="2026-07-20T09:00:00Z",  # type: ignore[arg-type]
                data_through="2026-07-19T23:59:59Z",  # type: ignore[arg-type]
                origin="upload",
                query_reference="upload:aws/cost",
            )
        ],
        available_source_types=["cost", "usage"],
        data_origin="upload",
        findings=[
            Finding(
                finding_id="AWS-F01",
                provider="aws",
                category="unexplained_increase",
                suspected_root_cause="Cost rose; the mechanism is unconfirmed.",
                confidence=0.4,
                is_uncertain=True,
            )
        ],
    )

    revived = WorkerResponse.model_validate(response.model_dump(mode="json"))
    assert revived.sources[0].origin == "upload"
    assert revived.sources[0].is_fixture is False
    assert revived.available_source_types == ["cost", "usage"]
    assert revived.data_origin == "upload"


def test_the_deprecated_flag_still_drives_origin_for_old_payloads() -> None:
    """``fixtures/*/manifest.json`` carries ``is_fixture`` literally."""

    from_flag = Provenance.model_validate(
        {
            "provider": "gcp",
            "source": "billing-export-bigquery",
            "observed_at": "2026-07-19T23:59:59Z",
            "retrieved_at": "2026-07-20T09:00:00Z",
            "data_through": "2026-07-19T23:59:59Z",
            "is_fixture": True,
        }
    )
    assert from_flag.origin == "fixture"

    live = Provenance.model_validate(
        {
            "provider": "gcp",
            "source": "billing-export-bigquery",
            "observed_at": "2026-07-19T23:59:59Z",
            "retrieved_at": "2026-07-20T09:00:00Z",
            "data_through": "2026-07-19T23:59:59Z",
            "is_fixture": False,
        }
    )
    assert live.origin == "live"
