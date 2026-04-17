"""Thai national ID detector (checksum-validated).

Thai national IDs are 13 digits, optionally displayed as
``X-XXXX-XXXXX-XX-X``. The last digit is a modulus-11 check digit
computed from the first 12 under the weight sequence
[13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2]. The checksum keeps false
positives low — arbitrary 13-digit runs from prose or other IDs
almost never validate.

Rejection rules:
- Numbers starting with 0 are not valid Thai IDs.
- Any number failing the checksum is ignored.

The ``thai_tax_id`` detector uses the SAME pattern + checksum but is
context-gated. See that file for the distinction.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_THAI_ID_RE = re.compile(
    r"(?<!\d)(\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d)(?!\d)"
)
_WEIGHTS = (13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2)


def _thai_id_valid(digits: str) -> bool:
    """Return True iff *digits* (exactly 13 digits) passes the Thai ID checksum."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    if digits[0] == "0":
        return False
    total = sum(int(digits[i]) * _WEIGHTS[i] for i in range(12))
    check = (11 - (total % 11)) % 10
    return int(digits[12]) == check


class ThaiNationalIdDetector(BaseDetector):
    name = "thai_national_id"
    category = "identity"
    default_sensitivity = SensitivityLevel.HIGH
    # 13-digit + checksum is far more specific than phone.py's generic
    # 10-15 digit run — raise priority so the transformer picks this
    # detector's mask token on the same span.
    priority = 10

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _THAI_ID_RE.finditer(text):
            raw = m.group(1)
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not _thai_id_valid(digits):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_national_id",
                    category=self.category,
                    start=m.start(1),
                    end=m.end(1),
                    matched_text=raw,
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )
        return matches
