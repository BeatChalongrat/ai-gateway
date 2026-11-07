"""A row of one of the two tables, and the keys its texts reduce to.

A row's ``id`` comes from the file it was read from (which file, which sheet,
which row), never from its position in a list built during a run: an answer
given about a pair of rows must land on the same two rows in every later pass.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher

Q2 = Decimal("0.01")
_TH_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
# what a company's name carries that says nothing about which company it is
_LEGAL = ("บริษัท", "จำกัด", "มหาชน", "ห้างหุ้นส่วน", "สามัญ", "นิติบุคคล", "บจก", "บมจ", "หจก", "หสน",
          "company", "limited", "co", "ltd", "plc", "inc", "corp", "partnership")
_LEGAL_RE = re.compile("|".join(sorted((re.escape(w) for w in _LEGAL), key=len, reverse=True)))
MIN_REF = 3  # a reference shorter than this is too weak to be a key


def q2(v) -> Decimal:
    return Decimal(str(v)).quantize(Q2, rounding=ROUND_HALF_UP)


def ref_key(text) -> str:
    """A reference as written → what it is compared by: Thai digits read as
    Arabic, upper case, nothing but letters and digits, and a number without
    its leading zeros ("เช็ค 0012345" and "12345" name one cheque). Empty when
    too short to tell two rows apart."""
    s = str(text or "").translate(_TH_DIGITS).upper()
    s = re.sub(r"[^0-9A-Zก-๙]", "", s)
    if s.isdigit():
        s = s.lstrip("0")
    return s if len(s) >= MIN_REF else ""


def name_key(text) -> str:
    """A name or a description → its skeleton: lower case, Thai digits read as
    Arabic, legal forms and everything that is not a letter or a digit removed."""
    s = str(text or "").translate(_TH_DIGITS).lower()
    s = re.sub(r"[^0-9a-zก-๙]", " ", s)
    s = _LEGAL_RE.sub(" ", s)
    return "".join(s.split())


def similarity(a, b) -> Decimal:
    """How much two texts resemble each other, 0.00–1.00: the larger of the
    share of characters they have in common in order, and the longest run
    they share as a part of the shorter text (a bank line often holds a
    name among other words). A fixed calculation — the same two texts always
    give the same figure."""
    x, y = name_key(a), name_key(b)
    if not x or not y:
        return Decimal("0.00")
    if x == y:
        return Decimal("1.00")
    m = SequenceMatcher(None, x, y, autojunk=False)
    ratio = m.ratio()
    short = min(len(x), len(y))
    block = m.find_longest_match(0, len(x), 0, len(y)).size
    part = (block / short) * 0.95 if short >= 4 and block >= 4 else 0.0
    return Decimal(str(max(ratio, part))).quantize(Q2, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Row:
    id: str
    side: str                     # left | right
    amount: Decimal               # signed, two decimals
    date: _dt.date | None = None
    ref: str = ""                 # as written
    name: str = ""                # as written: who, or what the line says
    source: dict = field(default_factory=dict, compare=False, hash=False)   # file, sheet, row, cells
    tags: tuple = ()
    order: int = 0                # where the row stands in its table

    @property
    def ref_key(self) -> str:
        return ref_key(self.ref)

    def ref_out(self) -> dict:
        """The row as the groups name it: what it says and where it is."""
        s = self.source or {}
        return {"id": self.id, "side": self.side, "date": self.date.isoformat() if self.date else None,
                "amount": f"{self.amount:.2f}", "ref": self.ref, "name": self.name,
                "file": s.get("file"), "sheet": s.get("sheet"), "row": s.get("row"), "cells": s.get("cells") or {},
                "tags": list(self.tags)}


def check_rows(rows: list, side: str) -> list:
    """The table as the engine takes it: every row of this side, an id no
    other row has, an amount of two decimals."""
    seen: dict[str, int] = {}
    out = []
    for i, r in enumerate(rows):
        if r.side != side:
            raise ValueError(f"row {r.id!r} is of the {r.side} table, not the {side} one")
        if not r.id or "~" in r.id or "+" in r.id:
            raise ValueError(f"bad row id {r.id!r}")
        if r.id in seen:
            raise ValueError(f"two rows share the id {r.id!r}")
        seen[r.id] = i
        if not isinstance(r.amount, Decimal) or r.amount != r.amount.quantize(Q2):
            raise ValueError(f"row {r.id!r}: the amount is not a two-decimal Decimal")
        out.append(r if r.order == i else Row(id=r.id, side=r.side, amount=r.amount, date=r.date, ref=r.ref, name=r.name,
                                              source=r.source, tags=r.tags, order=i))
    return out
