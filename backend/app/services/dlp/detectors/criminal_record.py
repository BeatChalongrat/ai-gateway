"""Criminal record detector — PDPA Section 26 sensitive data.

Context-keyword gated: triggers only when a criminal/legal keyword
appears near a personal reference. News articles or general legal
discussions without personal attribution do NOT trigger.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_CRIMINAL_KEYWORDS_RE = re.compile(
    r"(?:"
    # Thai criminal/legal keywords — personal context checked separately
    r"ถูกจับ|ต้องหา|จำคุก|ติดคุก|รอลงอาญา|คุมประพฤติ|ถูกฟ้อง|"
    r"คดี(?:ลัก|ฉ้อ|ยา|ข่มขืน|ฆ่า|ทำร้าย|อาญา|แพ่ง)|"
    r"ประวัติอาชญากรรม|ประวัติคดี|ประวัติต้องหา|"
    r"(?:ผู้ต้องหา|จำเลย|นักโทษ)(?:.{0,20})(?:ชื่อ|คือ|นาย|นาง)|"
    r"(?:พิพากษา|ตัดสิน|ลงโทษ)(?:.{0,30})(?:จำคุก|ปรับ|รอลงอาญา|คุมประพฤติ)|"
    # English with personal context
    r"(?:I|he|she|they)\s+(?:was|were|got|been)\s+(?:arrested|convicted|sentenced|charged|incarcerated)|"
    r"(?:my|his|her|their)\s+(?:criminal\s+record|conviction|arrest|sentence|felony|parole|probation)|"
    r"(?:I|he|she|they)\s+(?:served|spent)\s+(?:time|years?|months?)\s+(?:in\s+(?:prison|jail))|"
    r"(?:criminal\s+record|arrest\s+record|police\s+record)\s+(?:of|for|shows)|"
    r"(?:convicted|sentenced)\s+(?:of|to|for)\s+\w+"
    r")",
    re.IGNORECASE,
)

_PERSONAL_CONTEXT_RE = re.compile(
    r"(?:"
    r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|นาย|นาง|น\.ส\.|"
    r"ข้าพเจ้า|กระผม|ผู้ต้องหา|จำเลย|"
    r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b|"
    r"\bdefendant\b|\bsuspect\b"
    r")",
    re.IGNORECASE,
)

_CONTEXT_WINDOW = 150


class CriminalRecordDetector(BaseDetector):
    name = "criminal_record"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for m in _CRIMINAL_KEYWORDS_RE.finditer(text):
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
                    entity_type="criminal_record",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )

        return matches
