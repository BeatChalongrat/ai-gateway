"""Thai bank account detector (context-gated).

Thai bank account numbers are 10-12 digits and collide with phone
numbers, order IDs, and arbitrary identifiers. A context keyword is
required to keep false positives acceptable — the 60-char window is
narrow enough that it needs to appear alongside the number, not just
somewhere in the conversation.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# 10-12 digit account numbers, with optional dashes/spaces between groups.
_ACCT_RE = re.compile(
    r"(?<!\d)(\d{3}[-\s]?\d[-\s]?\d{5}[-\s]?\d{1,3})(?!\d)"
)

_ACCOUNT_KEYWORDS = (
    "บัญชี",
    "เลขบัญชี",
    "บัญชีเลขที่",
    "ธนาคาร",
    "account no",
    "account number",
    "bank account",
    "acc#",
    "acct",
)

_CONTEXT_WINDOW = 60


def _has_account_context(text: str, start: int, end: int) -> bool:
    low = max(0, start - _CONTEXT_WINDOW)
    high = min(len(text), end + _CONTEXT_WINDOW)
    window = text[low:high].lower()
    return any(kw in window for kw in _ACCOUNT_KEYWORDS)


class ThaiBankAccountDetector(BaseDetector):
    name = "thai_bank_account"
    category = "financial"
    default_sensitivity = SensitivityLevel.HIGH
    # Context-gated (requires a bank keyword) — more specific than the
    # generic phone detector that matches bare 10-12 digit runs.
    priority = 10

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _ACCT_RE.finditer(text):
            raw = m.group(1)
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not (10 <= len(digits) <= 12):
                continue
            if not _has_account_context(text, m.start(1), m.end(1)):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_bank_account",
                    category=self.category,
                    start=m.start(1),
                    end=m.end(1),
                    matched_text=raw,
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )
        return matches
