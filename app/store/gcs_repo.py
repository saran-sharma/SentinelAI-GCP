"""Cloud Storage archive for digests and incident postmortems."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)


class ArtifactStore:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client(project=self._settings.project_id)
        return self._client

    def write_markdown(self, path: str, content: str) -> str | None:
        """Best-effort archive write. A failed archive must never fail triage."""
        bucket_name = self._settings.artifacts_bucket
        if not bucket_name:
            logger.info("artifact_store_disabled", extra={"path": path})
            return None
        try:
            blob = self.client.bucket(bucket_name).blob(path)
            blob.upload_from_string(content, content_type="text/markdown; charset=utf-8")
            return f"gs://{bucket_name}/{path}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact_write_failed", extra={"error": str(exc), "path": path})
            return None

    @staticmethod
    def digest_path(now: datetime | None = None) -> str:
        stamp = (now or datetime.now(UTC)).strftime("%Y/%m/%d/digest-%H%M")
        return f"digests/{stamp}.md"

    @staticmethod
    def postmortem_path(fingerprint: str, now: datetime | None = None) -> str:
        stamp = (now or datetime.now(UTC)).strftime("%Y/%m/%d")
        return f"postmortems/{stamp}/{fingerprint}.md"
