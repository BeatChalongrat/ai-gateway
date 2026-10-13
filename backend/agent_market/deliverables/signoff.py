"""A reviewer's sign-off on the face of a delivered file (AM3).

A run's files are made by an agent; a financial document that goes into a
real close needs a person's name on it. When a reviewer marks a run
reviewed, agent-runner hands the run's finished files to this op (with the
runner's CURRENT engine — it is a platform op that changes no number, so it
never depends on the version the run was pinned to) and it writes each one
back under the same name with the review on its face:

  * .docx — a boxed block as the first thing in the body: who reviewed and
    when, who prepared it with which agent, the reviewer's note;
  * .pptx — the same lines in a small box in the bottom band of slide 1;
  * .xlsx — a sheet "ตรวจทาน" added at ZIP level and opened first. The
    workbook is never loaded and saved again: openpyxl would drop every
    formula's cached value. Every other part keeps its exact bytes, except
    that the sheet that was selected is no longer "selected" (two selected
    tabs would group the sheets).

Stamping a file that already carries a stamp replaces it (one block, one
box, one sheet). Anything else — a customer template, a PDF — is returned
in "skipped" with the reason, never an error: the runner converts the
stamped .docx to PDF itself.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import zipfile
from xml.sax.saxutils import escape, quoteattr

from ..engine.words import SHARED as S

STAMP_TAG = "am-review-stamp"              # docx: the stamp table's caption
PPTX_SHAPE = "Review stamp"                 # pptx: the stamp box's name
# AM6: the stamp names no organisation. A stamp written before then carried the publisher's name in
# front of the same words; it is still found and replaced when the document is signed again.
_STAMP_CAPTION = re.compile(r"^(?:[a-z]+-)?review-stamp$")
_STAMP_SHAPE = re.compile(r"^(?:[A-Za-z]+ )?review stamp$", re.I)
XLSX_PART = "xl/worksheets/am_review.xml"   # xlsx: the stamp sheet's part
XLSX_RID = "rIdAmReview"
XLSX_SHEET = "docs.sheet.review"

SUPPORTED = ("docx", "pptx", "xlsx")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WS_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
WS_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"


def lines(review: dict) -> list[str]:
    """The stamp's text, one entry per line (the note only when there is one)."""
    r = {k: str(review.get(k) or "").strip() for k in ("reviewer", "at_label", "preparer", "agent", "run_ref", "note")}
    out = [S("docs.review.line", v=r["reviewer"] or "—", v2=r["at_label"] or "—")]
    made = S("docs.review.prepared_by", v=r["preparer"] or "—")
    if r["agent"]:
        made += " " + S("docs.review.with", agent=r["agent"])
    if r["run_ref"]:
        made += " " + S("docs.review.run", run_ref=r["run_ref"])
    out.append(made)
    if r["note"]:
        out.append(S("docs.review.note", note=r["note"]))
    return out


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ── .docx ────────────────────────────────────────────────────────────────

def _docx_font(doc) -> str:
    rpr = doc.styles["Normal"].element.rPr
    fonts = rpr.find(f"{{{W_NS}}}rFonts") if rpr is not None else None
    for attr in ("cs", "ascii", "hAnsi"):
        name = fonts.get(f"{{{W_NS}}}{attr}") if fonts is not None else None
        if name:
            return name
    return "Sarabun"


def _is_stamp(el) -> bool:
    if el.tag != f"{{{W_NS}}}tbl":
        return False
    cap = el.find(f"{{{W_NS}}}tblPr/{{{W_NS}}}tblCaption")
    return cap is not None and bool(_STAMP_CAPTION.match(cap.get(f"{{{W_NS}}}val") or ""))


