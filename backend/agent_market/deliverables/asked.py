"""What a person asked of a round's documents, on the renderer's side (AM11b, AM13).

A model reads a person's words into ids of the list the package declares (agent.json "instructions"); the
runner sends them as ``req["instructions"]["apply"]``. A renderer applies what it knows and says what it
applied; its gate then opens the finished file and reads back that it was done. This module is the part
every workbook agent would otherwise write again: reading the ids, choosing the one order a table can
have, the gate's one check, and the keys rows are ordered by.

An order changes the order of rows and nothing else — the same rows, the same figures, each still naming
where in the person's files it was read.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

CHECK = "xlsx.asked"
LABEL = "เอกสารเป็นไปตามที่ขอ (อ่านจากไฟล์)"


def ids(req: dict) -> list[str]:
    """The directives asked for, as the runner sent them."""
    return list((req.get("instructions") or {}).get("apply") or [])


def one_order(req: dict, orders: tuple[str, ...]) -> str | None:
    """The one order the table is to have. The platform sends at most one of a group (the one asked
    last takes the place of the one before); should two arrive, the last is the one."""
    mine = [i for i in ids(req) if i in orders]
    return mine[-1] if mine else None


def check(ok: bool, detail: str, values: int = 0) -> dict:
    out = {"name": CHECK, "ok": bool(ok), "label": LABEL, "detail": detail}
    if ok:
        out["values"] = values
    return out


def number(v) -> Decimal | None:
    """A computed amount or a cell's number as a Decimal; None when there is none."""
    if v in (None, "") or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def largest_first(rows: list, size) -> list:
    """rows from the largest size() down; rows with no size after them. Stable: equal rows keep the
    order they had, which is the order of the person's file."""
    def key(r):
        v = size(r)
        return (1, Decimal(0)) if v is None else (0, -v)

    return sorted(rows, key=key)


def smallest_first(rows: list, size) -> list:
    def key(r):
        v = size(r)
        return (1, Decimal(0)) if v is None else (0, v)

    return sorted(rows, key=key)


def runs_down(values: list) -> bool:
    """values as read from a file, top to bottom: every number not above the one before it, and no
    number after a row that has none."""
    return _runs(values, lambda a, b: a >= b)


def runs_up(values: list) -> bool:
    return _runs(values, lambda a, b: a <= b)


def _runs(values: list, in_order) -> bool:
    last, ended = None, False
    for v in values:
        if v is None:
            ended = True
            continue
        if ended or (last is not None and not in_order(last, v)):
            return False
        last = v
    return True


_LEADING = "เแโใไ"


def thai_key(s) -> str:
    """A name as it is looked up in a Thai dictionary: a leading vowel is written before its consonant and
    read after it (เ-ก is filed under ก). Latin letters are filed without regard to case."""
    s = str(s or "").strip().casefold()
    out, i = [], 0
    while i < len(s):
        if s[i] in _LEADING and i + 1 < len(s) and "ก" <= s[i + 1] <= "ฮ":
            out.append(s[i + 1] + s[i])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def by_name(rows: list, name) -> list:
    """rows by name; rows with no name after them. Stable."""
    return sorted(rows, key=lambda r: (0, thai_key(name(r))) if str(name(r) or "").strip() else (1, ""))


def names_run_up(names: list) -> bool:
    keys = [thai_key(n) if str(n or "").strip() else None for n in names]
    return _runs(keys, lambda a, b: a <= b)
