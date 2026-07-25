"""Custom Cloud Monitoring metrics.

These are what the dashboard and the alert policies in
`terraform/modules/monitoring` are actually built on — the platform monitors
itself with the same primitives it monitors everything else with.

Every write is best-effort and swallowed: a telemetry failure must never
turn into a Pub/Sub nack and a redelivery storm.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

PREFIX = "custom.googleapis.com/sentinelai"

INCIDENTS = f"{PREFIX}/incidents"
SUPPRESSED = f"{PREFIX}/suppressed_events"
AI_LATENCY = f"{PREFIX}/ai_latency_ms"
AI_FAILURES = f"{PREFIX}/ai_failures"


class MetricsPublisher:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client
        self._enabled = True

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import monitoring_v3

            self._client = monitoring_v3.MetricServiceClient()
        return self._client

    def _write(self, metric_type: str, value: float, labels: dict[str, str], *, is_int: bool) -> None:
        if not self._enabled:
            return
        try:
            from google.api import metric_pb2
            from google.cloud import monitoring_v3

            now = time.time()
            point = monitoring_v3.Point(
                interval=monitoring_v3.TimeInterval(
                    end_time={"seconds": int(now), "nanos": int((now - int(now)) * 1e9)}
                ),
                value=monitoring_v3.TypedValue(int64_value=int(value))
                if is_int
                else monitoring_v3.TypedValue(double_value=float(value)),
            )
            series = monitoring_v3.TimeSeries(
                metric=metric_pb2.Metric(
                    type=metric_type,
                    labels={k: str(v)[:100] for k, v in labels.items()},
                ),
                resource={"type": "global", "labels": {"project_id": self._settings.project_id}},
                points=[point],
            )
            self.client.create_time_series(
                name=f"projects/{self._settings.project_id}",
                time_series=[series],
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("metric_write_failed", extra={"error": str(exc), "metric": metric_type})

    def incident_recorded(self, severity: str, service: str, action: str, category: str) -> None:
        self._write(
            INCIDENTS,
            1,
            {"severity": severity, "service": service, "action": action, "category": category},
            is_int=True,
        )

    def event_suppressed(self, service: str) -> None:
        self._write(SUPPRESSED, 1, {"service": service}, is_int=True)

    def ai_latency(self, latency_ms: int, degraded: bool) -> None:
        self._write(AI_LATENCY, latency_ms, {"degraded": str(degraded).lower()}, is_int=False)

    def ai_failure(self, reason: str) -> None:
        self._write(AI_FAILURES, 1, {"reason": reason[:60]}, is_int=True)


class InMemoryMetrics(MetricsPublisher):
    """Test/local double. Same surface, no network."""

    def __init__(self, settings: Settings) -> None:  # noqa: D107
        super().__init__(settings, client=object())
        self.written: list[tuple[str, float, dict[str, str]]] = []

    def _write(self, metric_type: str, value: float, labels: dict[str, str], *, is_int: bool) -> None:
        self.written.append((metric_type, value, labels))
