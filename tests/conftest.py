from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.config import Settings
from app.models import AIAnalysis, Category, EventSource, NormalizedEvent, Severity


@pytest.fixture
def settings() -> Settings:
    return Settings(
        project_id="test-project",
        environment="test",
        verify_oidc=False,
        notifications_enabled=False,
        artifacts_bucket="",
        suppression_window_minutes=30,
    )


@pytest.fixture
def event() -> NormalizedEvent:
    return NormalizedEvent(
        source=EventSource.LOG_SINK,
        service="checkout-api",
        resource_type="cloud_run_revision",
        raw_severity="ERROR",
        message="connection to db-primary timed out after 30000ms (trace 4f2a1b9c)",
    )


class FakeRepository:
    """In-memory stand-in for Firestore with the same atomic-increment semantics."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        return self.docs.get(fingerprint)

    def create(self, incident) -> None:
        self.docs[incident.fingerprint] = incident.to_document()

    def reopen(self, incident) -> None:
        self.docs[incident.fingerprint] = {**incident.to_document(), "status": "REOPENED"}

    def record_duplicate(self, fingerprint: str, seen_at: datetime) -> int:
        doc = self.docs[fingerprint]
        doc["occurrences"] = int(doc.get("occurrences", 1)) + 1
        doc["last_seen"] = seen_at.isoformat()
        return doc["occurrences"]

    def list_recent(self, hours: int = 24, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.docs.values())[:limit]


class FakeAnalyzer:
    """Counts invocations — that count is the cost-control assertion."""

    def __init__(self, analysis: AIAnalysis | None = None) -> None:
        self.calls = 0
        self.digest_calls = 0
        self._analysis = analysis or AIAnalysis(
            severity=Severity.SEV2,
            category=Category.DEPENDENCY,
            title="Database connection timeout",
            probable_root_cause="Connection pool exhausted against db-primary",
            confidence=0.8,
            model_used="fake-model",
        )

    def analyze(self, event) -> AIAnalysis:
        self.calls += 1
        return self._analysis.model_copy(deep=True)

    def summarize_digest(self, window_hours, stats, incidents) -> str:
        self.digest_calls += 1
        return "# Digest\nAll good."


class FakeNotifier:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.sent: list[Any] = []
        self.texts: list[str] = []

    def should_notify(self, incident) -> bool:
        return self.enabled and incident.analysis.is_actionable

    def send(self, incident) -> bool:
        self.sent.append(incident)
        return True

    def send_text(self, text: str) -> bool:
        self.texts.append(text)
        return True


class FakeMetrics:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def incident_recorded(self, **kwargs) -> None:
        self.events.append(("incident", kwargs))

    def event_suppressed(self, service: str) -> None:
        self.events.append(("suppressed", {"service": service}))

    def ai_latency(self, latency_ms: int, degraded: bool) -> None:
        self.events.append(("latency", {"ms": latency_ms, "degraded": degraded}))

    def ai_failure(self, reason: str) -> None:
        self.events.append(("ai_failure", {"reason": reason}))


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def analyzer() -> FakeAnalyzer:
    return FakeAnalyzer()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def metrics() -> FakeMetrics:
    return FakeMetrics()


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)
