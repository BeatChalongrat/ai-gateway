"""Passport number detector (multi-country, context-gated).

Passport numbers are hard to detect in isolation — every country has its
own length and character mix, and most patterns overlap with ordinary
alphanumeric strings. This detector requires a **context keyword** within
~40 characters before the candidate (e.g. "passport", "passport number",
"หนังสือเดินทาง") to dramatically reduce false positives.

Supported shapes:
- 1 letter + 7 digits   (Thai, many EU)
- 2 letters + 7 digits  (Thai new, Indonesia, several others)
- 9 digits              (US passport book)
- 1 letter + 8 digits   (UK, Canada, Australia)
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_CONTEXT_RE = re.compile(
    # Keyword set covers English (``passport``, ``passport number``),
    # the colloquial Thai transliteration (``พาสปอร์ต``), the formal
    # Thai noun (``หนังสือเดินทาง``) optionally prefixed with the
    # word ``เลข`` (literally "number-of"), the short-form ``pp no``,
    # and the generic ``travel document``. Adding these closes the
    # audit's Thai-prefixed FN gap.
    r"(?:passport(?:\s*(?:number|no\.?))?"
    r"|เลขหนังสือเดินทาง"
    r"|หนังสือเดินทาง"
    r"|พาสปอร์ต"
    r"|travel\s*document"
    r"|pp\s*no)"
    # Zero-or-more joiner tokens so combinations like "number:" work.
    # With a single-optional group, "passport number: AA1234567" would
    # consume "number" but then fail on the following colon.
    r"(?:\s*(?:is|=|:|number|no\.?|#))*"
    r"\s*"
    r"(?P<value>"
    # Broadened from \d{7,8} to \d{6,9}: real-world passport numbers
    # range 6-9 digits (diplomatic 6, common 7-8, US/UK 9). Keyword
    # gating above keeps FP risk negligible — random alphanumeric
    # runs without a passport-context word are still ignored.
    r"[A-Z]{1,2}\d{6,9}"     # 1-2 letters + 6-9 digits
    r"|\d{9}"                # US bare 9-digit
    r")",
    re.IGNORECASE,
)


class PassportDetector(BaseDetector):
    name = "passport"
    category = "identity"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _CONTEXT_RE.finditer(text):
            value = m.group("value")
            start = m.start("value")
            end = m.end("value")
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="passport_number",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=value,
                    severity=self.default_sensitivity,
                )
            )
        return matches
