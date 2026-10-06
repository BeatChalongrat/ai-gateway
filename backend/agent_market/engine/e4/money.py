"""A printed figure as a number — mechanically.

``readings`` takes a string as a page prints it ("฿12,490.00", "12.490,00 บาท",
"THB 5 200") and gives every number it can be by the rules of writing numbers
alone: the marks between digits are thousands separators or a decimal point,
and which is which follows from where they stand. It knows no word. A string
with letters or signs around the figure is read with them set aside; a string
that holds more than one figure has no reading at all — the reader was asked
for the figure itself.

A figure a model returns is accepted only if it equals one of these readings
(``agrees``). The model can settle an ambiguity ("4.590" is 4590 or 4.59); it
cannot produce a number the string does not hold.

``mark`` reads a currency's own mark where the page prints one beside the
figure. The table is the marks currencies are written with, nothing more.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
_THAI_DIGITS = {ord(c): str(i) for i, c in enumerate("๐๑๒๓๔๕๖๗๘๙")}
_FIGURE = re.compile(r"\d[\d.,'’    ]*\d|\d")

# the marks a currency is written with → its code
MARKS = {
    "THB": ("฿", "บาท", "thb", "baht", "บ."),
    "USD": ("us$", "usd", "$"),
    "EUR": ("€", "eur"),
    "GBP": ("£", "gbp"),
    "JPY": ("円", "jpy", "¥"),
    "CNY": ("元", "cny", "rmb"),
    "SGD": ("s$", "sgd"),
    "MYR": ("rm", "myr"),
    "VND": ("₫", "vnd"),
    "KRW": ("₩", "krw"),
    "INR": ("₹", "inr"),
    "AUD": ("a$", "aud"),
    "HKD": ("hk$", "hkd"),
}


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s)).translate(_THAI_DIGITS)


def figures(display: str) -> list[str]:
    """The runs of digits and number marks a string holds."""
    return [m.group(0) for m in _FIGURE.finditer(_plain(display))]


def _grouped(head: str, sep: str) -> bool:
    """Digits written in thousands: one to three, then threes, with one and the same mark between them."""
    parts = head.split(sep)
    return 1 <= len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]) and all(p.isdigit() for p in parts)


def _one(figure: str) -> list[Decimal]:
    f = re.sub(r"[    '’]", "", figure)
    marks = [c for c in f if c in ".,"]
    if not marks:
        return [Decimal(f)]
    last = max(f.rfind("."), f.rfind(","))
    point, head, tail = f[last], f[:last], f[last + 1:]
    other = "," if point == "." else "."
    out: list[str] = []
    if other in head:
        # two different marks: the last is the decimal point, the other separates thousands
        if point not in head and _grouped(head, other) and tail.isdigit():
            out.append(head.replace(other, "") + "." + tail)
    elif point in head:
        # one mark, several times: it can only be separating thousands
        if _grouped(f, point):
            out.append(f.replace(point, ""))
    else:
        # one mark, once: thousands when exactly three digits follow it, a decimal point in any case
        if _grouped(f, point):
            out.append(f.replace(point, ""))
        if head.isdigit() and tail.isdigit():
            out.append(head + "." + tail)
    res: list[Decimal] = []
    for s in out:
        try:
            d = Decimal(s)
        except InvalidOperation:
            continue
        if d not in res:
            res.append(d)
    return res


def readings(display: str) -> list[Decimal]:
    """Every number the string is, by the rules of writing numbers alone; none when it holds no figure or
    more than one."""
    found = figures(display)
    if len(found) != 1:
        return []
    return _one(found[0])


# how many decimal places a currency's amounts are written with, where it is not two
PLACES = {"JPY": 0, "KRW": 0, "VND": 0}


def places(currency: str | None) -> int:
    return PLACES.get(currency or "", 2)


def agrees(display: str, amount, currency: str | None = None) -> Decimal | None:
    """The amount as a number, when it is one of the string's readings; None otherwise. With the currency
    known, a reading finer than that currency is written ("3.990" of a currency of two places) is not one."""
    try:
        d = Decimal(str(amount).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    for r in readings(display):
        if currency and -r.as_tuple().exponent > places(currency):
            continue
        if r == d:
            return r
    return None


def mark(*texts: str) -> str | None:
    """The currency whose mark is printed in the texts, when exactly one currency's is."""
    seen = set()
    for t in texts:
        low = _plain(t or "").lower()
        # a longer mark is read first ("us$" before "$")
        spans: list[tuple[int, int]] = []
        for code, marks in sorted(MARKS.items(), key=lambda kv: -max(len(m) for m in kv[1])):
            for m in sorted(marks, key=len, reverse=True):
                at = low.find(m)
                while at >= 0:
                    end = at + len(m)
                    lettered = m.isascii() and m.isalpha()
                    whole = not lettered or ((at == 0 or not low[at - 1].isalpha()) and (end == len(low) or not low[end].isalpha()))
                    if whole and not any(a < end and at < b for a, b in spans):
                        spans.append((at, end))
                        seen.add(code)
                    at = low.find(m, end)
    return seen.pop() if len(seen) == 1 else None


def cents(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def text(d) -> str:
    """An amount as text for computed.json: two decimals, no separators."""
    return str(cents(Decimal(str(d))))


def shown(d, currency: str | None = None) -> str:
    """An amount as a person reads it: 12,490.00 (with its currency's code in front when given)."""
    s = f"{cents(Decimal(str(d))):,.2f}"
    return f"{currency} {s}" if currency else s
