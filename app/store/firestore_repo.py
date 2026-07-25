"""Firestore-backed incident store.

Document id == fingerprint, which gives idempotency for free: Pub/Sub is
at-least-once, so the same log entry can and will arrive twice. A duplicate
delivery lands on the same document and increments a counter rather than
paging anyone a second time.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.models import Incident

logger = logging.getLogger(__name__)


class IncidentRepository:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import firestore

            self._client = firestore.Client(
                project=self._settings.project_id,
                database=self._settings.firestore_database,
            )
        return self._client

    def _doc(self, fingerprint: str) -> Any:
        return self.client.collection(self._settings.incidents_collection).document(fingerprint)

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        snapshot = self._doc(fingerprint).get()
        return snapshot.to_dict() if snapshot.exists else None

    def create(self, incident: Incident) -> None:
        self._doc(incident.fingerprint).set(incident.to_document())

    def record_duplicate(self, fingerprint: str, seen_at: datetime) -> int:
        """Fold a repeat occurrence into the existing incident.

        Uses a server-side atomic increment so concurrent Cloud Run instances
        processing the same burst cannot lose counts to read-modify-write races.
        """
        from google.cloud import firestore

        self._doc(fingerprint).update(
            {
                "occurrences": firestore.Increment(1),
                "last_seen": seen_at.isoformat(),
            }
        )
        doc = self.get(fingerprint) or {}
        return int(doc.get("occurrences", 1))

    def reopen(self, incident: Incident) -> None:
        """Same failure mode returning after the suppression window closed."""
        self._doc(incident.fingerprint).set(
            {
                **incident.to_document(),
                "status": "REOPENED",
                "first_seen": incident.first_seen.isoformat(),
            }
        )

    def list_recent(self, hours: int = 24, limit: int = 100) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        query = (
            self.client.collection(self._settings.incidents_collection)
            .where("last_seen", ">=", cutoff)
            .order_by("last_seen", direction="DESCENDING")
            .limit(limit)
        )
        return [doc.to_dict() for doc in query.stream()]


def is_within_suppression_window(document: dict[str, Any], window_minutes: int, now: datetime) -> bool:
    """True when the last sighting is recent enough to fold silently."""
    last_seen = document.get("last_seen")
    if not last_seen:
        return False
    try:
        parsed = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (now - parsed) <= timedelta(minutes=window_minutes)
