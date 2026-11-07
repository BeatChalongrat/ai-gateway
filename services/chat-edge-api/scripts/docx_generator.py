#!/usr/bin/env python3
"""Word document generator — applies a JSON plan to produce a .docx.

Sibling of excel_executor.py. Two modes:

    create: when <source.docx> is omitted, builds a fresh document
            with the requested settings then applies the actions.
    edit:   when <source.docx> is provided, copies it to <output> first
            and applies the actions on the copy. Originals are never
            mutated.

Each action runs inside a try/except so a single malformed step is
recorded in the change log instead of aborting the whole plan; the
validator (doc_validator.py) catches structural breakage downstream.

Template filling (AM2): fill_placeholders fills {{key}} markers wherever
the document shows text (body, tables, headers, footers, text boxes), also
a marker Word split across runs, and repeats a table row carrying
{{<list>.<field>}} once per item; fill_content_controls fills plain-text
content controls by tag or alias. Anything not filled is reported, never
skipped silently. replace_text is unchanged.

Usage:  python3 docx_generator.py <plan.json> <output.docx> [source.docx]
Output: JSON result on stdout
"""

import copy
import json
import os
import re
import shutil
import sys
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor


# ─── Corporate look ────────────────────────────────────────────
CORPORATE_NAVY_HEX = "0A1230"
CORPORATE_NAVY_RGB = RGBColor(0x0A, 0x12, 0x30)


# ─── Thai font wiring ──────────────────────────────────────────
def setup_thai_font(doc, font_name: str = "Sarabun", base_size: int = 16) -> None:
    """Configure Normal + Heading styles to render Thai correctly.

    python-docx's default Normal style only sets ascii/hAnsi font
    runs. Thai glyphs are tagged with the `cs` (complex script) run,
    so without an explicit `w:rFonts cs=…` Word falls back to its
    default Thai font (often invisible / boxed)."""
    style = doc.styles["Normal"]
    style.font.name = font_name
    style.font.size = Pt(base_size)

    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:cs"), font_name)

    for level in range(1, 5):
        style_name = f"Heading {level}"
        if style_name not in doc.styles:
            continue
        hs = doc.styles[style_name]
        hs.font.name = font_name
        hs.font.color.rgb = CORPORATE_NAVY_RGB
        hrPr = hs.element.get_or_add_rPr()
        hrFonts = hrPr.find(qn("w:rFonts"))
        if hrFonts is None:
            hrFonts = OxmlElement("w:rFonts")
            hrPr.insert(0, hrFonts)
        hrFonts.set(qn("w:ascii"), font_name)
        hrFonts.set(qn("w:hAnsi"), font_name)
        hrFonts.set(qn("w:cs"), font_name)


def setup_page(doc, settings: dict[str, Any]) -> None:
    section = doc.sections[0]
    page_size = (settings.get("page_size") or "A4").upper()
    orientation = (settings.get("orientation") or "portrait").lower()

    if page_size == "LETTER":
        w, h = Inches(8.5), Inches(11)
    else:
        w, h = Cm(21), Cm(29.7)  # A4

    if orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = h
        section.page_height = w
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = w
        section.page_height = h

    margins = settings.get("margins") or {}
    section.top_margin = Inches(margins.get("top", 1.0))
    section.bottom_margin = Inches(margins.get("bottom", 1.0))
    section.left_margin = Inches(margins.get("left", 1.0))
    section.right_margin = Inches(margins.get("right", 1.0))


# ─── Action handlers ───────────────────────────────────────────
def _set_run_cs_font(run, font_name: str) -> None:
    """Tag a single run so Thai (complex-script) characters in its
    text are rendered with the same font as latin. Used for
    headers/footers built from scratch where the Normal-style
    inheritance doesn't always carry the cs setting."""
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:cs"), font_name)


def add_heading(doc, action: dict, _ctx: dict) -> str:
    level = int(action.get("level", 1))
    text = action.get("text", "")
    doc.add_heading(text, level=level)
    return f"Added heading L{level}: {text[:60]}"


