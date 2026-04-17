"""Thai tax ID detector (context-gated, checksum-validated).

Same shape + checksum as ``thai_national_id``, but disambiguated by
requiring a tax-related keyword within ~60 characters before or after
the number. Without the gate, Thai national IDs and tax IDs would
double-mask or collide semantically.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector
from app.services.dlp.detectors.thai_national_id import _thai_id_valid

_NUMBER_RE = re.compile(
    r"(?<!\d)(\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d)(?!\d)"
)

# Keywords that indicate a tax ID context (Thai + English).
_TAX_KEYWORDS = (
    "เลขประจำตัวผู้เสียภาษี",
    "เลขภาษี",
    "ผู้เสียภาษี",
    "tax id",
    "taxpayer id",
    "tin",
    "tax number",
)

_CONTEXT_WINDOW = 60


def _has_tax_context(text: str, start: int, end: int) -> bool:
    """Return True iff a tax keyword is within the context window."""
    low = max(0, start - _CONTEXT_WINDOW)
    high = min(len(text), end + _CONTEXT_WINDOW)
    window = text[low:high].lower()
    return any(kw in window for kw in _TAX_KEYWORDS)


class ThaiTaxIdDetector(BaseDetector):
    name = "thai_tax_id"
    category = "identity"
    default_sensitivity = SensitivityLevel.HIGH
    # Checksum + keyword-gated. Beats generic digit matchers (phone)
    # on the same span. Same priority as thai_national_id — they match
    # identical 13-digit shapes and are disambiguated by the keyword
    # gate, not the overlap resolver.
    priority = 10

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _NUMBER_RE.finditer(text):
            raw = m.group(1)
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not _thai_id_valid(digits):
                continue
            if not _has_tax_context(text, m.start(1), m.end(1)):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_tax_id",
                    category=self.category,
                    start=m.start(1),
                    end=m.end(1),
                    matched_text=raw,
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )
        return matches
