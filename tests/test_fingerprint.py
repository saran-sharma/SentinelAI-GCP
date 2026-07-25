from __future__ import annotations

import pytest

from app.fingerprint import compute_fingerprint, normalise_message
from app.models import EventSource, NormalizedEvent


def make_event(message: str, service: str = "checkout-api") -> NormalizedEvent:
    return NormalizedEvent(
        source=EventSource.LOG_SINK,
        service=service,
        resource_type="cloud_run_revision",
        message=message,
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Same failure, different request id
        (
            "request 3f8a9d21-4c11-4a9e-b0c1-9f2e7a6b5c40 failed",
            "request 91bb2c04-7d33-4f21-a1e2-0c4d8e9f1a22 failed",
        ),
        # Same failure, different pod
        (
            "pod checkout-api-7d4f9b8c6d-x2klm OOMKilled",
            "pod checkout-api-5a1c8e2f9b-q7wzp OOMKilled",
        ),
        # Same failure, different upstream address and duration
        (
            "upstream 10.24.1.7:8080 timed out after 30000ms",
            "upstream 10.24.9.201:8080 timed out after 12500ms",
        ),
        # Same failure, different timestamp
        (
            "2026-07-25T04:11:09Z lock wait exceeded",
            "2026-07-24T22:03:44.512Z lock wait exceeded",
        ),
    ],
)
def test_variance_collapses_to_one_fingerprint(left: str, right: str) -> None:
    assert compute_fingerprint(make_event(left)) == compute_fingerprint(make_event(right))


def test_distinct_failure_modes_stay_distinct() -> None:
    a = compute_fingerprint(make_event("connection to db-primary timed out"))
    b = compute_fingerprint(make_event("permission denied on bucket assets-prod"))
    assert a != b


def test_same_error_in_different_services_is_a_different_incident() -> None:
    a = compute_fingerprint(make_event("connection refused", service="checkout-api"))
    b = compute_fingerprint(make_event("connection refused", service="ledger-api"))
    assert a != b


def test_fingerprint_is_stable_and_short() -> None:
    fp = compute_fingerprint(make_event("some failure"))
    assert len(fp) == 16
    assert fp == compute_fingerprint(make_event("some failure"))


def test_normalise_strips_high_cardinality_tokens() -> None:
    out = normalise_message("GET https://api.example.com/v1/x failed for user a@b.com in 4200ms")
    assert "<url>" in out
    assert "<email>" in out
    assert "<qty>" in out