def add_paragraph(doc, action: dict, _ctx: dict) -> str:
    text = action.get("text", "")
    p = doc.add_paragraph(text)
    if action.get("bold"):
        for run in p.runs:
            run.bold = True
    if action.get("italic"):
        for run in p.runs:
            run.italic = True
    align_map = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    p.alignment = align_map.get(
        (action.get("alignment") or "left").lower(), WD_ALIGN_PARAGRAPH.LEFT
    )
    return f"Added paragraph ({len(text)} chars)"


def add_bullet_list(doc, action: dict, _ctx: dict) -> str:
    items = action.get("items") or []
    for item in items:
        doc.add_paragraph(str(item), style="List Bullet")
    return f"Added {len(items)} bullet items"


def add_numbered_list(doc, action: dict, _ctx: dict) -> str:
    items = action.get("items") or []
    for item in items:
        doc.add_paragraph(str(item), style="List Number")
    return f"Added {len(items)} numbered items"


def add_table(doc, action: dict, _ctx: dict) -> str:
    headers = action.get("headers") or []
    rows = action.get("rows") or []
    style = (action.get("style") or "professional").lower()

    cols = len(headers) if headers else (len(rows[0]) if rows else 0)
    if cols == 0:
        return "Skipped empty table"

    num_rows = (1 if headers else 0) + len(rows)
    table = doc.add_table(rows=num_rows, cols=cols)

    if style == "light":
        table.style = "Light List Accent 1"
    else:
        table.style = "Table Grid"

    row_offset = 0
    if headers:
        for j, header in enumerate(headers):
            cell = table.rows[0].cells[j]
            cell.text = str(header)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
        row_offset = 1

    for i, row_data in enumerate(rows):
        for j, cell_val in enumerate(row_data):
            if j < cols:
                table.rows[row_offset + i].cells[j].text = str(cell_val)

    if headers and style == "professional":
        for cell in table.rows[0].cells:
            shading = OxmlElement("w:shd")
            shading.set(qn("w:fill"), CORPORATE_NAVY_HEX)
            shading.set(qn("w:val"), "clear")
            cell._element.get_or_add_tcPr().append(shading)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    return f"Added table: {cols} cols × {len(rows)} rows"


def add_page_break(doc, _action: dict, _ctx: dict) -> str:
    doc.add_page_break()
    return "Added page break"


def add_horizontal_rule(doc, _action: dict, _ctx: dict) -> str:
    p = doc.add_paragraph()
    pPr = p._element.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "CCCCCC")
    pBdr.append(bottom)
    pPr.append(pBdr)
    return "Added horizontal rule"


def replace_text(doc, action: dict, _ctx: dict) -> str:
    find = action.get("find", "")
    replace = action.get("replace", "")
    if not find:
        return "Skipped replace_text (empty find)"
    count = 0
    for p in doc.paragraphs:
        if find not in p.text:
            continue
        for run in p.runs:
            if find in run.text:
                run.text = run.text.replace(find, replace)
                count += 1
    return f"Replaced '{find[:30]}' ({count} run hits)"


def _build_paged_footer(p, text: str, font_name: str) -> None:
    """Render '{page}' / '{total}' substitutions as live Word field
    codes inside the footer paragraph p, with the surrounding text
    as plain runs. Uses _set_run_cs_font on every run so Thai
    glyphs (e.g. 'หน้า') render under the same font as the field."""
    parts = text.split("{page}", 1)
    if parts[0]:
        run = p.add_run(parts[0])
        _set_run_cs_font(run, font_name)

    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    r1 = p.add_run()
    r1._element.append(fldChar1)
    _set_run_cs_font(r1, font_name)
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    r2 = p.add_run()
    r2._element.append(instr)
    _set_run_cs_font(r2, font_name)
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    r3 = p.add_run()
    r3._element.append(fldChar2)
    _set_run_cs_font(r3, font_name)

    if len(parts) < 2 or not parts[1]:
        return
    remaining = parts[1]
    if "{total}" not in remaining:
        run = p.add_run(remaining)
        _set_run_cs_font(run, font_name)
        return
    before_total, after_total = remaining.split("{total}", 1)
    if before_total:
        run = p.add_run(before_total)
        _set_run_cs_font(run, font_name)
    fc1 = OxmlElement("w:fldChar")
    fc1.set(qn("w:fldCharType"), "begin")
    r4 = p.add_run()
    r4._element.append(fc1)
    _set_run_cs_font(r4, font_name)
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = " NUMPAGES "
    r5 = p.add_run()
    r5._element.append(it)
    _set_run_cs_font(r5, font_name)
    fc2 = OxmlElement("w:fldChar")
    fc2.set(qn("w:fldCharType"), "end")
    r6 = p.add_run()
    r6._element.append(fc2)
    _set_run_cs_font(r6, font_name)
    if after_total:
        run = p.add_run(after_total)
        _set_run_cs_font(run, font_name)


