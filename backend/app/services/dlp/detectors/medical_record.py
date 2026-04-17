"""Medical record number detector (context-gated).

Medical record / patient IDs are 6-10 digits in Thai hospitals and
have no fixed prefix. A bare 6-digit number is far too common to
match unconditionally, so the detector requires a medical keyword
(HN, MRN, OPD, ``หมายเลขผู้ป่วย`` …) within 60 characters before the
number.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_KEYWORDS = (
    "mrn",
    "medical record no",
    "medical record number",
    "patient id",
    "patient no",
    "patient number",
    "hn",
    "opd",
    "เลข hn",
    "หมายเลขผู้ป่วย",
    "เลขที่ผู้ป่วย",
)

_CONTEXT_WINDOW = 60

# 6-10 digit run at word boundary. Require not-digit before/after so we
# don't chunk into the middle of a longer number (phone, credit card).
_NUMBER_RE = re.compile(r"(?<!\d)(\d{6,10})(?!\d)")

# Keyword search must use word boundaries, not plain substring. The
# original ``kw in window`` check fired ``"hn"`` on every word
# containing that bigram ("john", "johnson", "henderson", ...) and
# ``"mrn"`` on "mrna". Joining the keywords into a single anchored
# alternation gives one regex pass per window and kills those false
# positives. Unicode-aware ``\b`` handles the Thai keywords too —
# Thai characters are word characters for the Python re engine, so
# surrounding spaces / punctuation still act as boundaries.
_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _has_medical_context(text: str, start: int) -> bool:
    """Return True iff a medical keyword appears in the lead-in window.

    Searches the full ``text`` (not a slice) so ``\\b`` word boundaries
    see the real surrounding characters. Slicing to ``text[low:start]``
    would create a false boundary at the slice edge — ``"hn"`` in
    ``"hn123456"`` would match because the slice ends at ``"hn"`` and
    the regex engine sees "end of string" as a boundary, even though
    the real next character is a digit (not a boundary).

    The filter in Python keeps only matches whose end ≤ ``start``
    (keyword lies before the number) and whose start ≥ ``low``
    (keyword is within the context window).
    """
    low = max(0, start - _CONTEXT_WINDOW)
    for m in _KEYWORD_RE.finditer(text):
        if m.start() >= start:
            # Past the number — no later match can be "before".
            break
        if m.end() <= start and m.start() >= low:
            return True
    return False


class MedicalRecordDetector(BaseDetector):
    name = "medical_record"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH
    # Keyword-gated. Without priority, the generic phone detector
    # would win on any 10-digit patient number. A 6-digit MRN is
    # below phone's threshold, but 10-digit MRNs would overlap.
    priority = 10

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _NUMBER_RE.finditer(text):
            if not _has_medical_context(text, m.start(1)):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="medical_record",
                    category=self.category,
                    start=m.start(1),
                    end=m.end(1),
                    matched_text=m.group(1),
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )
        return matches
