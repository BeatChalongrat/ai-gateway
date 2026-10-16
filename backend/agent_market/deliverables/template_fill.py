"""Customer templates (AM2 Phase E): see what a Word / Excel / PowerPoint
template asks for, and fill it with the values a run produced.

inspect(path) lists the {{markers}}, Word content controls, Excel defined
names and tables a template carries. A template with none of them gets
proposed mapping targets — a label with an empty cell to its right, or
under it where the cell beside it holds something — for the mapping
screen. AM11: each target says whether it looks like a blank the form
expects filled (``target_info``: ``required``), where its label is, and on
which side; a required-looking blank left empty is a gap, mapped or not.
What is NOT read as a blank: a line of underscores or dots after a label in
running text ("ผู้จัดทำ: ______"), and the rows of a table under its first
empty row — unless the agent asks (AM12, form_regions, ``inspect(reads=…)``):
then a row of headings over empty rows is one place that takes a list
(``regions``), and in a Word form such a line is a blank (``lines``).

fill(path, out_path, values, mapping=None) fills markers (also one Word
split across runs, in headers, footers and table cells), repeating table
rows, plain-text content controls, named ranges and mapped targets. The
filling itself is done by the shared scripts (docx_generator.py,
excel_executor.py: imported, one implementation for chat and Agent
Market). The report says what was filled, what was refused and why, and
what could not be filled and why: a template is never filled silently
wrong, and never silently not at all.

Excel: input cells only. A formula cell and a locked cell of a protected
sheet are refused; the formulas are checked unchanged after the fill, and
needs_recalc says their computed values must be recalculated (the
LibreOffice converter does that, openpyxl cannot).
"""

from __future__ import annotations

import copy
import os
import re
import zipfile
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from pptx import Presentation

import docx_generator as dg  # /sandbox/scripts — shared
import excel_executor as ex  # /sandbox/scripts — shared

from ..engine.words import SHARED as S
from . import form_regions as fr  # AM12: the rows of a table of items, a blank drawn as a line

KINDS = {".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx", ".pdf": "pdf"}  # AM12: a PDF with fields (pdf_form)
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
A_P, A_T = A_NS + "p", A_NS + "t"
TARGET_RE = re.compile(r"^table(\d+)\.r(\d+)c(\d+)$")
MAX_TARGETS = 200
MAX_LABEL = 60
# Not-filled reasons that are only information: a computed or protected
# name nobody gave a value for is meant to stay as it is.
_INFO_ONLY = {ex.REFUSE_FORMULA, ex.REFUSE_PROTECTED}
_SAFE_XML = etree.XMLParser(resolve_entities=False, no_network=True)


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext not in KINDS:
        raise ValueError(f"not a template this can fill: {ext or path}")
    return KINDS[ext]


def _unique(keys) -> list[str]:
    out: list[str] = []
    for k in keys:
        if k not in out:
            out.append(k)
    return out


def _natural(name: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _part_label(partname: str) -> str:
    partname = str(partname).lstrip("/")
    if partname == "word/document.xml":
        return "body"
    return partname.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _part_order(name: str):
    for i, prefix in enumerate(("word/document.xml", "word/header", "word/footer", "ppt/slides/", "ppt/notesSlides/")):
        if name.startswith(prefix):
            return (i, _natural(name))
    return (9, _natural(name))


# AM8: where the filler writes. A marker anywhere else (a footnote, a chart's own XML, a comment) is
# not filled — the template is refused before work starts, never delivered with the marker in it.
FILLED_PARTS = {"docx": ("word/document.xml", "word/header", "word/footer"),
                "pptx": ("ppt/slides/", "ppt/notesSlides/", "ppt/slideLayouts/", "ppt/slideMasters/")}
# PowerPoint names a shape it adds "<what> <n>" ("TextBox 3", "กล่องข้อความ 3"): a shape a person
# renamed carries the name they gave it, and that name says what the shape is for.
AUTO_NAME = re.compile(r"^.+\s\d+$")


def _marker_parts(path: str) -> list[tuple[str, str, str]]:
    """(key, part name, the paragraph's text) for every {{key}} in every XML part of a .docx / .pptx."""
    out: list[tuple[str, str, str]] = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".xml") and "_rels/" not in n
                 and not n.startswith(("docProps/", "customXml/", "[Content_Types]"))]
        for name in sorted(names, key=_part_order):
            data = z.read(name)
            if b"{" not in data:
                continue
            root = etree.fromstring(data, _SAFE_XML)
            for p_tag, t_tag in ((dg.W_P, dg.W_T), (A_P, A_T)):
                for p in root.iter(p_tag):
                    keys = dg.markers_in(p, t_tag, p_tag)
                    if keys:
                        text = dg.paragraph_text(p, t_tag, p_tag)
                        out.extend((k, name, text) for k in keys)
    return out


def chart_parts(path: str) -> list[str]:
    """A Word or PowerPoint template's own charts: their numbers are stored in the file and are not
    the run's — the filler does not redraw them (an Excel chart reads its cells, so it is not here)."""
    with zipfile.ZipFile(path) as z:
        return sorted(n for n in z.namelist() if re.match(r"^(word|ppt)/charts/chart\d+\.xml$", n))


def _context(text: str, key: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:120]


def zip_markers(path: str) -> list[tuple[str, str]]:
    """(key, part) for every {{key}} in every XML part of a .docx / .pptx —
    body, headers, footers, footnotes, slides, notes, layouts, charts —
    also one split across runs."""
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".xml") and "_rels/" not in n
                 and not n.startswith(("docProps/", "customXml/", "[Content_Types]"))]
        for name in sorted(names, key=_part_order):
            data = z.read(name)
            if b"{" not in data:
                continue
            root = etree.fromstring(data, _SAFE_XML)
            for p_tag, t_tag in ((dg.W_P, dg.W_T), (A_P, A_T)):
                out.extend((key, _part_label(name)) for key in dg.markers_in(root, t_tag, p_tag))
    return out


