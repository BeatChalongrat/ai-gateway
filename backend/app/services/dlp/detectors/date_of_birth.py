"""Date-of-birth detector (context-gated).

Dates are ubiquitous in prose ("the meeting on 2024-05-01") so this detector
only fires when a date-like token appears within ~30 characters after a DOB
context keyword: "born", "dob", "date of birth", "birthday", "birthdate",
"วันเกิด", "เกิดวันที่".

Accepted shapes (the keyword is not part of the masked span):
- DD/MM/YYYY or MM/DD/YYYY  (slashes, dots, or hyphens)
- YYYY-MM-DD                 (ISO)
- Month DD, YYYY             ("May 15, 1990" / "15 May 1990")
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_MONTHS = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
)

_DATE_ALT = (
    rf"(?:\d{{1,2}}[/\-.]\d{{1,2}}[/\-.]\d{{2,4}}"   # 15/05/1990
    rf"|\d{{4}}-\d{{1,2}}-\d{{1,2}}"                  # 1990-05-15
    rf"|{_MONTHS}\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}}"    # May 15, 1990
    rf"|\d{{1,2}}\s+{_MONTHS}\s+\d{{4}})"             # 15 May 1990
)

_DOB_RE = re.compile(
    rf"(?:born|d\.?o\.?b\.?|date\s*of\s*birth|birthday|birthdate|วันเกิด|เกิดวันที่)"
    rf"(?:\s+(?:on|in|at|is|was|=|:))?"
    rf"[\s:=\-]{{0,5}}"
    rf"(?P<value>{_DATE_ALT})",
    re.IGNORECASE,
)


class DateOfBirthDetector(BaseDetector):
    name = "date_of_birth"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _DOB_RE.finditer(text):
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="date_of_birth",
                    category=self.category,
                    start=m.start("value"),
                    end=m.end("value"),
                    matched_text=m.group("value"),
                    severity=self.default_sensitivity,
                )
            )
        return matches
