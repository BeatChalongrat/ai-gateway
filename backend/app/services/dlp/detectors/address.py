"""Labeled postal address detector.

High-precision detector for address fields. It intentionally requires an
address label and an address-like value, so ordinary prose such as
"I walked on Sukhumvit Road" does not match.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_LABEL = (
    r"(?:"
    r"(?:home\s+|billing\s+|shipping\s+)?address"
    r"|"
    r"(?:\u0e17\u0e35\u0e48\u0e2d\u0e22\u0e39\u0e48"
    r"(?:\u0e08\u0e31\u0e14\u0e2a\u0e48\u0e07|"
    r"\u0e15\u0e32\u0e21\u0e1a\u0e31\u0e15\u0e23\u0e1b\u0e23\u0e30\u0e0a\u0e32\u0e0a\u0e19)?)"
    r")"
)

_ADDRESS_RE = re.compile(
    rf"(?P<label>{_LABEL})\s*[:\uff1a]\s*(?P<value>[^\n\r]{{6,180}})",
    re.IGNORECASE,
)

_ADDRESS_LIKE_RE = re.compile(
    r"(?:"
    r"\d{1,5}\s*/\s*\d{1,5}|\b\d{1,5}\b"
    r").{0,120}(?:"
    r"\b(?:road|rd\.?|street|st\.?|avenue|ave\.?|lane|soi|rama)\b"
    r"|\b(?:bangkok|huai\s+khwang|sukhumvit)\b"
    r"|[\u0e01-\u0e5b]*(?:\u0e16\u0e19\u0e19|\u0e0b\u0e2d\u0e22|\u0e2a\u0e38\u0e02\u0e38\u0e21\u0e27\u0e34\u0e17|\u0e01\u0e23\u0e38\u0e07\u0e40\u0e17\u0e1e)"
    r"|\b\d{5}\b"
    r")",
    re.IGNORECASE,
)


class AddressDetector(BaseDetector):
    name = "address"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _ADDRESS_RE.finditer(text):
            value = m.group("value").strip()
            if not _ADDRESS_LIKE_RE.search(value):
                continue
            start = m.start("value") + (len(m.group("value")) - len(m.group("value").lstrip()))
            end = start + len(value)
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="address",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=value,
                    severity=self.default_sensitivity,
                )
            )
        return matches
