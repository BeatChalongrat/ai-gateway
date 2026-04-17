"""Email address detector."""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

# RFC 5322-ish pattern.  Intentionally excludes very unusual local parts
# (quoted strings, comments) to reduce false positives.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)


class EmailDetector(BaseDetector):
    name = "email"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _EMAIL_RE.finditer(text):
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="email_address",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )
        return matches
