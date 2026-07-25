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
    # Cloud Run IAM is the real gate; in-app OIDC verification is layer two.
    verify_oidc: bool = True
    expected_audience: str = ""
    allowed_invoker_sas: str = ""

    @property
    def allowed_invoker_list(self) -> list[str]:
        return [s.strip() for s in self.allowed_invoker_sas.split(",") if s.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
