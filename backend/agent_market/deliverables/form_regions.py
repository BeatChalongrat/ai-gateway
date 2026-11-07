"""What a form with no marker asks for besides single blanks (AM12).

template_fill reads a blank beside (or under) its label. Two more shapes are
read here, and one thing is written:

* the rows of a table of items — a row of headings ("ลำดับ | รายการ | จำนวน |
  …") over empty rows. It is one place that takes a list: each heading is a
  column a person (or the model's proposal they confirm) maps to a column of
  the agent's list, and the lines are written one per row. Word: when there
  are more lines than rows, the last empty row is repeated (its formatting
  with it). Excel: rows are never inserted — a sheet's formulas and merged
  cells do not move with them — so lines beyond the rows the form has are
  not written, and that is said (``rows_short``);
* a blank drawn as a line after its label, in running text or in a cell
  ("ผู้ขอซื้อ: ____________"). The line is replaced by the value; the label
  stays where it was;
* ``append_gaps`` prints, at the end of the filled file (a last page of a
  Word form, a last sheet of an Excel one), what could not be filled and
  why — the form's own blanks that are still empty, and whatever the package
  knows cannot be known. ``gaps_printed`` opens the file again and looks for
  each of them.

Nothing here knows what any form is for: a heading is a short text over
empty cells, a line is underscores or dots.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from docx.oxml.ns import qn
from openpyxl.utils import column_index_from_string, get_column_letter

import docx_generator as dg  # /sandbox/scripts — shared
import excel_executor as ex  # /sandbox/scripts — shared

ROWS_RE = re.compile(r"^rows@([ts])(\d+)r(\d+)$")      # rows@t0r3: table 0 of a Word form, its rows from row 3; rows@s1r12: sheet 1
LINE_RE = re.compile(r"^line@p(\d+)b(\d+)$")           # line@p14b0: paragraph 14 of a Word form, its first drawn line
DRAWN_LINE = re.compile(r"_{3,}|\.{5,}|…{2,}|-{6,}")
MIN_COLUMNS, MIN_ROWS = 3, 2
MAX_ROWS, MAX_ADDED, MAX_LABEL = 80, 60, 60
_ONLY_A_NUMBER = re.compile(r"^\s*\d{1,3}[.)]?\s*$")


def _label_of(text) -> str:
    return (text or "").strip().rstrip(":：").strip()


def _is_label(label: str) -> bool:
    return bool(label) and len(label) <= MAX_LABEL and "{{" not in label and not DRAWN_LINE.search(label)


def _squash(text) -> str:
    return re.sub(r"\s+", "", "" if text is None else str(text))


# ─── Word: the rows of a table of items ────────────────────────
def _distinct(cells) -> list[tuple[int, Any]]:
    """A row's cells, a merged one once: (its first column, the cell)."""
    out, last = [], None
    for ci, cell in enumerate(cells):
        if cell._tc is not last:
            out.append((ci, cell))
            last = cell._tc
    return out


def _empty_row(cells: list[tuple[int, Any]]) -> bool:
    """A row to write in: nothing in it, or nothing but its own number in the first cell."""
    for n, (_, cell) in enumerate(cells):
        text = cell.text.strip()
        if text and not (n == 0 and _ONLY_A_NUMBER.match(text)):
            return False
    return True


def docx_regions(doc) -> list[dict]:
    out = []
    for ti, table in enumerate(doc.tables):
        rows = [_distinct(row.cells) for row in table.rows]
        r = 0
        while r < len(rows):
            heads = [(ci, _label_of(cell.text)) for ci, cell in rows[r]]
            named = [(ci, label) for ci, label in heads if _is_label(label)]
            k = 0
            if len(named) >= MIN_COLUMNS and len(named) >= len(heads) - 1:
                while r + 1 + k < len(rows) and len(rows[r + 1 + k]) == len(rows[r]) and _empty_row(rows[r + 1 + k]):
                    k += 1
            if k >= MIN_ROWS:
                out.append({"key": f"rows@t{ti}r{r + 1}", "table": ti, "header": r, "first": r + 1, "rows": k,
                            "columns": [f"c{ci}" for ci, _ in named], "column_labels": {f"c{ci}": label for ci, label in named},
                            "label": " | ".join(label for _, label in named)[:200], "where": f"table{ti}"})
                r += k + 1
            else:
                r += 1
    return out


