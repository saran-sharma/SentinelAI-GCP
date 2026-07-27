"""Caller verification behaviour.

These tests encode the fix for the defect that made the deployed service
unusable: every endpoint was pinned to a list of service accounts, so the human
operator running `make smoke` was rejected with 403 no matter what token they
presented.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import verify_oidc_token
from app.config import Settings


def make_request(path: str = "/v1/analyze", token: str | None = "t"):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(headers=headers, url=SimpleNamespace(path=path))


@pytest.fixture
def claims(monkeypatch):
    """Stub Google token verification; return the mutable claims dict.

    `auth.py` resolves `id_token.verify_oauth2_token` at call time, so patching
    the attribute on the real module is enough — no import machinery needed.
    """
    from google.oauth2 import id_token

    data = {"email": "human@example.com", "email_verified": True}

    def fake_verify(token, request, audience=None):
        if token == "bad":
            raise ValueError("Token expired")
        return data

    monkeypatch.setattr(id_token, "verify_oauth2_token", fake_verify)
    return data


@pytest.fixture
def settings() -> Settings:
    return Settings(
        verify_oidc=True,
        verify_token_signature=True,
        pubsub_invoker_sa="pubsub@p.iam.gserviceaccount.com",
    )


def test_disabled_verification_short_circuits():
    s = Settings(verify_oidc=False)
    assert verify_oidc_token(make_request(), s) == "verification-disabled"


def test_missing_bearer_token_is_401(settings):
    with pytest.raises(HTTPException) as exc:
        verify_oidc_token(make_request(token=None), settings)
    assert exc.value.status_code == 401


def test_verification_failure_surfaces_the_reason(settings, claims):
    """A bare 'invalid OIDC token' cost hours of debugging; the cause is echoed."""
    with pytest.raises(HTTPException) as exc:
        verify_oidc_token(make_request(token="bad"), settings)

    assert exc.value.status_code == 401
    assert "Token expired" in exc.value.detail


def test_operator_endpoint_accepts_any_verified_identity(settings, claims):
    # No allowed_callers: Cloud Run IAM has already authorised this identity.
    assert verify_oidc_token(make_request(), settings) == "human@example.com"


def test_machine_endpoint_rejects_a_human(settings, claims):
    with pytest.raises(HTTPException) as exc:
        verify_oidc_token(
            make_request("/v1/events/pubsub"),
            settings,
            allowed_callers=settings.pubsub_callers,
        )

    assert exc.value.status_code == 403
    assert "human@example.com" in exc.value.detail


def test_machine_endpoint_accepts_its_pinned_service_account(settings, claims):
    claims["email"] = "pubsub@p.iam.gserviceaccount.com"

    caller = verify_oidc_token(
        make_request("/v1/events/pubsub"),
        settings,
        allowed_callers=settings.pubsub_callers,
    )

    assert caller == "pubsub@p.iam.gserviceaccount.com"


def test_unverified_email_is_rejected(settings, claims):
    claims["email_verified"] = False

    with pytest.raises(HTTPException) as exc:
        verify_oidc_token(make_request(), settings)

    assert exc.value.status_code == 403


def test_empty_allowlist_does_not_lock_everyone_out(settings, claims):
    """An unset SENTINEL_*_SA must not silently reject every caller."""
    assert verify_oidc_token(make_request(), settings, allowed_callers=[]) == "human@example.com"
    assert verify_oidc_token(make_request(), settings, allowed_callers=[""]) == "human@example.com"


# --- platform-verified path (the default on Cloud Run) --------------------


def make_token(claims: dict) -> str:
    import base64
    import json

    def seg(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.not-a-real-signature"


@pytest.fixture
def platform_settings() -> Settings:
    """Cloud Run's posture: the platform verified the token, we read the claims."""
    return Settings(
        verify_oidc=True,
        verify_token_signature=False,
        pubsub_invoker_sa="pubsub@p.iam.gserviceaccount.com",
    )


def test_platform_verified_token_is_accepted_without_a_signature_check(platform_settings):
    """Cloud Run already checked the signature; an unsigned-looking token from
    it must not be rejected, which is what broke the deployed service."""
    token = make_token(
        {
            "iss": "https://accounts.google.com",
            "email": "human@example.com",
            "email_verified": True,
            "aud": "32555940559.apps.googleusercontent.com",
        }
    )

    caller = verify_oidc_token(make_request(token=token), platform_settings)

    assert caller == "human@example.com"


def test_platform_path_still_enforces_the_endpoint_allowlist(platform_settings):
    token = make_token({"iss": "https://accounts.google.com", "email": "human@example.com", "email_verified": True})

    with pytest.raises(HTTPException) as exc:
        verify_oidc_token(
            make_request("/v1/events/pubsub", token=token),
            platform_settings,
            allowed_callers=platform_settings.pubsub_callers,
        )

    assert exc.value.status_code == 403


def test_platform_path_rejects_a_non_google_issuer(platform_settings):
    token = make_token({"iss": "https://evil.example.com", "email": "a@b.com", "email_verified": True})

    with pytest.raises(HTTPException) as exc:
        verify_oidc_token(make_request(token=token), platform_settings)

    assert exc.value.status_code == 401
    assert "issuer" in exc.value.detail


def test_platform_path_rejects_a_non_jwt(platform_settings):
    with pytest.raises(HTTPException) as exc:
        verify_oidc_token(make_request(token="not-a-jwt"), platform_settings)

    assert exc.value.status_code == 401
    assert "3 segments" in exc.value.detail


def test_platform_path_makes_no_network_call(platform_settings, monkeypatch):
    """The old path fetched Google's certs on every request. That is a latency
    cost and a failure mode; it must not happen on the default path."""
    from google.oauth2 import id_token

    def explode(*_args, **_kwargs):
        raise AssertionError("verify_oauth2_token must not be called")

    monkeypatch.setattr(id_token, "verify_oauth2_token", explode)
    token = make_token({"iss": "https://accounts.google.com", "email": "human@example.com", "email_verified": True})

    assert verify_oidc_token(make_request(token=token), platform_settings) == "human@example.com"
