"""Thai address detector (keyword-anchored).

Thai postal addresses are structured with recognisable leading
keywords (``บ้านเลขที่``, ``ที่อยู่``, …). Rather than try to parse
every address component, we anchor on the leading keyword and span
forward up to 200 characters or a newline — whichever comes first.

Severity is MEDIUM; addresses rarely warrant BLOCK action.

Precision/recall stance (FIX26 §1.2, the owner's keyword-precision split):
the leading keyword is graded. HIGH-precision keywords (``บ้านเลขที่``,
``อยู่บ้านเลขที่``, ``ที่อยู่`` as a phrase-initial noun) fire on a house
number within 30 characters alone, so building/condo/village-only
addresses mask again. AMBIGUOUS keywords (``เลขที่`` "the number that…",
``อยู่ที่`` "is at", ``ส่งที่`` "send to") keep FIX23's strict rule — house
number AND an address component (road, soi, moo, tambon/amphoe/province
words or abbreviations, khwaeng/khet, Bangkok, or a 5-digit postcode).
KNOWN GAPS that remain: addresses with no house number, a prose gap of
more than 30 characters before the number, and ambiguous-keyword
addresses written with only a building / estate / bare province name.
Fixtures exist in BOTH directions (``test_thai_address_recall.py`` must
mask; ``test_thai_address.py`` prose must not; the FIX26 prose corpus).

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


# FIX26 §1.2 — keyword-precision split (owner decision after FIX23b §2).
#   HIGH-precision keywords (บ้านเลขที่ / อยู่บ้านเลขที่ / ที่อยู่): a digit
#   within 30 chars is enough — the permissive body returns for them, so
#   ``ที่อยู่ 199/22 คอนโดลุมพินี`` masks again.
#   AMBIGUOUS keywords (เลขที่ "the number that…", อยู่ที่ "is at", ส่งที่
#   "send to"): FIX23's strict rule stays — digit AND an address component
#   or postcode — which is what keeps ``เลขที่ให้จำเมื่อกี้คืออะไร`` unmasked.
# ``ที่อยู่`` is itself two-faced: the noun "address" starts a phrase, while
# glued after a Thai letter (สินค้าที่อยู่ในคลัง) or followed by a locative
# preposition (ที่อยู่ใน / ที่อยู่ตรง …) it is the relative clause "…that is
# at/in…" — those two shapes are excluded from the HIGH grade.
_HIGH_KW = (
    r"(?:อยู่บ้านเลขที่|บ้านเลขที่"
    r"|(?<![ก-ฺเ-๎])ที่อยู่(?![\s:：]*(?:ใน|ตรง|บน|ใต้|ข้าง|ระหว่าง|ใกล้|แถว|กับ|ไหน)))"
)
# A ``ที่อยู่`` that fails the HIGH shape (glued: จำที่อยู่ / ส่งไปที่อยู่) is
# not thrown away — it drops to the AMBIGUOUS grade and still masks a real
# address that carries a component or postcode.
_AMBIG_KW = r"(?:เลขที่|ที่อยู่|อยู่ที่|ส่งที่)"
_DIGIT_NEAR = r"(?=[^\n\d]{0,30}\d)"
_COMPONENT = (
    r"(?=[^\n]{0,200}?(?:ถนน|ถ\.|ซอย|ซ\.|หมู่|ม\.|ตำบล|ต\.|อำเภอ|อ\.|จังหวัด|จ\."
    r"|แขวง|เขต|กรุงเทพ|กทม|\d{5}(?=[\s,]|$)))"
)
_BODY = r"([^\n]{3,200}?\d{5}(?=[\s,]|$)|[^\n]{3,200}?(?=\n|$|\s{3,}))"
_ADDRESS_RE = re.compile(
    rf"({_HIGH_KW}[\s:：]*{_DIGIT_NEAR}"
    rf"|{_AMBIG_KW}[\s:：]*{_DIGIT_NEAR}{_COMPONENT})"
    rf"{_BODY}"
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
