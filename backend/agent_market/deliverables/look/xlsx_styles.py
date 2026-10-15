"""The workbooks' look: named styles from look.json, widths measured from the content (AM21).

Every working paper of every agent is written through ``Book``: a sheet's title, a table with one header
style, one cell style, one money style, one note style — each a named style of the file built from the
scale and the theme's palette — and nothing a package sets itself. Column widths are measured from what
the cells hold, in the typeface, so no cell shows ``####`` and no text is clipped; a sheet wider than a
page at the smallest readable scale has its wrapping columns narrowed first, so it still prints one page
across (AM10). A sheet name over Excel's 31 characters, or with a character Excel refuses, is refused here
with the name said, never silently renamed. The header row is frozen and repeats on every printed page
(workpaper); a finished workbook opens on its first sheet at A1; its author is the organisation, never
the product.
"""

from __future__ import annotations

import os
import re

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.views import Selection

from .. import workpaper
from . import measure
from .tokens import LOOK, LookError, color

FMT = "xlsx"
# number formats by meaning — the one set every agent's workbook draws on; a negative is in parentheses, as
# the report prints it, and a zero is a dash
MONEY = '#,##0.00;(#,##0.00);"-"'
QTY = '#,##0.##;(#,##0.##);"-"'
PCT = '0.00"%";(0.00"%");"-"'
COUNT = '#,##0;(#,##0);"-"'
TEXT = "@"
FORMATS = {"money": MONEY, "qty": QTY, "pct": PCT, "count": COUNT, "text": TEXT, "general": "General"}
BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
SHEET_NAME_MAX = 31
# widths, in Excel's character units (the default font's digit), that a column may not go under or over
MIN_W, MAX_TEXT_W, WRAP_W, MIN_WRAP_W = 6.0, 48.0, 40.0, 14.0
# how much wider a column is than its widest text: the cell's own padding
PAD_W = 1.6
# a page fitted below this scale is read with a glass; the text columns of a wider sheet are narrowed and
# wrapped until the sheet prints at this scale or larger (the paper's MIN_SCALE is where printing gives up)
READABLE_SCALE = 0.67
# the widest a sheet may be, in inches, and still print one page across at the readable scale
PRINTABLE_IN = (workpaper.PAPER_H - 2 * workpaper.MARGIN_LR) / READABLE_SCALE


def check_sheet_name(name: str) -> str:
    """A sheet name Excel takes as it is; any other is refused with the reason."""
    if len(name) > SHEET_NAME_MAX:
        raise LookError(f"sheet name {name!r} is {len(name)} characters; Excel allows {SHEET_NAME_MAX}")
    if BAD_SHEET_CHARS.search(name):
        raise LookError(f"sheet name {name!r} holds a character Excel refuses ([ ] : * ? / \\)")
    if not name.strip():
        raise LookError("a sheet needs a name")
    return name


def width_units(text: str, size_pt: float, bold: bool = False) -> float:
    """A text's width in Excel's column units: pixels at 96 dpi, less Excel's 5 px of cell padding, over
    the 7 px of a digit (workpaper.col_inches is the inverse)."""
    px = measure.width_pt(str(text), size_pt, bold) * 96 / 72
    return max(0.0, (px - 5) / 7)