def dropped_parts(src: str, out: str) -> list[str]:
    """Parts of the template the filled file no longer has (the library
    could not carry them over); calcChain.xml is rebuilt by Excel."""
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
        kept = set(b.namelist())
        return sorted(n for n in a.namelist() if n not in kept and not n.endswith("calcChain.xml")
                      and not n.endswith("/"))


# ─── Excel ─────────────────────────────────────────────────────
def _load_xlsx(path: str):
    return openpyxl.load_workbook(path, keep_vba=path.lower().endswith(".xlsm"), rich_text=True)


def _cells(ws):
    """The cells a sheet stores, in reading order (iter_rows would create
    every empty cell of the used range)."""
    return sorted(ws._cells.values(), key=lambda c: (c.row, c.column))


def _cell_text(cell) -> str | None:
    v = cell.value
    if cell.data_type != "s" or v is None:
        return None
    return v if isinstance(v, str) else str(v)  # CellRichText reads as its text


def _xlsx_markers(wb) -> list[tuple[str, str]]:
    out = []
    for ws in wb.worksheets:
        for cell in _cells(ws):
            text = _cell_text(cell)
            if text and "{{" in text:
                out.extend((m.group(1), f"{ws.title}!{cell.coordinate}") for m in dg.MARKER_RE.finditer(text))
    return out


def formulas(wb) -> dict[str, str]:
    """{"Sheet!A1": formula text} for every formula cell."""
    out = {}
    for ws in wb.worksheets:
        for cell in _cells(ws):
            if cell.data_type == "f":
                v = cell.value
                out[f"{ws.title}!{cell.coordinate}"] = v if isinstance(v, str) else getattr(v, "text", None) or repr(v)
    return out


def _label_of(text) -> str:
    return (text or "").strip().rstrip(":：").strip()


def _is_label(label: str) -> bool:
    return bool(label) and len(label) <= MAX_LABEL and "{{" not in label


def _drawn_as_blank(cell) -> bool:
    """A cell a form draws as a place to write: a border on any side, or a fill of its own."""
    if cell is None:
        return False
    b = cell.border
    if any(getattr(side, "style", None) for side in (b.left, b.right, b.top, b.bottom)):
        return True
    return bool(cell.fill is not None and cell.fill.fill_type)


def _xlsx_targets(wb, skip: set | None = None) -> tuple[list[dict[str, str]], dict[str, dict]]:
    """A text label with an empty, writable cell to its right — or under it, where the cell to its right
    holds something (a row of headings over a row of blanks).

    Returns the targets ({"cell", "label"}) and, per cell, what kind of blank it is (AM11): ``required``
    when the form marks it as a place to fill — the label ends with a colon, or the blank is drawn as one
    (a border, a fill) — so a title or a note with nothing beside it is offered for mapping but is not a
    gap when left empty; ``side`` and ``label_at`` say where its label is."""
    out: list[dict[str, str]] = []
    info: dict[str, dict] = {}

    def add(ws, row, col, label, side, required, label_cell, refused: str | None = None) -> bool:
        ref = f"{ws.title}!{get_column_letter(col)}{row}"
        if ref not in info:
            out.append({"cell": ref, "label": label})
            info[ref] = {"required": bool(required), "side": side, "label_at": f"{ws.title}!{label_cell.coordinate}"}
            merged = _merge_of(ws, row, col)
            if merged is not None:
                # AM22 §2.4: a blank that is a merged range is the range — its top-left takes the value, and the
                # filled file is read again to see the range is still one; a blank inside a range but not its
                # top-left is refused by name, never written to the top-left and hoped for
                info[ref]["merged"] = merged
            if refused:
                info[ref].update(fillable=False, reason=refused)
        return len(out) >= MAX_TARGETS

    skip = skip or set()
    for ws in wb.worksheets:
        labels = []
        for cell in _cells(ws):
            if (ws.title, cell.row) in skip:
                continue  # AM12: a heading or a row of a table of items (a region)
            text = _cell_text(cell)
            label = _label_of(text)
            if _is_label(label):
                labels.append((cell, label, (text or "").strip().endswith((":", "："))))
        at = {(c.row, c.column) for c, _, _ in labels}

        def free(row, col):
            c = ex.peek_cell(ws, row, col)
            return (ws.title, row) not in skip and (c is None or c.value is None) and ex.cell_refusal(ws, c) is None

        def in_a_list(cell):
            # one of a column of labels, each with a blank beside it: a list of fields, however plain
            return any((cell.row + d, cell.column) in at and free(cell.row + d, cell.column + 1) for d in (-1, 1))

        later = []
        # 1. a blank beside its label that the form marks as one: a colon, a drawn cell, a list of fields.
        # AM22: "beside" is right of the label's own merged range, when the label is one (a label drawn across
        # several columns to a blank at the end of the row was never read before: its first neighbour is its own merge)
        for cell, label, colon in labels:
            nxt = _after_label(ws, cell)
            beside = ex.peek_cell(ws, cell.row, nxt)
            if isinstance(beside, ex.MergedCell) and (ws.title, cell.row) not in skip and (
                    colon or _drawn_as_blank(_top_left(ws, cell.row, nxt))):
                # AM22: the label's blank is inside a merged range and is not its top-left — named, refused
                if add(ws, cell.row, nxt, label, "right", True, cell, refused="merged_cell"):
                    return out, info
                continue
            if not free(cell.row, nxt):
                later.append((cell, label, False))
            elif colon or _drawn_as_blank(ex.peek_cell(ws, cell.row, nxt)) or in_a_list(cell):
                if add(ws, cell.row, nxt, label, "right", True, cell):
                    return out, info
            else:
                later.append((cell, label, True))
        # 2. a drawn blank under a heading; 3. else the plain cell beside it — offered, not expected
        for cell, label, beside in later:
            if free(cell.row + 1, cell.column) and _drawn_as_blank(ex.peek_cell(ws, cell.row + 1, cell.column)):
                if add(ws, cell.row + 1, cell.column, label, "below", True, cell):
                    return out, info
            elif beside:
                if add(ws, cell.row, _after_label(ws, cell), label, "right", False, cell):
                    return out, info
    return out, info


