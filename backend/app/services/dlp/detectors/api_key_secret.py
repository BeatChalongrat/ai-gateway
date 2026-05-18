"""Credential / secret detector for patterns not covered by bearer_token.

Complements bearer_token.py (which handles OpenAI `sk-`, AWS access key IDs
`AKIA…`, GitHub, Slack). This one adds:
- Anthropic keys:   sk-ant-api03-<base58>
- JWT tokens:       eyJ<base64>.<base64>.<base64>
- Google API keys:  AIza[A-Za-z0-9_-]{35}
- Generic key=value assignments for password / api_key / secret / token
  / access_key / aws_secret_access_key

All matches are classified category="credential" and severity=CRITICAL so
the strict policy profile blocks them and the default profile masks them.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# Standalone tokens
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        # Long-form Anthropic keys — the canonical 32+ char body length
        # of real production keys. Kept alongside the shorter (20+)
        # ``api03`` / ``admin01`` variants below so this stricter
        # pattern still preferentially matches a real key's full body
        # even when a shorter alternative would also fire (dedup via
        # ``seen_spans`` in ``detect``).
        "anthropic_api_key",
        re.compile(r"\bsk-ant-(?:api\d+-)?[A-Za-z0-9_\-]{32,}\b"),
    ),
    (
        # Newer Anthropic key forms with a known role prefix
        # (``api03``, ``admin01``) and a body of at least 12 chars.
        # The literal ``sk-ant-(api03|admin01)-`` prefix is itself a
        # strong discriminator — anything matching that prefix with
        # ≥12 trailing chars is almost certainly an issued key
        # (full or truncated), not random text.
        "anthropic_api_key",
        re.compile(r"\bsk-ant-(?:api03|admin01)-[A-Za-z0-9_\-]{12,}\b"),
    ),
    (
        # OpenAI project keys (issued via the dashboard's "project"
        # scope) — distinct prefix from the legacy ``sk-`` user keys
        # that ``bearer_token`` handles.
        "openai_project_key",
        re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{12,}\b"),
    ),
    (
        # OpenAI live / org keys — rare in normal traffic but still
        # an issued credential format.
        "openai_live_key",
        re.compile(r"\bsk-live-[A-Za-z0-9_\-]{12,}\b"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"),
    ),
    (
        "jwt_token",
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"
        ),
    ),
]

# Generic `key=value` or `key: "value"` assignments for sensitive field names.
# The value (not the key) is what gets captured and masked.
_DECODED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "anthropic_api_key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}\b"),
    ),
]

_ASSIGNMENT_RE = re.compile(
    r"""(?ix)                               # case-insensitive, verbose
    \b(?:
        password|passwd|pwd
        |api[_\-]?key
        |secret(?:[_\-]?key)?
        |access[_\-]?token
        |aws[_\-]?secret[_\-]?access[_\-]?key
        |auth[_\-]?token
    )
    \s* [:=] \s*
    ["']?                                   # optional opening quote
    (?P<value>[A-Za-z0-9+/_\-]{12,})        # the secret value itself
    """,
)


class APIKeySecretDetector(BaseDetector):
    name = "api_key_secret"
    category = "credential"
    default_sensitivity = SensitivityLevel.CRITICAL
    ACCEPTS_DECODED = True

    def detect(self, text: str, *, from_decoded: bool = False) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        def _emit(entity_type: str, start: int, end: int, matched_text: str) -> None:
            if (start, end) in seen_spans:
                return
            seen_spans.add((start, end))
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type=entity_type,
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=matched_text,
                    severity=self.default_sensitivity,
                )
            )

        for entity_type, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                _emit(entity_type, m.start(), m.end(), m.group())

        if from_decoded:
            for entity_type, pattern in _DECODED_PATTERNS:
                for m in pattern.finditer(text):
                    _emit(entity_type, m.start(), m.end(), m.group())

        for m in _ASSIGNMENT_RE.finditer(text):
            start = m.start("value")
            end = m.end("value")
            _emit("credential_assignment", start, end, m.group("value"))

        return matches
