"""Reading a finished workbook back, for the quality gates (AM8).

A gate opens the delivered file and compares what is in its cells with
computed.json — never with what the writer said it wrote. ``Tally`` counts
every value compared and words every difference the way a person reads it;
an empty cell where a figure belongs is a difference, never a skip.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")


def squash(text) -> str:
    return re.sub(r"\s+", "", "" if text is None else str(text))


def dec(v) -> Decimal | None:
    if v in (None, "") or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v).replace(",", "")).quantize(CENT)
    except (InvalidOperation, ValueError):
        return None


def check(name: str, ok: bool, detail: str = "", label: str = "", values: int | None = None) -> dict:
    out = {"name": name, "ok": bool(ok), "detail": detail, "label": label or name}
    if values is not None:
        out["values"] = int(values)  # how many values were read back and compared: what was asserted
    return out


class Tally:
    """Values compared, and the ones that differ (said in plain Thai)."""

    def __init__(self):
        self.n = 0
        self.bad: list[str] = []

    def fail(self, what: str) -> None:
        self.n += 1
        self.bad.append(what)

    def text(self, where: str, got, want) -> None:
        self.n += 1
        if squash(got) != squash(want):
            shown = "ว่าง" if squash(got) == "" else str(got).strip()
            self.bad.append(f"{where}: ในไฟล์ “{shown}” ควรเป็น “{want}”")

    def number(self, where: str, got, want) -> None:
        """A figure: equal to the satang. A figure the computed table has must be in the cell."""
        self.n += 1
        w = dec(want)
        g = dec(got) if isinstance(got, (int, float, Decimal)) and not isinstance(got, bool) else None
        if w is None and got in (None, ""):
            return
        if g is None:
            self.bad.append(f"{where}: ในไฟล์{'ว่าง' if got in (None, '') else ' “' + str(got) + '”'} ควรเป็น {_money(w)}")
        elif w is None or g != w:
            self.bad.append(f"{where}: ในไฟล์ {_money(g)} ควรเป็น {'ว่าง' if w is None else _money(w)}")

    def result(self, name: str, label: str, what: str) -> dict:
        if self.bad:
            more = f" (และอีก {len(self.bad) - 4} จุด)" if len(self.bad) > 4 else ""
            return check(name, False, "; ".join(self.bad[:4]) + more, label, self.n)
        return check(name, self.n > 0, f"เปิดไฟล์อ่าน{what}กลับ {self.n} ค่า ตรงกับที่คำนวณทุกค่า" if self.n
                     else f"ไม่พบ{what}ในไฟล์", label, self.n)


def _money(d: Decimal | None) -> str:
    return "ว่าง" if d is None else f"{d:,.2f}"


def table(ws, heads: list, top: int = 4) -> list[list]:
    """The table whose header row is ``top``, every row given in the order of ``heads`` (found by header
    name): a check reads what it asserts by name, whatever order the writer printed the columns in (AM9).
    Rows whose first named cell is empty are left out, like the writers' own readers do.

    ``ws`` may be the sheets a wide table prints on (AM10: the main sheet and its companions, which repeat
    its identity columns and are read side by side by row number): a header is read from the first sheet
    that has it, the rows paired by position."""
    sheets = list(ws) if isinstance(ws, (list, tuple)) else [ws]
    headers = [[str(c.value).strip() if c.value is not None else None for c in s[top]] for s in sheets]
    where = [next(((i, hdr.index(h)) for i, hdr in enumerate(headers) if h in hdr), None) for h in heads]
    out = []
    for rs in zip(*(s.iter_rows(min_row=top + 1, values_only=True) for s in sheets)):
        row = []
        for w in where:
            i, j = w or (0, 0)
            row.append(rs[i][j] if w and j < len(rs[i]) else None)
        if row and row[0] is not None:
            out.append(row)
    return out


def sheets(wb, *names: str) -> list:
    """The sheets of ``names`` that are in the workbook, for ``table`` — a file made before a table was split
    has the main sheet only."""
    return [wb[n] for n in names if n in wb.sheetnames]


def rows(ws, header_row: int) -> list[tuple]:
    """The data rows under a header row, up to the first empty row."""
    out = []
    for r in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if not r or all(v in (None, "") for v in r):
            break
        out.append(r)
    return out


def labelled(ws, label_col: int = 1, value_col: int = 2, min_row: int = 1) -> dict:
    """{label: value} of a two-column face (label in A, value in B); the first occurrence of a label wins."""
    out = {}
    for row in ws.iter_rows(min_row=min_row, values_only=True):
        if row and len(row) >= value_col and isinstance(row[label_col - 1], str) and row[label_col - 1] not in out:
            out[row[label_col - 1]] = row[value_col - 1]
    return out
