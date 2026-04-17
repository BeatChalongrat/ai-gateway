"""Bearer token and API key detector.

Matches tokens with well-known prefixes that are highly unlikely to appear
in normal prose.  Each prefix is its own sub-pattern for clarity.

Covered prefixes:
    Bearer <token>        — HTTP Authorization header value
    sk-<base58/62>        — OpenAI API keys
    sk-proj-<base58/62>   — OpenAI project-scoped keys
    AKIA[A-Z0-9]{16}      — AWS access key IDs
    ghp_<alnum>           — GitHub personal access tokens
    gho_<alnum>           — GitHub OAuth tokens
    ghs_<alnum>           — GitHub server tokens
    xoxb-<...>            — Slack bot tokens
    xoxp-<...>            — Slack user tokens

Known limitations:
    - Does not detect arbitrary high-entropy strings (too many false positives).
    - Only matches known prefixed formats.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b", re.ASCII)),
    ("aws_access_key", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    # Excludes `sk-ant-...` so Anthropic keys are handled by the
    # api_key_secret detector (category="credential").
    ("openai_api_key", re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9\-_]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghs)_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[bp]-[A-Za-z0-9\-]{10,}\b")),
]


class BearerTokenDetector(BaseDetector):
    name = "bearer_token"
    category = "secret"
    default_sensitivity = SensitivityLevel.CRITICAL

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for entity_type, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                matches.append(
                    DetectionMatch(
                        detector_name=self.name,
                        entity_type=entity_type,
                        category=self.category,
                        start=m.start(),
                        end=m.end(),
                        matched_text=m.group(),
                        severity=self.default_sensitivity,
                    )
                )
        return matches
