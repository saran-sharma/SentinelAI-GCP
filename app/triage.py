"""The triage pipeline: normalise -> fingerprint -> suppress-or-analyse -> notify.

Everything expensive sits behind the suppression check on purpose. Ordering is
the whole cost model:

    fingerprint (free)  ->  Firestore read (~1ms, negligible cost)
                        ->  [suppressed? stop here]
                        ->  Gemini call (the only meaningful spend)
                        ->  notify a human (the only *irreplaceable* resource)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.ai.analyzer import GeminiAnalyzer
from app.config import Settings
from app.fingerprint import compute_fingerprint
from app.models import Incident, NormalizedEvent, TriageResult
from app.notify.slack import Notifier
from app.observability.metrics import MetricsPublisher
from app.store.firestore_repo import IncidentRepository, is_within_suppression_window

logger = logging.getLogger(__name__)


class TriageService:
    def __init__(
        self,
        settings: Settings,
        repository: IncidentRepository,
        analyzer: GeminiAnalyzer,
        notifier: Notifier,
        metrics: MetricsPublisher,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._analyzer = analyzer
        self._notifier = notifier
        self._metrics = metrics

    def handle(self, event: NormalizedEvent) -> TriageResult:
        fingerprint = compute_fingerprint(event)
        now = datetime.now(UTC)

        existing = self._repo.get(fingerprint)

        # --- Fast path: known failure mode, still inside its window --------
        if existing and is_within_suppression_window(existing, self._settings.suppression_window_minutes, now):
            occurrences = self._repo.record_duplicate(fingerprint, now)
            self._metrics.event_suppressed(event.service)
            severity = _severity_of(existing)
            logger.info(
                "incident_suppressed",
                extra={
                    "fingerprint": fingerprint,
                    "service": event.service,
                    "occurrences": occurrences,
                    "severity": severity,
                },
            )
            return TriageResult(
                fingerprint=fingerprint,
                action="suppressed",
                severity=severity,
                occurrences=occurrences,
                notified=False,
                ai_invoked=False,
            )

        # --- Slow path: new or re-opened failure mode ----------------------
        analysis = self._analyzer.analyze(event)
        self._metrics.ai_latency(analysis.latency_ms, analysis.degraded)
        if analysis.degraded:
            self._metrics.ai_failure("vertex_unavailable")

        incident = Incident(
            fingerprint=fingerprint,
            occurrences=int(existing.get("occurrences", 0)) + 1 if existing else 1,
            first_seen=_first_seen(existing, now),
            last_seen=now,
            service=event.service,
            source=event.source,
            sample_message=event.message[:2000],
            analysis=analysis,
            environment=self._settings.environment,
        )

        action = "reopened" if existing else "created"
        notified = False
        if self._notifier.should_notify(incident):
            notified = self._notifier.send(incident)
        elif not analysis.is_actionable:
            action = "ignored"

        incident.notified = notified
        if existing:
            self._repo.reopen(incident)
        else:
            self._repo.create(incident)

        self._metrics.incident_recorded(
            severity=analysis.severity.value,
            service=event.service,
            action=action,
            category=analysis.category.value,
        )
        logger.info(
            "incident_triaged",
            extra={
                "fingerprint": fingerprint,
                "action": action,
                "severity": analysis.severity.value,
                "category": analysis.category.value,
                "service": event.service,
                "notified": notified,
                "degraded": analysis.degraded,
                "ai_latency_ms": analysis.latency_ms,
            },
        )

        return TriageResult(
            fingerprint=fingerprint,
            action=action,
            severity=analysis.severity,
            occurrences=incident.occurrences,
            notified=notified,
            ai_invoked=True,
            degraded=analysis.degraded,
        )


def _first_seen(existing: dict | None, now: datetime) -> datetime:
    if not existing:
        return now
    try:
        parsed = datetime.fromisoformat(str(existing.get("first_seen", "")).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return now


def _severity_of(document: dict) -> str:
    from app.models import Severity

    raw = str((document.get("analysis") or {}).get("severity", "SEV3"))
    try:
        return Severity(raw).value
    except ValueError:
        return Severity.SEV3.value