def _after_label(ws, cell) -> int:
    """The column right of a label: past its own merged range when the label is the top-left of one."""
    for rng in ws.merged_cells.ranges:
        if rng.min_row == cell.row and rng.min_col == cell.column:
            return rng.max_col + 1
    return cell.column + 1


def _merge_of(ws, row: int, col: int) -> str | None:
    """The merged range a cell is part of ("B5:D5"), or None."""
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= row <= rng.max_row and rng.min_col <= col <= rng.max_col:
            return rng.coord
    return None


def _top_left(ws, row: int, col: int):
    """The top-left cell of the merged range a cell is part of (the cell itself when none)."""
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= row <= rng.max_row and rng.min_col <= col <= rng.max_col:
            return ws.cell(row=rng.min_row, column=rng.min_col)
    return ex.peek_cell(ws, row, col)


def still_merged(path: str, ref: str, merged: str) -> bool:
    """AM22: the filled file, opened again — is the blank's merged range still one range?"""
    sheet = ref.rpartition("!")[0].strip("'").replace("''", "'")
    wb = openpyxl.load_workbook(path)
    return sheet in wb.sheetnames and any(r.coord == merged for r in wb[sheet].merged_cells.ranges)


# ─── AM22 §2.2: what this round has no concept of ─────────────
def _docx_row_paragraphs(doc, table: int, row: int) -> tuple[int, int]:
    """(the paragraph index after the row, how many paragraphs the row holds), counted as fr.moved_key counts."""
    tr = doc.tables[table].rows[row]._tr
    at, n = 0, 0
    for i, p in enumerate(fr._paragraphs(doc), start=1):
        if any(a is tr for a in p.iterancestors()):
            n += 1
            at = i
    return at + 1, n


def drop_not_applicable(path: str, kind: str, slots: list[dict]) -> tuple[list[dict], list[dict]]:
    """Take the places a round has no concept of off the filled form's face (AM22 §2.2) — the package said
    which (template_gaps "not_applicable"): an Excel row is hidden (its formulas stay where they are), a Word
    table row that holds nothing but the place and its label is removed, the rows of a table of items under
    their heading are removed; a PDF field cannot be taken off its page and is left empty. Every one is
    returned with what was done ({"slot", "label", "field", "why", "done"}), for the run's record — none is
    printed as a gap. Returns (that list, the moves of what stood under a removed Word row)."""
    done: list[dict] = []
    moves: list[dict] = []
    if not slots:
        return done, moves
    if kind == "xlsx":
        wb = _load_xlsx(path)
        regions = {g["key"]: g for g in fr.xlsx_regions(wb)} if any(fr.ROWS_RE.match(s["key"]) for s in slots) else {}
        for s in slots:
            rows: list[tuple[str, int]] = []
            if fr.ROWS_RE.match(s["key"]) and s["key"] in regions:
                g = regions[s["key"]]
                rows = [(g["sheet"], r) for r in range(g["header"], g["first"] + g["rows"])]
            elif "!" in s["key"]:
                for ref in (s["key"], s.get("label_at") or ""):
                    if "!" in ref:
                        sheet, _, coord = ref.rpartition("!")
                        rows.append((sheet.strip("'").replace("''", "'"), coordinate_from_string(coord)[1]))
            for sheet, r in set(rows):
                if sheet in wb.sheetnames:
                    wb[sheet].row_dimensions[r].hidden = True
            done.append({**s, "done": "hidden" if rows else "left_blank"})
        wb.save(path)
    elif kind == "docx":
        doc = Document(path)
        regions = {g["key"]: g for g in fr.docx_regions(doc)} if any(fr.ROWS_RE.match(s["key"]) for s in slots) else {}
        drop: list[tuple[int, int, dict]] = []
        for s in slots:
            m = TARGET_RE.match(s["key"])
            if fr.ROWS_RE.match(s["key"]) and s["key"] in regions:
                g = regions[s["key"]]
                table = doc.tables[g["table"]]
                if g["first"] + g["rows"] - g["header"] >= len(table.rows):
                    done.append({**s, "done": "left_blank"})   # the table is nothing but the list: it stays, empty
                    continue
                drop += [(g["table"], r, s) for r in range(g["header"], g["first"] + g["rows"])]
            elif m:
                ti, r, c = (int(x) for x in m.groups())
                cells = fr._distinct(doc.tables[ti].rows[r].cells)
                lm = TARGET_RE.match(s.get("label_at") or "")
                own = {c} | ({int(lm.group(3))} if lm and int(lm.group(2)) == r else set())
                if all(ci in own or not cell.text.strip() for ci, cell in cells):
                    drop.append((ti, r, s))
                else:
                    done.append({**s, "done": "left_blank"})   # the row holds another place: only this one stays empty
            else:
                done.append({**s, "done": "left_blank"})
        seen = set()
        for ti, r, s in sorted(drop, key=lambda x: (x[0], x[1]), reverse=True):
            if (ti, r) in seen:
                continue
            seen.add((ti, r))
            after, n = _docx_row_paragraphs(doc, ti, r)
            tr = doc.tables[ti].rows[r]._tr
            tr.getparent().remove(tr)
            moves.append({"table": ti, "from_row": r + 1, "rows": -1, "from_paragraph": after, "paragraphs": -n})
        for s in {id(s): s for _, _, s in drop}.values():
            done.append({**s, "done": "removed"})
        doc.save(path)
    else:
        done += [{**s, "done": "left_blank"} for s in slots]
    # the moves are applied from the bottom up, so a key is moved by each removal above it, the lowest first
    return done, sorted(moves, key=lambda mv: (mv["table"], -mv["from_row"]))