def docx_region_rows(regions: list[dict]) -> set[tuple[int, int]]:
    """(table, row) of every heading row and every row under it that belongs to a region."""
    return {(g["table"], r) for g in regions for r in range(g["header"], g["first"] + g["rows"])}


def fill_docx_rows(doc, key: str, lines: list[dict], set_cell) -> dict:
    """Write the lines into the region's rows; ``set_cell(table, row, column, text)`` puts one value.
    Returns {"written", "added", "short"}."""
    region = next((g for g in docx_regions(doc) if g["key"] == key), None)
    if region is None:
        return {"written": 0, "added": 0, "short": len(lines), "reason": "unknown_target"}
    table = doc.tables[region["table"]]
    have, added = region["rows"], 0
    last = table.rows[region["first"] + have - 1]._tr
    # where the rows that are added begin, counted in the document's paragraphs: what stands after them
    # (a line drawn under the table) is that much further down when the file is opened again
    at = 0
    for n, p in enumerate(_paragraphs(doc), start=1):
        if any(a is last for a in p.iterancestors()):
            at = n
    per_row = len(list(last.iter(dg.W_P)))
    while have + added < len(lines) and added < MAX_ADDED:
        again = copy.deepcopy(last)
        last.addnext(again)
        last = again
        added += 1
    written = 0
    for i, line in enumerate(lines[: have + added]):
        for col, value in line.items():
            text = dg.as_text(value)
            if text is None or not text.strip() or not re.match(r"^c\d+$", col):
                continue
            set_cell(region["table"], region["first"] + i, int(col[1:]), text)
        written += 1
    return {"written": written, "added": added, "short": len(lines) - written,
            "moved": {"table": region["table"], "from_row": region["first"] + have, "rows": added,
                      "from_paragraph": at, "paragraphs": added * per_row} if added else None}


def moved_key(key: str, moves: list[dict]) -> str:
    """Where a place of the form is in the filled file, when rows were added above it: a cell of the same
    table under the added rows, a drawn line in a paragraph after them."""
    cell = re.match(r"^table(\d+)\.r(\d+)c(\d+)$", key or "")
    line = LINE_RE.match(key or "")
    for mv in moves or []:
        if cell and int(cell.group(1)) == mv["table"] and int(cell.group(2)) >= mv["from_row"]:
            key = f"table{cell.group(1)}.r{int(cell.group(2)) + mv['rows']}c{cell.group(3)}"
            cell = re.match(r"^table(\d+)\.r(\d+)c(\d+)$", key)
        elif line and int(line.group(1)) >= mv["from_paragraph"]:
            key = f"line@p{int(line.group(1)) + mv['paragraphs']}b{line.group(2)}"
            line = LINE_RE.match(key)
    return key


def docx_rows_hold(doc, region: dict, lines: list[dict]) -> tuple[bool, str]:
    """The filled file, opened again: every line is in its row under the headings it was mapped by."""
    try:
        table = doc.tables[region["table"]]
        head = table.rows[region["header"]].cells
        for col, label in (region.get("column_labels") or {}).items():
            if _squash(_label_of(head[int(col[1:])].text)) != _squash(label):
                return False, "wrong_label"
        for i, line in enumerate(lines):
            cells = table.rows[region["first"] + i].cells
            for col, value in line.items():
                want = _squash(dg.as_text(value))
                if want and _squash(cells[int(col[1:])].text) != want:
                    return False, "not_in_file"
    except (IndexError, ValueError):
        return False, "rows_short"
    return True, ""


# ─── Excel: the rows of a table of items ───────────────────────
def _cell_text(cell) -> str | None:
    v = None if cell is None else cell.value
    return v if isinstance(v, str) else None


