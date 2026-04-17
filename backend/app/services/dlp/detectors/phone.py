"""Phone number detector.

Matches common phone formats with explicit separators or grouping to
reduce false positives on arbitrary digit sequences.  Requires at least
10 digits (with optional country code prefix).

Supported formats:
    +1-555-123-4567   +66 81 234 5678   (555) 123-4567
    555-123-4567      555.123.4567       5551234567 (10+ digits at word boundary)

Known limitations:
    - US/international bias.  May miss formats from some countries.
    - A bare 10-digit number at a word boundary will match, which may
      false-positive on account numbers.  Acceptable for MVP.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

# Matches phone-like sequences with separators, or bare 10+ digits at word boundary.
_PHONE_RE = re.compile(
    r"(?<!\d)"           # no digit before
    r"(?:"
    r"\+?\d{1,3}"        # optional country code
    r"[\s.\-]?"          # optional separator
    r")?"
    r"(?:"
    r"\(?\d{2,4}\)?"     # area code (optionally in parens)
    r"[\s.\-]"           # separator required after area code
    r"\d{3,4}"           # first group
    r"[\s.\-]"           # separator
    r"\d{3,4}"           # second group
    r"|"
    r"\b\d{10,15}\b"    # or bare 10-15 digit sequence at word boundary
    r")"
    r"(?!\d)",           # no digit after
)


class PhoneDetector(BaseDetector):
    name = "phone"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _PHONE_RE.finditer(text):
            # Require at least 10 digits total to filter short matches
            digits = sum(1 for c in m.group() if c.isdigit())
            if digits < 10:
                continue
            # Reject when the match is immediately followed by more phone-like
            # digits via a separator — this indicates the span is actually part
            # of a longer run like a 16-digit credit card number.
            tail = text[m.end():m.end() + 2]
            if len(tail) == 2 and tail[0] in " -." and tail[1].isdigit():
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="phone_number",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )
        return matches
