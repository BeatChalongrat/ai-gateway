"""IBAN detector (ISO 13616, mod-97 checksum validated).

Matches 2-letter country code + 2 check digits + 11-30 alphanumeric chars,
then runs the mod-97 checksum to reject random strings matching the shape.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# 4 fixed header chars (CC + 2 digits) followed by 11-30 alphanumerics,
# allowing embedded spaces in 4-char groups (common when users type them).
_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b"
)


def _mod97(iban: str) -> int:
    """Compute the ISO 13616 mod-97 residue."""
    rearranged = iban[4:] + iban[:4]
    digits = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        else:
            # A=10, B=11, ... Z=35
            digits.append(str(ord(ch) - 55))
    big = "".join(digits)
    residue = 0
    # Process in chunks to avoid huge int conversion in degenerate cases
    for i in range(0, len(big), 9):
        residue = int(str(residue) + big[i:i + 9]) % 97
    return residue


class IBANDetector(BaseDetector):
    name = "iban"
    category = "financial"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _IBAN_RE.finditer(text):
            raw = m.group()
            normalized = raw.replace(" ", "").upper()
            if len(normalized) < 15 or len(normalized) > 34:
                continue
            if not normalized[:2].isalpha() or not normalized[2:4].isdigit():
                continue
            try:
                if _mod97(normalized) != 1:
                    continue
            except Exception:
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="iban",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=raw,
                    severity=self.default_sensitivity,
                )
            )
        return matches