def not_applicable_off(path: str, kind: str, item: dict) -> bool:
    """The filled file, opened again: is a place the round has no concept of off the form's face?"""
    if item["done"] == "left_blank":
        return still_blank(path, kind, item) if kind != "xlsx" and not fr.ROWS_RE.match(item["key"]) else True
    if kind == "xlsx":
        wb = openpyxl.load_workbook(path)
        ref = item["key"] if "!" in item["key"] else ""
        if not ref:
            return True
        sheet, _, coord = ref.rpartition("!")
        sheet = sheet.strip("'").replace("''", "'")
        r = coordinate_from_string(coord)[1]
        return sheet in wb.sheetnames and bool(wb[sheet].row_dimensions[r].hidden)
    if kind == "docx":
        doc = Document(path)
        text = "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
        return _squash(item.get("label")) not in _squash(text)
    return True


def _xlsx_tables(wb, names: list[dict]) -> list[dict[str, Any]]:
    out = [{"name": n["key"], "ref": n["ref"], "kind": "named_range"} for n in names if n["cells"] > 1]
    for ws in wb.worksheets:
        out.extend({"name": t.name, "ref": f"{ws.title}!{t.ref}", "kind": "table"} for t in ws.tables.values())
    return out


def _fill_xlsx_markers(wb, values, mapping, report) -> None:
    for ws in wb.worksheets:
        for cell in _cells(ws):
            text = _cell_text(cell)
            if not text or "{{" not in text:
                continue
            where = f"{ws.title}!{cell.coordinate}"
            found = list(dg.MARKER_RE.finditer(text))
            if not found:
                continue
            vals = {m.group(1): dg.scalar_value(values, mapping, m.group(1)) for m in found}
            missing = [k for k, v in vals.items() if dg.as_text(v) is None]
            for k in missing:
                report["not_filled_explained"].append({"marker": k, "reason": dg.why_not(vals[k]), "where": where})
            if len(found) == 1 and found[0].group(0) == text.strip() and not missing:
                new, numbers = vals[found[0].group(1)], True  # the cell is the value: a number stays a number
            else:
                def put(m, vals=vals):
                    t = dg.as_text(vals[m.group(1)])
                    return m.group(0) if t is None else t

                new, numbers = dg.MARKER_RE.sub(put, text), False
            if new == text:
                continue
            ref = "'" + ws.title.replace("'", "''") + "'!" + cell.coordinate
            entry = ex.write_name(wb, ref, new, numbers=numbers)
            if entry["status"] == "written":
                report["filled"].extend(k for k in vals if k not in missing and k not in report["filled"])
            else:
                report["refused"][where] = entry["reason"]


_AMOUNT = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})+(\.\d+)?\)?$|^\(?-?\d+\.\d{2}\)?$")


def _amount_formats(wb, todo: dict, written) -> None:
    """AM11: a value that was handed over as an amount ("12,480,300.00") is written as a number, and a
    blank of an unmarked form says nothing about how to show one — it would read 12480300. Such a cell
    is given the amount's own format (thousands separated, its decimals). A cell whose form already
    chose a format keeps it, and so does every cell of a marked form's name the customer formatted."""
    for ref in written:
        value = todo.get(ref)
        if not isinstance(value, str) or "!" not in ref:
            continue
        m = _AMOUNT.match(value.strip())
        if not m:
            continue
        cell = _xlsx_cell(wb, ref)
        if cell is None or cell.number_format != "General" or not isinstance(cell.value, (int, float)):
            continue
        decimals = len(m.group(1)) - 1 if m.group(1) else (2 if "." in value else 0)
        cell.number_format = "#,##0" + ("." + "0" * decimals if decimals else "")


def _fill_xlsx(path, out_path, values, mapping, report) -> None:
    wb = _load_xlsx(path)
    before = formulas(wb)
    todo: dict[str, Any] = {}
    for n in ex.list_defined_names(wb):
        value = dg.template_value(values, mapping, n["key"], n["name"])
        if value is dg.MISSING:
            report["not_filled_explained"].append({"name": n["key"], "reason": n["reason"] or "no_value",
                                                   "ref": n["ref"]})
        else:
            todo[n["key"]] = value
    # Targets picked on the mapping screen ("Sheet1!B1" -> values key), or
    # values given for a reference directly.
    for ref, src in mapping.items():
        if "!" in str(ref) and ref not in todo:
            value = values.get(src, dg.MISSING)
            if value is dg.MISSING:
                report["not_filled_explained"].append({"name": ref, "reason": "no_value"})
            else:
                todo[ref] = value
    for ref, value in values.items():
        if "!" in str(ref) and ref not in todo and ref not in mapping:
            todo[ref] = value
    # AM12: the lines of a list, into the rows the form draws under a row of headings
    report.setdefault("rows", {})
    report.setdefault("rows_short", {})
    region_cells: dict[str, list[str]] = {}
    for key, lines in values.items():
        if fr.ROWS_RE.match(str(key)) and isinstance(lines, list):
            cells, res = fr.xlsx_row_cells(wb, key, lines)
            todo.update(cells)
            region_cells[key] = list(cells)
            report["rows"][key] = res["written"]
            if res["short"]:
                report["rows_short"][key] = res["short"]
    res = ex.write_names(wb, todo)
    of_a_region = {c for cells in region_cells.values() for c in cells}
    report["filled"].extend(k for k in res["written"] if k not in of_a_region)
    report["filled"].extend(k for k, cells in region_cells.items() if cells and all(c in res["written"] for c in cells))
    report["refused"].update(res["refused"])
    _amount_formats(wb, todo, res["written"])
    _fill_xlsx_markers(wb, values, mapping, report)
    if before:
        wb.calculation.fullCalcOnLoad = True  # Excel recomputes on open
    wb.save(out_path)
    after = formulas(_load_xlsx(out_path))
    report["formulas_kept"] = [k for k, f in before.items() if after.get(k) == f]
    changed = sorted(k for k in before if after.get(k) != before[k])
    if changed:
        report["formulas_changed"] = changed
    report["needs_recalc"] = bool(before)


