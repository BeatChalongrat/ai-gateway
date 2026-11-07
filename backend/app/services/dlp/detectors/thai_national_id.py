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

# Keyword-context gate. A 13-digit run preceded by any of these
# tokens (with optional separator / colon) is treated as a Thai ID
# even when the check-digit fails — admin / DBA CSV dumps frequently
# contain placeholder or test IDs that don't checksum but are still
# in fields the operator has labelled as ``บัตรประชาชน`` / ``ID:``.
# Better to over-mask here than leak any number sitting in an
# explicitly-labelled ID field.
# Expanded keyword list and digit pattern (real-LLM E2E round 2):
#
# Coverage gaps fixed:
#   1. ``บัตร ปชช`` / ``บัตรปชช`` / ``บัตร ปชช.`` — common Thai
#      abbreviation for ``บัตรประชาชน``. The plain ``บัตรประชาชน``
#      alternative didn't match because of the embedded space + dot.
#   2. ``บัตร ประชาชน`` — same word but with a space between
#      ``บัตร`` and ``ประชาชน``.
#   3. ``เลข ปชช`` / ``เลขปชช`` — short form of ``เลขประจำตัวประชาชน``.
#   4. ``หมายเลขบัตร`` / ``หมายเลข บัตร``.
#   5. ``เลข 13 หลัก`` — informal reference to the NID's digit count.
#   6. ``NID`` / ``N.I.D`` / ``N.I.D.`` — English abbreviation.
#
# Digit pattern (`(?:\d[-\s]?){12}\d`) now allows the canonical
# dashed form (``1-1234-56789-01-0``) and the space-separated form
# (``1 1234 56789 01 0``), not just bare 13-digit runs. The matched
# text still includes any separators — that becomes the masked
# token's source text; restore swaps it back verbatim.
#
# Ordering note: longer alternatives come before shorter prefixes
# (``เลขประจำตัวประชาชน`` before ``เลขประจำตัว``), and the bare
# ``ID`` is last so it doesn't shadow ``national id`` / ``citizen id``.
_KEYWORD_CONTEXT_RE = re.compile(
    r"(?:เลขประจำตัวประชาชน"
    r"|เลขประจำตัว"
    r"|บัตรประจำตัว"
    r"|บัตร\s*ประชาชน"
    r"|บัตร\s*ปชช\.?"
    r"|เลข\s*ปชช\.?"
    r"|หมายเลข\s*บัตร"
    r"|เลข\s*บัตร"
    r"|เลข\s*13\s*หลัก"
    r"|national\s*id"
    r"|citizen\s*id"
    r"|N\.?I\.?D\.?"
    r"|ID)"
    r"\s*[:#.]?\s*"
    r"((?:\d[-\s]?){12}\d)"
    r"(?!\d)",
    re.IGNORECASE,
)


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
        # Track (start, end) so the keyword path doesn't double-emit
        # a span the checksum path has already covered.
        seen_spans: set[tuple[int, int]] = set()

        def _emit(start: int, end: int, raw: str) -> None:
            if (start, end) in seen_spans:
                return
            seen_spans.add((start, end))
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_national_id",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=raw,
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )

        # Gate 1: checksum-validated path (existing behaviour). Catches
        # both the canonical dashed form and any raw 13-digit run whose
        # check digit checks out. Low FP because random 13-digit runs
        # almost never pass the mod-11 weighted sum.
        for m in _THAI_ID_RE.finditer(text):
            raw = m.group(1)
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not _thai_id_valid(digits):
                continue
            _emit(m.start(1), m.end(1), raw)

        # Gate 2: keyword-context path. A 13-digit run preceded by an
        # ID-labelling keyword is masked regardless of checksum — the
        # operator's column header is the strongest possible signal
        # that this IS an ID field, and checksum-invalid placeholders
        # in such fields are still PDPA-sensitive (they identify the
        # row, even if not the canonical citizen).
        for m in _KEYWORD_CONTEXT_RE.finditer(text):
            _emit(m.start(1), m.end(1), m.group(1))

        return matches
