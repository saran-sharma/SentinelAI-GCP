"""SentinelAI triage API.

Endpoints:
    GET  /healthz               liveness
    GET  /readyz                readiness (dependency probe)
    POST /v1/events/pubsub      Pub/Sub push — log sink, alerts, budgets
    POST /v1/analyze            manual triage (demos, backfills, load tests)
    GET  /v1/incidents          recent incidents
    POST /jobs/digest           Cloud Scheduler entrypoint
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.ai.analyzer import GeminiAnalyzer
from app.auth import verify_oidc_token
from app.config import Settings, get_settings
from app.ingest import IngestError, decode_pubsub_push, normalize
from app.jobs.digest import DigestJob
from app.models import EventSource, NormalizedEvent
from app.notify.slack import Notifier
from app.observability.logging_setup import configure_logging, extract_trace_id, trace_context
from app.observability.metrics import MetricsPublisher
from app.store.firestore_repo import IncidentRepository
from app.store.gcs_repo import ArtifactStore
from app.triage import TriageService

logger = logging.getLogger(__name__)


class Container:
    """Hand-rolled DI so every collaborator can be swapped in tests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = IncidentRepository(settings)
        self.analyzer = GeminiAnalyzer(settings)
        self.notifier = Notifier(settings)
        self.metrics = MetricsPublisher(settings)
        self.artifacts = ArtifactStore(settings)
        self.triage = TriageService(settings, self.repository, self.analyzer, self.notifier, self.metrics)
        self.digest = DigestJob(settings, self.repository, self.analyzer, self.artifacts, self.notifier)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.project_id, settings.service_name)
    app.state.container = Container(settings)
    logger.info(
        "service_started",
        extra={
            "environment": settings.environment,
            "model": settings.model_name,
            "suppression_window_minutes": settings.suppression_window_minutes,
        },
    )
    yield
    logger.info("service_stopping")


app = FastAPI(
    title="SentinelAI — AI Incident Triage",
    description="Event-driven AIOps triage for Google Cloud.",
    version="1.0.0",
    lifespan=lifespan,
)


def container(request: Request) -> Container:
    return request.app.state.container


def settings_dep(request: Request) -> Settings:
    return request.app.state.container.settings


@app.middleware("http")
async def request_context(request: Request, call_next):
    token = trace_context.set(extract_trace_id(request.headers.get("x-cloud-trace-context")))
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        trace_context.reset(token)
    elapsed = (time.perf_counter() - started) * 1000
    if request.url.path not in ("/healthz", "/readyz"):
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": round(elapsed, 2),
            },
        )
    return response


# --- health ---------------------------------------------------------------


# Liveness is exposed on three paths deliberately.
#
# `/healthz` on this deployment returns Google's HTML 404 from upstream of the
# container — authenticated and unauthenticated alike, with no matching row in
# the Cloud Run request log — while `/readyz` and `/docs` reach the app over the
# same host with the same token. Whatever is intercepting it is keyed on the
# path and survives new revisions, so the application cannot fix it; it can only
# stop depending on that one path being reachable.
#
# `/livez` is the alias to prefer. `/_health` is a second, differently-shaped
# fallback in case the interception matches a `*health*` pattern rather than the
# exact string.
@app.get("/healthz", include_in_schema=False)
@app.get("/livez", include_in_schema=False)
@app.get("/_health", include_in_schema=False)
async def healthz() -> Response:
    return JSONResponse({"status": "ok"})


@app.get("/readyz", include_in_schema=False)
async def readyz(c: Container = Depends(container)) -> Response:
    """Readiness probes Firestore only.

    Vertex AI is deliberately excluded: the service is designed to run
    degraded without it, so failing readiness on an AI outage would take the
    whole pipeline down for a dependency we can survive losing.
    """
    try:
        c.repository.get("__readiness_probe__")
    except Exception as exc:  # noqa: BLE001
        # The exception type and text are both needed: "unreachable" could be a
        # missing database, a wrong location, or an IAM denial, and the fix
        # differs for each. Returning it costs nothing on a private service.
        reason = f"{type(exc).__name__}: {exc}"
        logger.error("readiness_failed", extra={"error": reason})
        return JSONResponse(
            {"status": "degraded", "firestore": "unreachable", "reason": reason},
            status_code=503,
        )
    return JSONResponse({"status": "ready"})


