"""Cells of an uploaded table as values: a header's key, a text, an amount, a quantity, a date,
a tax ID.

Every reader returns None (or "") when the cell does not hold the thing: what is missing becomes a
question or a skipped row with its reason, never a guessed value. Money keeps two decimals, a
quantity or a unit price up to six — a price of 0.0125 baht per sheet is a real price.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import thai as TH
from .e2 import thai_text as T

Q2 = Decimal("0.01")
Q6 = Decimal("0.000001")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?")
_CURRENCY_WORDS = ("บาท", "฿", "THB", "USD", "EUR", "JPY", "CNY", "SGD", "GBP", "$", "€", "¥", "£")


def h(v) -> str:
    """A header cell → what it is compared by: no spaces or punctuation, lower case, Thai digits
    read as Arabic, a unit in brackets dropped ("จำนวนเงิน (บาท)" and "จำนวนเงิน" are one header)."""
    s = "".join(T.arabic(str(v or "")).split()).lower()
    s = re.sub(r"\((thb|บาท|baht|฿|usd)\)", "", s)
    return re.sub(r"[.\-_:()#*/\\]", "", s)


def text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()[:10]
    return " ".join(str(v).split())


def _decimal(v) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    t = T.arabic(str(v)).strip()
    for w in _CURRENCY_WORDS:
        t = t.replace(w, "")
    t = t.replace(" ", "")
    if t in ("", "-", "—", "–"):
        return None
    neg = (t.startswith("(") and t.endswith(")")) or t.startswith("-") or t.endswith("-")
    t = t.strip("()-")
    if not _NUMBER.fullmatch(t):
        return None
    try:
        d = Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None
    return -d if neg else d


def money(v) -> str | None:
    """A money cell → "1234.50" (its sign kept), or None when the cell holds no amount."""
    d = _decimal(v)
    return None if d is None else f"{d.quantize(Q2, rounding=ROUND_HALF_UP):.2f}"


def number(v) -> str | None:
    """A quantity, a unit price or a rate → its digits, up to six decimals, without trailing zeros
    beyond two ("12.50", "0.0125"); None when the cell holds no number."""
    d = _decimal(v)
    if d is None:
        return None
    d = d.quantize(Q6, rounding=ROUND_HALF_UP)
    s = f"{d:f}"
    if "." in s:
        whole, frac = s.split(".")
        frac = frac.rstrip("0")
        s = whole + "." + (frac if len(frac) >= 2 else frac.ljust(2, "0"))
    return s


def date_of(v) -> str | None:
    """A date cell → ISO. A Thai file writes the day first and the year in the Buddhist era
    (01/09/2569, 01-09-69, 1 ก.ย. 69); a real date cell and yyyy-mm-dd are read as they are."""
    if isinstance(v, _dt.datetime):
        return v.date().isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if v is None or v == "" or isinstance(v, bool):
        return None
    d = TH.parse_date(v)
    if d is None:
        hit = T.parse_date(T.arabic(str(v)))
        d = hit[0] if hit else None
    if d is None or not (1990 <= d.year <= 2100):
        return None
    return d.isoformat()


def tax_id(v) -> str:
    """A tax ID cell → its 13 digits, or "" when the cell does not hold 13 digits. Excel keeps a
    13-digit number as a float: 105556012345.0 is read back as its digits."""
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    digits = re.sub(r"\D", "", T.arabic(str(v)))
    if len(digits) == 12 and T.tax_id_check_ok("0" + digits):
        # a spreadsheet keeps 0105557000118 as the number 105557000118: the zero it dropped is put
        # back when the 13 digits then pass their own check digit
        return "0" + digits
    return digits if len(digits) == 13 else ""


def tax_id_ok(tid: str) -> bool:
    return T.tax_id_check_ok(tid)
