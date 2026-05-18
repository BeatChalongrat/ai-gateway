"""Date-of-birth detector (context-gated).

Dates are ubiquitous in prose ("the meeting on 2024-05-01") so this detector
only fires when a date-like token appears within ~30 characters after a DOB
context keyword: "born", "dob", "date of birth", "birthday", "birthdate",
"วันเกิด", "เกิดวันที่", "เกิด".

Accepted shapes (the keyword is not part of the masked span):
- DD/MM/YYYY or MM/DD/YYYY  (slashes, dots, or hyphens)
- YYYY-MM-DD                 (ISO)
- Month DD, YYYY             ("May 15, 1990" / "15 May 1990")
- DD <Thai-month> YYYY       ("15 มีนาคม 2532" — Buddhist or Gregorian year)
- DD <Thai-month-abbrev> YY  ("15 มี.ค. 32" — short form, optional dots)

Thai keywords use a negative lookbehind ``(?<![฀-๿])`` so the bare
"เกิด" keyword does not match as a substring of compound Thai words
like ``เหตุเกิด`` ("incident occurred") or ``ประชุมเกิดวันที่``
("meeting took place on"). English keywords are left as-is.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_MONTHS = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
)

_THAI_MONTHS = (
    r"(?:มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|"
    r"กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)"
)

# Abbreviated Thai month forms with optional dots: "มี.ค.", "มี.ค", "มีค".
_THAI_MONTHS_SHORT = (
    r"(?:ม\.?ค\.?|ก\.?พ\.?|มี\.?ค\.?|เม\.?ย\.?|พ\.?ค\.?|มิ\.?ย\.?|"
    r"ก\.?ค\.?|ส\.?ค\.?|ก\.?ย\.?|ต\.?ค\.?|พ\.?ย\.?|ธ\.?ค\.?)"
)

_DATE_ALT = (
    rf"(?:\d{{1,2}}[/\-.]\d{{1,2}}[/\-.]\d{{2,4}}"      # 15/05/1990
    rf"|\d{{4}}-\d{{1,2}}-\d{{1,2}}"                     # 1990-05-15
    rf"|{_MONTHS}\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}}"       # May 15, 1990
    rf"|\d{{1,2}}\s+{_MONTHS}\s+\d{{4}}"                 # 15 May 1990
    rf"|\d{{1,2}}\s+{_THAI_MONTHS}\s+\d{{4}}"            # 15 มีนาคม 2532
    rf"|\d{{1,2}}\s+{_THAI_MONTHS_SHORT}\s+\d{{2,4}})"   # 15 มี.ค. 32
)

_DOB_RE = re.compile(
    rf"(?:"
    rf"born|d\.?o\.?b\.?|date\s*of\s*birth|birthday|birthdate"
    # Thai keywords need a non-Thai-letter prefix so 'เกิด' / 'เกิดวันที่'
    # / 'วันเกิด' as substrings of compound Thai words like
    # ``เหตุเกิด`` / ``ประชุมเกิดวันที่`` / ``อุบัติเหตุเกิด`` are NOT
    # treated as DOB keywords. The regression tests in
    # ``test_dlp_dob_thai.TestDobNegativeCases`` lock this contract
    # in; relaxing the lookbehind here re-introduces those false
    # positives. A patient real-prose case like
    # ``ลูกค้าเกิดวันที่ 12/05/1998`` is the cost — it falls back to
    # NER/thai_name to catch the person component, which is what
    # masks the broader sentence.
    rf"|(?<![฀-๿])(?:วันเกิด|เกิดวันที่|เกิด)"
    rf")"
    rf"(?:\s+(?:on|in|at|is|was|=|:))?"
    rf"[\s:=\-]{{0,5}}"
    rf"(?P<value>{_DATE_ALT})",
    re.IGNORECASE,
)


class DateOfBirthDetector(BaseDetector):
    name = "date_of_birth"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

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