# --- ingestion ------------------------------------------------------------


@app.post("/v1/events/pubsub")
async def pubsub_push(
    request: Request,
    c: Container = Depends(container),
    settings: Settings = Depends(settings_dep),
) -> Response:
    """Pub/Sub push endpoint.

    Status-code contract matters here, because it drives redelivery:
      204/200 -> ack   (handled, or permanently unprocessable)
      5xx     -> nack  (retry with backoff, then dead-letter)

    Malformed payloads are acked on purpose: Pub/Sub would otherwise redeliver
    a poison message until the dead-letter policy fires, burning quota and
    Gemini spend on something that can never succeed.
    """
    # Pinned: only the Pub/Sub push identity may inject events.
    verify_oidc_token(request, settings, allowed_callers=settings.pubsub_callers)

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pubsub_body_not_json", extra={"error": str(exc)})
        return JSONResponse({"status": "dropped", "reason": "invalid json"}, status_code=200)

    try:
        payload, attributes, message_id = decode_pubsub_push(body)
        event = normalize(payload, attributes)
    except IngestError as exc:
        logger.warning("pubsub_ingest_rejected", extra={"error": str(exc)})
        return JSONResponse({"status": "dropped", "reason": str(exc)}, status_code=200)

    try:
        result = c.triage.handle(event)
    except Exception as exc:  # noqa: BLE001
        # Genuinely transient (Firestore blip, quota) — let Pub/Sub retry.
        logger.exception("triage_failed", extra={"error": str(exc), "message_id": message_id})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "triage failed, retry") from exc

    return JSONResponse(result.model_dump(mode="json"))


@app.post("/v1/analyze")
async def analyze(
    request: Request,
    c: Container = Depends(container),
    settings: Settings = Depends(settings_dep),
) -> Response:
    """Triage a raw signal directly. Used by the demo script and for backfills.

    Operator-facing: any identity Cloud Run IAM has already authorised may call
    this. Pinning it to service accounts would lock out the human running
    `make smoke` / `make demo`, which is the entire point of the endpoint.
    """
    verify_oidc_token(request, settings)

    # A scalar or malformed body must be a 400, not a 500. `"message" in body`
    # against a non-mapping raises TypeError, which surfaced as an opaque
    # Internal Server Error and told the caller nothing about what was wrong.
    try:
        body: Any = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body must be valid JSON") from exc

    if not isinstance(body, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"body must be a JSON object, got {type(body).__name__}",
        )

    if "message" in body and isinstance(body["message"], dict):
        payload, attributes, _ = decode_pubsub_push(body)
        event = normalize(payload, attributes)
    elif "log" in body or "resource" in body:
        event = normalize(body.get("log", body), {})
    else:
        event = NormalizedEvent(
            source=EventSource.MANUAL,
            service=str(body.get("service", "manual")),
            resource_type=str(body.get("resource_type", "manual")),
            raw_severity=str(body.get("severity", "ERROR")).upper(),
            message=str(body.get("text", "")),
        )

    if not event.message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty signal")

    return JSONResponse(c.triage.handle(event).model_dump(mode="json"))


# --- read model -----------------------------------------------------------


@app.get("/v1/incidents")
async def list_incidents(
    request: Request,
    hours: int = 24,
    limit: int = 50,
    c: Container = Depends(container),
    settings: Settings = Depends(settings_dep),
) -> Response:
    verify_oidc_token(request, settings)
    hours = max(1, min(hours, 168))
    limit = max(1, min(limit, 200))
    incidents = c.repository.list_recent(hours=hours, limit=limit)
    return JSONResponse({"count": len(incidents), "window_hours": hours, "incidents": incidents})


# --- scheduled jobs -------------------------------------------------------


@app.post("/jobs/digest")
async def run_digest(
    request: Request,
    window_hours: int = 24,
    c: Container = Depends(container),
    settings: Settings = Depends(settings_dep),
) -> Response:
    # Pinned: only Cloud Scheduler may trigger the digest.
    verify_oidc_token(request, settings, allowed_callers=settings.scheduler_callers)
    return JSONResponse(c.digest.run(window_hours=max(1, min(window_hours, 168))))