def _drawn(cell) -> bool:
    if cell is None:
        return False
    b = cell.border
    if any(getattr(side, "style", None) for side in (b.left, b.right, b.top, b.bottom)):
        return True
    return bool(cell.fill is not None and cell.fill.fill_type)


def xlsx_regions(wb) -> list[dict]:
    """A row of headings over rows the form draws as places to write (a border, a fill): the rows count
    while every cell under a heading is empty and writable — a sheet's open space is not a table."""
    out = []
    for si, ws in enumerate(wb.worksheets):
        by_row: dict[int, list[tuple[int, str]]] = {}
        for (row, col), cell in sorted(ws._cells.items()):
            text = _cell_text(cell)
            label = _label_of(text)
            if text and _is_label(label) and not text.strip().endswith((":", "：")):
                by_row.setdefault(row, []).append((col, label))
        taken = 0
        for row in sorted(by_row):
            heads = sorted(by_row[row])
            if row <= taken or len(heads) < MIN_COLUMNS:
                continue
            cols = [c for c, _ in heads]

            def free(r: int, cols=cols, ws=ws) -> bool:
                for n, c in enumerate(cols):
                    cell = ex.peek_cell(ws, r, c)
                    if ex.cell_refusal(ws, cell) is not None:
                        return False
                    v = None if cell is None else cell.value
                    if v is None or (isinstance(v, str) and not v.strip()):
                        continue
                    if n == 0 and _ONLY_A_NUMBER.match(str(v)):
                        continue  # the row's own number
                    return False
                return any(_drawn(ex.peek_cell(ws, r, c)) for c in cols)

            k = 0
            while k < MAX_ROWS and free(row + 1 + k):
                k += 1
            if k >= MIN_ROWS:
                letters = [get_column_letter(c) for c in cols]
                out.append({"key": f"rows@s{si}r{row + 1}", "sheet": ws.title, "sheet_index": si, "header": row, "first": row + 1,
                            "rows": k, "columns": letters, "column_labels": dict(zip(letters, (l for _, l in heads))),
                            "label": " | ".join(l for _, l in heads)[:200],
                            "where": f"{ws.title}!{letters[0]}{row + 1}:{letters[-1]}{row + k}"})
                taken = row + k
    return out


def xlsx_region_rows(regions: list[dict]) -> set[tuple[str, int]]:
    return {(g["sheet"], r) for g in regions for r in range(g["header"], g["first"] + g["rows"])}


def xlsx_row_cells(wb, key: str, lines: list[dict]) -> tuple[dict[str, Any], dict]:
    """The cells the lines go into ({"Sheet!B12": value}) and {"written", "short"}: lines beyond the rows
    the form has are not written."""
    region = next((g for g in xlsx_regions(wb) if g["key"] == key), None)
    if region is None:
        return {}, {"written": 0, "added": 0, "short": len(lines), "reason": "unknown_target"}
    todo: dict[str, Any] = {}
    fit = lines[: region["rows"]]
    for i, line in enumerate(fit):
        for col, value in line.items():
            text = dg.as_text(value)
            if text is None or not str(text).strip() or col not in region["columns"]:
                continue
            todo[f"{region['sheet']}!{col}{region['first'] + i}"] = value
    return todo, {"written": len(fit), "added": 0, "short": len(lines) - len(fit)}


def xlsx_rows_hold(wb, region: dict, lines: list[dict], same) -> tuple[bool, str]:
    ws = wb[region["sheet"]] if region["sheet"] in wb.sheetnames else None
    if ws is None:
        return False, "not_in_file"
    for col, label in (region.get("column_labels") or {}).items():
        if _squash(_label_of(_cell_text(ws[f"{col}{region['header']}"]))) != _squash(label):
            return False, "wrong_label"
    if len(lines) > region["rows"]:
        return False, "rows_short"
    for i, line in enumerate(lines):
        for col, value in line.items():
            if _squash(dg.as_text(value)) and not same(ws.cell(row=region["first"] + i, column=column_index_from_string(col)).value, value):
                return False, "not_in_file"
    return True, ""