def stamp_docx(src: str, dst: str, review: dict) -> dict:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    from .report_docx import _borders, _cell_margins, _cell_text, _shade

    doc = Document(src)
    body = doc.element.body
    replaced = 0
    for el in list(body.iterchildren()):
        if _is_stamp(el):
            body.remove(el)
            replaced += 1
    font = _docx_font(doc)
    sec = doc.sections[0]
    width = sec.page_width - sec.left_margin - sec.right_margin
    table = doc.add_table(rows=1, cols=1)
    _borders(table, "5F6B7A", inside_h=False, outer=True)
    _cell_margins(table, top=80, bottom=80, left=120, right=120)
    tblPr = table._tbl.tblPr
    cap = OxmlElement("w:tblCaption")
    cap.set(qn("w:val"), STAMP_TAG)
    tblPr.append(cap)
    cell = table.rows[0].cells[0]
    cell.width = width
    _shade(cell, "F3F5F9")
    text = lines(review)
    for i, line in enumerate(text):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = 0
        pf.space_after = 0
        # the pad is the cell's own margins (120 twips a side = 12 pt)
        _cell_text(p, line, font, 10 if i == 0 else 9, width, bold=(i == 0),
                   color="1C2230" if i == 0 else "5F6B7A", pad_pt=12.0)
    # a little air between the stamp and the document's own first element
    spacer = OxmlElement("w:p")
    body.remove(table._tbl)
    body.insert(0, spacer)
    body.insert(0, table._tbl)
    doc.save(dst)
    first = next(iter(Document(dst).element.body.iterchildren()))
    return {"first_element_is_stamp": _is_stamp(first), "font": font, "lines": len(text), "replaced": replaced}


# ── .pptx ────────────────────────────────────────────────────────────────

def _pptx_font(slide) -> str:
    for sh in slide.shapes:
        if sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if r.font.name:
                        return r.font.name
    return "Sarabun"


def stamp_pptx(src: str, dst: str, review: dict) -> dict:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt

    from .fit import shrink_to_fit

    prs = Presentation(src)
    slide = prs.slides[0]
    replaced = 0
    for sh in list(slide.shapes):
        if _STAMP_SHAPE.match(sh.name or ""):
            sh._element.getparent().remove(sh._element)
            replaced += 1
    sw, shh = prs.slide_width, prs.slide_height
    margin = Emu(int(shh * 0.012))
    # the band below the lowest text on the slide; the content column when there is one
    boxes = [sh for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
    lowest = max((sh.top + sh.height for sh in boxes), default=0)
    left = min((sh.left for sh in boxes), default=int(sw * 0.05))
    width = max((sh.width for sh in boxes), default=int(sw * 0.9))
    left = max(left, int(sw * 0.03))
    width = min(width, sw - 2 * left)
    height = Emu(int(Pt(8) * 1.3 * 3) + int(Pt(4)))
    top = shh - height - margin
    font = _pptx_font(slide)
    tb = slide.shapes.add_textbox(left, top, width, height)
    tb.name = PPTX_SHAPE
    tb.fill.solid()
    tb.fill.fore_color.rgb = RGBColor(0xF3, 0xF5, 0xF9)
    tb.line.color.rgb = RGBColor(0x5F, 0x6B, 0x7A)
    tb.line.width = Pt(0.75)
    tf = tb.text_frame
    tf.word_wrap = True
    for side in ("margin_left", "margin_right"):
        setattr(tf, side, Pt(6))
    for side in ("margin_top", "margin_bottom"):
        setattr(tf, side, Pt(2))
    for i, line in enumerate(lines(review)):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        r = p.add_run()
        r.text = line
        r.font.name = font
        r.font.size = Pt(8)
        r.font.bold = i == 0
        r.font.color.rgb = RGBColor(0x1C, 0x22, 0x30) if i == 0 else RGBColor(0x5F, 0x6B, 0x7A)
    shrink_to_fit(tb, face=font)
    prs.save(dst)
    inside = left >= 0 and top >= 0 and left + width <= sw and top + height <= shh
    overlaps = [sh.text_frame.text[:30] for sh in boxes
                if not (sh.top + sh.height <= top or sh.top >= top + height or sh.left + sh.width <= left or sh.left >= left + width)]
    return {"inside_slide": inside, "overlaps": overlaps, "below_text": lowest <= top, "font": font, "replaced": replaced}


# ── .xlsx (ZIP level) ────────────────────────────────────────────────────

def _cell(ref: str, text: str, bold: bool = False) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_xml(review: dict, doc_name: str = "") -> bytes:
    r = {k: str(review.get(k) or "").strip() for k in ("reviewer", "reviewer_email", "at_label", "preparer", "agent", "run_ref", "note")}
    rows = [("A1", S("docs.review.reviewed"), None)]
    table = [(S("docs.review.reviewer"), r["reviewer"] + (f" ({r['reviewer_email']})" if r["reviewer_email"] else "")),
             (S("docs.review.at"), r["at_label"]),
             (S("docs.review.preparer"), r["preparer"]),
             ("agent", r["agent"]),
             (S("docs.review.run_label"), r["run_ref"])]
    if r["note"]:
        table.append((S("docs.col.note"), r["note"]))
    table.append((S("docs.review.this_file"), S("docs.review.unchanged")))
    xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
           '<sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>'
           '<sheetFormatPr defaultRowHeight="15"/>'
           '<cols><col min="1" max="1" width="20" customWidth="1"/><col min="2" max="2" width="80" customWidth="1"/></cols>'
           '<sheetData>']
    xml.append(f'<row r="1">{_cell("A1", rows[0][1])}</row>')
    for i, (k, v) in enumerate(table, start=3):
        xml.append(f'<row r="{i}">{_cell(f"A{i}", k)}{_cell(f"B{i}", v)}</row>')
    # AM8: the review sheet prints like the rest of the working paper — one A4 page, the document's
    # name and the page number on it
    foot = escape(S("docs.review.footer", doc_name=doc_name))
    xml.append('</sheetData><pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
               '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="1"/>'
               f'<headerFooter><oddFooter>{foot}</oddFooter></headerFooter></worksheet>')
    return "".join(xml).encode("utf-8")


