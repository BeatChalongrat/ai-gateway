"""A value a person typed into an E2 card → a checked value, or the reason it is not one.

Deterministic only (the card's input kind decides the parser); a typed value
that does not parse is asked again (``e2_typed_invalid``) — it is never
guessed at, and never handed to a model to interpret.
"""

from __future__ import annotations

import re
from decimal import Decimal

from . import thai_text as T


def parse(kind: str, text: str):
    """(value, None) or (None, reason in Thai)."""
    t = T.arabic(str(text or "")).strip()
    if not t:
        return None, "ไม่ได้พิมพ์ค่า"
    if kind == "money":
        t = t.replace("บาท", "").replace("฿", "").replace(" ", "")
        if re.fullmatch(r"-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|-?\d+(?:\.\d{1,2})?", t):
            return T.q2(Decimal(t.replace(",", ""))), None
        return None, "ต้องเป็นตัวเลขจำนวนเงิน เช่น 12,850.70"
    if kind == "tax_id":
        d = re.sub(r"[\s\-]", "", t)
        if not re.fullmatch(r"\d{13}", d):
            return None, "เลขผู้เสียภาษีต้องมี 13 หลัก"
        if not T.tax_id_check_ok(d):
            return None, "เลข 13 หลักนี้ไม่ผ่านการตรวจหลักสุดท้าย ช่วยตรวจอีกครั้ง"
        return d, None
    if kind == "date":
        hit = T.parse_date(t)
        if not hit:
            return None, "อ่านเป็นวันที่ไม่ได้ ลองแบบ 15/09/2569"
        return hit[0], None
    if kind == "rate":
        if re.fullmatch(r"\d+(?:\.\d+)?", t.replace(",", "")):
            v = Decimal(t.replace(",", ""))
            if v > 0:
                return v, None
        return None, "ต้องเป็นตัวเลขอัตราแลกเปลี่ยน เช่น 36.25"
    return t[:200], None
