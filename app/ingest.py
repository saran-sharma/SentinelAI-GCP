"""Pub/Sub envelope decoding and multi-producer event normalisation."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

from app.models import EventSource, NormalizedEvent


class IngestError(ValueError):
    """Malformed input. Always a 4xx — retrying will not help, and letting
    Pub/Sub redeliver a poison message forever is how you burn a free tier."""


def decode_pubsub_push(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], str]:
    """Unwrap a Pub/Sub push request into (payload, attributes, message_id)."""
    message = body.get("message")
    if not isinstance(message, dict):
        raise IngestError("missing 'message' field in Pub/Sub push body")

    attributes = {str(k): str(v) for k, v in (message.get("attributes") or {}).items()}
    message_id = str(message.get("messageId", ""))
    encoded = message.get("data")

    if not encoded:
        # Attribute-only messages are legal (some alert channels use them).
        return {}, attributes, message_id

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise IngestError(f"undecodable Pub/Sub data: {exc}") from exc

    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        # Plain-text log lines still carry signal; keep them.
        payload = {"textPayload": decoded}

    if not isinstance(payload, dict):
        payload = {"textPayload": str(payload)}

    return payload, attributes, message_id


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def _extract_log_message(payload: dict[str, Any]) -> str:
    if text := payload.get("textPayload"):
        return str(text)

    json_payload = payload.get("jsonPayload") or {}
    if isinstance(json_payload, dict):
        for key in ("message", "msg", "error", "exception", "event"):
            if value := json_payload.get(key):
                return str(value)
        if json_payload:
            return json.dumps(json_payload, default=str)

    proto = payload.get("protoPayload") or {}
    if isinstance(proto, dict):
        status = proto.get("status") or {}
        if message := status.get("message"):
            return f"{proto.get('methodName', 'api')}: {message}"
        if method := proto.get("methodName"):
            return f"{method} failed"

    return json.dumps(payload, default=str)[:2000] or "empty log entry"


def _service_from_resource(resource: dict[str, Any]) -> str:
    labels = resource.get("labels") or {}
    for key in ("service_name", "function_name", "cluster_name", "instance_id", "job_id", "database_id"):
        if value := labels.get(key):
            return str(value)
    return str(resource.get("type", "unknown"))


def normalize(payload: dict[str, Any], attributes: dict[str, str]) -> NormalizedEvent:
    """Collapse a log entry, monitoring alert or budget notification into one shape."""
    # --- Cloud Monitoring alert (has 'incident') --------------------------
    if isinstance(payload.get("incident"), dict):
        incident = payload["incident"]
        resource = incident.get("resource") or {}
        return NormalizedEvent(
            source=EventSource.MONITORING_ALERT,
            service=str(
                (resource.get("labels") or {}).get("service_name") or incident.get("resource_display_name") or "unknown"
            ),
            resource_type=str(incident.get("resource_type_display_name") or resource.get("type") or "unknown"),
            raw_severity=str(incident.get("severity") or "ERROR").upper(),
            message=str(
                incident.get("summary") or incident.get("documentation", {}).get("content") or "monitoring alert fired"
            ),
            labels={
                "policy": str(incident.get("policy_name", "")),
                "condition": str(incident.get("condition_name", "")),
                "state": str(incident.get("state", "")),
            },
            occurred_at=_parse_ts(incident.get("started_at")),
            payload=payload,
        )

    # --- Billing budget notification (has 'budgetDisplayName') ------------
    if "budgetDisplayName" in payload:
        spend = float(payload.get("costAmount") or 0)
        budget = float(payload.get("budgetAmount") or 0) or 1.0
        pct = spend / budget * 100
        return NormalizedEvent(
            source=EventSource.BUDGET_ALERT,
            service="billing",
            resource_type="billing_account",
            raw_severity="WARNING" if pct < 100 else "ERROR",
            message=(
                f"Budget '{payload.get('budgetDisplayName')}' at {pct:.0f}% "
                f"({spend:.2f}/{budget:.2f} {payload.get('currencyCode', 'USD')})"
            ),
            labels={"budget": str(payload.get("budgetDisplayName", "")), "threshold_pct": f"{pct:.0f}"},
            payload=payload,
        )

    # --- Cloud Logging sink entry ----------------------------------------
    resource = payload.get("resource") or {}
    labels = {str(k): str(v) for k, v in (payload.get("labels") or {}).items()}
    if resource_labels := resource.get("labels"):
        labels.update({f"resource.{k}": str(v) for k, v in resource_labels.items()})

    return NormalizedEvent(
        source=EventSource.LOG_SINK,
        service=_service_from_resource(resource),
        resource_type=str(resource.get("type", "unknown")),
        raw_severity=str(payload.get("severity") or attributes.get("severity") or "ERROR").upper(),
        message=_extract_log_message(payload),
        labels=labels,
        occurred_at=_parse_ts(payload.get("timestamp") or payload.get("receiveTimestamp")),
        trace=payload.get("trace"),
        payload=payload,
    )
