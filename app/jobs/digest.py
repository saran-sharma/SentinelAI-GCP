"""Scheduled reliability digest.

Cloud Scheduler -> OIDC -> POST /jobs/digest. Aggregates the window from
Firestore, has Gemini turn it into something an on-call engineer will actually
read, archives it to GCS and posts the TL;DR to Slack.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.ai.analyzer import GeminiAnalyzer
from app.config import Settings
from app.notify.slack import Notifier
from app.store.firestore_repo import IncidentRepository
from app.store.gcs_repo import ArtifactStore

logger = logging.getLogger(__name__)


def build_stats(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    by_severity: Counter[str] = Counter()
    by_service: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    total_occurrences = 0
    degraded = 0

    for doc in incidents:
        analysis = doc.get("analysis") or {}
        by_severity[str(analysis.get("severity", "SEV3"))] += 1
        by_service[str(doc.get("service", "unknown"))] += 1
        by_category[str(analysis.get("category", "UNKNOWN"))] += 1
        total_occurrences += int(doc.get("occurrences", 1))
        degraded += 1 if analysis.get("degraded") else 0

    return {
        "total": len(incidents),
        "total_occurrences": total_occurrences,
        # The headline number: raw signals we absorbed without paging anyone.
        "suppressed_occurrences": max(0, total_occurrences - len(incidents)),
        "noise_reduction_pct": round((1 - len(incidents) / total_occurrences) * 100, 1) if total_occurrences else 0.0,
        "by_severity": dict(by_severity),
        "by_service": dict(by_service.most_common(10)),
        "by_category": dict(by_category),
        "degraded_triages": degraded,
    }


def _compact(doc: dict[str, Any]) -> dict[str, Any]:
    analysis = doc.get("analysis") or {}
    return {
        "fingerprint": doc.get("fingerprint"),
        "title": analysis.get("title"),
        "severity": analysis.get("severity"),
        "category": analysis.get("category"),
        "service": doc.get("service"),
        "occurrences": doc.get("occurrences", 1),
        "root_cause": str(analysis.get("probable_root_cause", ""))[:300],
        "last_seen": doc.get("last_seen"),
    }


class DigestJob:
    def __init__(
        self,
        settings: Settings,
        repository: IncidentRepository,
        analyzer: GeminiAnalyzer,
        artifacts: ArtifactStore,
        notifier: Notifier,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._analyzer = analyzer
        self._artifacts = artifacts
        self._notifier = notifier

    def run(self, window_hours: int = 24) -> dict[str, Any]:
        now = datetime.now(UTC)
        incidents = self._repo.list_recent(hours=window_hours, limit=200)

        if not incidents:
            logger.info("digest_empty_window", extra={"window_hours": window_hours})
            self._notifier.send_text(
                f":white_check_mark: No incidents in the last {window_hours}h " f"({self._settings.environment})."
            )
            return {"status": "empty", "window_hours": window_hours, "incidents": 0}

        stats = build_stats(incidents)
        ranked = sorted(incidents, key=lambda d: int(d.get("occurrences", 1)), reverse=True)
        markdown = self._analyzer.summarize_digest(window_hours, stats, [_compact(d) for d in ranked])

        header = (
            f"<!-- generated {now.isoformat()} | env={self._settings.environment} " f"| window={window_hours}h -->\n\n"
        )
        uri = self._artifacts.write_markdown(ArtifactStore.digest_path(now), header + markdown)

        self._notifier.send_text(
            f"*Reliability digest — last {window_hours}h ({self._settings.environment})*\n"
            f"{stats['total']} incidents from {stats['total_occurrences']} raw signals "
            f"({stats['noise_reduction_pct']}% noise absorbed).\n"
            f"{markdown[:1200]}" + (f"\n\nFull report: `{uri}`" if uri else "")
        )

        logger.info("digest_generated", extra={"window_hours": window_hours, "uri": uri, **stats})
        return {"status": "ok", "window_hours": window_hours, "artifact": uri, **stats}
