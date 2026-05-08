"""Religion/belief detector — PDPA Section 26 sensitive data.

Context-keyword gated: triggers only when a religion/belief keyword
appears near a personal reference (pronouns, possessives). General
discussion about religious landmarks or history does NOT trigger.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_RELIGION_KEYWORDS_RE = re.compile(
    r"(?:"
    # Thai
    r"นับถือ|ศาสนา|ศรัทธา|ละหมาด|สวดมนต์|"
    r"เข้าวัด|ถือศีล|บวช|มุสลิม|อิสลาม|พุทธ|คริสต์|ฮินดู|ซิกข์|"
    r"ยิว|ยูดาย์|"
    # English
    r"(?:my|his|her|their)\s+(?:religion|faith|belief)|"
    r"(?:I|he|she|they)\s+(?:am|is|are|was|were)\s+(?:an?\s+)?(?:practicing\s+|devout\s+|observant\s+)?(?:Buddhist|Muslim|Christian|Hindu|Sikh|Jewish|Catholic|Protestant|atheist|agnostic)|"
    r"I'm\s+(?:an?\s+)?(?:practicing\s+|devout\s+|observant\s+)?(?:Buddhist|Muslim|Christian|Hindu|Sikh|Jewish|Catholic|Protestant|atheist|agnostic)|"
    r"(?:I|he|she|they)\s+(?:believe|worship|pray|practice)|"
    r"(?:I|he|she|they)\s+(?:attend|go\s+to|visit)(?:s|ed)?\s+(?:the\s+)?(?:local\s+)?(?:mosque|church|synagogue|temple|gurudwara|chapel|cathedral)|"
    r"(?:practicing|devout|observant)\s+(?:Muslim|Christian|Buddhist|Hindu|Sikh|Jewish|Catholic|Protestant)|"
    r"(?:I|he|she|they)\s+(?:converted?\s+to|follow)"
    r")",
    re.IGNORECASE,
)

_PERSONAL_CONTEXT_RE = re.compile(
    r"(?:"
    # Thai pronouns/personal references
    r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|ลูก|พ่อ|แม่|สามี|ภรรยา|"
    r"ข้าพเจ้า|กระผม|หนู|"
    # English pronouns/possessives
    r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b"
    r")",
    re.IGNORECASE,
)

_CONTEXT_WINDOW = 150


def _merge_adjacent(
    matches: list[DetectionMatch], text: str
) -> list[DetectionMatch]:
    """Merge adjacent/overlapping matches into single spans."""
    if len(matches) <= 1:
        return matches
    matches.sort(key=lambda m: m.start)
    merged: list[DetectionMatch] = [matches[0]]
    for m in matches[1:]:
        prev = merged[-1]
        if m.start <= prev.end:
            merged[-1] = DetectionMatch(
                detector_name=prev.detector_name,
                entity_type=prev.entity_type,
                category=prev.category,
                start=prev.start,
                end=max(prev.end, m.end),
                matched_text=text[prev.start : max(prev.end, m.end)],
                severity=max(prev.severity, m.severity),
            )
        else:
            merged.append(m)
    return merged


class ReligionBeliefDetector(BaseDetector):
    name = "religion_belief"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for m in _RELIGION_KEYWORDS_RE.finditer(text):
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
                    entity_type="religion_belief",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )

        return _merge_adjacent(matches, text)
