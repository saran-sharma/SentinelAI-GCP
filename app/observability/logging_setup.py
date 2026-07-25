"""Structured JSON logging wired to Cloud Logging's expected field names.

Emitting the right keys to stdout (`severity`, `logging.googleapis.com/trace`)
means Cloud Run's agent picks logs up already parsed and trace-correlated —
no logging agent, no sidecar, no client library on the hot path.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

trace_context: ContextVar[str] = ContextVar("trace_context", default="")

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)

_LEVEL_TO_SEVERITY = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}


class CloudLoggingFormatter(logging.Formatter):
    def __init__(self, project_id: str, service: str) -> None:
        super().__init__()
        self._project_id = project_id
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": _LEVEL_TO_SEVERITY.get(record.levelname, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
            "service": self._service,
        }

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value

        if trace_id := trace_context.get():
            entry["logging.googleapis.com/trace"] = f"projects/{self._project_id}/traces/{trace_id}"

        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def configure_logging(project_id: str, service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingFormatter(project_id, service))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn's own access log duplicates our request middleware.
    logging.getLogger("uvicorn.access").disabled = True


def extract_trace_id(header: str | None) -> str:
    """Pull the trace id out of an X-Cloud-Trace-Context header."""
    if not header:
        return ""
    return header.split("/")[0].strip()