_SHEET_RE = re.compile(r"<sheet\b[^>]*/>|<sheet\b[^>]*>.*?</sheet>", re.S)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(r'(?:^|\s)' + re.escape(name) + r'="([^"]*)"', tag)
    return m.group(1) if m else None


def _set_attr(tag: str, name: str, value: str) -> str:
    if _attr(tag, name) is not None:
        return re.sub(r'((?:^|\s)' + re.escape(name) + r')="[^"]*"', lambda m: f'{m.group(1)}="{value}"', tag, count=1)
    return re.sub(r"\s*/?>$", lambda m: f' {name}="{value}"{m.group(0)}', tag, count=1)


def _rels_target(rels: str, rid: str) -> str | None:
    for m in re.finditer(r"<Relationship\b[^>]*/?>", rels):
        if _attr(m.group(0), "Id") == rid:
            return _attr(m.group(0), "Target")
    return None


def _part_path(target: str) -> str:
    return target.lstrip("/") if target.startswith("/") else "xl/" + target


def stamp_xlsx(src: str, dst: str, review: dict) -> dict:
    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
        parts = {i.filename: z.read(i.filename) for i in infos}
    wb = parts["xl/workbook.xml"].decode("utf-8")
    rels = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    ct = parts["[Content_Types].xml"].decode("utf-8")
    replaced = XLSX_PART in parts
    changed = {XLSX_PART}

    sheets_m = re.search(r"<sheets>(.*?)</sheets>", wb, re.S)
    if not sheets_m:
        raise ValueError("workbook has no <sheets>")
    sheet_tags = _SHEET_RE.findall(sheets_m.group(1))
    names = [_attr(t, "name") for t in sheet_tags]
    ours = next((i for i, t in enumerate(sheet_tags) if _attr(t, "r:id") == XLSX_RID), None)
    if ours is None:
        name = S(XLSX_SHEET)
        n = 2
        while name in names:
            name = f"{S(XLSX_SHEET)} ({n})"
            n += 1
        sheet_id = max((int(_attr(t, "sheetId") or 0) for t in sheet_tags), default=0) + 1
        new = (f'<sheet xmlns:r="{R_NS}" name={quoteattr(name)} sheetId="{sheet_id}" state="visible" '
               f'r:id="{XLSX_RID}"/>')
        wb = wb.replace("</sheets>", new + "</sheets>", 1)
        ours = len(sheet_tags)
        rels = rels.replace("</Relationships>",
                            f'<Relationship Id="{XLSX_RID}" Type="{WS_TYPE}" Target="worksheets/am_review.xml"/></Relationships>', 1)
        ct = ct.replace("</Types>", f'<Override PartName="/{XLSX_PART}" ContentType="{WS_CT}"/></Types>', 1)
        changed |= {"xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml"}
    else:
        name = _attr(sheet_tags[ours], "name")

    # open on the stamp sheet: activeTab points at it (appending never shifts
    # another sheet's index, so no definedName's localSheetId moves)
    bv = re.search(r"<workbookView\b[^>]*/?>", wb)
    if bv:
        tag = bv.group(0)
        new_tag = _set_attr(tag, "activeTab", str(ours))
        if new_tag != tag:
            wb = wb.replace(tag, new_tag, 1)
            changed.add("xl/workbook.xml")
    else:
        wb = wb.replace("<sheets>", f'<bookViews><workbookView activeTab="{ours}"/></bookViews><sheets>', 1)
        changed.add("xl/workbook.xml")

    # no other sheet stays "selected" (two selected tabs would group them)
    unselected = []
    for t in sheet_tags:
        rid = _attr(t, "r:id")
        if rid == XLSX_RID:
            continue
        target = _rels_target(rels, rid or "")
        path = _part_path(target) if target else None
        if not path or path not in parts:
            continue
        xml = parts[path].decode("utf-8")
        new_xml = re.sub(r'(<sheetView\b[^>]*?)\s+tabSelected="(?:1|true)"', r"\1", xml)
        if new_xml != xml:
            parts[path] = new_xml.encode("utf-8")
            changed.add(path)
            unselected.append(_attr(t, "name"))

    parts["xl/workbook.xml"] = wb.encode("utf-8")
    parts["xl/_rels/workbook.xml.rels"] = rels.encode("utf-8")
    parts["[Content_Types].xml"] = ct.encode("utf-8")
    parts[XLSX_PART] = _sheet_xml(review, os.path.basename(dst))

    tmp = dst + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        seen = set()
        for i in infos:
            out.writestr(i.filename, parts[i.filename], compress_type=zipfile.ZIP_DEFLATED)
            seen.add(i.filename)
        if XLSX_PART not in seen:
            out.writestr(XLSX_PART, parts[XLSX_PART], compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, dst)
    untouched = len([n for n in parts if n not in changed])
    return {"sheet": name, "active_index": ours, "replaced": replaced, "unselected": unselected,
            "parts_changed": sorted(changed), "parts_unchanged": untouched}


