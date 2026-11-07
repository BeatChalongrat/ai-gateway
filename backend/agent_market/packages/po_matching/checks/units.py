"""Units of measure, and the words that mark a cost nobody ordered.

A purchase order says "10 กล่อง" and the invoice bills "120 ชิ้น": the same goods, or twelve times
too many. Two units are compared only when one of these holds:

  * they are the same unit written two ways ("pcs" and "ชิ้น", "กก." and "kg");
  * one converts into the other by a measure that is the same everywhere (1 โหล = 12 ชิ้น,
    1 กิโลกรัม = 1,000 กรัม);
  * the install has a conversion for them ("1 กล่อง = 12 ชิ้น", for every item or for one).

Anything else is not converted and not guessed: the line goes to a person with both units named.
A unit of time or of work (งาน, ครั้ง, เดือน, ชั่วโมง) marks a service line — nothing is delivered
to a warehouse, so no goods receipt is expected for it.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ....engine.e2 import thai_text as T
from ..words import W

# canonical unit → the ways it is written (spaces, dots and case ignored)
UNITS = {
    "ชิ้น": ("ชิ้น", "pcs", "pc", "pce", "piece", "pieces", "ea", "each"),
    "อัน": ("อัน",),
    "ตัว": ("ตัว",),
    "เครื่อง": ("เครื่อง",),
    "ชุด": ("ชุด", "set", "sets"),
    "กล่อง": ("กล่อง", "box", "boxes", "bx"),
    "ลัง": ("ลัง", "carton", "cartons", "ctn", "case", "cs"),
    "แพ็ก": ("แพ็ก", "แพ็ค", "แพค", "pack", "packs", "pk", "pkg"),
    "โหล": ("โหล", "dozen", "doz", "dz"),
    "รีม": ("รีม", "ream", "reams", "rm"),
    "แผ่น": ("แผ่น", "sheet", "sheets"),
    "ม้วน": ("ม้วน", "roll", "rolls"),
    "ขวด": ("ขวด", "bottle", "bottles", "btl"),
    "กระป๋อง": ("กระป๋อง", "can", "cans"),
    "ถุง": ("ถุง", "bag", "bags"),
    "ถัง": ("ถัง", "drum", "drums", "pail"),
    "คู่": ("คู่", "pair", "pairs", "pr"),
    "กรัม": ("กรัม", "ก", "g", "gm", "gram", "grams"),
    "กิโลกรัม": ("กิโลกรัม", "กก", "กิโล", "kg", "kgs", "kilogram", "kilograms"),
    "ตัน": ("ตัน", "ton", "tons", "tonne", "mt"),
    "มิลลิลิตร": ("มิลลิลิตร", "มล", "ml"),
    "ลิตร": ("ลิตร", "ล", "l", "lt", "ltr", "liter", "litre", "liters", "litres"),
    "เซนติเมตร": ("เซนติเมตร", "ซม", "cm"),
    "เมตร": ("เมตร", "ม", "m", "mtr", "meter", "metre", "meters", "metres"),
    "ตารางเมตร": ("ตารางเมตร", "ตรม", "sqm", "m2"),
    # time and work
    "งาน": ("งาน", "job", "jobs", "ls", "lumpsum"),
    "ครั้ง": ("ครั้ง", "time", "times"),
    "เที่ยว": ("เที่ยว", "trip", "trips"),
    "ชั่วโมง": ("ชั่วโมง", "ชม", "hr", "hrs", "hour", "hours"),
    "วัน": ("วัน", "day", "days"),
    "เดือน": ("เดือน", "month", "months", "mth", "mo"),
    "ปี": ("ปี", "year", "years", "yr"),
    "คนวัน": ("คนวัน", "manday", "mandays", "md"),
    "ผู้ใช้": ("ผู้ใช้", "user", "users", "license", "licenses", "licence", "licences", "seat", "seats"),
}
SERVICE_UNITS = ("งาน", "ครั้ง", "เที่ยว", "ชั่วโมง", "วัน", "เดือน", "ปี", "คนวัน", "ผู้ใช้")
# a measure that is the same everywhere: (from, to) → how many of `to` one `from` is
UNIVERSAL = {
    ("โหล", "ชิ้น"): Decimal("12"), ("กิโลกรัม", "กรัม"): Decimal("1000"), ("ตัน", "กิโลกรัม"): Decimal("1000"),
    ("ลิตร", "มิลลิลิตร"): Decimal("1000"), ("เมตร", "เซนติเมตร"): Decimal("100"),
}
# what a PO's type column calls a line of work rather than of goods
SERVICE_TYPES = ("บริการ", "ค่าบริการ", "งานบริการ", "จ้าง", "จ้างทำของ", "จ้างเหมา", "service", "services", "svc", "nonstock",
                 "non-stock", "ไม่ใช่สินค้าคงคลัง")
BLANKET_TYPES = ("blanket", "open", "openpo", "blanketpo", "framework", "contract", "สัญญา", "วงเงิน", "poเปิด", "ใบสั่งซื้อแบบวงเงิน",
                 "ใบสั่งซื้อแบบเปิด", "สั่งซื้อต่อเนื่อง")
CLOSED_STATUS = ("closed", "close", "shortclosed", "shortclose", "completed", "complete", "ปิด", "ปิดแล้ว", "ปิดpo", "ปิดใบสั่งซื้อ",
                 "ปิดก่อนครบ", "ปิดรับไม่ครบ", "เสร็จสิ้น")
CANCELLED_STATUS = ("cancelled", "canceled", "cancel", "void", "ยกเลิก", "ยกเลิกแล้ว")
# costs that are billed without having been ordered: freight, insurance, handling, surcharges
UNPLANNED_WORDS = ("ค่าขนส่ง", "ค่าส่ง", "ค่าจัดส่ง", "ค่าระวาง", "ค่าบรรทุก", "ค่าประกันภัย", "ค่าประกันสินค้า", "ค่าเบี้ยประกัน",
                   "ค่าธรรมเนียม", "ค่าดำเนินการ", "ค่าบริการจัดส่ง", "ค่าแพ็ค", "ค่าบรรจุ", "ค่าหีบห่อ", "ค่าพาเลท", "ค่าน้ำมัน",
                   "ค่ายกของ", "ค่าแรงยก", "ค่าขนถ่าย", "ค่าผ่านทาง", "ค่าพิธีการ", "ค่าเอกสาร", "ค่าเร่งด่วน",
                   "freight", "shipping", "delivery", "deliverycharge", "transport", "transportation", "carriage", "insurance",
                   "handling", "surcharge", "fuelsurcharge", "packing", "pallet", "customs", "documentation", "expedite")
UNPLANNED_KIND = (
    ("freight", "unplanned.freight", ("ค่าขนส่ง", "ค่าส่ง", "ค่าจัดส่ง", "ค่าระวาง", "ค่าบรรทุก", "ค่าบริการจัดส่ง", "ค่าผ่านทาง", "freight", "shipping",
                           "delivery", "transport", "carriage")),
    ("insurance", "unplanned.insurance", ("ค่าประกัน", "ค่าเบี้ยประกัน", "insurance")),
    ("handling", "unplanned.handling", ("ค่าดำเนินการ", "ค่ายกของ", "ค่าแรงยก", "ค่าขนถ่าย", "ค่าแพ็ค", "ค่าบรรจุ", "ค่าหีบห่อ", "ค่าพาเลท",
                                         "ค่าพิธีการ", "ค่าเอกสาร", "handling", "packing", "pallet", "customs", "documentation")),
    ("surcharge", "unplanned.surcharge", ("ค่าธรรมเนียม", "ค่าน้ำมัน", "ค่าเร่งด่วน", "surcharge", "expedite")),
)
_BY_WORD = {}
for _canon, _ways in UNITS.items():
    for _w in _ways:
        _BY_WORD[_w] = _canon


def key(v) -> str:
    """A unit, a status or a type as written → what it is compared by."""
    return re.sub(r"[\s.\-_/()]", "", T.arabic(str(v or ""))).lower()


def canonical(unit) -> str:
    """The unit's own name: "PCS." → "ชิ้น". A unit nobody listed stays as it was written (without
    spaces or dots), so two lines that both say "พาเลท" still agree."""
    k = key(unit)
    return _BY_WORD.get(k, k)


# A unit's name on a screen. The names above are the ones the files are read by; on an English screen a unit
# is said by the Latin way of writing it that is read back as the same unit (agent.json: unit.*). A unit
# with no Latin way of writing, and one nobody listed, is said as it is.
_SAID = {W.strings[f"unit.{c}"]: f"unit.{c}" for c in (
    "pcs", "set", "box", "carton", "pack", "dozen", "ream", "sheet", "roll", "bottle", "can", "bag", "drum", "pair", "g", "kg",
    "ton", "ml", "litre", "cm", "metre", "sqm", "job", "time", "trip", "hour", "day", "month", "year", "manday", "user")}


def said(unit) -> str:
    """The unit's own name in the language of the run ("PCS." is the canonical name in Thai, "pcs" in English)."""
    c = canonical(unit)
    return W(_SAID[c]) if c in _SAID else c


