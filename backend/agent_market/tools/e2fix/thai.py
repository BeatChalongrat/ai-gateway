"""Thai formatting for the fixture generator (AM2 Phase A).

Written independently of agent_market.engine (the parser under test): the
generator formats what the engine must read back, so the two must not share
tables — a wrong month table shared by both would pass its own test.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

TH_DIGITS = "๐๑๒๓๔๕๖๗๘๙"
MONTHS_FULL = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
MONTHS_ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

Q2 = Decimal("0.01")


def q2(v) -> Decimal:
    return Decimal(v).quantize(Q2, rounding=ROUND_HALF_UP)


def thai_digits(s: str) -> str:
    return "".join(TH_DIGITS[int(c)] if c.isdigit() and c.isascii() else c for c in s)


def money(v, *, thai: bool = False) -> str:
    s = f"{q2(v):,.2f}"
    return thai_digits(s) if thai else s


def fmt_date(d, style: str) -> str:
    """d: datetime.date (CE). Styles seen on Thai invoices."""
    be = d.year + 543
    if style == "th_full":          # 15 กันยายน 2569
        return f"{d.day} {MONTHS_FULL[d.month - 1]} {be}"
    if style == "th_abbr":          # 15 ก.ย. 2569
        return f"{d.day} {MONTHS_ABBR[d.month - 1]} {be}"
    if style == "th_abbr_yy":       # 15 ก.ย. 69
        return f"{d.day} {MONTHS_ABBR[d.month - 1]} {be % 100:02d}"
    if style == "slash_be":         # 15/09/2569
        return f"{d.day:02d}/{d.month:02d}/{be}"
    if style == "slash_ce":         # 15/09/2026
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if style == "iso":              # 2026-09-15
        return d.isoformat()
    if style == "en":               # 15-Sep-2026
        return f"{d.day:02d}-{MONTHS_EN[d.month - 1]}-{d.year}"
    if style == "th_digits_full":   # ๑๕ กันยายน ๒๕๖๙
        return thai_digits(fmt_date(d, "th_full"))
    if style == "th_digits_abbr_yy":
        return thai_digits(fmt_date(d, "th_abbr_yy"))
    raise ValueError(style)


# ── Thai tax identification number (13 digits, mod-11 check digit) ─────────

def tax_id_check_digit(first12: str) -> int:
    s = sum(int(c) * (13 - i) for i, c in enumerate(first12))
    return (11 - s % 11) % 10


def make_tax_id(first12: str) -> str:
    assert len(first12) == 12 and first12.isdigit()
    return first12 + str(tax_id_check_digit(first12))


def tax_id_valid(tid: str) -> bool:
    return len(tid) == 13 and tid.isdigit() and tax_id_check_digit(tid[:12]) == int(tid[12])


def fmt_tax_id(tid: str, style: str) -> str:
    if style == "plain":            # 0105560123456
        return tid
    if style == "hyphen":           # 0-1055-60123-45-6 (the card's own grouping)
        return f"{tid[0]}-{tid[1:5]}-{tid[5:10]}-{tid[10:12]}-{tid[12]}"
    if style == "space":            # 0 1055 60123 45 6
        return f"{tid[0]} {tid[1:5]} {tid[5:10]} {tid[10:12]} {tid[12]}"
    if style == "thai":
        return thai_digits(tid)
    raise ValueError(style)


# ── amount in words (บาทถ้วน) ────────────────────────────────────────────────

_W = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_P = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def _below_million(n: int) -> str:
    out = []
    digits = str(n)
    ln = len(digits)
    for i, ch in enumerate(digits):
        d = int(ch)
        pos = ln - i - 1
        if d == 0:
            continue
        if pos == 1 and d == 1:
            out.append("สิบ")
        elif pos == 1 and d == 2:
            out.append("ยี่สิบ")
        elif pos == 0 and d == 1 and ln > 1:
            out.append("เอ็ด")
        else:
            out.append(_W[d] + _P[pos])
    return "".join(out)


def _int_words(n: int) -> str:
    if n == 0:
        return "ศูนย์"
    parts = []
    millions, rest = divmod(n, 1_000_000)
    if millions:
        parts.append(_int_words(millions) + "ล้าน")
    if rest:
        parts.append(_below_million(rest))
    return "".join(parts)


def bahttext(v) -> str:
    v = q2(v)
    baht = int(v)
    satang = int((v - baht) * 100)
    s = _int_words(baht) + "บาท" if baht else ""
    if satang:
        s += _int_words(satang) + "สตางค์"
    else:
        s += "ถ้วน"
    return s
