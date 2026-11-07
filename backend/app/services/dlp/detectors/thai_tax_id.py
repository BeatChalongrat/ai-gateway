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
from app.services.dlp.detectors.thai_national_id import _WEIGHTS

_NUMBER_RE = re.compile(
    r"(?<!\d)(\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d)(?!\d)"
)


def _thai_tax_id_valid(digits: str) -> bool:
    """Same mod-11 weighted checksum as the citizen ID — but corporate
    tax IDs are issued in the ``0xxxxxxxxxxxx`` range, so we do NOT
    reject leading-zero values the way ``_thai_id_valid`` does. The
    keyword gate in ``_has_tax_context`` is what disambiguates a tax
    ID from any other 13-digit run that happens to pass the checksum.
    """
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(int(digits[i]) * _WEIGHTS[i] for i in range(12))
    check = (11 - (total % 11)) % 10
    return int(digits[12]) == check


# Keywords that indicate a tax ID context (Thai + English).
# Extended on 2026-05-18 after the E2E test caught
# ``เลขผู้เสียภาษีบริษัทคือ 0105558123456`` slipping through —
# ``เลขผู้เสียภาษี`` (no ``ประจำตัว`` middle) wasn't in the list,
# nor were the corporate-registration variants.
_TAX_KEYWORDS = (
    "เลขประจำตัวผู้เสียภาษี",
    "เลขผู้เสียภาษี",
    "เลขภาษี",
    "ผู้เสียภาษี",
    "เลขทะเบียนนิติบุคคล",
    "เลขนิติบุคคล",
    "tax id",
    "tax id number",
    "tax number",
    "taxpayer id",
    "taxpayer number",
    "tin",
    "tax registration",
    "vat registration",
)

_CONTEXT_WINDOW = 60

# Stronger gate: the "this number IS a tax ID" labels. When one of
# these appears IMMEDIATELY before the digit run (within 30 chars),
# the user is explicitly labelling the value as a tax/corporate ID
# and we relax the mod-11 checksum gate. This is the only path that
# accepts checksum-invalid IDs — e.g. masked / typo'd / fake test
# values like ``0105558123456`` (E2E audit May 2026, T16).
_STRONG_TAX_LABELS = (
    "เลขประจำตัวผู้เสียภาษี",
    "เลขผู้เสียภาษี",
    "เลขทะเบียนนิติบุคคล",
    "เลขนิติบุคคล",
)
_STRONG_PREFIX_WINDOW = 30


def _has_tax_context(text: str, start: int, end: int) -> bool:
    """Return True iff a tax keyword is within the context window."""
    low = max(0, start - _CONTEXT_WINDOW)
    high = min(len(text), end + _CONTEXT_WINDOW)
    window = text[low:high].lower()
    return any(kw in window for kw in _TAX_KEYWORDS)


def _has_strong_tax_prefix(text: str, start: int) -> bool:
    """Return True iff an explicit tax-ID label sits in the
    ``_STRONG_PREFIX_WINDOW`` chars immediately preceding ``start``.

    Used to relax the checksum gate when the user's intent is
    unambiguous ("เลขผู้เสียภาษีบริษัท 0105558123456"). The wider
    ``_has_tax_context`` window is too permissive for checksum
    bypass — a stray "ภาษี" 60 chars away from a random 13-digit
    number would be enough.
    """
    low = max(0, start - _STRONG_PREFIX_WINDOW)
    window = text[low:start]
    return any(kw in window for kw in _STRONG_TAX_LABELS)


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
            if len(digits) != 13:
                continue
            checksum_ok = _thai_tax_id_valid(digits)
            if checksum_ok:
                if not _has_tax_context(text, m.start(1), m.end(1)):
                    continue
            else:
                # Checksum-invalid path: only fire if a strong
                # tax-ID label sits in the 30 chars immediately
                # before the number. Catches user-supplied fake /
                # masked / typo'd IDs that still need to be redacted.
                if not _has_strong_tax_prefix(text, m.start(1)):
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
