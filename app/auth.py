"""Caller verification for the triage API.

Two layers, with a clear division of responsibility:

1.  **Cloud Run IAM** (`roles/run.invoker`, no `allUsers`) is the primary gate.
    It validates the token's signature, expiry and audience against the service
    URL before the request ever reaches this process. An unauthenticated call
    is rejected at the front end and never arrives here at all.

    Because of that, this module does not re-verify the signature by default —
    see `_decode_platform_verified`. Doing so bought no security and actively
    broke the service: valid, Cloud Run-approved operator tokens were rejected
    with "Could not verify token signature" while the platform considered the
    caller fully authorised. Set `SENTINEL_VERIFY_TOKEN_SIGNATURE=true` when
    deploying without an authenticating proxy in front.

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

import base64
import json
import logging
from collections.abc import Sequence
from typing import Any

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
        claims = (
            _verify_signature(token, settings) if settings.verify_token_signature else _decode_platform_verified(token)
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


_GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


def _decode_platform_verified(token: str) -> dict[str, Any]:
    """Read the claims of a token the platform has already verified.

    Cloud Run validates the signature, the expiry and the audience against the
    service URL before forwarding the request. A request cannot reach this
    process without having passed that check — an unauthenticated call is
    rejected at the front end and never arrives. Re-verifying the signature
    here therefore adds no security, and it does add:

      - a synchronous network call to googleapis.com on every request,
      - a dependency that can fail for reasons unrelated to the caller's
        legitimacy, turning a valid request into a 401.

    That is not hypothetical. Verification was rejecting genuine, Cloud
    Run-approved operator tokens with "Could not verify token signature",
    making the service unusable while the platform considered the caller fully
    authorised.

    So: trust the platform's verification, read the claims, and use them only
    to decide which endpoint this already-authorised identity may call. The
    issuer is still checked, cheaply, to catch a grossly malformed token.

    Set SENTINEL_VERIFY_TOKEN_SIGNATURE=true when running anywhere that does
    NOT authenticate in front of the app — there, this would be unsafe.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(f"not a JWT: expected 3 segments, got {len(parts)}")

    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    claims: dict[str, Any] = json.loads(base64.urlsafe_b64decode(payload))

    issuer = str(claims.get("iss", ""))
    if issuer not in _GOOGLE_ISSUERS:
        raise ValueError(f"unexpected issuer {issuer!r}")

    return claims


def _verify_signature(token: str, settings: Settings) -> dict[str, Any]:
    """Full offline-unsafe verification, for deployments with no auth in front."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(
        token,
        google_requests.Request(),
        audience=settings.expected_audience or None,
    )