# ── the op ───────────────────────────────────────────────────────────────

def stamp_files(files: list[dict], review: dict, in_dir: str, out_dir: str) -> dict:
    """The stamp_review op: every supported file stamped into out_dir under its own name."""
    artifacts, skipped = [], []
    for f in files or []:
        name = os.path.basename(str(f.get("name") or ""))
        fmt = str(f.get("format") or "").lower()
        src = os.path.join(in_dir, os.path.basename(str(f.get("input") or "")))
        if not name:
            skipped.append({"name": name, "reason": S("docs.review.no_name")})
            continue
        if fmt not in SUPPORTED:
            skipped.append({"name": name, "reason": S("docs.review.not_stamped", v=fmt or "?")
                            + (" " + S("docs.review.from_docx") if fmt == "pdf" else "")})
            continue
        if not os.path.isfile(src):
            skipped.append({"name": name, "reason": S("docs.review.no_source")})
            continue
        dst = os.path.join(out_dir, name)
        if os.path.abspath(dst) == os.path.abspath(src):
            shutil.copyfile(src, src + ".orig")
            src = src + ".orig"
        stamp = {"docx": stamp_docx, "pptx": stamp_pptx, "xlsx": stamp_xlsx}[fmt]
        checks = stamp(src, dst, review)
        artifacts.append({"name": name, "format": fmt, "size": os.path.getsize(dst), "sha256": _sha(dst),
                          "stamped": True, "checks": checks})
    return {"artifacts": artifacts, "skipped": skipped}
