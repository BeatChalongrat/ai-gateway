"""Labeled salary / income detector.

Detects personal income only when a salary/income label is present. This
avoids classifying normal product prices such as "costs 500 baht".
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_LABEL = (
    r"(?:"
    r"salary|income|monthly\s+income|compensation|wage|wages|pay"
    r"|"
    # Thai labels: \u0e23\u0e32\u0e22\u0e44\u0e14\u0e49, \u0e40\u0e07\u0e34\u0e19\u0e40\u0e14\u0e37\u0e2d\u0e19, \u0e23\u0e32\u0e22\u0e23\u0e31\u0e1a\u0e15\u0e48\u0e2d\u0e40\u0e14\u0e37\u0e2d\u0e19, \u0e04\u0e48\u0e32\u0e08\u0e49\u0e32\u0e07,
    # \u0e04\u0e48\u0e32\u0e15\u0e2d\u0e1a\u0e41\u0e17\u0e19. Written as unicode escapes so the source file
    # diffs cleanly in editors that don't render Thai glyphs.
    r"\u0e23\u0e32\u0e22\u0e44\u0e14\u0e49|"
    r"\u0e40\u0e07\u0e34\u0e19\u0e40\u0e14\u0e37\u0e2d\u0e19|"
    r"\u0e23\u0e32\u0e22\u0e23\u0e31\u0e1a\u0e15\u0e48\u0e2d\u0e40\u0e14\u0e37\u0e2d\u0e19|"
    r"\u0e04\u0e48\u0e32\u0e08\u0e49\u0e32\u0e07|"
    r"\u0e04\u0e48\u0e32\u0e15\u0e2d\u0e1a\u0e41\u0e17\u0e19"
    r")"
)

# Optional Thai possessive pronouns the speaker may put between the
# label and the value: \u0e09\u0e31\u0e19 / \u0e02\u0e2d\u0e07\u0e09\u0e31\u0e19 / \u0e1c\u0e21 / \u0e2b\u0e19\u0e39 / \u0e40\u0e23\u0e32. Without this
# the regex required pure whitespace there, so a casual "\u0e40\u0e07\u0e34\u0e19\u0e40\u0e14\u0e37\u0e2d\u0e19
# \u0e09\u0e31\u0e19 85,000 \u0e1a\u0e32\u0e17" never fired. Group is non-capturing so the
# ``value`` span boundaries stay correct.
_OPTIONAL_PRONOUN = (
    r"(?:"
    r"\s*(?:\u0e09\u0e31\u0e19|\u0e02\u0e2d\u0e07\u0e09\u0e31\u0e19"
    r"|\u0e1c\u0e21|\u0e2b\u0e19\u0e39|\u0e40\u0e23\u0e32"
    r"|my|of\s+mine)"
    r")?"
)

_INCOME_RE = re.compile(
    rf"(?P<label>{_LABEL}){_OPTIONAL_PRONOUN}\s*[:\uff1a]?\s*"
    r"(?P<value>(?:THB\s*)?\d{2,3}(?:,\d{3})+|\d{4,9})"
    r"\s*(?:THB|baht|\u0e1a\u0e32\u0e17)?"
    r"(?:\s*(?:/|per\s+|\u0e15\u0e48\u0e2d)?(?:month|monthly|\u0e40\u0e14\u0e37\u0e2d\u0e19))?",
    re.IGNORECASE,
)


class IncomeDetector(BaseDetector):
    name = "income"
    category = "financial"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _INCOME_RE.finditer(text):
            value = m.group("value")
            if not value:
                continue
            # Mask only the value (plus any trailing unit/period
            # captured by the tail alternations) — the label
            # ``Salary:`` / ``Income:`` stays visible so the audit
            # trail still reads as a salary line rather than a bare
            # ``[INCOME_0]`` placeholder.
            start = m.start("value")
            end = m.end()
            matched = text[start:end].strip()
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="income",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=matched,
                    severity=self.default_sensitivity,
                )
            )
        return matches
