"""The upload endpoints, including every way they say no.

The contract the browser codes against: create, stream one file per request, seal,
read, delete. There is no multipart anywhere, and no filename is ever accepted.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from cloudcause_api import API_PREFIX, app, main
from cloudcause_contracts import DatasetSummary, Settings, utcnow
from cloudcause_datasets import build_dataset_store
from conftest import (
    aws_audit_json,
    aws_cur_csv,
    aws_cur_json,
    aws_inventory_json,
    aws_metrics_json,
    azure_cost_management_json,
    gcp_billing_export_csv,
    gzipped,
)
from fastapi.testclient import TestClient

DATASETS = f"{API_PREFIX}/datasets"
JSON = {"content-type": "application/json"}
CSV = {"content-type": "text/csv"}
GZIP = {"content-type": "application/gzip"}


@pytest.fixture(autouse=True)
def restore_gateway() -> Iterator[None]:
    """Every test that reconfigures the gateway must hand it back unchanged."""

    yield
    main.configure(Settings.from_env({}))


def create(client: TestClient) -> str:
    response = client.post(DATASETS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["max_bytes_per_file"] > 0
    assert payload["accepted_content_types"] == [
        "application/json",
        "text/csv",
        "application/gzip",
    ]
    assert "multipart/form-data" not in payload["accepted_content_types"]
    return payload["dataset_id"]


def put(client: TestClient, dataset_id: str, provider: str, kind: str, body: bytes, headers=JSON):
    return client.put(
        f"{DATASETS}/{dataset_id}/sources/{provider}/{kind}", content=body, headers=headers
    )


def seal_aws_cost(client: TestClient) -> str:
    dataset_id = create(client)
    assert put(client, dataset_id, "aws", "cost", aws_cur_json()).status_code == 200
    assert client.post(f"{DATASETS}/{dataset_id}/seal").status_code == 200
    return dataset_id


# ------------------------------------------------------------------ happy paths


def test_create_stream_seal_get_and_delete() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)

        report = put(client, dataset_id, "aws", "cost", aws_cur_json()).json()
        assert report["dataset_id"] == dataset_id
        assert report["sealed"] is False
        assert report["source"]["detected_format"] == "aws-cur-csv"
        assert report["source"]["raw_rows"] == 672
        assert report["source"]["stored_records"] == 28
        assert report["source"]["currency"] == "USD"
        assert report["source"]["period_start"] == "2026-07-06"
        assert report["source"]["period_end"] == "2026-07-19"

        summary = DatasetSummary.model_validate(client.get(f"{DATASETS}/{dataset_id}").json())
        assert summary.sealed is False
        assert summary.providers == ["aws"]
        assert summary.available_source_types == {"aws": ["cost", "usage"]}

        sealed = DatasetSummary.model_validate(
            client.post(f"{DATASETS}/{dataset_id}/seal").json()
        )
        assert sealed.sealed is True
        assert sealed.sealed_at is not None
        assert sealed.suggested_request is not None
        assert sealed.suggested_request.dataset_id == dataset_id
        assert sealed.suggested_request.start_date.isoformat() == "2026-07-13"
        assert sealed.suggested_request.end_date.isoformat() == "2026-07-19"
        assert sealed.suggested_request.comparison_start_date.isoformat() == "2026-07-06"

        assert client.delete(f"{DATASETS}/{dataset_id}").status_code == 204
        assert client.get(f"{DATASETS}/{dataset_id}").status_code == 404
        assert client.delete(f"{DATASETS}/{dataset_id}").status_code == 404


@pytest.mark.parametrize(
    ("provider", "build", "headers"),
    [
        ("aws", aws_cur_json, JSON),
        ("aws", aws_cur_csv, CSV),
        ("azure", azure_cost_management_json, JSON),
        ("gcp", gcp_billing_export_csv, CSV),
    ],
    ids=["aws-json", "aws-csv", "azure-json", "gcp-csv"],
)
def test_every_provider_and_encoding_is_accepted(provider: str, build, headers) -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(client, dataset_id, provider, "cost", build(), headers)
        assert response.status_code == 200, response.text
        assert response.json()["source"]["provider"] == provider


def test_all_four_evidence_kinds_land_beside_the_cost_export() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        put(client, dataset_id, "aws", "cost", aws_cur_json())
        for kind, payload in (
            ("metrics", aws_metrics_json()),
            ("audit", aws_audit_json()),
            ("inventory", aws_inventory_json()),
        ):
            assert put(client, dataset_id, "aws", kind, payload).status_code == 200

        summary = DatasetSummary.model_validate(
            client.post(f"{DATASETS}/{dataset_id}/seal").json()
        )
        assert sorted(summary.available_source_types["aws"]) == [
            "audit",
            "cost",
            "inventory",
            "metric",
            "usage",
        ]


def test_a_gzip_body_is_accepted() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(client, dataset_id, "aws", "cost", gzipped(aws_cur_json()), GZIP)
        assert response.status_code == 200, response.text
        assert response.json()["source"]["compressed"] is True


def test_health_reports_the_dataset_store_separately_from_history() -> None:
    with TestClient(app) as client:
        payload = client.get("/health").json()
    assert payload["datasets"]["backend"] == "memory"
    assert payload["datasets"]["uploads_enabled"] is True
    assert payload["history"]["backend"] == "memory"


# --------------------------------------------------------------- refusal paths


def test_a_multipart_or_unknown_content_type_is_refused() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(
            client,
            dataset_id,
            "aws",
            "cost",
            aws_cur_json(),
            {"content-type": "multipart/form-data; boundary=x"},
        )
    assert response.status_code == 422
    assert "not accepted" in response.json()["detail"]


def test_an_oversize_body_answers_413_not_422() -> None:
    main.configure(Settings.from_env({}).with_overrides(upload_max_bytes=256))
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(client, dataset_id, "aws", "cost", aws_cur_json())
    assert response.status_code == 413
    assert "byte limit" in response.json()["detail"]


def test_too_many_rows_answers_413() -> None:
    main.configure(Settings.from_env({}).with_overrides(upload_max_rows=10))
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(client, dataset_id, "aws", "cost", aws_cur_json())
    assert response.status_code == 413
    assert "rows" in response.json()["detail"]


def test_the_wrong_provider_slot_answers_422_naming_both_formats() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(client, dataset_id, "aws", "cost", azure_cost_management_json())
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "azure-cost-management-json" in detail
    assert "/sources/azure/cost" in detail


def test_a_gzip_bomb_is_refused() -> None:
    main.configure(
        Settings.from_env({}).with_overrides(upload_max_decompressed_bytes=64 * 1024)
    )
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(client, dataset_id, "aws", "cost", gzipped(b"0" * (8 * 1024 * 1024)), GZIP)
    assert response.status_code == 413


def test_a_mixed_currency_file_answers_422_naming_both() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(client, dataset_id, "aws", "cost", aws_cur_json(extra_currency="EUR"))
    assert response.status_code == 422
    assert "EUR" in response.json()["detail"]


def test_a_credential_shaped_field_is_refused() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        response = put(
            client,
            dataset_id,
            "aws",
            "cost",
            b'{"rows": [{"line_item_usage_start_date": "2026-07-06", "client_secret": "x"}]}',
        )
    assert response.status_code == 422
    assert "credential" in response.json()["detail"]


def test_an_empty_body_is_refused() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        assert put(client, dataset_id, "aws", "cost", b"").status_code == 422


def test_an_unknown_dataset_provider_or_kind_is_a_404() -> None:
    with TestClient(app) as client:
        assert put(client, "not-a-dataset", "aws", "cost", aws_cur_json()).status_code == 404
        dataset_id = create(client)
        assert put(client, dataset_id, "ibm", "cost", aws_cur_json()).status_code == 404
        assert put(client, dataset_id, "aws", "tarot", aws_cur_json()).status_code == 404
        assert client.get(f"{DATASETS}/not-a-dataset").status_code == 404


def test_sealing_without_a_cost_export_is_refused() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        assert put(client, dataset_id, "aws", "metrics", aws_metrics_json()).status_code == 200
        response = client.post(f"{DATASETS}/{dataset_id}/seal")
    assert response.status_code == 422
    assert "cost export" in response.json()["detail"]


def test_a_sealed_dataset_refuses_another_source() -> None:
    with TestClient(app) as client:
        dataset_id = seal_aws_cost(client)
        response = put(client, dataset_id, "aws", "metrics", aws_metrics_json())
    assert response.status_code == 409
    assert "immutable" in response.json()["detail"]


def test_too_many_sources_is_refused() -> None:
    main.configure(Settings.from_env({}).with_overrides(upload_max_sources=1))
    with TestClient(app) as client:
        dataset_id = create(client)
        assert put(client, dataset_id, "aws", "cost", aws_cur_json()).status_code == 200
        response = put(client, dataset_id, "aws", "metrics", aws_metrics_json())
    assert response.status_code == 422
    assert "at most 1 sources" in response.json()["detail"]


def test_the_feature_flag_turns_every_endpoint_into_a_503() -> None:
    main.configure(Settings.from_env({}).with_overrides(uploads_enabled=False))
    with TestClient(app) as client:
        assert client.post(DATASETS).status_code == 503
        assert put(client, "any", "aws", "cost", aws_cur_json()).status_code == 503
        assert client.get(f"{DATASETS}/any").status_code == 503
        assert client.delete(f"{DATASETS}/any").status_code == 503
        health = client.get("/health").json()
    assert health["datasets"]["enabled"] is False
    assert "CLOUDCAUSE_UPLOADS_ENABLED" in health["datasets"]["reason"]


def test_the_http_topology_without_a_database_answers_503_naming_the_fix() -> None:
    main.configure(
        Settings.from_env({}).with_overrides(
            orchestrator_mode="http", worker_mode="http", database_url=""
        )
    )
    with TestClient(app) as client:
        response = client.post(DATASETS)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "CLOUDCAUSE_DATABASE_URL" in detail
    assert "orchestrator" in detail


# --------------------------------------------------- investigating a dataset


def test_an_investigation_can_run_from_a_sealed_dataset() -> None:
    with TestClient(app) as client:
        dataset_id = seal_aws_cost(client)
        created = client.post(
            f"{API_PREFIX}/investigations?wait=true",
            json={
                "providers": ["aws"],
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "comparison_start_date": "2026-07-06",
                "comparison_end_date": "2026-07-12",
                "question": "Why did our AWS spending increase?",
                "scenario_id": "",
                "dataset_id": dataset_id,
            },
        )
        assert created.status_code == 200, created.text
        investigation_id = created.json()["investigation_id"]
        report = client.get(
            f"{API_PREFIX}/investigations/{investigation_id}/report"
        ).json()

    assert report["data_origin"] == "upload"
    assert report["request"]["dataset_id"] == dataset_id
    assert all(source["origin"] == "upload" for source in report["sources"])


def test_an_unsealed_dataset_cannot_start_an_investigation() -> None:
    with TestClient(app) as client:
        dataset_id = create(client)
        put(client, dataset_id, "aws", "cost", aws_cur_json())
        response = client.post(
            f"{API_PREFIX}/investigations",
            json={
                "providers": ["aws"],
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "comparison_start_date": "2026-07-06",
                "comparison_end_date": "2026-07-12",
                "dataset_id": dataset_id,
            },
        )
    assert response.status_code == 409
    assert "seal it" in response.json()["detail"]


def test_a_deleted_dataset_answers_409_dataset_expired_not_404() -> None:
    """History keeps the id forever; the dataset lives two hours."""

    with TestClient(app) as client:
        dataset_id = seal_aws_cost(client)
        client.delete(f"{DATASETS}/{dataset_id}")
        response = client.post(
            f"{API_PREFIX}/investigations",
            json={
                "providers": ["aws"],
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "comparison_start_date": "2026-07-06",
                "comparison_end_date": "2026-07-12",
                "dataset_id": dataset_id,
            },
        )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith("dataset_expired:")
    assert "Upload the export again" in detail


def test_an_expired_dataset_answers_409_dataset_expired() -> None:
    with TestClient(app) as client:
        dataset_id = seal_aws_cost(client)
        store = build_dataset_store(Settings.from_env({}))
        stale = store.get(dataset_id)
        stale.expires_at = utcnow() - timedelta(seconds=1)
        store.put(stale)

        response = client.post(
            f"{API_PREFIX}/investigations",
            json={
                "providers": ["aws"],
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "comparison_start_date": "2026-07-06",
                "comparison_end_date": "2026-07-12",
                "dataset_id": dataset_id,
            },
        )
    assert response.status_code == 409
    assert response.json()["detail"].startswith("dataset_expired:")


def test_re_running_a_stored_investigation_whose_dataset_is_gone_explains_itself() -> None:
    with TestClient(app) as client:
        dataset_id = seal_aws_cost(client)
        payload = {
            "providers": ["aws"],
            "start_date": "2026-07-13",
            "end_date": "2026-07-19",
            "comparison_start_date": "2026-07-06",
            "comparison_end_date": "2026-07-12",
            "dataset_id": dataset_id,
        }
        first = client.post(f"{API_PREFIX}/investigations?wait=true", json=payload)
        assert first.status_code == 200
        stored = client.get(
            f"{API_PREFIX}/investigations/{first.json()['investigation_id']}"
        ).json()
        assert stored["request"]["dataset_id"] == dataset_id

        client.delete(f"{DATASETS}/{dataset_id}")
        again = client.post(f"{API_PREFIX}/investigations", json=payload)

        assert again.status_code == 409
        assert "dataset_expired" in again.json()["detail"]
        assert (
            client.get(
                f"{API_PREFIX}/investigations/{first.json()['investigation_id']}/report"
            ).status_code
            == 200
        ), "the report the dataset produced outlives it"


def test_the_demo_and_scenario_paths_are_unchanged_by_any_of_this() -> None:
    with TestClient(app) as client:
        created = client.post(
            f"{API_PREFIX}/investigations?wait=true",
            json={
                "providers": ["aws", "azure", "gcp"],
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "comparison_start_date": "2026-07-06",
                "comparison_end_date": "2026-07-12",
                "scenario_id": "default",
            },
        ).json()
        report = client.get(
            f"{API_PREFIX}/investigations/{created['investigation_id']}/report"
        ).json()
    assert report["data_origin"] == "fixture"
    assert report["findings"]
    assert all(source["origin"] == "fixture" for source in report["sources"])
