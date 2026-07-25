"""Domain models shared across ingest, AI analysis, storage and notification."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    SEV1 = "SEV1"  # customer-facing outage, page now
    SEV2 = "SEV2"  # major degradation, page during business hours
    SEV3 = "SEV3"  # single-component failure, ticket
    SEV4 = "SEV4"  # noise / informational

    @property
    def rank(self) -> int:
        return {"SEV1": 1, "SEV2": 2, "SEV3": 3, "SEV4": 4}[self.value]


class Category(str, Enum):
    AVAILABILITY = "AVAILABILITY"
    PERFORMANCE = "PERFORMANCE"
    CAPACITY = "CAPACITY"
    CONFIGURATION = "CONFIGURATION"
    DEPLOYMENT = "DEPLOYMENT"
    SECURITY = "SECURITY"
    DEPENDENCY = "DEPENDENCY"
    COST = "COST"
    DATA = "DATA"
    UNKNOWN = "UNKNOWN"


class EventSource(str, Enum):
    LOG_SINK = "LOG_SINK"
    MONITORING_ALERT = "MONITORING_ALERT"
    BUDGET_ALERT = "BUDGET_ALERT"
    MANUAL = "MANUAL"


class NormalizedEvent(BaseModel):
    """One signal, flattened from whichever GCP producer emitted it.

    Log sink entries, Cloud Monitoring alerts and billing budget notifications
    all arrive on the same Pub/Sub topic in wildly different shapes; the whole
    pipeline downstream of ingest only ever sees this.
    """

    source: EventSource
    service: str = "unknown"
    resource_type: str = "unknown"
    raw_severity: str = "ERROR"
    message: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RemediationStep(BaseModel):
    description: str
    command: str = ""
    requires_approval: bool = True


class AIAnalysis(BaseModel):
    """Structured Gemini output. Never free-form prose — this gets stored,
    metered and rendered, so the schema is enforced at generation time."""

    severity: Severity = Severity.SEV3
    category: Category = Category.UNKNOWN
    title: str = "Unclassified incident"
    probable_root_cause: str = ""
    blast_radius: str = ""
    customer_impact: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    remediation: list[RemediationStep] = Field(default_factory=list)
    investigation_queries: list[str] = Field(default_factory=list)
    is_actionable: bool = True
    model_used: str = ""
    latency_ms: int = 0
    degraded: bool = False  # True when the heuristic fallback produced this


class Incident(BaseModel):
    fingerprint: str
    status: str = "OPEN"
    occurrences: int = 1
    first_seen: datetime
    last_seen: datetime
    service: str
    source: EventSource
    sample_message: str
    analysis: AIAnalysis
    notified: bool = False
    environment: str = "dev"

    def to_document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TriageResult(BaseModel):
    """What the API hands back — deliberately explicit about which path was
    taken so the behaviour is observable from a curl, not just from logs."""

    fingerprint: str
    action: str  # created | suppressed | reopened | ignored
    severity: Severity
    occurrences: int
    notified: bool
    ai_invoked: bool
    degraded: bool = False