# ─── Word: a blank drawn as a line after its label ─────────────
def lines_in(text: str) -> list[dict]:
    """Every drawn line of a text with the words it follows: {"n", "start", "end", "label", "colon"}; the
    label is what stands between the line before it (or a wide gap) and this one."""
    out, prev = [], 0
    for n, m in enumerate(DRAWN_LINE.finditer(text)):
        before = re.split(r"\s{3,}|\t", text[prev:m.start()].rstrip())[-1].strip()
        out.append({"n": n, "start": m.start(), "end": m.end(), "label": _label_of(before),
                    "colon": before.endswith((":", "："))})
        prev = m.end()
    return out


def _paragraphs(doc) -> list:
    return list(doc.element.body.iter(dg.W_P))


def docx_lines(doc) -> list[dict]:
    """The drawn lines of a Word form that follow a label: where each is and what it is read under. A
    line with a colon before it is one the form expects filled; a line after a bare word ("ลงชื่อ ……") is
    offered for mapping and is not a gap when left as it is — a signature is not the agent's to give."""
    out = []
    for pi, p in enumerate(_paragraphs(doc)):
        text = dg.paragraph_text(p)
        if not DRAWN_LINE.search(text):
            continue
        for b in lines_in(text):
            if _is_label(b["label"]):
                out.append({"key": f"line@p{pi}b{b['n']}", "label": b["label"], "required": b["colon"], "paragraph": pi,
                            "context": " ".join(text.split())[:160]})
    return out


def _replace_span(p, start: int, end: int, text: str) -> None:
    """Put ``text`` where the paragraph's characters [start, end) are, in the run the span begins in."""
    at, done = 0, False
    for t in dg.own_texts(p):
        s = t.text or ""
        a, b = at, at + len(s)
        at = b
        if b <= start or a >= end:
            continue
        keep_before, keep_after = s[: max(0, start - a)], s[max(0, end - a):] if b > end else ""
        t.text = keep_before + (text if not done else "") + keep_after
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        done = True


def fill_docx_line(doc, key: str, value: str) -> str | None:
    """Replace one drawn line with a value; the reason when the form has no such line."""
    m = LINE_RE.match(key)
    paragraphs = _paragraphs(doc)
    if not m or int(m.group(1)) >= len(paragraphs):
        return "unknown_target"
    p = paragraphs[int(m.group(1))]
    blank = next((b for b in lines_in(dg.paragraph_text(p)) if b["n"] == int(m.group(2))), None)
    if blank is None:
        return "unknown_target"
    _replace_span(p, blank["start"], blank["end"], f" {value} ")
    return None


def fill_docx_lines(doc, values: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Fill several lines; the later lines of a paragraph first, so the earlier ones stay where they were read."""
    filled, refused = [], {}
    order = sorted(values, key=lambda k: tuple(-int(x) for x in LINE_RE.match(k).groups()))
    for key in order:
        why = fill_docx_line(doc, key, values[key])
        if why:
            refused[key] = why
        else:
            filled.append(key)
    return filled, refused


def docx_line_state(doc, slot: dict) -> tuple[str, str]:
    """The paragraph a line was read in, as the file has it now, and the slot's label: (text, label)."""
    m = LINE_RE.match(slot["key"])
    paragraphs = _paragraphs(doc)
    if not m or int(m.group(1)) >= len(paragraphs):
        return "", slot.get("label") or ""
    return dg.paragraph_text(paragraphs[int(m.group(1))]), slot.get("label") or ""


def docx_line_holds(doc, slot: dict, value) -> bool:
    text, label = docx_line_state(doc, slot)
    want = _squash(dg.as_text(value))
    at = _squash(text).find(_squash(label))
    return bool(want) and at >= 0 and want in _squash(text)[at + len(_squash(label)):]


def docx_line_blank(doc, slot: dict) -> bool:
    text, label = docx_line_state(doc, slot)
    return any(b["label"] == label for b in lines_in(text))


def docx_line_labelled(doc, slot: dict) -> bool:
    text, label = docx_line_state(doc, slot)
    return _squash(label) in _squash(text)


# ─── What could not be filled, on the face of the file ─────────
def _borders(table) -> None:
    """A plain grid, whatever styles the form has (a customer's form need not have "Table Grid")."""
    from docx.oxml import OxmlElement

    pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "808080")
        borders.append(el)
    pr.append(borders)


