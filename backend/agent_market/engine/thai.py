"""Thai calendar, month names and number formatting.

Accounting exports in Thailand mix Buddhist-era years (2569) and Gregorian
years (2026), day-first dates, Thai month abbreviations and Excel serial
dates. Everything here is deterministic and dependency-free.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal

BE_OFFSET = 543

MONTHS_TH = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]
MONTHS_TH_ABBR = [
    "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]
MONTHS_EN = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]

# Words a user types in a chat message or a file title for each month.
_MONTH_WORDS: list[tuple[int, tuple[str, ...]]] = [
    (i + 1, (MONTHS_TH[i], MONTHS_TH_ABBR[i], MONTHS_TH_ABBR[i].replace(".", ""),
             MONTHS_EN[i].lower(), MONTHS_EN[i][:3].lower()))
    for i in range(12)
]


def ce_year(year: int) -> int:
    """A year as written in a file → Gregorian. 2400+ is Buddhist era."""
    if year >= 2400:
        return year - BE_OFFSET
    if year < 100:  # two-digit year: 69 → 2569 BE, 26 → 2026
        return (2500 + year - BE_OFFSET) if year >= 50 else 2000 + year
    return year


def period_key(d: _dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def period_label_th(period: str, style: str = "be") -> str:
    """'2026-09' → 'กันยายน 2569' (or 'กันยายน 2026' with style='ce')."""
    y, m = (int(x) for x in period.split("-"))
    year = y + BE_OFFSET if style == "be" else y
    return f"{MONTHS_TH[m - 1]} {year}"


def period_short_th(period: str) -> str:
    """'2026-09' → 'ก.ย. 69'."""
    y, m = (int(x) for x in period.split("-"))
    return f"{MONTHS_TH_ABBR[m - 1]} {str(y + BE_OFFSET)[-2:]}"


def prev_period(period: str) -> str:
    y, m = (int(x) for x in period.split("-"))
    if m == 1:
        return f"{y - 1:04d}-12"
    return f"{y:04d}-{m - 1:02d}"


def period_bounds(period: str) -> tuple[_dt.date, _dt.date]:
    y, m = (int(x) for x in period.split("-"))
    start = _dt.date(y, m, 1)
    end = (_dt.date(y + (m // 12), (m % 12) + 1, 1) - _dt.timedelta(days=1))
    return start, end


_EXCEL_EPOCH = _dt.date(1899, 12, 30)
_DATE_RE = re.compile(r"^\s*(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})\s*$")
_TH_MONTH_DATE_RE = re.compile(r"^\s*(\d{1,2})\s+([^\d\s]+)\s+(\d{2,4})\s*$")
# A date typed with its day and month as the first two numbers — the only
# shape whose order (DD/MM or MM/DD) is not written in the text itself.
_TWO_FIRST_RE = re.compile(r"^\s*(\d{1,2})([/\-.])(\d{1,2})[/\-.](\d{1,4})\s*$")

DATE_ORDERS = ("dmy", "mdy")


def parse_date(value, order: str = "dmy") -> _dt.date | None:
    """Best-effort date from an Excel cell or CSV text.

    Accepts datetime/date objects, Excel serials, 'dd/mm/yyyy' (day first,
    BE or CE year — or month first with order='mdy', decided per column by
    date_order_evidence), 'yyyy-mm-dd', and '1 ก.ย. 2569'. Returns None when
    the value is not a date — the caller decides whether that is a finding.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 < float(value) < 80000:  # Excel serial between 1954 and 2119
            return _EXCEL_EPOCH + _dt.timedelta(days=int(value))
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2}[ T]", text) else text
    m = _DATE_RE.match(text)
    if m:
        a, b, c = m.groups()
        try:
            if len(a) == 4:  # yyyy-mm-dd
                return _dt.date(ce_year(int(a)), int(b), int(c))
            if order == "mdy":
                return _dt.date(ce_year(int(c)), int(a), int(b))  # mm/dd/yyyy
            return _dt.date(ce_year(int(c)), int(b), int(a))  # dd/mm/yyyy
        except ValueError:
            return None
    m = _TH_MONTH_DATE_RE.match(text)
    if m:
        day, mon, year = m.groups()
        month = month_from_word(mon)
        if month:
            try:
                return _dt.date(ce_year(int(year)), month, int(day))
            except ValueError:
                return None
    return None


def looks_like_date(value) -> bool:
    """A date in either day/month order — for telling a date column by its
    values before the column's order is known."""
    return parse_date(value) is not None or parse_date(value, "mdy") is not None


def date_text_kind(value) -> dict | None:
    """What a text written 'a/b/y' (or with '-' or '.') says about its own order.

    None for anything whose order is not in question: a real date cell, an
    Excel serial, 'yyyy-mm-dd', '5 ก.ย. 2569'. Otherwise
    {kind, separator, buddhist}: kind 'day_first' when the first number is
    over 12 (it cannot be a month, so MM/DD is impossible), 'month_first'
    when the second is (DD/MM impossible), 'ambiguous' when both are 1-12,
    'invalid' when both are over 12. buddhist: the year is Buddhist era
    (≥ 2400, or two digits read as BE by ce_year) — only a Thai locale writes
    one, and a Thai locale writes the day first.
    """
    if not isinstance(value, str):
        return None
    m = _TWO_FIRST_RE.match(value)
    if not m:
        return None
    a, sep, b, y = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
    year = int(y)
    buddhist = year >= 2400 or (len(y) == 2 and year >= 50)
    if a < 1 or b < 1:
        kind = "invalid"
    elif a > 12 and b > 12:
        kind = "invalid"
    elif a > 12:
        kind = "day_first"
    elif b > 12:
        kind = "month_first"
    else:
        kind = "ambiguous"
    return {"kind": kind, "separator": sep, "buddhist": buddhist}


