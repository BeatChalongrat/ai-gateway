"""Biometric data detector — PDPA Section 26 sensitive data.

Context-keyword gated: triggers when biometric-related keywords appear
near a personal reference or system action context (scan, register, use).
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_BIOMETRIC_KEYWORDS_RE = re.compile(
    r"(?:"
    # Thai biometric keywords
    r"ลายนิ้วมือ|สแกนนิ้ว|สแกนใบหน้า|จดจำใบหน้า|"
    r"ม่านตา|สแกนม่านตา|เสียง(?:ของ)?(?:ผม|ฉัน|เขา|เธอ)|"
    r"ข้อมูลชีวภาพ|ไบโอเมตริก|"
    # English biometric keywords
    r"fingerprint|face\s*(?:scan|recognition|ID|detection|unlock)|"
    r"retina\s*scan|iris\s*scan|voiceprint|voice\s*recognition|"
    r"biometric\s*(?:data|scan|identifier|authentication|template)|"
    r"touch\s*ID|face\s*ID|palm\s*(?:print|scan)"
    r")",
    re.IGNORECASE,
)

_PERSONAL_CONTEXT_RE = re.compile(
    r"(?:"
    r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|"
    r"ข้าพเจ้า|กระผม|หนู|ของ|ลงทะเบียน|ใช้|สแกน|"
    r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b|"
    r"\buse\b|\bregister|\bscan|\benroll|\bstore|\brecord"
    r")",
    re.IGNORECASE,
)

_CONTEXT_WINDOW = 150


class BiometricDataDetector(BaseDetector):
    name = "biometric_data"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for m in _BIOMETRIC_KEYWORDS_RE.finditer(text):
            start, end = m.start(), m.end()

            # Check personal/action context within window
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
                    entity_type="biometric_data",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )

        return matches