class Book:
    """One workbook, written in the look's styles."""

    def __init__(self, theme: dict, lang: str | None = None):
        self.t = theme
        self.p = theme["palette"]
        self.lang = lang
        self.wb = Workbook()
        self._styles: dict[str, NamedStyle] = {}
        self._wrapped: dict[str, set] = {}      # sheet title → columns that wrap
        self._merged: dict[str, list] = {}      # sheet title → [(row, first col, last col, text, size, bold)]
        self._register()

    # ── the named styles ─────────────────────────────────────────────
    def c(self, token: str) -> str:
        return color(self.p, token)

    def _font(self, role: str, token: str, bold: bool | None = None) -> Font:
        return Font(name=LOOK.family, size=LOOK.pt(FMT, role), bold=LOOK.bold(role) if bold is None else bold, color=self.c(token))

    def _rule(self) -> Border:
        return Border(bottom=Side(style="thin", color=self.c("rule")))

    def _add(self, name: str, font: Font, *, fill: str | None = None, border: Border | None = None,
             align: Alignment | None = None, number_format: str | None = None) -> None:
        st = NamedStyle(name=name)
        st.font = font
        if fill:
            st.fill = PatternFill("solid", fgColor=self.c(fill))
        if border is not None:
            st.border = border
        if align is not None:
            st.alignment = align
        if number_format:
            st.number_format = number_format
        self.wb.add_named_style(st)
        self._styles[name] = st

    def _register(self) -> None:
        t = self.t["table"]
        top = Alignment(vertical="top")
        wrap = Alignment(wrap_text=True, vertical="top")
        right = Alignment(horizontal="right", vertical="top")
        self._add("AM Title", self._font("display", "primary"))
        self._add("AM Subtitle", self._font("small", "muted"))
        self._add("AM Section", self._font("heading", "primary"))
        self._add("AM Header", self._font("heading", t["header_text"], bold=True), fill=t["header"],
                  align=Alignment(horizontal="center", vertical="center", wrap_text=True))
        self._add("AM Cell", self._font("body", "ink"), border=self._rule(), align=top)
        self._add("AM Cell Wrap", self._font("body", "ink"), border=self._rule(), align=wrap)
        self._add("AM Cell Text", self._font("body", "ink"), border=self._rule(), align=top, number_format=TEXT)
        for kind, fmt in (("Money", MONEY), ("Qty", QTY), ("Pct", PCT), ("Count", COUNT)):
            self._add(f"AM {kind}", self._font("body", "ink"), border=self._rule(), align=right, number_format=fmt)
            self._add(f"AM {kind} Strong", self._font("body", "ink", bold=True), border=self._rule(), align=right, number_format=fmt)
        self._add("AM Label", self._font("body", "ink"), border=self._rule(), align=wrap)
        self._add("AM Label Strong", self._font("body", "ink", bold=True), border=self._rule(), align=wrap)
        self._add("AM Value", self._font("body", "ink"), border=self._rule(), align=wrap)
        self._add("AM Value Strong", self._font("body", "ink", bold=True), border=self._rule(), align=wrap)
        self._add("AM Primary Strong", self._font("body", "primary", bold=True), border=self._rule(), align=wrap)
        self._add("AM Primary Money", self._font("body", "primary", bold=True), border=self._rule(), align=right, number_format=MONEY)
        self._add("AM Good", self._font("body", "positive", bold=True), border=self._rule(), align=wrap)
        self._add("AM Bad", self._font("body", "negative", bold=True), border=self._rule(), align=wrap)
        self._add("AM Note", self._font("small", "muted"), align=wrap)
        self._add("AM Note Bad", self._font("small", "negative", bold=True), align=wrap)
        self._add("AM Muted", self._font("small", "muted"), align=wrap)
        self._add("AM Mark", self._font("subheading", "negative"))

    def style(self, cell, name: str):
        if name not in self._styles:
            raise LookError(f"no workbook style {name!r}: the styles are {', '.join(self._styles)}")
        cell.style = name
        return cell

    # ── sheets ───────────────────────────────────────────────────────
    def sheet(self, name: str):
        """A new sheet (the first call names the workbook's first sheet); the name is checked, never cut."""
        check_sheet_name(name)
        if len(self.wb.worksheets) == 1 and self.wb.active.title == "Sheet" and self.wb.active.max_row == 1 and self.wb.active["A1"].value is None:
            ws = self.wb.active
            ws.title = name
            return ws
        return self.wb.create_sheet(name)

    def title(self, ws, text: str, sub: str = "") -> None:
        self.style(ws.cell(row=1, column=1, value=text), "AM Title")
        if sub:
            self.style(ws.cell(row=2, column=1, value=sub), "AM Subtitle")

    def section(self, ws, r: int, text: str) -> None:
        self.style(ws.cell(row=r, column=1, value=text), "AM Section")

    def mark(self, ws, r: int, text: str) -> None:
        """A draft mark on a face: strong, in the negative colour."""
        self.style(ws.cell(row=r, column=1, value=text), "AM Mark")

    def note(self, ws, r: int, text: str, cols: int = 4, bad: bool = False) -> None:
        """A note across ``cols`` columns; its row is sized to the lines the text takes (a merged cell is
        not sized by Excel itself)."""
        cell = self.style(ws.cell(row=r, column=1, value=text), "AM Note Bad" if bad else "AM Note")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=cols)
        self._merged.setdefault(ws.title, []).append((r, 1, cols, text, LOOK.pt(FMT, "small"), bad))

    def muted(self, ws, r: int, c: int, text) -> None:
        self.style(ws.cell(row=r, column=c, value=text), "AM Muted")
        self._wrapped.setdefault(ws.title, set()).add(c)

    def pair(self, ws, r: int, label: str, value, *, money: bool = False, strong: bool = False, tone: str | None = None) -> None:
        """A label and its value on one row (a face). ``tone``: primary, good or bad."""
        a = ws.cell(row=r, column=1, value=label)
        b = ws.cell(row=r, column=2, value=value)
        if tone == "primary":
            self.style(a, "AM Primary Strong")
            self.style(b, "AM Primary Money" if money else "AM Primary Strong")
        elif tone in ("good", "bad"):
            self.style(a, "AM Label Strong")
            self.style(b, "AM Good" if tone == "good" else "AM Bad")
        else:
            self.style(a, "AM Label Strong" if strong else "AM Label")
            self.style(b, ("AM Money Strong" if strong else "AM Money") if money else ("AM Value Strong" if strong else "AM Value"))
        self._wrapped.setdefault(ws.title, set()).update({1, 2})

    def fmt(self, ws, r: int, c: int, kind: str) -> None:
        """A cell's number format by meaning (money, qty, pct, count, text)."""
        cell = ws.cell(row=r, column=c)
        name = {"money": "AM Money", "qty": "AM Qty", "pct": "AM Pct", "count": "AM Count", "text": "AM Cell Text"}[kind]
        self.style(cell, name)

    def table(self, ws, top: int, headers: list, rows: list, widths=None, *, money_cols=(), qty_cols=(), pct_cols=(), count_cols=(),
              text_cols=(), wrap_cols=(), freeze: bool = True, first: list | None = None, filter: bool = True) -> None:
        """A table: the header row in the header style, every cell in the cell style of its meaning, the
        theme's bands by conditional format, the header frozen (and so repeated on every printed page). The
        columns' widths are measured when the book is finished. ``first``: the columns, by header name, that
        print first (workpaper.lead). ``widths`` is taken and ignored: the renderer measures."""
        if first:
            headers, rows, _w, cols = workpaper.lead(list(headers), rows, None, first, money_cols=money_cols, qty_cols=qty_cols,
                                                     pct_cols=pct_cols, count_cols=count_cols, text_cols=text_cols, wrap_cols=wrap_cols)
            money_cols, qty_cols, pct_cols = cols["money_cols"], cols["qty_cols"], cols["pct_cols"]
            count_cols, text_cols, wrap_cols = cols["count_cols"], cols["text_cols"], cols["wrap_cols"]
        # a text column whose longest text would not fit the widest plain column wraps, whatever the caller said
        body = LOOK.pt(FMT, "body")
        wrap_cols = set(wrap_cols)
        for j in range(1, len(headers) + 1):
            if j in money_cols or j in qty_cols or j in pct_cols or j in count_cols or j in wrap_cols:
                continue
            longest = max((width_units(str(r[j - 1]), body) for r in rows if j - 1 < len(r) and isinstance(r[j - 1], str)), default=0.0)
            if longest + PAD_W > MAX_TEXT_W:
                wrap_cols.add(j)
        for j, h in enumerate(headers, start=1):
            self.style(ws.cell(row=top, column=j, value=h), "AM Header")
        for i, row in enumerate(rows):
            r = top + 1 + i
            for j, v in enumerate(row, start=1):
                cell = ws.cell(row=r, column=j, value=v)
                if j in money_cols:
                    self.style(cell, "AM Money")
                elif j in qty_cols:
                    self.style(cell, "AM Qty")
                elif j in pct_cols:
                    self.style(cell, "AM Pct")
                elif j in count_cols:
                    self.style(cell, "AM Count")
                elif j in text_cols:
                    self.style(cell, "AM Cell Text")
                elif j in wrap_cols:
                    self.style(cell, "AM Cell Wrap")
                else:
                    self.style(cell, "AM Cell")
        if rows and self.t["table"].get("zebra"):
            rng = f"A{top + 1}:{get_column_letter(len(headers))}{top + len(rows)}"
            ws.conditional_formatting.add(rng, FormulaRule(formula=[f"MOD(ROW()-{top + 1},2)=1"],
                                                           fill=PatternFill("solid", fgColor=self.c("surface"), bgColor=self.c("surface"))))
        self._wrapped.setdefault(ws.title, set()).update(wrap_cols)
        if freeze:
            ws.freeze_panes = ws.cell(row=top + 1, column=1)
            if rows and filter:
                ws.auto_filter.ref = f"A{top}:{get_column_letter(len(headers))}{top + len(rows)}"

    def part(self, ws, heads: list, rows: list, layout: list, **cols) -> None:
        """One sheet of a wide table (AM10): the columns ``layout`` names (header names, in print order),
        picked from the whole table; ``cols`` are the writer's column tuples against ``heads``."""
        picked, prow, cols = workpaper.pick(list(heads), rows, list(layout), **cols)
        self.table(ws, 4, picked, prow, **cols)

    # ── widths and heights, measured ─────────────────────────────────
    def _measure(self, ws) -> None:
        body, small = LOOK.pt(FMT, "body"), LOOK.pt(FMT, "small")
        wrapped = self._wrapped.get(ws.title, set())
        merged_cells = set()
        for rng in ws.merged_cells.ranges:
            for row in ws.iter_rows(min_row=rng.min_row, max_row=rng.max_row, min_col=rng.min_col, max_col=rng.max_col):
                for c in row:
                    merged_cells.add(c.coordinate)
        n = ws.max_column or 1
        natural = [MIN_W] * (n + 1)      # the longest line of text in the column
        figures = [0.0] * (n + 1)        # the widest number in it, as formatted (never squeezed: #### is a lie)
        words = [MIN_W] * (n + 1)
        is_wrap = [False] * (n + 1)
        numeric = [False] * (n + 1)
        for row in ws.iter_rows():
            for c in row:
                if c.value is None or c.coordinate in merged_cells or c.row <= 2:
                    continue
                j = c.column
                size = c.font.sz if c.font and c.font.sz else body
                bold = bool(c.font and c.font.b)
                wrap = bool(c.alignment and c.alignment.wrap_text) and (j in wrapped or c.style in ("AM Cell Wrap", "AM Label", "AM Label Strong", "AM Value", "AM Value Strong", "AM Good", "AM Bad", "AM Primary Strong"))
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    numeric[j] = True
                    figures[j] = max(figures[j], width_units(_formatted(c.value, c.number_format), size, bold) + PAD_W)
                    continue
                text = str(c.value)
                if c.style == "AM Header":
                    wrap = True   # a header wraps between its words; its width need is its widest word
                for line in text.split("\n"):
                    w = width_units(line, size, bold) + PAD_W
                    natural[j] = max(natural[j], w)
                    words[j] = max(words[j], max((width_units(wd, size, bold) for wd, _ in _words(line)), default=0.0) + PAD_W)
                if wrap:
                    is_wrap[j] = True
        widths = [0.0] * (n + 1)
        floors = [0.0] * (n + 1)
        for j in range(1, n + 1):
            # the text's share: its longest line up to the cap, never under its widest word (up to a cap)
            if is_wrap[j]:
                text_w = max(min(words[j], MIN_WRAP_W), min(natural[j], WRAP_W))
            else:
                text_w = max(min(words[j], MIN_WRAP_W), min(natural[j], MAX_TEXT_W))
            if numeric[j]:
                # a column with numbers in it is never narrower than its widest number; text beside the
                # numbers (a policy's value column, a note under a total) may be squeezed and wrapped
                widths[j] = max(MIN_W, figures[j], text_w if natural[j] > MIN_W else 0.0)
                floors[j] = max(MIN_W, figures[j])
            else:
                widths[j] = text_w
                floors[j] = MIN_W
        # one page across (AM10): every column that can give gives in proportion to what it has over its
        # floor — a number never, a text down to its widest word — until the sheet fits the page at the
        # smallest readable scale; a text column squeezed under its longest text wraps
        total = sum(workpaper.col_inches(w) for w in widths[1:])
        squeezed: set = set()
        for _ in range(8):
            over = total - PRINTABLE_IN
            if over <= 1e-6:
                break
            givers = [j for j in range(1, n + 1) if widths[j] - floors[j] > 0.01]
            room = sum(workpaper.col_inches(widths[j]) - workpaper.col_inches(floors[j]) for j in givers)
            if not givers or room <= 0:
                break
            frac = min(1.0, over / room)
            for j in givers:
                widths[j] -= (widths[j] - floors[j]) * frac
                if widths[j] + 0.01 < natural[j]:
                    squeezed.add(j)
            total = sum(workpaper.col_inches(w) for w in widths[1:])
        for row in ws.iter_rows(min_row=3):
            for c in row:
                if c.column in squeezed and c.value is not None and c.style == "AM Cell":
                    c.style = "AM Cell Wrap"
        for j in range(1, n + 1):
            ws.column_dimensions[get_column_letter(j)].width = round(widths[j], 2)
        # the rows of merged notes: as many lines as the text takes across its columns
        for r, c1, c2, text, size, bold in self._merged.get(ws.title, []):
            span = sum(workpaper.col_inches(widths[j]) for j in range(c1, min(c2, n) + 1)) * 72 - 8
            lines = sum(measure.lines_in(line, size, span, bold) for line in str(text).split("\n")) if span > 0 else 1
            ws.row_dimensions[r].height = max(15.0, lines * size * 1.35 + 4)

    # ── finish ───────────────────────────────────────────────────────
    def finish(self, path: str, *, id_cols: dict | None = None, author: str = "", title: str = "") -> dict:
        """Widths and heights measured, every sheet set up to print, the workbook opening on its first
        sheet at A1, its author the organisation (never the product); saved."""
        for ws in self.wb.worksheets:
            check_sheet_name(ws.title)
            self._measure(ws)
        plan = workpaper.setup_workbook(self.wb, doc_name=os.path.basename(path), id_cols=id_cols or {}, lang=self.lang)
        self.wb.active = 0   # (the workbook view's activeTab; no sheet carries tabSelected, which a review stamp would have to move)
        for ws in self.wb.worksheets:
            ws.sheet_view.tabSelected = None
            ws.sheet_view.selection = [Selection(activeCell="A1", sqref="A1")]
            if ws.freeze_panes:
                # the pane under the frozen header starts at its top-left, so the first row of data shows
                for sel in ws.sheet_view.selection:
                    sel.activeCell = sel.sqref = "A1"
            ws.sheet_view.topLeftCell = "A1"
        props = self.wb.properties
        props.creator = author or ""
        props.lastModifiedBy = author or ""
        if title:
            props.title = title
        self.wb.save(path)
        return plan