def date_order_evidence(values) -> dict:
    """Counts over one column's values: what its own dates prove about DD/MM
    vs MM/DD. `day_first` (first number > 12) and `buddhist` (a Thai-era
    year) say DD/MM; `month_first` (second number > 12) says MM/DD; the rest
    are `ambiguous`. `separators` counts '/', '-', '.' among these texts."""
    ev = {"day_first": 0, "month_first": 0, "ambiguous": 0, "buddhist": 0, "invalid": 0,
          "separators": {}, "samples": {"day_first": [], "month_first": [], "ambiguous": []}}
    for v in values:
        k = date_text_kind(v)
        if k is None:
            continue
        ev[k["kind"]] += 1
        if k["buddhist"]:
            ev["buddhist"] += 1
        ev["separators"][k["separator"]] = ev["separators"].get(k["separator"], 0) + 1
        s = ev["samples"].get(k["kind"])
        if s is not None and len(s) < 5 and v.strip() not in s:
            s.append(v.strip())
    return ev


def month_from_word(word: str) -> int | None:
    w = word.strip().lower()
    for month, words in _MONTH_WORDS:
        if w in words:
            return month
    return None


def year_style(values: list) -> str | None:
    """'be' / 'ce' / 'mixed' from raw date texts (None when no text years)."""
    seen = set()
    for v in values:
        if isinstance(v, str):
            m = _DATE_RE.match(v.strip())
            if m:
                a, _, c = m.groups()
                y = int(a) if len(a) == 4 else int(c)
                if y >= 1000:
                    seen.add("be" if y >= 2400 else "ce")
    if not seen:
        return None
    return "mixed" if len(seen) > 1 else seen.pop()


def find_period_in_text(text: str, today: _dt.date | None = None) -> str | None:
    """A month the user named: 'ปิดงบเดือนกันยายน', 'Sep 2026', 'GL_2569-09'.

    Returns 'YYYY-MM' or None. A month without a year resolves to the most
    recent such month on or before `today`.
    """
    if not text:
        return None
    low = text.lower()
    m = re.search(r"(25\d{2}|20\d{2})[-_/.](\d{1,2})(?!\d)", low)
    if m:
        y, mo = ce_year(int(m.group(1))), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}"
    # Longest words first so 'กันยายน' wins over 'ก.ย.' and 'มิถุนายน' over 'มิ.ย'.
    candidates = sorted(
        ((w, month) for month, words in _MONTH_WORDS for w in words if len(w) >= 3),
        key=lambda t: -len(t[0]),
    )
    found: dict[int, tuple[int, str]] = {}
    masked = low
    for word, month in candidates:
        idx = masked.find(word)
        if idx < 0:
            continue
        if word.isascii() and re.search(rf"\b{re.escape(word)}\b", masked) is None:
            continue
        found.setdefault(month, (idx, word))
        masked = masked[:idx] + " " * len(word) + masked[idx + len(word):]
    if len(found) != 1:
        return None  # nothing named, or a range ('เมษายน - กันยายน'): not one period
    month, (idx, word) = next(iter(found.items()))
    tail = low[idx + len(word): idx + len(word) + 8]
    ym = re.search(r"(25\d{2}|20\d{2})", tail)
    today = today or _dt.date.today()
    if ym:
        year = ce_year(int(ym.group(1)))
    else:
        year = today.year if month <= today.month else today.year - 1
    return f"{year:04d}-{month:02d}"


def fmt_money(value: Decimal | int | float | str, decimals: int = 2) -> str:
    """Thai accounting style: 1,284,500.00 and (12,400.00) for negatives."""
    d = Decimal(str(value))
    q = Decimal(1).scaleb(-decimals) if decimals else Decimal(1)
    d = d.quantize(q, rounding=ROUND_HALF_UP)
    s = f"{abs(d):,.{decimals}f}"
    return f"({s})" if d < 0 else s


def fmt_int(value) -> str:
    d = Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    s = f"{abs(d):,.0f}"
    return f"-{s}" if d < 0 else s


def fmt_millions(value, decimals: int = 2) -> str:
    """12,480,300 → '12.48 ล้าน' ('12.48 M' in an English run)."""
    from .words import SHARED

    d = Decimal(str(value)) / Decimal(1_000_000)
    q = Decimal(1).scaleb(-decimals)
    return f"{d.quantize(q, rounding=ROUND_HALF_UP):,.{decimals}f} {SHARED('docs.unit.m')}"


def fmt_pct(value, decimals: int = 1, signed: bool = False) -> str:
    if value is None:
        return "—"
    d = Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    s = f"{d:,.{decimals}f}%"
    if signed and d > 0:
        s = "+" + s
    return s