# ─── Word ──────────────────────────────────────────────────────
def _docx_tables(doc) -> list[dict[str, Any]]:
    out = []
    for i, t in enumerate(doc.tables):
        repeat = None
        for r, tr in enumerate(t._tbl.tr_lst):
            name = dg.row_list_name(tr)
            if name:
                repeat = {"row": r, "list": name}
                break
        out.append({"index": i, "rows": len(t.rows), "cols": len(t.columns), "repeat_row": repeat})
    return out


def _docx_targets(doc, skip: set | None = None) -> tuple[list[dict[str, str]], dict[str, dict]]:
    """A table cell right of a label cell — or under it, where the cell to its right is not empty (a row
    of headings over a row of blanks): {"cell": "table0.r0c1", "label"}, and per cell what kind of blank
    it is (AM11, see _xlsx_targets). A table of a Word form is a grid of fields: every such blank is one
    the form expects filled."""
    out: list[dict[str, str]] = []
    info: dict[str, dict] = {}

    def add(ti, r, c, label, side, lr, lc) -> bool:
        ref = f"table{ti}.r{r}c{c}"
        if ref not in info:
            out.append({"cell": ref, "label": label})
            info[ref] = {"required": True, "side": side, "label_at": f"table{ti}.r{lr}c{lc}"}
        return len(out) >= MAX_TARGETS

    skip = skip or set()
    for ti, table in enumerate(doc.tables):
        grid = [row.cells for row in table.rows]
        under = []
        for ri, cells in enumerate(grid):
            if (ti, ri) in skip:
                continue  # AM12: a heading or a row of a table of items (a region)
            for ci, a in enumerate(cells):
                if ci > 0 and cells[ci - 1]._tc is a._tc:
                    continue  # the same merged cell, seen again
                label = _label_of(a.text)
                if not _is_label(label):
                    continue
                b = cells[ci + 1] if ci + 1 < len(cells) else None
                if b is not None and b._tc is a._tc:
                    continue
                if b is not None and not b.text.strip():
                    if add(ti, ri, ci + 1, label, "right", ri, ci):
                        return out, info
                else:  # the next cell holds something, or this is the last column: a heading, perhaps
                    under.append((ri, ci, a, label))
        for ri, ci, a, label in under:
            if ri + 1 >= len(grid) or ci >= len(grid[ri + 1]):
                continue
            below = grid[ri + 1][ci]
            if below._tc is a._tc or below.text.strip():
                continue
            if add(ti, ri + 1, ci, label, "below", ri, ci):
                return out, info
    return out, info


_PARA_MARK_ONLY = {qn("w:ins"), qn("w:del"), qn("w:moveFrom"), qn("w:moveTo"), qn("w:rPrChange")}


def _set_docx_target(doc, target: str, text: str) -> str | None:
    """Put text in a table cell (table<i>.r<row>c<col>); the reason when
    there is no such cell."""
    ti, r, c = (int(x) for x in TARGET_RE.match(target).groups())
    if ti >= len(doc.tables):
        return "unknown_target"
    table = doc.tables[ti]
    if r >= len(table.rows) or c >= len(table.columns):
        return "unknown_target"
    p = table.cell(r, c).paragraphs[0]
    if p.runs:
        p.runs[0].text = text
        for run in p.runs[1:]:
            run.text = ""
        return None
    run = p.add_run(text)
    ppr = p._p.pPr
    mark = ppr.find(qn("w:rPr")) if ppr is not None else None
    if mark is not None:  # the empty cell's own formatting
        rpr = copy.deepcopy(mark)
        for el in list(rpr):
            if el.tag in _PARA_MARK_ONLY:
                rpr.remove(el)
        run._r.insert(0, rpr)
    return None


def _fill_docx(path, out_path, values, mapping, report) -> None:
    doc = Document(path)
    ph = dg.fill_placeholders(doc, values, mapping)
    report["filled"].extend(ph["filled"])
    report["rows"] = ph["rows"]
    report["not_filled_explained"].extend(ph["not_filled"])
    cc = dg.fill_content_controls(doc, values, mapping)
    report["filled"].extend(c["tag"] or c["alias"] for c in cc["filled"])
    report["not_filled_explained"].extend(cc["not_filled"])
    # AM12: a value onto a drawn line
    report.setdefault("rows_short", {})
    drawn = {k: dg.as_text(v) for k, v in values.items() if fr.LINE_RE.match(str(k)) and (dg.as_text(v) or "").strip()}
    done, refused = fr.fill_docx_lines(doc, drawn)
    report["filled"].extend(done)
    report["refused"].update(refused)
    targets = {t: values.get(src, dg.MISSING) for t, src in mapping.items() if TARGET_RE.match(str(t))}
    targets.update({t: v for t, v in values.items() if TARGET_RE.match(str(t)) and t not in targets})
    for target, value in targets.items():
        text = dg.as_text(value)
        if text is None:
            report["not_filled_explained"].append({"target": target, "reason": dg.why_not(value)})
            continue
        why = _set_docx_target(doc, target, text)
        if why:
            report["refused"][target] = why
        else:
            report["filled"].append(target)
    # AM12: the lines of a list, into the rows under a row of headings — last, and the tables from the end
    # of the form backwards: a row added to a table moves what stands under it, and everything else was
    # written where the form was read. ``moved`` says by how much, for whoever opens the file again.
    report["moved"] = []
    regions = sorted((k for k, v in values.items() if fr.ROWS_RE.match(str(k)) and isinstance(v, list)),
                     key=lambda k: -int(fr.ROWS_RE.match(k).group(2)))
    for key in regions:
        res = fr.fill_docx_rows(doc, key, values[key], lambda t, r, c, text: _set_docx_target(doc, f"table{t}.r{r}c{c}", text))
        report["rows"][key] = res["written"]
        if res["short"]:
            report["rows_short"][key] = res["short"]
        if res.get("moved"):
            report["moved"].append(res["moved"])
        if res["written"]:
            report["filled"].append(key)
        else:
            report["refused"][key] = res.get("reason") or "no_value"
    doc.save(out_path)


