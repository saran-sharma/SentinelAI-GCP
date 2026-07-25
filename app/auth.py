"""Defence-in-depth OIDC verification for Pub/Sub push and Scheduler calls.

Cloud Run's `--no-allow-unauthenticated` + `roles/run.invoker` is the primary
control and it is enforced before a request ever reaches this process. This
module is the second layer: it pins the *specific* service account allowed to
invoke each entrypoint, so a future misconfiguration that widens the IAM
binding does not silently widen the trust boundary.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from app.config import Settings

logger = logging.getLogger(__name__)


def verify_oidc_token(request: Request, settings: Settings) -> str:
    """Return the verified caller email, or raise 401/403."""
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
        logger.warning("oidc_verification_failed", extra={"error": str(exc)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid OIDC token") from exc

    email = str(claims.get("email", ""))
    if not claims.get("email_verified", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "unverified token identity")

    allowed = settings.allowed_invoker_list
    if allowed and email not in allowed:
        logger.warning("oidc_caller_not_allowlisted", extra={"caller": email})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "caller not permitted")

    return email
