"""Thai address detector (keyword-anchored).

Thai postal addresses are structured with recognisable leading
keywords (``บ้านเลขที่``, ``ที่อยู่``, …). Rather than try to parse
every address component, we anchor on the leading keyword and span
forward up to 200 characters or a newline — whichever comes first.

Severity is MEDIUM; addresses rarely warrant BLOCK action.

Precision/recall stance (current, FIX23/FIX23b): the detector fires only
when the text after the keyword looks like a postal address — a house
number close to the keyword AND an address component (road, soi, moo,
tambon/amphoe/province words or abbreviations, khwaeng/khet, Bangkok, or
a 5-digit postcode). It no longer prefers over-matching: the earlier
"anything to end-of-line" body masked ordinary Thai sentences whole and
broke the product (see below). KNOWN GAPS accepted pending owner review
(FIX23b §2): addresses written with only a building / condo / village /
estate name or a bare province name and no postcode, addresses with no
house number, and a prose gap of more than 30 characters between the
keyword and the house number are not matched by this detector.

FIX23 — the body must (a) carry a digit within 30 characters of the
keyword (a house number) AND (b) contain an address component on the
same line (ถนน/ซอย/หมู่/ตำบล/อำเภอ/จังหวัด/แขวง/เขต/กรุงเทพ/กทม or a
5-digit postcode). Without those anchors, ordinary Thai prose
containing ``เลขที่`` ("the number that…") / ``อยู่ที่`` ("is at") /
``ที่อยู่`` was swept to end-of-line as an "address": the question
``เลขที่ให้จำเมื่อกี้คืออะไร`` became ``<<ADDRESS_1>>`` on input (the
model echoed the token and restore turned it back into the user's own
question), and the answer ``เลขที่ให้จำคือ 74-…`` was masked on output. The Rust port (``dlp-sidecar/src/detectors/
thai_address.rs``) carries the identical regex — change both.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_ADDRESS_RE = re.compile(
    r"(อยู่บ้านเลขที่|บ้านเลขที่|เลขที่|ที่อยู่|อยู่ที่|ส่งที่)"
    r"[\s:：]*"
    r"(?=[^\n\d]{0,30}\d)"
    r"(?=[^\n]{0,200}?(?:ถนน|ถ\.|ซอย|ซ\.|หมู่|ม\.|ตำบล|ต\.|อำเภอ|อ\.|จังหวัด|จ\."
    r"|แขวง|เขต|กรุงเทพ|กทม|\d{5}(?=[\s,]|$)))"
    r"([^\n]{3,200}?\d{5}(?=[\s,]|$)"
    r"|[^\n]{3,200}?(?=\n|$|\s{3,}))"
)


class ThaiAddressDetector(BaseDetector):
    name = "thai_address"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _ADDRESS_RE.finditer(text):
            # Match the full span from the keyword through the address body.
            full_start = m.start(1)
            full_end = m.end(2)
            matched = text[full_start:full_end].strip()
            if len(matched) < 6:
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="thai_address",
                    category=self.category,
                    start=full_start,
                    end=full_end,
                    matched_text=matched,
                    severity=self.default_sensitivity,
                )
            )
        return matches
