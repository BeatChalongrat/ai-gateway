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


class HealthConditionDetector(BaseDetector):
    name = "health_condition"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

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
