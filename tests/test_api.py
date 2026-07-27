"""API contract tests, including the Pub/Sub ack/nack semantics."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.jobs.digest import DigestJob, build_stats
from app.main import Container, app
from app.triage import TriageService
from tests.conftest import FakeAnalyzer, FakeMetrics, FakeNotifier, FakeRepository


@pytest.fixture
def client(settings):
    container = Container.__new__(Container)
    container.settings = settings
    container.repository = FakeRepository()
    container.analyzer = FakeAnalyzer()
    container.notifier = FakeNotifier()
    container.metrics = FakeMetrics()
    container.artifacts = type("NoopArtifacts", (), {"write_markdown": lambda self, p, c: None})()
    container.triage = TriageService(
        settings, container.repository, container.analyzer, container.notifier, container.metrics
    )
    container.digest = DigestJob(
        settings, container.repository, container.analyzer, container.artifacts, container.notifier
    )

    app.state.container = container
    with TestClient(app) as test_client:
        # TestClient runs lifespan, which would rebuild the real container.
        app.state.container = container
        yield test_client


def push_body(payload: dict) -> dict:
    return {
        "message": {
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
            "messageId": "1",
        }
    }


LOG_ENTRY = {
    "severity": "ERROR",
    "textPayload": "connection to db-primary timed out after 30000ms",
    "resource": {"type": "cloud_run_revision", "labels": {"service_name": "checkout-api"}},
}


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_pubsub_push_triages_a_log_entry(client):
    response = client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "created"
    assert body["ai_invoked"] is True
    assert len(body["fingerprint"]) == 16


def test_repeat_delivery_is_acked_without_a_second_ai_call(client):
    client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))
    response = client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))

    assert response.status_code == 200
    assert response.json()["action"] == "suppressed"
    assert app.state.container.analyzer.calls == 1


def test_malformed_envelope_is_acked_not_retried(client):
    """A poison message must be dropped, not redelivered forever."""
    response = client.post("/v1/events/pubsub", json={"not": "a pubsub push"})

    assert response.status_code == 200
    assert response.json()["status"] == "dropped"


def test_non_json_body_is_acked(client):
    response = client.post("/v1/events/pubsub", content=b"<<<garbage", headers={"content-type": "application/json"})
    assert response.status_code == 200


def test_transient_failure_nacks_so_pubsub_retries(client, monkeypatch):
    def boom(_event):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(app.state.container.triage, "handle", boom)

    response = client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))

    assert response.status_code == 503


def test_manual_analyze_accepts_plain_text_signal(client):
    response = client.post("/v1/analyze", json={"service": "ledger", "text": "disk full on /var"})

    assert response.status_code == 200
    assert response.json()["action"] == "created"


def test_manual_analyze_rejects_empty_signal(client):
    assert client.post("/v1/analyze", json={"service": "ledger", "text": "  "}).status_code == 400


def test_list_incidents_clamps_the_window(client):
    client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))

    response = client.get("/v1/incidents?hours=100000&limit=99999")

    assert response.status_code == 200
    assert response.json()["window_hours"] == 168


def test_digest_job_runs_over_stored_incidents(client):
    client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))

    response = client.post("/jobs/digest?window_hours=24")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert app.state.container.analyzer.digest_calls == 1


def test_digest_handles_an_empty_window(client):
    response = client.post("/jobs/digest")
    assert response.json()["status"] == "empty"


def test_auth_is_enforced_when_oidc_verification_is_on(client):
    app.state.container.settings.verify_oidc = True
    try:
        response = client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))
        assert response.status_code == 401
    finally:
        app.state.container.settings.verify_oidc = False


# --- caller pinning -------------------------------------------------------
#
# The machine endpoints must stay pinned to one service account each, while the
# operator endpoints must stay open to any IAM-authorised identity. Getting this
# backwards is what made `make smoke` and `make demo` impossible to run.


def test_machine_endpoints_are_pinned_to_their_service_account(client, monkeypatch):
    seen: dict[str, object] = {}

    def capture(request, settings, *, allowed_callers=None):
        seen[request.url.path] = allowed_callers
        return "caller@example.com"

    monkeypatch.setattr("app.main.verify_oidc_token", capture)
    settings = app.state.container.settings
    settings.pubsub_invoker_sa = "pubsub@p.iam.gserviceaccount.com"
    settings.scheduler_sa = "sched@p.iam.gserviceaccount.com"

    client.post("/v1/events/pubsub", json=push_body(LOG_ENTRY))
    client.post("/jobs/digest")

    assert seen["/v1/events/pubsub"] == ["pubsub@p.iam.gserviceaccount.com"]
    assert seen["/jobs/digest"] == ["sched@p.iam.gserviceaccount.com"]


def test_operator_endpoints_accept_any_authorised_identity(client, monkeypatch):
    seen: dict[str, object] = {}

    def capture(request, settings, *, allowed_callers=None):
        seen[request.url.path] = allowed_callers
        return "human@example.com"

    monkeypatch.setattr("app.main.verify_oidc_token", capture)

    client.post("/v1/analyze", json={"service": "s", "text": "boom"})
    client.get("/v1/incidents")

    # None, not a service-account list — a human operator must be able to call these.
    assert seen["/v1/analyze"] is None
    assert seen["/v1/incidents"] is None


# --- digest aggregation ---------------------------------------------------


def test_build_stats_computes_noise_reduction():
    incidents = [
        {"service": "a", "occurrences": 90, "analysis": {"severity": "SEV2", "category": "DEPENDENCY"}},
        {"service": "b", "occurrences": 10, "analysis": {"severity": "SEV3", "category": "CAPACITY"}},
    ]

    stats = build_stats(incidents)

    assert stats["total"] == 2
    assert stats["total_occurrences"] == 100
    assert stats["suppressed_occurrences"] == 98
    assert stats["noise_reduction_pct"] == 98.0


def test_build_stats_on_empty_input_does_not_divide_by_zero():
    assert build_stats([])["noise_reduction_pct"] == 0.0