# ─── PowerPoint ────────────────────────────────────────────────
def _pptx_tables(prs) -> list[dict[str, Any]]:
    out = []
    for si, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if getattr(shape, "has_table", False) and shape.has_table:
                t = shape.table
                out.append({"slide": si, "shape": shape.name, "rows": len(t.rows), "cols": len(t.columns)})
    return out


def _named_shapes(prs) -> list[dict[str, Any]]:
    """Text shapes a person named (see AUTO_NAME), on slides: {"key", "name", "slide", "text"}."""
    out, seen = [], set()
    for si, slide in enumerate(prs.slides, start=1):
        for sh in slide.shapes:
            name = (sh.name or "").strip()
            if not name or AUTO_NAME.match(name) or not getattr(sh, "has_text_frame", False) or not sh.has_text_frame:
                continue
            if "{{" in sh.text_frame.text:
                continue          # its markers are the slots
            key = "shape:" + name
            if key in seen:
                continue
            seen.add(key)
            out.append({"key": key, "name": name, "slide": si, "text": sh.text_frame.text[:120]})
    return out


def _set_shape_text(sh, text: str) -> None:
    """A named shape's text becomes `text` in its first run, keeping that run's formatting."""
    tf_ = sh.text_frame
    paras = list(tf_.paragraphs)
    first = paras[0]
    if first.runs:
        first.runs[0].text = text
        for r in first.runs[1:]:
            r.text = ""
    else:
        first.text = text
    for p in paras[1:]:
        p._p.getparent().remove(p._p)


def _fill_pptx(path, out_path, values, mapping, report) -> None:
    prs = Presentation(path)
    stories = []
    for slide in prs.slides:
        stories.append((_part_label(slide.part.partname), slide._element))
        if slide.has_notes_slide:
            notes = slide.notes_slide
            stories.append((_part_label(notes.part.partname), notes._element))
    # AM8: the masters and layouts too — a marker on a master was reported as filled and left in place
    for master in prs.slide_masters:
        stories.append((_part_label(master.part.partname), master._element))
        for layout in master.slide_layouts:
            stories.append((_part_label(layout.part.partname), layout._element))
    for s in _named_shapes(prs):
        if s["key"] not in values:
            continue
        text = dg.as_text(values[s["key"]])
        if text is None:
            report["not_filled_explained"].append({"name": s["key"], "reason": dg.why_not(values[s["key"]])})
            continue
        for slide in prs.slides:
            for sh in slide.shapes:
                if (sh.name or "").strip() == s["name"] and getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                    _set_shape_text(sh, text)
        report["filled"].append(s["key"])
    for where, root in stories:
        for p in root.iter(A_P):
            done = dg.replace_markers_in_paragraph(
                p, lambda k: dg.as_text(dg.scalar_value(values, mapping, k)), A_T, A_P)
            for key, ok in done:
                if ok:
                    if key not in report["filled"]:
                        report["filled"].append(key)
                else:
                    report["not_filled_explained"].append(
                        {"marker": key, "reason": dg.why_not(dg.scalar_value(values, mapping, key)), "where": where})
    prs.save(out_path)


# ─── Reading the filled file back (AM8) ───────────────────────
# The gate does not take the filler's word: these open the delivered file and look.

def _squash(text) -> str:
    return re.sub(r"\s+", "", "" if text is None else str(text))


def _same_value(cell_value, value) -> bool:
    from decimal import Decimal, InvalidOperation

    if cell_value is None:
        return False
    try:
        if isinstance(value, (int, float, Decimal)) or isinstance(cell_value, (int, float)):
            return Decimal(str(cell_value).replace(",", "")) == Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        pass
    want = _squash(dg.as_text(value))
    return bool(want) and want in _squash(cell_value)


def _xlsx_cell(wb, ref: str):
    sheet, _, coord = ref.rpartition("!")
    sheet = sheet.strip("'").replace("''", "'")
    coord = coord.replace("$", "").split(":")[0]
    return wb[sheet][coord] if sheet in wb.sheetnames else None


def _all_text(path: str, kind: str) -> str:
    parts = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".xml") and name.startswith(FILLED_PARTS[kind]):
                root = etree.fromstring(z.read(name), _SAFE_XML)
                for p_tag, t_tag in ((dg.W_P, dg.W_T), (A_P, A_T)):
                    parts.extend(dg.paragraph_text(p, t_tag, p_tag) for p in root.iter(p_tag))
    return " ".join(parts)


def _text_at(path: str, kind: str, ref: str):
    """The text a file holds at a target's address ("Sheet1!B2", "table0.r1c1"); None when it has no
    such place."""
    if kind == "xlsx":
        cell = _xlsx_cell(openpyxl.load_workbook(path), ref)
        return None if cell is None else ("" if cell.value is None else str(cell.value))
    m = TARGET_RE.match(ref or "")
    if kind != "docx" or not m:
        return None
    ti, r, c = (int(x) for x in m.groups())
    try:
        return Document(path).tables[ti].cell(r, c).text
    except IndexError:
        return None


def still_blank(path: str, kind: str, slot: dict) -> bool:
    """AM11: the filled file, opened again — does this blank of the form still hold nothing?"""
    if kind == "pdf":
        from . import pdf_form

        return pdf_form.still_blank(path, slot)
    if kind == "docx" and fr.LINE_RE.match(slot["key"]):
        return fr.docx_line_blank(Document(path), slot)
    text = _text_at(path, kind, slot["key"])
    return text is not None and not text.strip()


