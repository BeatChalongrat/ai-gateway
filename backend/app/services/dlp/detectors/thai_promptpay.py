"""Thai PromptPay identifier detector (context-gated).

PromptPay IDs can be either a 10-digit Thai mobile number or a
13-digit Thai national ID. We reuse the same shape as phone / Thai ID
but require a PromptPay keyword within 80 chars so we don't
double-mask numbers that already have their own detector coverage.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector
from app.services.dlp.detectors.thai_national_id import _thai_id_valid


# Matches either a 10-digit Thai mobile (starts 0) or a 13-digit ID.
_PROMPTPAY_RE = re.compile(
    r"(?<!\d)("
    r"0\d{9}"                                  # 10-digit mobile
    r"|\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d"  # 13-digit national ID
    r")(?!\d)"
)

_KEYWORDS = (
    "พร้อมเพย์",
    "พร้อมเพย์เบอร์",
    "promptpay",
)

_CONTEXT_WINDOW = 80


def _has_context(text: str, start: int, end: int) -> bool:
    low = max(0, start - _CONTEXT_WINDOW)
    high = min(len(text), end + _CONTEXT_WINDOW)
    window = text[low:high].lower()
    return any(kw in window for kw in _KEYWORDS)


class ThaiPromptPayDetector(BaseDetector):
    name = "thai_promptpay"
    category = "financial"
    default_sensitivity = SensitivityLevel.MEDIUM
    # Keyword-gated — more specific than phone on the same 10-digit
    # mobile / 13-digit ID span.
    priority = 10

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _PROMPTPAY_RE.finditer(text):
            if not _has_context(text, m.start(1), m.end(1)):
                continue
            # 13-digit PromptPay IDs share the Thai national ID shape.
            # Reject mod-11 check failures to avoid flagging random
            # 13-digit sequences that happen to sit near the keyword.
            digits = "".join(ch for ch in m.group(1) if ch.isdigit())
            if len(digits) == 13 and not _thai_id_valid(digits):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_promptpay",
                    category=self.category,
                    start=m.start(1),
                    end=m.end(1),
                    matched_text=m.group(1),
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )
        return matches
