"""The dataset store, which must never quietly degrade.

Investigation history degrades to memory on purpose: losing history must not fail
an investigation. A dataset is the opposite. A dataset only the gateway can see
produces a mid-run "unknown dataset", or worse a fall-through to the demo fixtures
under an uploaded label, so every failure here is an error.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cloudcause_contracts import Settings, utcnow
from cloudcause_datasets import (
    Dataset,
    DatasetExpiredError,
    DatasetNotSealedError,
    DatasetSealedError,
    DatasetStoreFullError,
    DatasetTooLargeError,
    MemoryDatasetStore,
    SqlDatasetStore,
    UnknownDatasetError,
    UploadsUnavailableError,
    add_source,
    build_dataset_store,
    dataset_store_reason,
    parse_cost_source,
    seal_dataset,
    try_build_dataset_store,
    uploads_available,
)
from conftest import aws_cur_json


def sql_settings(database_url: str, **overrides) -> Settings:
    """The http topology, which is the one that needs a shared store."""

    base = Settings.from_env({}).with_overrides(
        orchestrator_mode="http",
        worker_mode="http",
        database_url=database_url,
    )
    return base.with_overrides(**overrides) if overrides else base


def fill(store, settings: Settings) -> str:
    dataset = store.create()
    parsed = parse_cost_source("aws", aws_cur_json(), settings)
    add_source(store, dataset.dataset_id, "aws", "cost", parsed, 1024, settings)
    return dataset.dataset_id




def test_the_in_process_topology_gets_the_memory_store(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    assert isinstance(store, MemoryDatasetStore)
    assert uploads_available(upload_settings) is True
    assert dataset_store_reason(upload_settings) is None


def test_the_memory_store_is_shared_across_callers(upload_settings: Settings) -> None:
    """Every process resolves a dataset from its id independently, so one dict."""

    first = build_dataset_store(upload_settings)
    dataset_id = fill(first, upload_settings)
    second = build_dataset_store(upload_settings)
    assert second.get(dataset_id).dataset_id == dataset_id


def test_the_http_topology_with_a_database_gets_the_sql_store(database_url: str) -> None:
    settings = sql_settings(database_url)
    store = build_dataset_store(settings)
    assert isinstance(store, SqlDatasetStore)
    assert store.applied_migrations == ["0001_datasets"]


def test_the_http_topology_without_a_database_refuses_uploads() -> None:
    settings = Settings.from_env({}).with_overrides(
        orchestrator_mode="http", worker_mode="http", database_url=""
    )
    reason = dataset_store_reason(settings)
    assert reason is not None
    assert "CLOUDCAUSE_DATABASE_URL" in reason
    assert "aws-worker" in reason and "azure-worker" in reason, (
        "the message must name every service that needs the DSN, or nobody guesses it"
    )
    with pytest.raises(UploadsUnavailableError):
        build_dataset_store(settings)
    store, message = try_build_dataset_store(settings)
    assert store is None and message == reason


def test_the_feature_flag_refuses_before_the_topology_is_consulted(
    upload_settings: Settings,
) -> None:
    off = upload_settings.with_overrides(uploads_enabled=False)
    assert dataset_store_reason(off) is not None
    assert "CLOUDCAUSE_UPLOADS_ENABLED" in dataset_store_reason(off)


def test_a_sql_store_failure_raises_rather_than_degrading() -> None:
    unreachable = Settings.from_env({}).with_overrides(
        orchestrator_mode="http",
        worker_mode="http",
        database_url="postgresql://cloudcause:nope@127.0.0.1:1/cloudcause",
        history_connect_timeout_seconds=1.0,
    )
    with pytest.raises(UploadsUnavailableError) as error:
        build_dataset_store(unreachable)
    assert "stay refused" in str(error.value), (
        "unlike history, a dataset store must not fall back to memory"
    )




def test_an_unsealed_dataset_cannot_start_an_investigation(
    upload_settings: Settings,
) -> None:
    store = build_dataset_store(upload_settings)
    dataset_id = fill(store, upload_settings)
    with pytest.raises(DatasetNotSealedError):
        store.get_for_investigation(dataset_id)

    seal_dataset(store, dataset_id)
    assert store.get_for_investigation(dataset_id).sealed is True


def test_a_sealed_dataset_is_immutable(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    dataset_id = fill(store, upload_settings)
    seal_dataset(store, dataset_id)
    parsed = parse_cost_source("aws", aws_cur_json(), upload_settings)
    with pytest.raises(DatasetSealedError):
        add_source(store, dataset_id, "aws", "cost", parsed, 1024, upload_settings)


def test_delete_is_honoured_immediately(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    dataset_id = fill(store, upload_settings)
    assert store.delete(dataset_id) is True
    assert store.delete(dataset_id) is False
    with pytest.raises(UnknownDatasetError):
        store.get(dataset_id)


def test_re_uploading_one_source_replaces_it_instead_of_double_counting(
    upload_settings: Settings,
) -> None:
    store = build_dataset_store(upload_settings)
    dataset_id = fill(store, upload_settings)
    before = store.get(dataset_id).record_count()
    parsed = parse_cost_source("aws", aws_cur_json(), upload_settings)
    add_source(store, dataset_id, "aws", "cost", parsed, 1024, upload_settings)
    after = store.get(dataset_id)
    assert after.record_count() == before
    assert len(after.sources) == 1




def test_an_expired_dataset_is_evicted_on_read(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    dataset_id = fill(store, upload_settings)
    expired = store.get(dataset_id)
    expired.expires_at = utcnow() - timedelta(seconds=1)
    store.put(expired)

    with pytest.raises(DatasetExpiredError) as error:
        store.get(dataset_id)
    assert dataset_id in str(error.value)
    with pytest.raises(UnknownDatasetError):
        store.get(dataset_id)


def test_ttl_eviction_sweeps_the_whole_store(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    stale = fill(store, upload_settings)
    fresh = fill(store, upload_settings)
    expired = store.get(stale)
    expired.expires_at = utcnow() - timedelta(seconds=1)
    store.put(expired)

    assert store.evict_expired() == [stale]
    assert store.count() == 1
    assert store.get(fresh).dataset_id == fresh


def test_the_ttl_is_absolute_and_not_extended_by_use(upload_settings: Settings) -> None:
    store = build_dataset_store(upload_settings)
    dataset_id = fill(store, upload_settings)
    first = store.get(dataset_id).expires_at
    seal_dataset(store, dataset_id)
    assert store.get(dataset_id).expires_at == first


def test_the_per_dataset_record_cap_is_refused_not_grown(
    upload_settings: Settings,
) -> None:
    tiny = upload_settings.with_overrides(dataset_max_records=5)
    store = build_dataset_store(tiny)
    dataset = store.create()
    parsed = parse_cost_source("aws", aws_cur_json(), tiny)
    with pytest.raises(DatasetTooLargeError) as error:
        add_source(store, dataset.dataset_id, "aws", "cost", parsed, 1024, tiny)
    assert "Every reader loads the whole set" in str(error.value)


def test_the_store_wide_byte_cap_is_refused(upload_settings: Settings) -> None:
    generous_enough_for_one = upload_settings.with_overrides(dataset_store_max_bytes=25_000)
    store = build_dataset_store(generous_enough_for_one)
    first = store.create()
    parsed = parse_cost_source("aws", aws_cur_json(), generous_enough_for_one)
    add_source(
        store, first.dataset_id, "aws", "cost", parsed, 1024, generous_enough_for_one
    )

    second = store.create()
    with pytest.raises(DatasetStoreFullError) as error:
        add_source(
            store, second.dataset_id, "aws", "cost", parsed, 1024, generous_enough_for_one
        )
    assert "byte" in str(error.value)




def test_a_dataset_survives_a_gateway_restart_with_sql_storage(database_url: str) -> None:
    settings = sql_settings(database_url)
    store = build_dataset_store(settings)
    dataset_id = fill(store, settings)
    seal_dataset(store, dataset_id)
    records = store.get(dataset_id).record_count()
    store.close()

    restarted = build_dataset_store(settings)
    assert isinstance(restarted, SqlDatasetStore)
    reloaded = restarted.get_for_investigation(dataset_id)
    assert reloaded.sealed is True
    assert reloaded.record_count() == records
    assert reloaded.cost_records("aws"), "the normalized rows come back, not the raw file"
    restarted.close()


def test_sql_and_memory_agree_on_what_a_dataset_looks_like(database_url: str) -> None:
    memory_settings = Settings.from_env({})
    memory = MemoryDatasetStore(memory_settings)
    dataset_id = fill(memory, memory_settings)

    sql = build_dataset_store(sql_settings(database_url))
    stored = memory.get(dataset_id)
    sql.put(stored)
    assert sql.get(dataset_id).model_dump() == stored.model_dump()
    sql.close()


def test_a_dataset_round_trips_through_json_without_losing_provenance(
    upload_settings: Settings,
) -> None:
    store = build_dataset_store(upload_settings)
    dataset_id = fill(store, upload_settings)
    payload = store.get(dataset_id).model_dump_json()
    revived = Dataset.model_validate_json(payload)
    assert revived.sources[0].provenance.origin == "upload"
    assert revived.sources[0].provenance.is_fixture is False
