"""Caller verification for the triage API.

Two layers, with a clear division of responsibility:

1.  **Cloud Run IAM** (`roles/run.invoker`, no `allUsers`) is the primary gate.
    It validates the OIDC token's signature *and its audience against the
    service URL* before the request ever reaches this process. That is why this
    module does not pin the audience itself: re-deriving our own URL in-app
    would be guesswork, and Cloud Run has already done it authoritatively.
    `expected_audience` remains available for deployments that are not behind
    Cloud Run, where nothing else performs that check.

2.  **This module** answers a narrower question that IAM cannot: is this the
    *specific* identity that should be calling *this endpoint*? The machine
    endpoints are pinned to exactly one service account each, so widening the
    IAM binding later — adding a debugging identity, say — does not silently
    grant that identity the ability to inject events or trigger jobs.

Endpoints a human operator legitimately drives (`/v1/analyze`, `/v1/incidents`)
pass `allowed_callers=None`: any identity Cloud Run IAM has already authorised
is acceptable. Pinning those to service accounts too was the original design and
it was wrong — it made `make smoke` and `make demo` impossible to run, because
the operator's own identity was never on the list.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from fastapi import HTTPException, Request, status

from app.config import Settings

logger = logging.getLogger(__name__)


def verify_oidc_token(
    request: Request,
    settings: Settings,
    *,
    allowed_callers: Sequence[str] | None = None,
) -> str:
    """Return the verified caller email, or raise 401/403.

    `allowed_callers` empty or None means "any identity Cloud Run already
    authorised", which is the correct posture for operator-facing endpoints.
    """
    if not settings.verify_oidc:
        return "verification-disabled"

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")

    token = header.split(" ", 1)[1].strip()

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=settings.expected_audience or None,
        )
    except Exception as exc:  # noqa: BLE001
        # The reason is echoed to the caller on purpose. This service is private
        # — only IAM-authorised identities can reach it — and a bare "invalid
        # OIDC token" costs far more debugging time than the detail is worth.
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("oidc_verification_failed", extra={"error": reason})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid OIDC token ({reason})") from exc

    email = str(claims.get("email", ""))
    if not claims.get("email_verified", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "unverified token identity")

    permitted = [c for c in (allowed_callers or []) if c]
    if permitted and email not in permitted:
        logger.warning(
            "oidc_caller_not_allowlisted",
            extra={"caller": email, "endpoint": request.url.path},
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"caller {email} is not permitted on {request.url.path}",
        )

    return email
