"""Behaviour tests for the suppression logic — the core cost/noise control."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import AIAnalysis, Category, Severity
from app.store.firestore_repo import is_within_suppression_window
from app.triage import TriageService


def build_service(settings, repo, analyzer, notifier, metrics) -> TriageService:
    return TriageService(settings, repo, analyzer, notifier, metrics)


def test_first_occurrence_creates_incident_and_calls_ai(settings, repo, analyzer, notifier, metrics, event):
    service = build_service(settings, repo, analyzer, notifier, metrics)

    result = service.handle(event)

    assert result.action == "created"
    assert result.ai_invoked is True
    assert result.notified is True
    assert analyzer.calls == 1
    assert repo.get(result.fingerprint) is not None


def test_burst_of_identical_events_calls_ai_once(settings, repo, analyzer, notifier, metrics, event):
    """The headline behaviour: 50 log lines, one Gemini call, one page."""
    service = build_service(settings, repo, analyzer, notifier, metrics)

    results = [service.handle(event) for _ in range(50)]

    assert analyzer.calls == 1
    assert len(notifier.sent) == 1
    assert results[0].action == "created"
    assert all(r.action == "suppressed" for r in results[1:])
    assert results[-1].occurrences == 50


def test_suppressed_events_still_increment_the_counter(settings, repo, analyzer, notifier, metrics, event):
    service = build_service(settings, repo, analyzer, notifier, metrics)

    service.handle(event)
    service.handle(event)
    service.handle(event)

    stored = repo.get(next(iter(repo.docs)))
    assert stored["occurrences"] == 3
    assert sum(1 for kind, _ in metrics.events if kind == "suppressed") == 2


def test_incident_reopens_after_window_expires(settings, repo, analyzer, notifier, metrics, event):
    service = build_service(settings, repo, analyzer, notifier, metrics)
    service.handle(event)

    # Age the stored incident past the suppression window.
    fingerprint = next(iter(repo.docs))
    stale = datetime.now(UTC) - timedelta(minutes=settings.suppression_window_minutes + 5)
    repo.docs[fingerprint]["last_seen"] = stale.isoformat()

    result = service.handle(event)

    assert result.action == "reopened"
    assert result.ai_invoked is True
    assert analyzer.calls == 2
    assert len(notifier.sent) == 2


def test_distinct_failure_modes_are_separate_incidents(settings, repo, analyzer, notifier, metrics, event):
    service = build_service(settings, repo, analyzer, notifier, metrics)
    other = event.model_copy(update={"message": "permission denied on bucket assets-prod"})

    first = service.handle(event)
    second = service.handle(other)

    assert first.fingerprint != second.fingerprint
    assert analyzer.calls == 2
    assert len(repo.docs) == 2


def test_non_actionable_signal_is_recorded_but_never_pages(settings, repo, analyzer, notifier, metrics, event):
    analyzer._analysis = AIAnalysis(
        severity=Severity.SEV4,
        category=Category.UNKNOWN,
        title="Health-check chatter",
        is_actionable=False,
    )
    service = build_service(settings, repo, analyzer, notifier, metrics)

    result = service.handle(event)

    assert result.action == "ignored"
    assert result.notified is False
    assert notifier.sent == []
    assert repo.get(result.fingerprint) is not None  # still auditable


def test_degraded_analysis_is_flagged_and_metered(settings, repo, analyzer, notifier, metrics, event):
    analyzer._analysis = AIAnalysis(severity=Severity.SEV2, degraded=True, model_used="heuristic-fallback")
    service = build_service(settings, repo, analyzer, notifier, metrics)

    result = service.handle(event)

    assert result.degraded is True
    assert any(kind == "ai_failure" for kind, _ in metrics.events)


# --- window helper --------------------------------------------------------


def test_window_helper_handles_missing_and_malformed_timestamps():
    now = datetime.now(UTC)
    assert is_within_suppression_window({}, 30, now) is False
    assert is_within_suppression_window({"last_seen": "not-a-date"}, 30, now) is False
    assert is_within_suppression_window({"last_seen": now.isoformat()}, 30, now) is True
    old = (now - timedelta(hours=2)).isoformat()
    assert is_within_suppression_window({"last_seen": old}, 30, now) is False


def test_window_helper_treats_naive_timestamps_as_utc():
    now = datetime.now(UTC)
    naive = now.replace(tzinfo=None).isoformat()
    assert is_within_suppression_window({"last_seen": naive}, 30, now) is True