def label_beside(path: str, kind: str, slot: dict) -> bool:
    """AM11: the filled file, opened again — is the label this blank was read under still where it was?
    A value written next to another label than the one it was mapped for is in the wrong slot."""
    if kind == "docx" and fr.LINE_RE.match(slot["key"]):
        return fr.docx_line_labelled(Document(path), slot)
    if kind == "pdf":
        return True  # a field is its own place: a value cannot be written into it beside another label
    at = slot.get("label_at")
    if not at:
        return True
    return _squash(_label_of(_text_at(path, kind, at))) == _squash(slot.get("label"))


def holds(path: str, kind: str, slot: dict, value) -> bool:
    """Does the filled file at ``path`` hold ``value`` where ``slot`` is?"""
    key, sk = slot["key"], slot["kind"]
    if kind == "pdf":
        from . import pdf_form

        return pdf_form.holds(path, slot, value)
    if kind == "xlsx":
        wb = openpyxl.load_workbook(path)
        if sk in ("name", "table"):
            n = next((x for x in ex.list_defined_names(wb) if x["key"] == key), None)
            cell = _xlsx_cell(wb, n["ref"]) if n and n.get("ref") else None
            return cell is not None and _same_value(cell.value, value)
        if sk == "target" or "!" in key:
            cell = _xlsx_cell(wb, key)
            return cell is not None and _same_value(cell.value, value)
        return any(_same_value(c.value, value) for ws in wb.worksheets for c in _cells(ws))
    if kind == "docx" and sk == "control":
        doc = Document(path)
        hits = [c for c in dg.list_content_controls(doc) if key in (c.get("tag"), c.get("alias"))]
        return bool(hits) and all(_squash(c.get("text")) == _squash(dg.as_text(value)) for c in hits)
    if kind == "docx" and fr.LINE_RE.match(key):
        return fr.docx_line_holds(Document(path), slot, value)
    if kind == "docx" and sk == "target":
        m = TARGET_RE.match(key)
        doc = Document(path)
        if not m:
            return False
        ti, r, c = (int(x) for x in m.groups())
        try:
            return _squash(doc.tables[ti].cell(r, c).text) == _squash(dg.as_text(value))
        except IndexError:
            return False
    if kind == "pptx" and sk == "shape":
        prs = Presentation(path)
        name = key.split(":", 1)[1]
        texts = [sh.text_frame.text for sl in prs.slides for sh in sl.shapes
                 if (sh.name or "").strip() == name and getattr(sh, "has_text_frame", False) and sh.has_text_frame]
        return bool(texts) and all(_squash(t) == _squash(dg.as_text(value)) for t in texts)
    want = _squash(dg.as_text(value))
    return bool(want) and want in _squash(_all_text(path, kind))


def markers_left(path: str) -> list[str]:
    """Every {{marker}} still in the filled file, in any part (masters, footnotes, charts included)."""
    if kind_of(path) == "pdf":
        return []
    if kind_of(path) == "xlsx":
        return _unique(k for k, _ in _xlsx_markers(_load_xlsx(path)))
    return _unique(k for k, _, _ in _marker_parts(path))


def cut_off(src: str, out: str) -> list[str]:
    """Filled text that no longer fits its box: a Word table row of fixed height (hRule="exact"), a
    PowerPoint text box — measured with the font's metrics, only where the fill changed the text."""
    if kind_of(out) == "pdf":
        return []  # (pdf_form reports a value too long for its field itself: report["cut"])
    from .fit import LINE, frame_overflow, wrap_lines

    kind = kind_of(out)
    found: list[str] = []
    if kind == "docx":
        before, after = Document(src), Document(out)
        default = after.styles["Normal"].font.size.pt if after.styles["Normal"].font.size else 11.0
        for ti, (tb, ta) in enumerate(zip(before.tables, after.tables)):
            for ri, (rb, ra) in enumerate(zip(tb.rows, ta.rows)):
                trh = ra._tr.find(qn("w:trPr"))
                h = trh.find(qn("w:trHeight")) if trh is not None else None
                if h is None or h.get(qn("w:hRule")) != "exact":
                    continue
                height_pt = int(h.get(qn("w:val")) or 0) / 20
                for ci, (cb, ca) in enumerate(zip(rb.cells, ra.cells)):
                    if _squash(cb.text) == _squash(ca.text):
                        continue
                    w = ca._tc.tcPr.find(qn("w:tcW")) if ca._tc.tcPr is not None else None
                    width_pt = (int(w.get(qn("w:w"))) / 20 if w is not None and w.get(qn("w:type")) in (None, "dxa")
                                and (w.get(qn("w:w")) or "0").isdigit() and int(w.get(qn("w:w"))) > 0
                                else (after.sections[0].page_width - after.sections[0].left_margin
                                      - after.sections[0].right_margin) / 12700 / max(1, len(ra.cells)))
                    size = default
                    for p in ca.paragraphs:
                        for r in p.runs:
                            if r.font.size:
                                size = r.font.size.pt
                    lines = sum(wrap_lines(p.text, "Sarabun", size, max(width_pt - 10.8, 10)) for p in ca.paragraphs if p.text)
                    if lines * size * LINE > height_pt * 1.02:
                        found.append(S("form.cut.cell", table=ti + 1, row=ri + 1, cell=ci + 1))
    elif kind == "pptx":
        before, after = Presentation(src), Presentation(out)
        for si, (sb, sa) in enumerate(zip(before.slides, after.slides), start=1):
            old = {sh.shape_id: sh.text_frame.text for sh in sb.shapes if getattr(sh, "has_text_frame", False) and sh.has_text_frame}
            for sh in sa.shapes:
                if not (getattr(sh, "has_text_frame", False) and sh.has_text_frame):
                    continue
                if _squash(old.get(sh.shape_id)) == _squash(sh.text_frame.text) or sh.width is None or sh.height is None:
                    continue
                if frame_overflow(sh.text_frame, sh.width, sh.height, "Sarabun") > 1.02:
                    found.append(S("form.cut.shape", slide=si, name=(sh.name or "").strip()))
    return found