def set_header(doc, action: dict, ctx: dict) -> str:
    text = action.get("text", "")
    font_name = (ctx.get("settings") or {}).get("font_family") or "Sarabun"
    for section in doc.sections:
        section.different_first_page_header_footer = False
        header = section.header
        header.is_linked_to_previous = False
        if header.paragraphs:
            p = header.paragraphs[0]
        else:
            p = header.add_paragraph()
        # Drop any existing runs so a re-run doesn't append.
        for r in list(p.runs):
            r._element.getparent().remove(r._element)
        run = p.add_run(text)
        _set_run_cs_font(run, font_name)
    return f"Set header: {text[:60]}"


def set_footer(doc, action: dict, ctx: dict) -> str:
    text = action.get("text", "")
    font_name = (ctx.get("settings") or {}).get("font_family") or "Sarabun"
    for section in doc.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        if footer.paragraphs:
            p = footer.paragraphs[0]
        else:
            p = footer.add_paragraph()
        for r in list(p.runs):
            r._element.getparent().remove(r._element)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if "{page}" in text:
            _build_paged_footer(p, text, font_name)
        else:
            run = p.add_run(text)
            _set_run_cs_font(run, font_name)
    return f"Set footer: {text[:60]}"


# ─── Template filling (AM2) ────────────────────────────────────
# A customer's template marks what to fill: {{key}} markers (Word splits
# one across runs as soon as the text is edited), a table row of
# {{line.<field>}} markers repeated per line item, or Word content
# controls (w:sdt). Every marker and control is either filled or reported
# back with the reason — never skipped silently.

MARKER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
W_P, W_T, W_TR = qn("w:p"), qn("w:t"), qn("w:tr")
W_SDT, W_SDTPR, W_SDTCONTENT = qn("w:sdt"), qn("w:sdtPr"), qn("w:sdtContent")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
_W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()  # a key the values do not have (None is a value)


