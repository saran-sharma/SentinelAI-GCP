"""Deterministic incident fingerprinting.

The single most valuable thing this platform does is decide that 4,000 log
lines are *one* incident. Fingerprinting normalises away the parts of an error
that vary per-occurrence (ids, timestamps, addresses, durations) and hashes
what remains, so the same failure mode always lands on the same document.

That collapse is what makes the AI spend bounded: Gemini is called once per
distinct failure mode per suppression window, not once per log line.
"""

from __future__ import annotations

import hashlib
import re

from app.models import NormalizedEvent

# Order matters: broadest-but-safest substitutions run last.
_NORMALISERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<hash>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
    (re.compile(r"https?://[^\s\"']+"), "<url>"),
    # Kubernetes pod suffixes: api-server-7d4f9b8c6d-x2klm
    (re.compile(r"-[0-9a-z]{8,10}-[0-9a-z]{5}\b"), "-<pod>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|MB|GB|KB|%)\b"), "<qty>"),
    (re.compile(r"\b\d{3,}\b"), "<num>"),
]

_WHITESPACE = re.compile(r"\s+")


def normalise_message(message: str, max_len: int = 240) -> str:
    """Strip per-occurrence variance out of an error string."""
    text = message.strip()
    for pattern, replacement in _NORMALISERS:
        text = pattern.sub(replacement, text)
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return text[:max_len]


def compute_fingerprint(event: NormalizedEvent) -> str:
    """Stable 16-char id for a failure mode.

    Keyed on service + resource type + normalised message. Severity is
    deliberately excluded: the same bug flapping between WARNING and ERROR is
    still the same bug, and splitting it would defeat suppression.
    """
    basis = "|".join(
        [
            event.service.strip().lower(),
            event.resource_type.strip().lower(),
            normalise_message(event.message),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