def _words(line: str):
    from .. import thai_break

    return thai_break.words(line)


def _formatted(value, number_format: str) -> str:
    """What a number prints as under its format, for measuring the column it needs."""
    fmt = number_format or "General"
    neg = value < 0
    v = abs(value)
    if "0.00" in fmt and "%" in fmt:
        s = f"{v:,.2f}%"
    elif "0.00" in fmt:
        s = f"{v:,.2f}"
    elif "0.##" in fmt:
        s = f"{v:,.2f}".rstrip("0").rstrip(".")
    elif "#,##0" in fmt:
        s = f"{v:,.0f}"
    else:
        s = f"{value:g}" if isinstance(value, float) else str(value)
        return s
    return f"({s})" if neg else s


# ── read back: the gate on a finished workbook ─────────────────────────

def check(path: str, lang: str | None = None) -> list[dict]:
    """What a reader opening the file meets (AM21): every sheet name within Excel's limit, the workbook on
    its first sheet at A1, every number in a format of the set (by meaning), no column narrower than a
    number it holds (``####``) or a header's widest word, no product name as its author."""
    from openpyxl import load_workbook

    from ...engine.words import SHARED as S
    from ..reread import check as _check
    from . import markers

    wb = load_workbook(path)
    bad: list[str] = []
    n = 0
    if wb.active is not wb.worksheets[0]:
        bad.append(S("gate.xlsx.look.active", sheet=wb.active.title))
    for ws in wb.worksheets:
        n += 1
        where = S("gate.where.sheet", sheet=ws.title)
        try:
            check_sheet_name(ws.title)
        except LookError:
            bad.append(S("gate.xlsx.look.name", sheet=ws.title[:40]))
        sel = ws.sheet_view.selection[0] if ws.sheet_view.selection else None
        if sel is not None and (sel.activeCell or "A1") != "A1":
            bad.append(S("gate.xlsx.look.cursor", where=where, cell=sel.activeCell))
        widths = {j: (ws.column_dimensions[get_column_letter(j)].width or workpaper.DEFAULT_WIDTH) for j in range(1, (ws.max_column or 1) + 1)}
        hashes = 0
        formats = set()
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    if c.number_format not in FORMATS.values():
                        formats.add(c.number_format)
                    if len(_formatted(c.value, c.number_format)) + 1 > widths[c.column] + 0.5:
                        hashes += 1
        if hashes:
            bad.append(S("gate.xlsx.look.hashes", where=where, n=hashes))
        if formats:
            bad.append(S("gate.xlsx.look.formats", where=where, formats=", ".join(sorted(formats))[:60]))
    label = S("gate.xlsx.look.label")
    out = [_check("xlsx.look", not bad, "; ".join(bad[:4]) if bad else S("gate.xlsx.look.ok", n=n), label, n)]
    out.append(markers.check(path))
    return out
