"""Runtime configuration, sourced entirely from the environment.

Cloud Run injects these via Terraform-managed env vars and Secret Manager
volume references, so nothing sensitive ever lands in the image or in git.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_", extra="ignore")

    # --- Platform ---------------------------------------------------------
    project_id: str = "local-dev"
    region: str = "us-central1"
    environment: str = "dev"
    service_name: str = "sentinelai-triage"

    # --- AI ---------------------------------------------------------------
    # Flash is ~20x cheaper than Pro and easily strong enough for structured
    # log triage. Escalation to Pro is a future enhancement (see docs/roadmap).
    model_name: str = "gemini-2.5-flash"
    ai_timeout_seconds: float = 25.0
    ai_max_attempts: int = 3
    # Hard cap on log payload sent to the model. Bounds both token spend and
    # the blast radius of prompt injection from attacker-controlled log lines.
    max_log_chars: int = 6000

    # --- Storage ----------------------------------------------------------
    firestore_database: str = "(default)"
    incidents_collection: str = "incidents"
    artifacts_bucket: str = ""

    # --- Triage behaviour -------------------------------------------------
    # Repeat of the same fingerprint inside this window is folded into the
    # existing incident: no second Gemini call, no second page.
    suppression_window_minutes: int = 30
    # Incidents at or below this severity never page a human.
    notify_min_severity: str = "SEV3"

    # --- Notification -----------------------------------------------------
    slack_webhook_url: str = ""
    notifications_enabled: bool = True

    # --- Security ---------------------------------------------------------
    # Cloud Run IAM is the real gate; in-app verification is layer two. See
    # app/auth.py for why the audience is not pinned here by default.
    verify_oidc: bool = True

    # Cloud Run verifies the token signature, expiry and audience before the
    # request reaches this process, so re-verifying here buys no security and
    # costs a network call per request that can fail on its own. Enable this
    # only when running somewhere with no authenticating proxy in front.
    verify_token_signature: bool = False

    # Only meaningful when verify_token_signature is true; Cloud Run already
    # validates the audience against the service URL.
    expected_audience: str = ""

    # Machine endpoints are pinned to exactly one service account each. Operator
    # endpoints (/v1/analyze, /v1/incidents) intentionally have no allowlist —
    # Cloud Run IAM already decides who may call the service at all.
    pubsub_invoker_sa: str = ""
    scheduler_sa: str = ""

    @property
    def pubsub_callers(self) -> list[str]:
        return [s for s in (self.pubsub_invoker_sa.strip(),) if s]

    @property
    def scheduler_callers(self) -> list[str]:
        return [s for s in (self.scheduler_sa.strip(),) if s]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
