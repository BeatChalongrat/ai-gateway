"""Health condition detector — PDPA Section 26 sensitive data.

Context-keyword gated: triggers only when a health/medical keyword
appears near a personal reference. General medical discussions
(e.g. "Diabetes is a common disease") do NOT trigger.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_HEALTH_KEYWORDS_RE = re.compile(
    r"(?:"
    # Thai medical/health keywords
    r"ป่วยเป็น|เป็นโรค|ผ่าตัด|แพ้ยา|ผลเลือด|ผลตรวจ|"
    r"ตั้งครรภ์|แท้ง(?:ลูก)?|พิการ|จิตเวช|ซึมเศร้า|"
    r"ติดเชื้อ|วินิจฉัย|รักษา(?:ตัว)?|"
    # Common disease names (Thai)
    r"เบาหวาน|มะเร็ง|ความดัน(?:สูง|ต่ำ)?|หัวใจ|ไต(?:วาย|เรื้อรัง)|"
    r"เอดส์|HIV|โควิด|วัณโรค|หอบหืด|ลมชัก|ไทรอยด์|"
    r"โรงพยาบาล(?:.{0,20})(?:ให้ยา|รักษา|ผ่าตัด|นอน|ตรวจ)|"
    r"(?:ผม|ฉัน|เขา|เธอ)(?:.{0,10})(?:โรค|ป่วย|ผ่าตัด|แพ้|เป็น)|"
    # English with personal context built-in
    r"(?:I|he|she|they|my|his|her)\s+(?:have|has|had|got|was\s+diagnosed\s+with|suffer(?:s|ed)?\s+from|"
    r"tested\s+positive|am\s+(?:HIV|diabetic|pregnant|disabled))|"
    r"(?:my|his|her|their)\s+(?:diagnosis|condition|disease|treatment|prescription|disability|"
    r"medical\s+(?:record|history|condition)|mental\s+health|blood\s+test)|"
    r"(?:patient|diagnosed)\s+(?:with|as)|"
    r"(?:I|he|she|they)\s+(?:was|were)\s+diagnosed\s+with\s+\w+"
    r")",
    re.IGNORECASE,
)

_PERSONAL_CONTEXT_RE = re.compile(
    r"(?:"
    r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|ลูก|พ่อ|แม่|สามี|ภรรยา|"
    r"ข้าพเจ้า|กระผม|หนู|คนไข้|ผู้ป่วย|"
    r"ปัจจุบัน|ตอนนี้|ขณะนี้|ล่าสุด|ผลตรวจ|ประวัติ|รายงาน|ข้อมูล|พนักงาน|สมาชิก|ลูกค้า|"
    r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b|"
    r"\bpatient\b|\bclient\b|\bcurrently\b|\bemployee\b|\bmember\b|"
    r"\breport\b|\brecord\b|\bhistory\b"
    r")",
    re.IGNORECASE,
)

_CONTEXT_WINDOW = 150

# ``value`` is a tempered greedy capture: it accepts any non-newline
# character that is NOT one of the explicit stop characters
# (``.``, ``;``, CR/LF) AND not the start of a ``, NextLabel:`` field
# boundary. Without the field-boundary clause the regex was greedy up
# to 160 chars and ate the next field on the same line \u2014 see the
# 2026-05-13 production trace where
# ``Medical: diabetes and high blood pressure, Salary: 120,000 THB/month``
# collapsed into one ``[HEALTH_CONDITION_0]`` span and the income
# detector's ``Salary:`` label never got a chance to fire.
#
# The ``,\s*[A-Z][a-z]+\s*[:\uff1a]`` lookahead is intentionally
# conservative \u2014 it requires both a capitalised English word and a
# colon, so legitimate comma-separated medical phrases like
# ``diabetes, hypertension, asthma`` still consume across commas
# (the words are lowercase, the lookahead doesn't trip).
_LABELLED_HEALTH_RE = re.compile(
    r"(?P<label>"
    r"medical(?:\s+info)?|diagnosis|health\s+condition|disease|"
    r"\u0e42\u0e23\u0e04|"
    r"\u0e1b\u0e23\u0e30\u0e27\u0e31\u0e15\u0e34\u0e2a\u0e38\u0e02\u0e20\u0e32\u0e1e|"
    r"\u0e2d\u0e32\u0e01\u0e32\u0e23"
    r")\s*[:\uff1a]\s*"
    r"(?P<value>(?:(?![.;\n\r]|,\s*[A-Z][a-z]+\s*[:\uff1a])[^\n\r]){3,160})",
    re.IGNORECASE,
)

_CONDITION_VALUE_RE = re.compile(
    r"(?:"
    r"diabetes|blood\s+pressure|hypertension|asthma|cancer|depression|"
    r"HIV|covid|heart\s+disease|kidney\s+disease|pregnan|allerg|"
    r"\u0e40\u0e1a\u0e32\u0e2b\u0e27\u0e32\u0e19|"
    r"\u0e04\u0e27\u0e32\u0e21\u0e14\u0e31\u0e19|"
    r"\u0e04\u0e27\u0e32\u0e21\u0e14\u0e31\u0e19\u0e2a\u0e39\u0e07|"
    r"\u0e2b\u0e2d\u0e1a\u0e2b\u0e37\u0e14|"
    r"\u0e21\u0e30\u0e40\u0e23\u0e47\u0e07|"
    r"\u0e0b\u0e36\u0e21\u0e40\u0e28\u0e23\u0e49\u0e32"
    r")",
    re.IGNORECASE,
)


class HealthConditionDetector(BaseDetector):
    name = "health_condition"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for m in _LABELLED_HEALTH_RE.finditer(text):
            value = m.group("value").strip()
            if not _CONDITION_VALUE_RE.search(value):
                continue
            start = m.start("value") + (len(m.group("value")) - len(m.group("value").lstrip()))
            end = start + len(value)
            seen_spans.add((start, end))
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="health_condition",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=value,
                    severity=self.default_sensitivity,
                )
            )

        for m in _HEALTH_KEYWORDS_RE.finditer(text):
            start, end = m.start(), m.end()

            # Check personal context within window
            window_start = max(0, start - _CONTEXT_WINDOW)
            window_end = min(len(text), end + _CONTEXT_WINDOW)
            window = text[window_start:window_end]

            if not _PERSONAL_CONTEXT_RE.search(window):
                continue

            if (start, end) in seen_spans:
                continue
            seen_spans.add((start, end))

            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="health_condition",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )

        return matches