def is_service_unit(unit) -> bool:
    return canonical(unit) in SERVICE_UNITS


def factor(frm, to, item: str = "", conversions: list | None = None) -> tuple[Decimal | None, str]:
    """How many of ``to`` one ``frm`` is, and where that comes from: same · universal · settings.
    None when nothing says."""
    a, b = canonical(frm), canonical(to)
    if not a or not b or a == b:
        # a line that names no unit is measured in the unit of the line it is compared with
        return Decimal("1"), "same" if a and b else "not_stated"
    if (a, b) in UNIVERSAL:
        return UNIVERSAL[(a, b)], "universal"
    if (b, a) in UNIVERSAL:
        return Decimal("1") / UNIVERSAL[(b, a)], "universal"
    item_key = key(item)
    own = [c for c in conversions or [] if c.get("item") and key(c["item"]) == item_key]
    for c in own + [c for c in conversions or [] if not c.get("item")]:
        f = Decimal(str(c["factor"]))
        if (c["from"], c["to"]) == (a, b):
            return f, "settings"
        if (c["from"], c["to"]) == (b, a) and f != 0:
            return Decimal("1") / f, "settings"
    return None, "unknown"


def unplanned_kind(description, words: tuple = UNPLANNED_WORDS) -> tuple[str, str] | None:
    """(kind, its name in the language of the run) when the line's words are those of a cost that is billed without being
    ordered; None otherwise. The install's own words count as a surcharge of their own name."""
    k = key(description)
    if not k or not any(w and w in k for w in words):
        return None
    for kind, label, ws in UNPLANNED_KIND:
        if any(w in k for w in ws):
            return kind, W(label)
    return "other", W("unplanned.other")
