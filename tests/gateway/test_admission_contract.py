"""API contract tests for Layer 1 gateway admission control (live investigations)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cloudcause_api import API_PREFIX, app, main
from cloudcause_contracts import Settings
from fastapi.testclient import TestClient


def _payload(**overrides: object) -> dict:
    payload = {
        "providers": ["aws"],
        "start_date": "2026-07-13",
        "end_date": "2026-07-19",
        "comparison_start_date": "2026-07-06",
        "comparison_end_date": "2026-07-12",
        "question": "Why did our cloud spending increase last week?",
        "agent_mode": "live",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def tight_admission() -> Iterator[TestClient]:
    """One live start allowed, no burst, generous global cap."""

    original = main.settings
    main.configure(
        Settings.from_env({}).with_overrides(
            live_investigations_per_hour=1,
            live_investigation_burst=0,
            global_live_starts_per_minute=100,
        )
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        main.configure(original)


def test_first_live_request_succeeds_the_next_is_429(tight_admission: TestClient) -> None:
    first = tight_admission.post(f"{API_PREFIX}/investigations", json=_payload())
    assert first.status_code == 200, first.text

    second = tight_admission.post(f"{API_PREFIX}/investigations", json=_payload())
    assert second.status_code == 429
    assert "retry-after" in {key.lower() for key in second.headers}
    body = second.json()
    assert body["error"] == "rate_limit_exceeded"
    assert body["scope"] == "client_live_investigation"
    assert body["retryable"] is True
    assert isinstance(body["retry_after_seconds"], (int, float))


def test_rejected_request_creates_no_history_record(tight_admission: TestClient) -> None:
    tight_admission.post(f"{API_PREFIX}/investigations", json=_payload())
    before = tight_admission.get(f"{API_PREFIX}/investigations").json()

    rejected = tight_admission.post(f"{API_PREFIX}/investigations", json=_payload())
    assert rejected.status_code == 429

    after = tight_admission.get(f"{API_PREFIX}/investigations").json()
    assert len(after) == len(before)


def test_stub_investigations_never_consume_live_admission_capacity(
    tight_admission: TestClient,
) -> None:
    tight_admission.post(f"{API_PREFIX}/investigations", json=_payload())  # exhausts the live bucket

    for _ in range(3):
        response = tight_admission.post(
            f"{API_PREFIX}/investigations?wait=true", json=_payload(agent_mode="stub")
        )
        assert response.status_code == 200, response.text


def test_disabled_admission_never_returns_429() -> None:
    original = main.settings
    main.configure(Settings.from_env({}).with_overrides(live_rate_limit_enabled=False))
    try:
        with TestClient(app) as client:
            for _ in range(5):
                response = client.post(f"{API_PREFIX}/investigations", json=_payload())
                assert response.status_code == 200
    finally:
        main.configure(original)


def test_peer_ip_headers_are_not_trusted_by_default() -> None:
    """A client cannot dodge its own bucket by forging X-Forwarded-For."""

    original = main.settings
    main.configure(
        Settings.from_env({}).with_overrides(
            live_investigations_per_hour=1,
            live_investigation_burst=0,
            global_live_starts_per_minute=100,
            trust_proxy_headers=False,
        )
    )
    try:
        with TestClient(app) as client:
            first = client.post(
                f"{API_PREFIX}/investigations",
                json=_payload(),
                headers={"X-Forwarded-For": "198.51.100.1"},
            )
            assert first.status_code == 200

            second = client.post(
                f"{API_PREFIX}/investigations",
                json=_payload(),
                headers={"X-Forwarded-For": "198.51.100.2"},  # different spoofed value, same real peer
            )
            assert second.status_code == 429
    finally:
        main.configure(original)


def test_trusted_proxy_mode_extracts_the_forwarded_client() -> None:
    original = main.settings
    main.configure(
        Settings.from_env({}).with_overrides(
            live_investigations_per_hour=1,
            live_investigation_burst=0,
            global_live_starts_per_minute=100,
            trust_proxy_headers=True,
        )
    )
    try:
        with TestClient(app) as client:
            first = client.post(
                f"{API_PREFIX}/investigations",
                json=_payload(),
                headers={"X-Forwarded-For": "198.51.100.1"},
            )
            assert first.status_code == 200

            # A distinct forwarded client gets its own bucket when the header is trusted.
            other_client = client.post(
                f"{API_PREFIX}/investigations",
                json=_payload(),
                headers={"X-Forwarded-For": "198.51.100.9"},
            )
            assert other_client.status_code == 200

            same_client_again = client.post(
                f"{API_PREFIX}/investigations",
                json=_payload(),
                headers={"X-Forwarded-For": "198.51.100.1"},
            )
            assert same_client_again.status_code == 429
    finally:
        main.configure(original)