def snake_case(key: str) -> str:
    """CompanyName -> company_name, VATAmount -> vat_amount."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", str(key))
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return re.sub(r"[\s\-]+", "_", s).lower()


def template_value(values: dict | None, mapping: dict | None, *keys: str | None) -> Any:
    """The value a template key asks for, or MISSING. A mapping entry
    (template key -> values key) wins; then the key itself; then its
    snake_case form. Several keys (a control's tag and alias) are tried in
    order."""
    values, mapping = values or {}, mapping or {}
    keys = tuple(k for k in keys if k)
    for k in keys:
        if k in mapping:
            return values.get(mapping[k], MISSING)
    for k in keys:
        if k in values:
            return values[k]
    for k in keys:
        s = snake_case(k)
        if s in values:
            return values[s]
    return MISSING


def scalar_value(values, mapping, key: str) -> Any:
    v = template_value(values, mapping, key)
    if v is MISSING and "." in key:  # {{company.name}} from {"company": {"name": …}}
        head, rest = key.split(".", 1)
        obj = template_value(values, mapping, head)
        if isinstance(obj, dict):
            v = obj.get(rest, MISSING)
    return v


def as_text(value: Any) -> str | None:
    if value is MISSING or value is None or isinstance(value, (list, tuple, dict)):
        return None
    return value if isinstance(value, str) else str(value)


def why_not(value: Any) -> str:
    return "no_value" if (value is MISSING or value is None) else "not_text"


def _nearest(el, tag: str):
    node = el.getparent()
    while node is not None and node.tag != tag:
        node = node.getparent()
    return node


def own_texts(p, t_tag: str = W_T, p_tag: str = W_P) -> list:
    """The text elements of paragraph p in order, leaving out those of
    paragraphs nested inside it (a text box). t_tag / p_tag default to
    WordprocessingML; DrawingML (a:t / a:p) works the same way."""
    return [t for t in p.iter(t_tag) if _nearest(t, p_tag) is p]


def paragraph_text(p, t_tag: str = W_T, p_tag: str = W_P) -> str:
    return "".join(t.text or "" for t in own_texts(p, t_tag, p_tag))


def markers_in(root, t_tag: str = W_T, p_tag: str = W_P) -> list[str]:
    """Every {{key}} under root in document order (one per occurrence),
    also one split across runs."""
    return [m.group(1) for p in root.iter(p_tag) for m in MARKER_RE.finditer(paragraph_text(p, t_tag, p_tag))]


def replace_markers_in_paragraph(p, resolve, t_tag: str = W_T, p_tag: str = W_P) -> list[tuple[str, bool]]:
    """Replace every {{key}} in paragraph p — also one split across runs —
    with resolve(key); resolve returns None to leave that marker as it is.
    The text goes into the run where the marker starts, so it keeps that
    run's formatting; the rest of the marker is removed from the runs after
    it. Returns [(key, filled)] in document order."""
    ts = own_texts(p, t_tag, p_tag)
    orig = [t.text or "" for t in ts]
    full = "".join(orig)
    if "{{" not in full:
        return []
    texts = list(orig)
    starts, pos = [], 0
    for s in orig:
        starts.append(pos)
        pos += len(s)
    owner = [i for i, s in enumerate(orig) for _ in s]
    results: list[tuple[str, bool]] = []
    # Right to left: a replacement never moves the text of a match before it.
    for m in reversed(list(MARKER_RE.finditer(full))):
        key = m.group(1)
        text = resolve(key)
        if text is None:
            results.append((key, False))
            continue
        i, j = owner[m.start()], owner[m.end() - 1]
        a, b = m.start() - starts[i], m.end() - starts[j]
        if i == j:
            texts[i] = texts[i][:a] + text + texts[i][b:]
        else:
            texts[i] = texts[i][:a] + text
            for k in range(i + 1, j):
                texts[k] = ""
            texts[j] = texts[j][b:]
        results.append((key, True))
    for t, old, new in zip(ts, orig, texts):
        if new != old:
            t.text = new
            if t_tag == W_T:
                t.set(_XML_SPACE, "preserve")
    return list(reversed(results))


def story_roots(doc) -> list[tuple[str, Any]]:
    """(name, element) of every story filled here: the body, then each
    header and footer part."""
    roots = [("body", doc.element.body)]
    for rel in doc.part.rels.values():
        if rel.is_external or rel.reltype not in (RT.HEADER, RT.FOOTER):
            continue
        part = rel.target_part
        roots.append((str(part.partname).rsplit("/", 1)[-1].rsplit(".", 1)[0], part.element))
    return roots


def row_list_name(tr, accept=None) -> str | None:
    """The <list> of the first {{<list>.<field>}} in table row tr (not in
    a table nested inside it) that accept(list) allows, or None."""
    for p in tr.iter(W_P):
        if _nearest(p, W_TR) is not tr:
            continue
        for m in MARKER_RE.finditer(paragraph_text(p)):
            if "." in m.group(1):
                name = m.group(1).split(".", 1)[0].strip()
                if accept is None or accept(name):
                    return name
    return None


def _list_value(values, mapping, name: str) -> list | None:
    for key in (name, name + "s"):  # {{line.x}} repeats over "line" or "lines"
        v = template_value(values, mapping, key)
        if isinstance(v, (list, tuple)):
            return list(v)
    return None


def _drop_ids(el) -> None:
    """Ids that must stay unique in a document, removed from a copied row."""
    for tag in (qn("w:bookmarkStart"), qn("w:bookmarkEnd")):
        for b in list(el.iter(tag)):
            b.getparent().remove(b)
    for pr in el.iter(W_SDTPR):
        for i in list(pr.iter(qn("w:id"))):
            pr.remove(i)
    for node in el.iter():
        if isinstance(node.tag, str):
            for attr in (_W14 + "paraId", _W14 + "textId"):
                node.attrib.pop(attr, None)


def _note(report: dict, entry: dict) -> None:
    same = (entry.get("marker"), entry.get("where"))
    if not any((e.get("marker"), e.get("where")) == same for e in report["not_filled"]):
        report["not_filled"].append(entry)


def _filled(report: dict, key: str) -> None:
    if key not in report["filled"]:
        report["filled"].append(key)


def _row_text(key: str, prefix: str, item: Any) -> str | None:
    if not key.startswith(prefix) or not isinstance(item, dict):
        return None
    return as_text(item.get(key[len(prefix):], MISSING))


def _expand_rows(root, rows: list, where: str, report: dict) -> None:
    for tr, name, items in rows:
        if not any(a is root for a in tr.iterancestors()):  # inside a row already repeated
            continue
        prefix = name + "."
        for n, item in enumerate(items):
            row = copy.deepcopy(tr)
            if n:
                _drop_ids(row)
            for p in row.iter(W_P):
                for key, ok in replace_markers_in_paragraph(p, lambda k, it=item, pre=prefix: _row_text(k, pre, it)):
                    if not key.startswith(prefix):
                        continue
                    if ok:
                        _filled(report, key)
                    else:
                        reason = ("not_a_row" if not isinstance(item, dict)
                                  else why_not(item.get(key[len(prefix):], MISSING)))
                        _note(report, {"marker": key, "reason": reason, "where": where})
            tr.addprevious(row)
        tr.getparent().remove(tr)
        report["rows"][name] = report["rows"].get(name, 0) + len(items)


def fill_placeholders(doc, values: dict, mapping: dict | None = None) -> dict[str, Any]:
    """Fill {{key}} markers in the body, tables, headers, footers and text
    boxes; a table row with {{<list>.<field>}} markers is repeated once per
    item of that list, in order, with its formatting, and the rows after it
    (a totals row) stay after the repeated rows. A marker with no value is
    left in place and reported.

    Returns {"filled": [keys], "not_filled": [{"marker", "reason", "where"}],
    "rows": {list: rows written}}."""
    report: dict[str, Any] = {"filled": [], "not_filled": [], "rows": {}}

    def is_list(name: str) -> bool:
        return _list_value(values, mapping, name) is not None

    for where, root in story_roots(doc):
        # Template rows first, then the single markers (a template row's own
        # {{period}} too), then the rows are repeated — so a value put in is
        # never read again as a marker.
        rows = []
        for tr in root.iter(W_TR):
            name = row_list_name(tr, is_list)
            if name:
                rows.append((tr, name, _list_value(values, mapping, name)))
        row_els = {tr for tr, _, _ in rows}
        row_lists = {name for _, name, _ in rows}
        for p in root.iter(W_P):
            in_row = any(a in row_els for a in p.iterancestors(W_TR))
            for key, ok in replace_markers_in_paragraph(p, lambda k: as_text(scalar_value(values, mapping, k))):
                if ok:
                    _filled(report, key)
                elif not (in_row and key.split(".", 1)[0].strip() in row_lists and "." in key):
                    _note(report, {"marker": key, "reason": why_not(scalar_value(values, mapping, key)),
                                   "where": where})
        _expand_rows(root, rows, where, report)
    return report


# Content-control types by the element that marks them in w:sdtPr. A
# control that names no type is rich text (ECMA-376 17.5.2).
_SDT_TYPES = (
    (qn("w:text"), "text"), (qn("w:richText"), "richText"), (qn("w:date"), "date"),
    (qn("w:dropDownList"), "dropDownList"), (qn("w:comboBox"), "comboBox"),
    (qn("w:picture"), "picture"), (qn("w:docPartObj"), "buildingBlock"),
    (qn("w:docPartList"), "buildingBlock"), (qn("w:citation"), "citation"),
    (qn("w:bibliography"), "bibliography"), (qn("w:equation"), "equation"),
    (qn("w:group"), "group"), (_W14 + "checkbox", "checkbox"),
    (_W15 + "repeatingSection", "repeatingSection"),
    (_W15 + "repeatingSectionItem", "repeatingSectionItem"),
)


def _sdt_val(pr, tag: str) -> str | None:
    el = pr.find(qn(tag)) if pr is not None else None
    return el.get(qn("w:val")) if el is not None else None


def content_control_info(sdt, where: str = "body") -> dict[str, Any]:
    """{tag, alias, type, fillable, reason, showing_placeholder, text, where}
    for one w:sdt. Only a plain-text control that is not bound to custom
    XML and not locked for editing is fillable."""
    pr = sdt.find(W_SDTPR)
    kind = "richText"
    if pr is not None:
        for tag, name in _SDT_TYPES:
            if pr.find(tag) is not None:
                kind = name
                break
    content = sdt.find(W_SDTCONTENT)
    info: dict[str, Any] = {
        "tag": _sdt_val(pr, "w:tag"), "alias": _sdt_val(pr, "w:alias"), "type": kind,
        "fillable": False, "reason": None,
        "showing_placeholder": pr is not None and pr.find(qn("w:showingPlcHdr")) is not None,
        "text": "".join(t.text or "" for t in content.iter(W_T)) if content is not None else "",
        "where": where,
    }
    if kind != "text":
        info["reason"] = "not_plain_text"
    elif pr is not None and pr.find(qn("w:dataBinding")) is not None:
        info["reason"] = "data_bound"  # Word would put the bound XML value back
    elif _sdt_val(pr, "w:lock") in ("contentLocked", "sdtContentLocked"):
        info["reason"] = "content_locked"
    else:
        info["fillable"] = True
    return info


def list_content_controls(doc) -> list[dict[str, Any]]:
    return [content_control_info(sdt, where) for where, root in story_roots(doc) for sdt in root.iter(W_SDT)]


def _set_sdt_text(sdt, text: str) -> None:
    """The control's text becomes `text` in its first run (keeping that
    run's formatting); the placeholder flag and the placeholder style go."""
    pr = sdt.find(W_SDTPR)
    content = sdt.find(W_SDTCONTENT)
    if content is None:
        content = OxmlElement("w:sdtContent")
        sdt.append(content)
    for r in content.iter(qn("w:r")):
        for el in list(r):
            if el.tag in (qn("w:br"), qn("w:cr"), qn("w:tab")):
                r.remove(el)
    ts = list(content.iter(W_T))
    if ts:
        ts[0].text = text
        ts[0].set(_XML_SPACE, "preserve")
        for t in ts[1:]:
            t.text = ""
    else:
        r = OxmlElement("w:r")
        rpr = pr.find(qn("w:rPr")) if pr is not None else None
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        t = OxmlElement("w:t")
        t.text = text
        t.set(_XML_SPACE, "preserve")
        r.append(t)
        host = next(content.iter(W_P), None)
        (content if host is None else host).append(r)
    flag = pr.find(qn("w:showingPlcHdr")) if pr is not None else None
    if flag is not None:
        pr.remove(flag)
        for rs in list(content.iter(qn("w:rStyle"))):
            if rs.get(qn("w:val")) == "PlaceholderText":
                rs.getparent().remove(rs)


def fill_content_controls(doc, values: dict, mapping: dict | None = None) -> dict[str, Any]:
    """Fill plain-text content controls by tag or alias (mapping: tag or
    alias -> values key; else the tag / alias itself or its snake_case).
    Every other control — date, rich text, drop-down, picture, checkbox,
    repeating section… — is left as it is and reported, as is a plain-text
    control with no value.

    Returns {"filled": [{tag, alias}], "not_filled": [{tag, alias, type,
    reason, where}]}."""
    report: dict[str, Any] = {"filled": [], "not_filled": []}
    for where, root in story_roots(doc):
        for sdt in list(root.iter(W_SDT)):
            info = content_control_info(sdt, where)
            base = {"tag": info["tag"], "alias": info["alias"], "type": info["type"]}
            reason = info["reason"]
            if info["fillable"]:
                value = template_value(values, mapping, info["tag"], info["alias"])
                text = as_text(value)
                if text is not None:
                    _set_sdt_text(sdt, text)
                    report["filled"].append({"tag": info["tag"], "alias": info["alias"]})
                    continue
                reason = why_not(value)
            report["not_filled"].append({**base, "reason": reason, "where": where})
    return report


def _fill_summary(what: str, filled: int, not_filled: list[dict], rows: dict | None = None) -> str:
    msg = f"Filled {filled} {what}"
    if rows:
        msg += " (" + ", ".join(f"{k}: {n} rows" for k, n in rows.items()) + ")"
    if not_filled:
        msg += "; not filled: " + ", ".join(
            f"{e.get('marker') or e.get('tag') or e.get('alias')}"
            + (f" [{e['type']}]" if e.get("type") else "") + f" ({e['reason']})" for e in not_filled)
    return msg


def fill_placeholders_action(doc, action: dict, _ctx: dict) -> str:
    """{"type": "fill_placeholders", "values": {...}, "mapping"?: {...}}"""
    rep = fill_placeholders(doc, action.get("values") or {}, action.get("mapping"))
    return _fill_summary("markers", len(rep["filled"]), rep["not_filled"], rep["rows"])


def fill_content_controls_action(doc, action: dict, _ctx: dict) -> str:
    """{"type": "fill_content_controls", "values": {...}, "mapping"?: {...}}"""
    rep = fill_content_controls(doc, action.get("values") or {}, action.get("mapping"))
    return _fill_summary("content controls", len(rep["filled"]), rep["not_filled"])


ACTION_HANDLERS = {
    "add_heading": add_heading,
    "add_paragraph": add_paragraph,
    "add_bullet_list": add_bullet_list,
    "add_numbered_list": add_numbered_list,
    "add_table": add_table,
    "add_page_break": add_page_break,
    "add_horizontal_rule": add_horizontal_rule,
    "replace_text": replace_text,
    "set_header": set_header,
    "set_footer": set_footer,
    "fill_placeholders": fill_placeholders_action,
    "fill_content_controls": fill_content_controls_action,
}


def generate_docx(plan: dict, output_path: str, source_path: str | None = None) -> dict:
    settings = plan.get("document_settings") or {}
    actions = plan.get("actions") or []

    if source_path and os.path.exists(source_path):
        # FIX131 — content only: copy2 also copies xattrs, and the sandbox
        # seccomp profile refuses listxattr (EPERM on /job/in/source.*),
        # which failed every same-kind edit (.docx → .docx) in the sandbox.
        shutil.copyfile(source_path, output_path)
        doc = Document(output_path)
        mode = "edit"
    else:
        doc = Document()
        mode = "create"

    language = (settings.get("language") or "en").lower()
    if language == "th":
        font = settings.get("font_family") or "Sarabun"
        size = int(settings.get("base_font_size") or 16)
        setup_thai_font(doc, font, size)
    else:
        font = settings.get("font_family") or "Arial"
        size = int(settings.get("base_font_size") or 12)
        normal = doc.styles["Normal"]
        normal.font.name = font
        normal.font.size = Pt(size)
    settings["font_family"] = font  # surface to ctx for h/f runs

    if mode == "create":
        setup_page(doc, settings)

    if settings.get("header_text"):
        set_header(doc, {"text": settings["header_text"]}, {"settings": settings})
    if settings.get("footer_text"):
        set_footer(doc, {"text": settings["footer_text"]}, {"settings": settings})

    change_log: list[dict[str, Any]] = []
    ctx = {"settings": settings}

    for i, action in enumerate(actions):
        action_type = action.get("type", "")
        handler = ACTION_HANDLERS.get(action_type)
        if handler is None:
            change_log.append(
                {
                    "action": i + 1,
                    "type": action_type or "<missing>",
                    "status": "skipped",
                    "detail": f"Unknown action: {action_type}",
                }
            )
            continue
        try:
            detail = handler(doc, action, ctx)
            change_log.append(
                {"action": i + 1, "type": action_type, "status": "ok", "detail": detail}
            )
        except Exception as exc:  # noqa: BLE001 — surface per-action
            change_log.append(
                {"action": i + 1, "type": action_type, "status": "error", "detail": str(exc)}
            )

    doc.save(output_path)
    file_size = os.path.getsize(output_path)
    return {
        "success": True,
        "mode": mode,
        "output_path": output_path,
        "file_size_bytes": file_size,
        "actions_executed": sum(1 for c in change_log if c["status"] == "ok"),
        "actions_failed": sum(1 for c in change_log if c["status"] == "error"),
        "change_log": change_log,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {
                    "error": (
                        "Usage: docx_generator.py <plan.json> <output.docx> [source.docx]"
                    )
                }
            )
        )
        return 1
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            plan = json.load(f)
        source = sys.argv[3] if len(sys.argv) > 3 else None
        result = generate_docx(plan, sys.argv[2], source)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__, "success": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
