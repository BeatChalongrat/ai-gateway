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


# FIX38 Part G — where does the address END? The body above is lazy but
# anchored on end-of-line, so with no postcode it always ran to the end of
# the line. The owner's own live-test sentence exposed it:
#
#   ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความ…
#
# was masked as ONE <<ADDRESS_1>> from ``55/12`` to the last word — the
# order, the quantities and the request itself all vanished and the model
# had nothing left to answer. That is the swallow shape the FIX23 audit
# named, one level up: not into the next word, into the rest of the turn.
#
# The trim below walks the body token by token (whitespace-split, which is
# how people separate address components) and keeps:
#   * address-ish tokens — carry a digit (55/12, 5, 10260), start with a
#     component word or abbreviation (ซอย/ถนน/หมู่/ต./อ./จ./แขวง/เขต/อาคาร/
#     ตึก/ชั้น/ห้อง/คอนโด/หมู่บ้าน …), or are กรุงเทพ/กทม;
#   * up to two bare Thai tokens in a row after those — a district and a
#     province written without keywords (``77/1 บางใหญ่ นนทบุรี``);
# and stops at the first token that begins a new clause — a verb or a
# particle from the stop list (สั่ง/ส่ง/ช่วย/ขอ/แล้ว/ครับ/ค่ะ/นะ …) or the
# third bare token in a row. The postcode rule is unchanged: a 5-digit
# postcode still ends the body on the spot. Mirrored in the Rust sidecar.
_COMPONENT_STARTS = (
    "ถนน", "ถ.", "ซอย", "ซ.", "หมู่บ้าน", "หมู่", "ม.", "ตำบล", "ต.", "อำเภอ",
    "อ.", "จังหวัด", "จ.", "แขวง", "เขต", "กรุงเทพ", "กทม", "อาคาร", "ตึก",
    "ชั้น", "ห้อง", "คอนโด", "อพาร์ทเม้นท์", "หอพัก", "นิคม", "บ้าน", "เลขที่",
    "แยก", "ตรอก", "ริม", "ใกล้", "ตรงข้าม", "หลัง", "ข้าง",
)
_CLAUSE_STARTS = (
    "สั่ง", "ส่ง", "ช่วย", "ขอ", "แล้ว", "และ", "แต่", "ให้", "เพราะ", "ถ้า",
    "ครับ", "ค่ะ", "คะ", "นะ", "จะ", "ได้", "มี", "เป็น", "ไป", "มา", "บอก",
    "แจ้ง", "โทร", "เบอร์", "ติดต่อ", "อยาก", "ต้อง", "ไม่", "ซื้อ", "ขาย",
    "จอง", "เรียน", "รบกวน", "ฝาก", "ตอนนี้", "วันนี้", "พรุ่งนี้", "เมื่อวาน",
    "ของ", "สินค้า", "พัสดุ", "เอกสาร", "ลูกค้า", "คุณ", "พี่", "น้อง",
    "ยัง", "เพิ่ง", "กำลัง", "เคย", "ค่อย", "รอ", "เจอ", "พบ", "ถาม", "ตอบ",
    "ทำ", "ดู", "เช็ค", "ตรวจ", "แก้", "เปลี่ยน", "ยกเลิก", "คืน", "จ่าย",
    "โอน", "รับ", "เอา", "ใช้", "ลอง", "เปิด", "ปิด", "หา", "นัด", "ประชุม",
)
_MAX_BARE_RUN = 2


def _is_component_token(tok: str) -> bool:
    if any(ch.isdigit() for ch in tok):
        return True
    return tok.startswith(_COMPONENT_STARTS)


def _starts_clause(tok: str) -> bool:
    return tok.startswith(_CLAUSE_STARTS)


def trim_address_end(text: str, body_start: int, body_end: int) -> int:
    """Return the end offset of the address inside ``text[body_start:body_end]``."""
    import re as _re

    end = body_start
    bare_run = 0
    seen_component = False
    for mt in _re.finditer(r"\S+", text[body_start:body_end]):
        tok = mt.group()
        tok_end = body_start + mt.end()
        if _is_component_token(tok):
            end = tok_end
            bare_run = 0
            seen_component = True
            continue
        if seen_component and _starts_clause(tok):
            break
        bare_run += 1
        if bare_run > _MAX_BARE_RUN:
            break
        end = tok_end
    return end if end > body_start else body_end


class ThaiAddressDetector(BaseDetector):
    name = "thai_address"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _ADDRESS_RE.finditer(text):
            # Match the full span from the keyword through the address body.
            full_start = m.start(1)
            full_end = trim_address_end(text, m.start(2), m.end(2))
            matched = text[full_start:full_end].strip()
            if len(matched) < 6:
                continue
            # The trim may have dropped the digit that qualified the match
            # (a far-away house number in prose): then it is not an address.
            if not any(ch.isdigit() for ch in text[m.start(2):full_end]):
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
