"""Slack notification with severity-gated routing."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.models import Incident, Severity

logger = logging.getLogger(__name__)

_COLOR = {
    Severity.SEV1: "#d7263d",
    Severity.SEV2: "#f46036",
    Severity.SEV3: "#ffc94a",
    Severity.SEV4: "#8d99ae",
}


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def should_notify(self, incident: Incident) -> bool:
        """Gate on config, actionability and severity — in that order.

        Everything is still recorded in Firestore and Cloud Monitoring; this
        only decides whether a human gets interrupted.
        """
        if not (self._settings.notifications_enabled and self._settings.slack_webhook_url):
            return False
        if not incident.analysis.is_actionable:
            return False
        floor = Severity(self._settings.notify_min_severity)
        return incident.analysis.severity.rank <= floor.rank

    def send(self, incident: Incident) -> bool:
        analysis = incident.analysis
        steps = (
            "\n".join(
                f"{i}. {s.description}" + (f"\n   `{s.command}`" if s.command else "")
                for i, s in enumerate(analysis.remediation[:3], start=1)
            )
            or "_No automated remediation proposed._"
        )

        payload = {
            "attachments": [
                {
                    "color": _COLOR.get(analysis.severity, "#8d99ae"),
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": f"{analysis.severity.value} · {analysis.title}"[:150],
                            },
                        },
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*Service*\n{incident.service}"},
                                {"type": "mrkdwn", "text": f"*Category*\n{analysis.category.value}"},
                                {"type": "mrkdwn", "text": f"*Occurrences*\n{incident.occurrences}"},
                                {"type": "mrkdwn", "text": f"*Confidence*\n{analysis.confidence:.0%}"},
                            ],
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*Probable root cause*\n{analysis.probable_root_cause}\n\n"
                                    f"*Customer impact*\n{analysis.customer_impact}\n\n"
                                    f"*Suggested remediation*\n{steps}"
                                )[:2900],
                            },
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"`{incident.fingerprint}` · {incident.environment} · "
                                        f"{analysis.model_used}"
                                        + (" · :warning: degraded triage" if analysis.degraded else "")
                                    ),
                                }
                            ],
                        },
                    ],
                }
            ]
        }

        try:
            response = httpx.post(self._settings.slack_webhook_url, json=payload, timeout=10.0)
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 — notification failure is not triage failure
            logger.warning("slack_notify_failed", extra={"error": str(exc), "fingerprint": incident.fingerprint})
            return False

    def send_text(self, text: str) -> bool:
        if not (self._settings.notifications_enabled and self._settings.slack_webhook_url):
            return False
        try:
            httpx.post(self._settings.slack_webhook_url, json={"text": text[:3000]}, timeout=10.0).raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("slack_notify_failed", extra={"error": str(exc)})
            return False
