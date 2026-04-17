"""US Social Security Number detector.

Matches the canonical XXX-XX-XXXX format with basic area-number validation:
- Area number (first 3 digits) must not be 000, 666, or 900-999.
- Group number (middle 2 digits) must not be 00.
- Serial number (last 4 digits) must not be 0000.

Requires dashes.  The bare 9-digit form (123456789) is intentionally NOT
matched to avoid false positives on account numbers, zip codes, etc.

Known limitations:
    - US SSN only.
    - Does not detect SSNs written with spaces (123 45 6789).
    - Does not catch the advertising SSN 078-05-1120.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

# Basic SSN pattern with dashes: XXX-XX-XXXX
_SSN_RE = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")


class SSNDetector(BaseDetector):
    name = "ssn"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _SSN_RE.finditer(text):
            area, group, serial = m.group(1), m.group(2), m.group(3)
            # Validate per SSA rules
            if area == "000" or area == "666" or area[0] == "9":
                continue
            if group == "00":
                continue
            if serial == "0000":
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="us_ssn",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )
        return matches
