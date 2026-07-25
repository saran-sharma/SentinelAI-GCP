from __future__ import annotations

import base64
import json

import pytest

from app.ingest import IngestError, decode_pubsub_push, normalize
from app.models import EventSource


def envelope(payload: dict | str, attributes: dict | None = None) -> dict:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "message": {
            "data": base64.b64encode(raw.encode()).decode(),
            "attributes": attributes or {},
            "messageId": "12345",
        },
        "subscription": "projects/p/subscriptions/s",
    }


def test_decode_roundtrip() -> None:
    payload, attributes, message_id = decode_pubsub_push(envelope({"severity": "ERROR"}, {"k": "v"}))
    assert payload == {"severity": "ERROR"}
    assert attributes == {"k": "v"}
    assert message_id == "12345"


def test_decode_rejects_missing_message() -> None:
    with pytest.raises(IngestError):
        decode_pubsub_push({"subscription": "x"})


def test_decode_wraps_plain_text_payloads() -> None:
    payload, _, _ = decode_pubsub_push(envelope("boom: not json"))
    assert payload == {"textPayload": "boom: not json"}


def test_decode_allows_attribute_only_message() -> None:
    payload, attributes, _ = decode_pubsub_push({"message": {"attributes": {"severity": "ERROR"}}})
    assert payload == {}
    assert attributes["severity"] == "ERROR"


def test_normalize_cloud_run_log_entry() -> None:
    entry = {
        "severity": "ERROR",
        "textPayload": "psycopg2.OperationalError: connection timed out",
        "resource": {"type": "cloud_run_revision", "labels": {"service_name": "checkout-api"}},
        "timestamp": "2026-07-25T04:11:09Z",
        "trace": "projects/p/traces/abc",
    }
    event = normalize(entry, {})
    assert event.source is EventSource.LOG_SINK
    assert event.service == "checkout-api"
    assert event.resource_type == "cloud_run_revision"
    assert "OperationalError" in event.message
    assert event.occurred_at.year == 2026


def test_normalize_json_payload_prefers_message_field() -> None:
    event = normalize(
        {"severity": "CRITICAL", "jsonPayload": {"message": "pool exhausted", "pool": "primary"}, "resource": {}},
        {},
    )
    assert event.message == "pool exhausted"
    assert event.raw_severity == "CRITICAL"


def test_normalize_audit_log_proto_payload() -> None:
    event = normalize(
        {
            "protoPayload": {
                "methodName": "storage.objects.get",
                "status": {"code": 7, "message": "does not have storage.objects.get access"},
            },
            "resource": {"type": "gcs_bucket", "labels": {"bucket_name": "assets-prod"}},
        },
        {},
    )
    assert "storage.objects.get" in event.message


def test_normalize_monitoring_alert() -> None:
    event = normalize(
        {
            "incident": {
                "summary": "Cloud Run 5xx rate above 5% for checkout-api",
                "policy_name": "checkout-error-rate",
                "condition_name": "5xx ratio",
                "state": "OPEN",
                "started_at": "2026-07-25T04:00:00Z",
                "resource": {"type": "cloud_run_revision", "labels": {"service_name": "checkout-api"}},
                "resource_type_display_name": "Cloud Run Revision",
            }
        },
        {},
    )
    assert event.source is EventSource.MONITORING_ALERT
    assert event.service == "checkout-api"
    assert event.labels["policy"] == "checkout-error-rate"


def test_normalize_budget_alert_computes_percentage() -> None:
    event = normalize(
        {
            "budgetDisplayName": "sentinelai-monthly",
            "costAmount": 9.0,
            "budgetAmount": 10.0,
            "currencyCode": "USD",
        },
        {},
    )
    assert event.source is EventSource.BUDGET_ALERT
    assert event.labels["threshold_pct"] == "90"
    assert "90%" in event.message


def test_normalize_never_raises_on_unknown_shape() -> None:
    event = normalize({"something": "unexpected"}, {})
    assert event.message
    assert event.service == "unknown"
