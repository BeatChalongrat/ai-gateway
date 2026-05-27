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

Usage:  python3 docx_generator.py <plan.json> <output.docx> [source.docx]
Output: JSON result on stdout
"""

import json
import os
import shutil
import sys
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
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
}


def generate_docx(plan: dict, output_path: str, source_path: str | None = None) -> dict:
    settings = plan.get("document_settings") or {}
    actions = plan.get("actions") or []

    if source_path and os.path.exists(source_path):
        shutil.copy2(source_path, output_path)
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
        print(json.dumps({"error": str(exc), "success": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