# ─── Public ────────────────────────────────────────────────────
def inspect(path: str, reads: tuple | list = ()) -> dict[str, Any]:
    """What a template asks for: {"kind", "markers", "controls", "names",
    "tables", "has_markers", "targets"}; targets (the mapping screen's
    proposals) only for a template with nothing to fill by itself, with
    "target_info" (per cell: required, side, label_at).

    AM12: ``reads`` is what else the agent asking reads in a form with no marker (its manifest's
    templates.reads): "rows" — a row of headings over empty rows is one place that takes a list
    ("regions"); "lines" — in a Word form, a line drawn after a label is a blank ("lines"). An agent
    that does not ask reads a form exactly as before."""
    kind = kind_of(path)
    if kind == "pdf":
        from . import pdf_form

        return pdf_form.inspect(path)
    out: dict[str, Any] = {"kind": kind, "markers": [], "controls": [], "names": [], "tables": [],
                           "has_markers": False, "targets": []}
    if kind == "xlsx":
        wb = _load_xlsx(path)
        out["markers"] = _unique(k for k, _ in _xlsx_markers(wb))
        out["names"] = ex.list_defined_names(wb)
        out["tables"] = _xlsx_tables(wb, out["names"])
        out["has_markers"] = bool(out["markers"]) or any(n["fillable"] for n in out["names"])
        if not out["has_markers"]:
            out["regions"] = fr.xlsx_regions(wb) if "rows" in reads else []
            out["targets"], out["target_info"] = _xlsx_targets(wb, fr.xlsx_region_rows(out["regions"]))
        return out
    found = _marker_parts(path)
    supported = FILLED_PARTS[kind]
    out["markers"] = _unique(k for k, part, _ in found if part.startswith(supported))
    out["context"] = {}
    for k, part, text in found:
        out["context"].setdefault(k, _context(text, k))
    # AM8: refused before work starts, each with its reason — never silently left in the delivered file
    out["refused"] = [{"key": f"{part}:{k}", "label": k, "reason": "unsupported_location", "where": part}
                      for k, part, _ in found if not part.startswith(supported)]
    out["refused"] += [{"key": c, "label": S("form.chart.n", i=i), "reason": "chart_not_updated", "where": c}
                       for i, c in enumerate(chart_parts(path), start=1)]
    if kind == "docx":
        doc = Document(path)
        out["controls"] = [{k: c[k] for k in ("tag", "alias", "type", "fillable", "reason", "showing_placeholder",
                                              "where")} for c in dg.list_content_controls(doc)]
        out["tables"] = _docx_tables(doc)
        out["has_markers"] = bool(out["markers"]) or any(c["fillable"] for c in out["controls"])
        if not out["has_markers"]:
            out["regions"] = fr.docx_regions(doc) if "rows" in reads else []
            out["targets"], out["target_info"] = _docx_targets(doc, fr.docx_region_rows(out["regions"]))
            out["lines"] = fr.docx_lines(doc) if "lines" in reads else []
    else:
        prs = Presentation(path)
        out["tables"] = _pptx_tables(prs)
        out["shapes"] = _named_shapes(prs)
        out["has_markers"] = bool(out["markers"]) or bool(out["shapes"])
    return out


def fill(path: str, out_path: str, values: dict | None, mapping: dict | None = None) -> dict[str, Any]:
    """Fill a template into out_path (the template itself is not changed).

    values: {key: text or number, "lines": [{field: …}, …]}. mapping:
    template key (marker, control tag or alias, defined name, mapped target
    such as "table0.r0c1" or "Sheet1!B1") -> values key.

    Returns {"kind", "filled": [keys], "refused": {key: reason},
    "not_filled_explained": [{marker|tag|name|target, reason, …}],
    "needs_recalc", "formulas_kept", "mapping_screen", "targets",
    "parts_dropped"} (+ "rows" — lines written per list — and "rows_short" —
    lines a form had no row for; "formulas_changed" if a formula changed — it
    must not)."""
    kind = kind_of(path)
    values, mapping = dict(values or {}), dict(mapping or {})
    if kind == "pdf":
        from . import pdf_form

        return pdf_form.fill(path, out_path, values)
    report: dict[str, Any] = {"kind": kind, "filled": [], "refused": {}, "not_filled_explained": [],
                              "needs_recalc": False, "formulas_kept": [], "mapping_screen": False, "targets": []}
    if kind == "docx":
        _fill_docx(path, out_path, values, mapping, report)
    elif kind == "xlsx":
        _fill_xlsx(path, out_path, values, mapping, report)
    else:
        _fill_pptx(path, out_path, values, mapping, report)
    if kind != "xlsx":
        # A marker somewhere the filler does not write (a footnote, a
        # layout, a chart title) is still reported.
        known = {(e.get("marker"), e.get("where")) for e in report["not_filled_explained"]}
        for key, where in zip_markers(out_path):
            if (key, where) not in known:
                known.add((key, where))
                report["not_filled_explained"].append({"marker": key, "reason": "unsupported_location",
                                                       "where": where})
    report["parts_dropped"] = dropped_parts(path, out_path)
    if not report["filled"] and not report["refused"] and not report["not_filled_explained"]:
        report["not_filled_explained"].append({"reason": "no_markers"})  # never a silent no-op
    actionable = [e for e in report["not_filled_explained"] if e.get("reason") not in _INFO_ONLY]
    report["mapping_screen"] = not report["filled"] or bool(actionable)
    if report["mapping_screen"]:
        report["targets"] = inspect(path)["targets"]
    return report
