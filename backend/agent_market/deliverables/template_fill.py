"""Customer templates (AM2 Phase E): see what a Word / Excel / PowerPoint
template asks for, and fill it with the values a run produced.

inspect(path) lists the {{markers}}, Word content controls, Excel defined
names and tables a template carries. A template with none of them gets
proposed mapping targets — a label with an empty cell to its right — for
the mapping screen.

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
from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from pptx import Presentation

import docx_generator as dg  # /sandbox/scripts — shared
import excel_executor as ex  # /sandbox/scripts — shared

KINDS = {".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx"}
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


def _xlsx_targets(wb) -> list[dict[str, str]]:
    """A text label with an empty, writable cell to its right."""
    out: list[dict[str, str]] = []
    for ws in wb.worksheets:
        for cell in _cells(ws):
            text = _cell_text(cell)
            label = (text or "").strip().rstrip(":：").strip()
            if not label or len(label) > MAX_LABEL or "{{" in label:
                continue
            right = ex.peek_cell(ws, cell.row, cell.column + 1)
            if right is not None and right.value is not None:
                continue
            if ex.cell_refusal(ws, right) is not None:
                continue
            out.append({"cell": f"{ws.title}!{get_column_letter(cell.column + 1)}{cell.row}",
                        "label": label})
            if len(out) >= MAX_TARGETS:
                return out
    return out


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
    res = ex.write_names(wb, todo)
    report["filled"].extend(res["written"])
    report["refused"].update(res["refused"])
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


def _docx_targets(doc) -> list[dict[str, str]]:
    """A table cell right of a label cell: {"cell": "table0.r0c1", "label"}."""
    out: list[dict[str, str]] = []
    for ti, table in enumerate(doc.tables):
        for ri, row in enumerate(table.rows):
            cells = row.cells
            for ci in range(len(cells) - 1):
                a, b = cells[ci], cells[ci + 1]
                if a._tc is b._tc:
                    continue
                label = a.text.strip().rstrip(":：").strip()
                if not label or len(label) > MAX_LABEL or "{{" in label or b.text.strip():
                    continue
                out.append({"cell": f"table{ti}.r{ri}c{ci + 1}", "label": label})
                if len(out) >= MAX_TARGETS:
                    return out
    return out


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


def _fill_pptx(path, out_path, values, mapping, report) -> None:
    prs = Presentation(path)
    stories = []
    for slide in prs.slides:
        stories.append((_part_label(slide.part.partname), slide._element))
        if slide.has_notes_slide:
            notes = slide.notes_slide
            stories.append((_part_label(notes.part.partname), notes._element))
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


# ─── Public ────────────────────────────────────────────────────
def inspect(path: str) -> dict[str, Any]:
    """What a template asks for: {"kind", "markers", "controls", "names",
    "tables", "has_markers", "targets"}; targets (the mapping screen's
    proposals) only for a template with nothing to fill by itself."""
    kind = kind_of(path)
    out: dict[str, Any] = {"kind": kind, "markers": [], "controls": [], "names": [], "tables": [],
                           "has_markers": False, "targets": []}
    if kind == "xlsx":
        wb = _load_xlsx(path)
        out["markers"] = _unique(k for k, _ in _xlsx_markers(wb))
        out["names"] = ex.list_defined_names(wb)
        out["tables"] = _xlsx_tables(wb, out["names"])
        out["has_markers"] = bool(out["markers"]) or any(n["fillable"] for n in out["names"])
        if not out["has_markers"]:
            out["targets"] = _xlsx_targets(wb)
        return out
    out["markers"] = _unique(k for k, _ in zip_markers(path))
    if kind == "docx":
        doc = Document(path)
        out["controls"] = [{k: c[k] for k in ("tag", "alias", "type", "fillable", "reason", "showing_placeholder",
                                              "where")} for c in dg.list_content_controls(doc)]
        out["tables"] = _docx_tables(doc)
        out["has_markers"] = bool(out["markers"]) or any(c["fillable"] for c in out["controls"])
        if not out["has_markers"]:
            out["targets"] = _docx_targets(doc)
    else:
        out["tables"] = _pptx_tables(Presentation(path))
        out["has_markers"] = bool(out["markers"])
    return out


def fill(path: str, out_path: str, values: dict | None, mapping: dict | None = None) -> dict[str, Any]:
    """Fill a template into out_path (the template itself is not changed).

    values: {key: text or number, "lines": [{field: …}, …]}. mapping:
    template key (marker, control tag or alias, defined name, mapped target
    such as "table0.r0c1" or "Sheet1!B1") -> values key.

    Returns {"kind", "filled": [keys], "refused": {key: reason},
    "not_filled_explained": [{marker|tag|name|target, reason, …}],
    "needs_recalc", "formulas_kept", "mapping_screen", "targets",
    "parts_dropped"} (+ "rows" for Word, "formulas_changed" if a formula
    changed — it must not)."""
    kind = kind_of(path)
    values, mapping = dict(values or {}), dict(mapping or {})
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
