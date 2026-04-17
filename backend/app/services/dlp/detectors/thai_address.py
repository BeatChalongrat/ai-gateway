"""Thai address detector (keyword-anchored).

Thai postal addresses are structured with recognisable leading
keywords (``บ้านเลขที่``, ``ที่อยู่``, …). Rather than try to parse
every address component, we anchor on the leading keyword and span
forward up to 200 characters or a newline — whichever comes first.

This is deliberately low-precision, low-severity (LOW). Addresses
rarely warrant BLOCK action, and over-matching is preferable to
leaking an address into a provider request.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_ADDRESS_RE = re.compile(
    r"(บ้านเลขที่|เลขที่|ที่อยู่|อยู่ที่|ส่งที่)"
    r"[\s:：]*"
    r"([^\n]{3,200}?)"
    r"(?=\n|$|\s{3,})"
)


class ThaiAddressDetector(BaseDetector):
    name = "thai_address"
    category = "pii"
    default_sensitivity = SensitivityLevel.LOW

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _ADDRESS_RE.finditer(text):
            # Match the full span from the keyword through the address body.
            full_start = m.start(1)
            full_end = m.end(2)
            matched = text[full_start:full_end].strip()
            if len(matched) < 6:
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_address",
                    category=self.category,
                    start=full_start,
                    end=full_end,
                    matched_text=matched,
                    severity=self.default_sensitivity,
                )
            )
        return matches
