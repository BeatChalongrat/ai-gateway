"""MAC address detector.

Matches 6-group colon- or hyphen-separated hex addresses. Rejects
all-zeros and broadcast (FF:FF:FF:FF:FF:FF) since those carry no
privacy risk and are sometimes used as placeholders in documentation.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_MAC_RE = re.compile(
    r"\b([0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5})\b"
)


def _is_trivial(value: str) -> bool:
    """Return True for broadcast / all-zeros / obviously-placeholder values."""
    normalized = value.replace("-", ":").lower()
    return normalized in (
        "00:00:00:00:00:00",
        "ff:ff:ff:ff:ff:ff",
    )


class MacAddressDetector(BaseDetector):
    name = "mac_address"
    category = "network"
    default_sensitivity = SensitivityLevel.LOW

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _MAC_RE.finditer(text):
            value = m.group(1)
            if _is_trivial(value):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="mac_address",
                    category=self.category,
                    start=m.start(1),
                    end=m.end(1),
                    matched_text=value,
                    severity=self.default_sensitivity,
                )
            )
        return matches
