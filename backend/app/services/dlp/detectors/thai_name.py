"""Thai personal name detector (title-prefix gated).

Detects Thai names by matching a Thai honorific/title prefix followed by
a first name and surname in Thai script.  The title prefix requirement
keeps false positives low — bare Thai words without a title are not
flagged.

Supported prefixes:
    นาย, นาง, นางสาว, น.ส., คุณ, เด็กชาย, เด็กหญิง, ด.ช., ด.ญ.,
    ดร., ศ., ผศ., รศ., นพ., พญ., ทพ., ทพญ., ภก., ภญ.,
    Mr., Mrs., Ms., Miss, Dr.  (+ Thai name)
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_THAI_CHAR = r"[ก-ฺเ-๎]"

_THAI_TITLE_PREFIXES = (
    r"นางสาว|น\.ส\.|"
    r"นาง|นาย|"
    r"เด็กชาย|เด็กหญิง|ด\.ช\.|ด\.ญ\.|"
    r"คุณ|"
    r"ศ\.|ผศ\.|รศ\.|ดร\.|"
    r"นพ\.|พญ\.|ทพ\.|ทพญ\.|ภก\.|ภญ\.|"
    r"พล\.อ\.|พล\.ท\.|พล\.ต\.|"
    r"ร\.ต\.อ\.|พ\.ต\.อ\.|"
    r"ว่าที่ร้อยตรี"
)

_ENGLISH_TITLE_PREFIXES = r"Mr\.|Mrs\.|Ms\.|Miss|Dr\."

_THAI_NAME_RE = re.compile(
    rf"(?:(?:{_THAI_TITLE_PREFIXES})\s*"
    rf"(?:{_THAI_CHAR}{{2,}})"
    rf"\s+"
    rf"(?:{_THAI_CHAR}{{2,}}))"
)

_MIXED_NAME_RE = re.compile(
    rf"(?:(?:{_ENGLISH_TITLE_PREFIXES})\s*"
    rf"(?:{_THAI_CHAR}{{2,}})"
    rf"\s+"
    rf"(?:{_THAI_CHAR}{{2,}}))"
)

_FALSE_POSITIVE_RE = re.compile(
    r"นายก(?:รัฐมนตรี|สมาคม|เทศมนตรี|สภา)|"
    r"นายพล|นายทหาร|นายตำรวจ|นายแพทย์|นายช่าง|นายหน้า|นายแบบ|"
    r"นางแบบ|นางงาม|นางฟ้า|นางเอก|นางพยาบาล"
)


class ThaiNameDetector(BaseDetector):
    name = "thai_name"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for pattern in (_THAI_NAME_RE, _MIXED_NAME_RE):
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                matched = m.group()

                if _FALSE_POSITIVE_RE.match(matched):
                    continue

                if (start, end) in seen_spans:
                    continue
                seen_spans.add((start, end))

                matches.append(
                    DetectionMatch(
                        detector_name=self.name,
                        entity_type="thai_name",
                        category=self.category,
                        start=start,
                        end=end,
                        matched_text=matched,
                        severity=self.default_sensitivity,
                    )
                )

        return matches
