"""Credit card number detector (Luhn-validated).

Matches 13-19 digit sequences that are either contiguous or grouped in
common delimiter patterns (spaces or hyphens). Applies a Luhn checksum to
reject arbitrary digit runs (phone numbers, order IDs, etc.).

Emits category="financial" so strict policy profiles can BLOCK these
outright, per the Strict policy's financial → BLOCK rule.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn_valid(digits: str) -> bool:
    """Standard Luhn mod-10 check. Input must be digits only."""
    total = 0
    alt = False
    for ch in reversed(digits):
        n = ord(ch) - 48
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


class CreditCardDetector(BaseDetector):
    name = "credit_card"
    category = "financial"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _CARD_RE.finditer(text):
            raw = m.group()
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not (13 <= len(digits) <= 19):
                continue
            if not _luhn_valid(digits):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="credit_card_number",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=raw,
                    severity=self.default_sensitivity,
                )
            )
        return matches
