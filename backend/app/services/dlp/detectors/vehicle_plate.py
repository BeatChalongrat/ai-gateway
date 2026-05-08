"""Thai vehicle plate detector.

Conservative matcher targeting Thai vehicle plates only. Since a raw plate
(e.g. "กข 1234") is indistinguishable from many short sequences, this detector
requires either:
    (a) a Thai province name adjacent to the plate, OR
    (b) a preceding Thai keyword "ทะเบียน" / English "license plate".

Supported shapes:
- Thai chars: 1-2 Thai consonants + 1-4 digits
- Romanized : 1-2 Latin letters + 1-4 digits

False-positive rate without these guardrails is very high; the spec
explicitly marks this as LOW severity.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# A subset of common Thai province names (Thai + English). Enough to anchor
# matches without maintaining the full 77-province list in every test pass.
_PROVINCES = (
    "กรุงเทพ|กรุงเทพมหานคร|นนทบุรี|เชียงใหม่|ภูเก็ต|ขอนแก่น|ชลบุรี|"
    "นครราชสีมา|สงขลา|อุดรธานี|พิษณุโลก|อยุธยา|ระยอง|สมุทรปราการ|"
    "Bangkok|Nonthaburi|Chiang\\s*Mai|Phuket|Khon\\s*Kaen|Chonburi|"
    "Nakhon\\s*Ratchasima|Songkhla|Udon\\s*Thani|Phitsanulok|Ayutthaya|"
    "Rayong|Samut\\s*Prakan"
)

# Plate body: 1-2 chars (Thai consonants or ASCII letters) + optional
# separator + 1-4 digits. Thai consonant range covers U+0E01..U+0E2E.
_PLATE_BODY = (
    r"(?:[\u0E01-\u0E2E]{1,2}|[A-Z]{1,2})[ \-]?\d{1,4}"
)

# (a) prefix keyword "ทะเบียน" or "license plate" / "plate no"
_PLATE_KEYWORD_RE = re.compile(
    rf"(?:ทะเบียน(?:รถ)?|license\s*plate|plate\s*(?:no\.?|number|#)?)"
    rf"[^A-Za-z0-9\u0E00-\u0E7F]{{0,10}}"
    rf"(?P<value>{_PLATE_BODY})",
    re.IGNORECASE,
)

# (b) plate followed by province name (within a few chars)
_PLATE_PROVINCE_RE = re.compile(
    rf"(?P<value>{_PLATE_BODY})\s*(?:{_PROVINCES})",
    re.IGNORECASE,
)


class VehiclePlateDetector(BaseDetector):
    name = "vehicle_plate"
    category = "identity"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for regex in (_PLATE_KEYWORD_RE, _PLATE_PROVINCE_RE):
            for m in regex.finditer(text):
                start = m.start("value")
                end = m.end("value")
                if (start, end) in seen_spans:
                    continue
                seen_spans.add((start, end))
                matches.append(
                    DetectionMatch(
                        detector_name=self.name,
                        entity_type="vehicle_plate",
                        category=self.category,
                        start=start,
                        end=end,
                        matched_text=m.group("value"),
                        severity=self.default_sensitivity,
                    )
                )
        return matches