def append_gaps(path: str, kind: str, gaps: dict) -> int | None:
    """Print ``gaps`` ({"title", "lead", "columns": [what, why], "rows": [[what, why]], "sheet"}) at the end
    of the filled form. The form's own pages and sheets are as the fill left them. For a PDF, returns the
    number of the first page added (what gaps_printed reads from)."""
    rows = gaps.get("rows") or []
    if kind == "pdf":
        from . import pdf_form

        first = pdf_form.pages(path)
        pdf_form.append_gaps(path, gaps)
        return first
    if kind == "docx":
        from docx import Document

        doc = Document(path)
        doc.add_page_break()
        head = doc.add_paragraph()
        head.add_run(gaps["title"]).bold = True
        doc.add_paragraph(gaps["lead"])
        table = doc.add_table(rows=1, cols=2)
        _borders(table)
        for i, text in enumerate(gaps["columns"]):
            table.rows[0].cells[i].paragraphs[0].add_run(text).bold = True
        for what, why in rows:
            cells = table.add_row().cells
            cells[0].text, cells[1].text = str(what), str(why)
        doc.save(path)
    elif kind == "xlsx":
        import openpyxl
        from openpyxl.styles import Alignment, Font

        wb = openpyxl.load_workbook(path, keep_vba=path.lower().endswith(".xlsm"))
        name, n = gaps.get("sheet") or gaps["title"], 1
        name = re.sub(r"[\\/*?:\[\]]", " ", name)[:28]
        title = name
        while title in wb.sheetnames:
            n += 1
            title = f"{name} {n}"
        ws = wb.create_sheet(title)
        wrap = Alignment(wrap_text=True, vertical="top")
        ws["A1"], ws["A2"] = gaps["title"], gaps["lead"]
        ws["A1"].font = Font(bold=True, size=13)
        ws.merge_cells("A2:B2")
        ws["A2"].alignment = wrap
        ws.row_dimensions[2].height = 48
        for i, text in enumerate(gaps["columns"], start=1):
            c = ws.cell(row=4, column=i, value=text)
            c.font = Font(bold=True)
        for r, (what, why) in enumerate(rows, start=5):
            for i, text in enumerate((what, why), start=1):
                ws.cell(row=r, column=i, value=str(text)).alignment = wrap
        ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 42, 78
        ws.page_setup.paperSize, ws.page_setup.orientation = ws.PAPERSIZE_A4, "landscape"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = "4:4"
        wb.save(path)


def gaps_printed(path: str, kind: str, gaps: dict) -> list[str]:
    """The filled file, opened again: the gaps that are NOT on its face under their heading (none, when it
    is as it should be)."""
    wanted = [str(what) for what, _ in gaps.get("rows") or []]
    if kind == "pdf":
        from . import pdf_form

        return pdf_form.gaps_printed(path, gaps, int(gaps.get("first_page") or 0))
    if kind == "docx":
        from docx import Document

        doc = Document(path)
        heading = any(_squash(p.text) == _squash(gaps["title"]) for p in doc.paragraphs)
        table = doc.tables[-1] if doc.tables else None
        listed = [(r.cells[0].text, r.cells[1].text) for r in table.rows[1:]] if table is not None and len(table.columns) == 2 else []
    elif kind == "xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path)
        ws = wb.worksheets[-1]
        heading = _squash(ws["A1"].value) == _squash(gaps["title"])
        listed = [(str(a or ""), str(b or "")) for a, b in ws.iter_rows(min_row=5, max_col=2, values_only=True)]
    else:
        return wanted
    if not heading:
        return wanted or [gaps["title"]]
    have = {(_squash(a), _squash(b)) for a, b in listed}
    return [str(what) for what, why in gaps.get("rows") or [] if (_squash(what), _squash(why)) not in have or not _squash(why)]
