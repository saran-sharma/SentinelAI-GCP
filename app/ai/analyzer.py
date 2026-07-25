"""Vertex AI (Gemini) triage with bounded retries and a heuristic fallback.

Design stance: the AI is an *enrichment* layer, never a hard dependency. If
Vertex AI is slow, quota-limited or down, triage still happens — degraded,
clearly labelled, and still paging on the things that matter. An observability
platform that goes dark when its own dependency goes dark is worthless during
exactly the incident you built it for.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from app.ai.prompts import (
    DIGEST_SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    build_digest_prompt,
    build_triage_prompt,
)
from app.config import Settings
from app.models import AIAnalysis, Category, NormalizedEvent, RemediationStep, Severity

logger = logging.getLogger(__name__)

# JSON schema handed to Gemini so the response is machine-parseable by
# construction. Cheaper and far more reliable than parsing prose.
TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "severity",
        "category",
        "title",
        "probable_root_cause",
        "blast_radius",
        "customer_impact",
        "confidence",
        "is_actionable",
        "remediation",
        "investigation_queries",
    ],
    "properties": {
        "severity": {"type": "STRING", "enum": [s.value for s in Severity]},
        "category": {"type": "STRING", "enum": [c.value for c in Category]},
        "title": {"type": "STRING", "description": "Under 90 chars, no timestamps or ids."},
        "probable_root_cause": {"type": "STRING"},
        "blast_radius": {"type": "STRING", "description": "Which services/users are affected."},
        "customer_impact": {"type": "STRING", "description": "'none' if internal only."},
        "confidence": {"type": "NUMBER"},
        "is_actionable": {"type": "BOOLEAN"},
        "remediation": {
            "type": "ARRAY",
            "maxItems": 5,
            "items": {
                "type": "OBJECT",
                "required": ["description", "command", "requires_approval"],
                "properties": {
                    "description": {"type": "STRING"},
                    "command": {"type": "STRING", "description": "Runnable gcloud/kubectl/terraform, or empty."},
                    "requires_approval": {"type": "BOOLEAN"},
                },
            },
        },
        "investigation_queries": {
            "type": "ARRAY",
            "maxItems": 3,
            "items": {"type": "STRING", "description": "Cloud Logging query filter."},
        },
    },
}

_RETRYABLE = ("429", "500", "503", "504", "deadline", "timeout", "unavailable", "resource exhausted")


class GeminiAnalyzer:
    """Thin, testable wrapper over the Vertex AI Gemini API."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        # Lazy so unit tests and `--help` never touch credentials or the network.
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                vertexai=True,
                project=self._settings.project_id,
                location=self._settings.region,
            )
        return self._client

    # -- public API --------------------------------------------------------

    def analyze(self, event: NormalizedEvent) -> AIAnalysis:
        started = time.perf_counter()
        try:
            raw = self._generate_json(
                system_instruction=SYSTEM_INSTRUCTION,
                prompt=build_triage_prompt(event, self._settings.max_log_chars),
                schema=TRIAGE_SCHEMA,
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001 — degradation is the point
            logger.warning(
                "vertex_ai_triage_failed, falling back to heuristics",
                extra={"error": str(exc), "service": event.service},
            )
            analysis = heuristic_analysis(event)
            analysis.latency_ms = int((time.perf_counter() - started) * 1000)
            return analysis

        analysis = self._to_analysis(raw)
        analysis.model_used = self._settings.model_name
        analysis.latency_ms = int((time.perf_counter() - started) * 1000)
        return analysis

    def summarize_digest(self, window_hours: int, stats: dict, incidents: list[dict]) -> str:
        try:
            response = self._call(
                system_instruction=DIGEST_SYSTEM_INSTRUCTION,
                prompt=build_digest_prompt(window_hours, stats, incidents),
                schema=None,
                temperature=0.3,
            )
            return (getattr(response, "text", "") or "").strip() or _fallback_digest(stats, incidents)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vertex_ai_digest_failed", extra={"error": str(exc)})
            return _fallback_digest(stats, incidents)

    # -- internals ---------------------------------------------------------

    def _call(self, *, system_instruction: str, prompt: str, schema: dict | None, temperature: float) -> Any:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=2048,
            response_mime_type="application/json" if schema else "text/plain",
            response_schema=schema,
            http_options=types.HttpOptions(timeout=int(self._settings.ai_timeout_seconds * 1000)),
        )

        last_error: Exception | None = None
        for attempt in range(1, self._settings.ai_max_attempts + 1):
            try:
                return self.client.models.generate_content(
                    model=self._settings.model_name,
                    contents=prompt,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not _is_retryable(exc) or attempt == self._settings.ai_max_attempts:
                    raise
                # Full jitter: synchronised retries from a fan-out of Pub/Sub
                # deliveries are how you turn a blip into a quota outage.
                backoff = min(2**attempt, 8) * random.random()  # noqa: S311 — jitter, not crypto
                logger.info("vertex_ai_retry", extra={"attempt": attempt, "sleep": round(backoff, 2)})
                time.sleep(backoff)

        raise last_error  # type: ignore[misc]

    def _generate_json(self, **kwargs: Any) -> dict[str, Any]:
        import json

        response = self._call(**kwargs)
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise ValueError("empty response from Gemini")
        return json.loads(text)

    @staticmethod
    def _to_analysis(raw: dict[str, Any]) -> AIAnalysis:
        steps = [
            RemediationStep(
                description=str(step.get("description", "")),
                command=str(step.get("command", "") or ""),
                requires_approval=bool(step.get("requires_approval", True)),
            )
            for step in raw.get("remediation", [])
            if isinstance(step, dict) and step.get("description")
        ]
        return AIAnalysis(
            severity=Severity(str(raw.get("severity", "SEV3")).upper()),
            category=Category(str(raw.get("category", "UNKNOWN")).upper()),
            title=str(raw.get("title", "Unclassified incident"))[:120],
            probable_root_cause=str(raw.get("probable_root_cause", "")),
            blast_radius=str(raw.get("blast_radius", "")),
            customer_impact=str(raw.get("customer_impact", "")),
            confidence=max(0.0, min(1.0, float(raw.get("confidence", 0.0)))),
            remediation=steps,
            investigation_queries=[str(q) for q in raw.get("investigation_queries", [])][:3],
            is_actionable=bool(raw.get("is_actionable", True)),
        )


# -- degraded path ---------------------------------------------------------

_SEVERITY_FLOOR = {
    "EMERGENCY": Severity.SEV1,
    "ALERT": Severity.SEV1,
    "CRITICAL": Severity.SEV2,
    "ERROR": Severity.SEV3,
    "WARNING": Severity.SEV4,
}

_KEYWORD_CATEGORY = [
    (("oom", "out of memory", "quota", "resource exhausted", "throttl", "capacity"), Category.CAPACITY),
    (("permission", "unauthor", "forbidden", "denied", "iam", "credential"), Category.SECURITY),
    (("timeout", "deadline exceeded", "connection refused", "upstream", "dns"), Category.DEPENDENCY),
    (("rollout", "revision", "image", "deploy", "crashloop"), Category.DEPLOYMENT),
    (("budget", "cost", "spend"), Category.COST),
    (("latency", "slow", "p99", "saturat"), Category.PERFORMANCE),
]


def heuristic_analysis(event: NormalizedEvent) -> AIAnalysis:
    """Rules-only triage used when Vertex AI is unavailable.

    Intentionally conservative: it never downgrades below what the raw log
    severity implies, because under-paging during an AI outage is the worst
    possible failure mode for this system.
    """
    text = event.message.lower()
    severity = _SEVERITY_FLOOR.get(event.raw_severity.upper(), Severity.SEV3)

    category = Category.UNKNOWN
    for keywords, mapped in _KEYWORD_CATEGORY:
        if any(k in text for k in keywords):
            category = mapped
            break

    if category in (Category.CAPACITY, Category.SECURITY) and severity.rank > Severity.SEV2.rank:
        severity = Severity.SEV2

    return AIAnalysis(
        severity=severity,
        category=category,
        title=f"[degraded] {event.service}: {event.message.strip()[:80]}",
        probable_root_cause="AI triage unavailable — classified by rules on log severity and keywords.",
        blast_radius=f"service={event.service}, resource={event.resource_type}",
        customer_impact="unknown (degraded triage)",
        confidence=0.25,
        remediation=[
            RemediationStep(
                description="Inspect the raw logs for this service directly.",
                command=(
                    f'gcloud logging read \'resource.type="{event.resource_type}" '
                    f"severity>=ERROR' --limit=50 --freshness=1h"
                ),
                requires_approval=False,
            )
        ],
        investigation_queries=[f'resource.type="{event.resource_type}" severity>=ERROR'],
        is_actionable=severity.rank <= Severity.SEV3.rank,
        model_used="heuristic-fallback",
        degraded=True,
    )


def _is_retryable(exc: Exception) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(token in blob for token in _RETRYABLE)


def _fallback_digest(stats: dict, incidents: list[dict]) -> str:
    lines = [
        "# Reliability Digest (degraded)",
        "",
        "_AI summarisation unavailable; raw aggregates below._",
        "",
        f"- Total incidents: **{stats.get('total', 0)}**",
        f"- By severity: `{stats.get('by_severity', {})}`",
        f"- Suppressed duplicates: **{stats.get('suppressed_occurrences', 0)}**",
        "",
        "## Top failure modes",
    ]
    for item in incidents[:10]:
        lines.append(f"- `{item.get('fingerprint', '?')}` ×{item.get('occurrences', 1)} — {item.get('title', 'n/a')}")
    return "\n".join(lines)
