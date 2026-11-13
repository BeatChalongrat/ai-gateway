"""Shared, data-driven Office starters for Web Chat and Desktop run_python.

No model/API calls, invented business facts, downloads, or package installs.
Call catalog() for contracts, create(template_id, data, output_path), then
inspect_file(path). Existing documents should be edited, not rebuilt here.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape


TEMPLATES = {
    "pptx.executive_brief": ("pptx", "Decision-oriented briefing", ["Summary", "Evidence", "Recommendation", "Next steps"]),
    "pptx.project_update": ("pptx", "Progress, risks and decisions", ["Progress", "Milestones", "Risks", "Decisions"]),
    "pptx.proposal": ("pptx", "Problem, approach and commercial proposal", ["Problem", "Approach", "Scope", "Timeline", "Cost", "Next steps"]),
    "docx.business_report": ("docx", "Structured business report", ["Executive summary", "Findings", "Recommendations", "Sources"]),
    "docx.proposal": ("docx", "Editable proposal", ["Objective", "Scope", "Deliverables", "Timeline", "Commercial terms"]),
    "docx.meeting_minutes": ("docx", "Decisions and action record", ["Attendees", "Discussion", "Decisions", "Actions"]),
    "pdf.business_report": ("pdf", "Paginated, printable report", ["Summary", "Findings", "Recommendations", "Sources"]),
    "pdf.invoice": ("pdf", "Invoice with calculated line totals", ["invoice_number", "customer", "items"]),
    "xlsx.budget": ("xlsx", "Budget, actual and formula-based variance", ["item", "planned", "actual"]),
    "xlsx.tracker": ("xlsx", "Filterable task/status register", ["task", "owner", "status", "due_date"]),
    "xlsx.analysis": ("xlsx", "Typed analysis table", ["columns", "rows"]),
    "csv.data_export": ("csv", "UTF-8 BOM, typed, formula-safe data exchange", ["columns", "rows"]),
}


def catalog() -> dict:
    return {
        "version": 1,
        "templates": [{"id": k, "format": v[0], "purpose": v[1], "recommended_fields_or_sections": v[2]} for k, v in TEMPLATES.items()],
        "contracts": {
            "document": {"title": "Required", "subtitle": "Optional", "language": "en or th", "sections": [{"heading": "Required", "body": "Optional paragraph", "bullets": ["Optional"], "table": {"headers": ["Column"], "rows": [["Cell"]]}, "sources": ["User-provided references"], "notes": "Optional speaker notes"}]},
            "table": {"title": "Required for XLSX", "columns": [{"key": "id", "label": "Record ID", "type": "text|number|date|formula"}], "rows": [{"id": "00123"}]},
            "invoice": {"title": "Required", "invoice_number": "Required", "customer": "Required", "currency": "THB", "items": [{"description": "Supplied work", "quantity": 1, "unit_price": 100}]},
        },
        "rules": ["Use only supplied facts; ask for missing essential facts.", "Use the original file for template edits; these starters create new files.", "Formula type is XLSX-only; CSV text is escaped against spreadsheet formula execution.", "Check warnings and inspect actual outputs; rendering is not proof of semantic or visual correctness."],
    }


def _text(value, field: str, required=False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{field} must be {'nonempty ' if required else ''}text")
    if len(value) > 200_000:
        raise ValueError(f"{field} is too long")
    return value


def _sections(data):
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections or len(sections) > 100:
        raise ValueError("Supply 1–100 sections; do not invent missing report content")
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("Each section must be an object")
        _text(section.get("heading"), "section.heading", True)
        _text(section.get("body"), "section.body")
        for key in ("bullets", "sources"):
            values = section.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"{key} must be a list")
            for value in values:
                _text(value, key, True)
        if section.get("table"):
            table = section["table"]
            headers, rows = table.get("headers"), table.get("rows")
            if not isinstance(headers, list) or not 1 <= len(headers) <= 12 or not isinstance(rows, list):
                raise ValueError("Table needs 1–12 headers and rows")
            if any(not isinstance(row, list) or len(row) != len(headers) for row in rows):
                raise ValueError("Every table row must match its headers")
    return sections


def _chunks(values, count):
    return [values[i:i + count] for i in range(0, len(values), count)]


def _pptx(data, output, template):
    from pptx_generator import generate_pptx
    title = data["title"]
    if len(title) > 110:
        raise ValueError("Use a shorter presentation title (110 characters maximum)")
    slides = [{"type": "title", "title": title, "subtitle": data.get("subtitle", ""), "author": data.get("author", "")}]
    for section in _sections(data):
        if len(section["heading"]) > 100:
            raise ValueError("Use a shorter slide heading (100 characters maximum)")
        # Break long text into bounded bullet lines rather than shrinking it to unreadable type.
        content = []
        for value in ([section["body"]] if section.get("body") else []) + section.get("bullets", []):
            while len(value) > 150:
                split = value.rfind(" ", 50, 150)
                split = split if split > 0 else 140
                content.append(value[:split])
                value = value[split:].lstrip()
            if value:
                content.append(value)
        for n, items in enumerate(_chunks(content, 4) or ([[]] if not section.get("table") else [])):
            slides.append({"type": "content", "title": section["heading"] + (f" ({n + 1})" if n else ""), "bullets": items,
                           "speaker_notes": "\n".join([section.get("notes", ""), *section.get("sources", [])])})
        if section.get("table"):
            table = section["table"]
            if len(table["headers"]) > 6 or any(len(str(c)) > 80 for row in table["rows"] for c in row):
                raise ValueError("Presentation tables need at most 6 columns and concise cells; move detail to an appendix workbook")
            for rows in _chunks(table["rows"], 6):
                slides.append({"type": "table", "title": section["heading"], "headers": table["headers"], "rows": rows})
    palettes = {"pptx.executive_brief": {"primary": "142B49", "accent": "D5A44C"},
                "pptx.project_update": {"primary": "123B3A", "accent": "24A89B"},
                "pptx.proposal": {"primary": "30244E", "accent": "A78BFA"}}
    settings = {"color_scheme": palettes[template], "language": data.get("language", "en"), "title": title, "author": data.get("author", ""),
                "font": {"heading": "Sarabun", "body": "Sarabun", "heading_size": 28, "body_size": 18}}
    result = generate_pptx({"presentation_settings": settings, "slides": slides}, str(output))
    if not result["success"]:
        raise ValueError("Presentation generation failed: " + json.dumps(result["change_log"]))


def _docx(data, output):
    from docx_generator import generate_docx
    actions = [{"type": "add_heading", "level": 0, "text": data["title"]}]
    if data.get("subtitle"):
        actions.append({"type": "add_paragraph", "text": data["subtitle"]})
    for section in _sections(data):
        actions.append({"type": "add_heading", "level": 1, "text": section["heading"]})
        if section.get("body"):
            actions.append({"type": "add_paragraph", "text": section["body"]})
        if section.get("bullets"):
            actions.append({"type": "add_bullet_list", "items": section["bullets"]})
        if section.get("table"):
            actions.append({"type": "add_table", **section["table"]})
        for source in section.get("sources", []):
            actions.append({"type": "add_paragraph", "text": source, "italic": True})
    actions.append({"type": "set_footer", "text": "หน้า {page}" if data.get("language") == "th" else "Page {page}"})
    result = generate_docx({"document_settings": {"language": data.get("language", "en"), "font_family": "Sarabun", "base_font_size": 14 if data.get("language") == "th" else 11}, "actions": actions}, str(output))
    if not result["success"]:
        raise ValueError("Document generation failed")
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.shared import Pt
    from docx_generator import _set_run_cs_font
    doc = Document(output)
    doc.core_properties.title = data["title"]
    doc.core_properties.author = data.get("author", "")
    for table in doc.tables:
        table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(5)
                    paragraph.paragraph_format.space_before = Pt(3)
                    for run in paragraph.runs:
                        run.font.name = "Sarabun"
                        run.font.size = Pt(14 if data.get("language") == "th" else 11)
                        _set_run_cs_font(run, "Sarabun")
    doc.save(output)


def _pdf(data, output, invoice=False):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
    from pdf_generator import register_thai_font
    font = register_thai_font()
    body = ParagraphStyle("OfficeBody", fontName=font, fontSize=12, leading=19, spaceAfter=8, wordWrap="CJK")
    heading = ParagraphStyle("OfficeHeading", parent=body, fontSize=18, leading=25, spaceBefore=16, keepWithNext=True, textColor=colors.HexColor("#16324F"))
    p = lambda value, style=body: Paragraph(escape(str(value)).replace("\n", "<br/>"), style)
    story = [p(data["title"], heading), Spacer(1, 12)]
    def table(headers, rows):
        grid = [[p(cell) for cell in headers]] + [[p(cell) for cell in row] for row in rows]
        t = LongTable(grid, colWidths=[(A4[0] - 96) / len(headers)] * len(headers), repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EFF5")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LINEBELOW", (0, 0), (-1, -1), .3, colors.lightgrey)]))
        story.append(t)
    if invoice:
        for field in ("invoice_number", "customer", "currency"):
            story.append(p(f"{field}: {_text(data.get(field), field, True)}"))
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("Invoice items are required")
        rows, total = [], Decimal(0)
        for item in items:
            description = _text(item.get("description"), "description", True)
            qty, price = Decimal(str(item["quantity"])), Decimal(str(item["unit_price"]))
            if not qty.is_finite() or not price.is_finite() or qty < 0 or price < 0:
                raise ValueError("Invoice quantities and prices must be finite and nonnegative")
            amount = (qty * price).quantize(Decimal("0.01"))
            total += amount
            rows.append([description, str(qty), f"{price:.2f}", f"{amount:.2f}"])
        table(["Description", "Quantity", "Unit price", "Amount"], rows)
        story.append(p(f"Total ({data['currency']}): {total:.2f}", heading))
    else:
        for section in _sections(data):
            story.append(p(section["heading"], heading))
            if section.get("body"):
                story.append(p(section["body"]))
            story.extend(p("• " + item) for item in section.get("bullets", []))
            if section.get("table"):
                table(section["table"]["headers"], section["table"]["rows"])
            story.extend(p(source) for source in section.get("sources", []))
    def footer(canvas, doc):
        canvas.setFont(font, 9)
        canvas.drawRightString(A4[0] - 48, 27, str(doc.page))
    SimpleDocTemplate(str(output), pagesize=A4, leftMargin=48, rightMargin=48, topMargin=40, bottomMargin=45, title=data["title"], author=data.get("author", "")).build(story, onFirstPage=footer, onLaterPages=footer)


def _typed(value, kind):
    if value is None:
        return None
    if kind == "text":
        return str(value)
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError("A boolean is not a numeric measure")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Numbers must be finite")
        return number
    if kind == "date":
        return dt.date.fromisoformat(str(value))
    if kind == "formula" and isinstance(value, str) and value.startswith("="):
        return value
    raise ValueError(f"Invalid value/type: {kind}")


def _table_data(template, data):
    defaults = {
        "xlsx.budget": [("item", "Item", "text"), ("planned", "Planned", "number"), ("actual", "Actual", "number")],
        "xlsx.tracker": [("task", "Task", "text"), ("owner", "Owner", "text"), ("status", "Status", "text"), ("due_date", "Due date", "date")],
    }
    columns = [{"key": k, "label": label, "type": kind} for k, label, kind in defaults.get(template, [])] or data.get("columns")
    rows = data.get("rows")
    if not isinstance(columns, list) or not 1 <= len(columns) <= 100 or not isinstance(rows, list) or len(rows) > 100_000:
        raise ValueError("Supply columns (1–100) and rows (up to 100000)")
    keys = []
    for col in columns:
        key = _text(col.get("key"), "column.key", True)
        _text(col.get("label", key), "column.label", True)
        if col.get("type", "text") not in ("text", "number", "date", "formula"):
            raise ValueError("Unknown column type")
        keys.append(key)
    if len(set(keys)) != len(keys):
        raise ValueError("Column keys must be unique")
    values = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - set(keys):
            raise ValueError("Rows must be objects with declared column keys")
        if template == "xlsx.budget" and any(row.get(key) is None for key in keys):
            raise ValueError("Budget rows require item, planned and actual; missing amounts are not zero")
        values.append([_typed(row.get(c["key"]), c.get("type", "text")) for c in columns])
    return columns, values


def _spreadsheet(template, data, output):
    columns, rows = _table_data(template, data)
    if template == "csv.data_export":
        if any(c.get("type") == "formula" for c in columns):
            raise ValueError("CSV does not accept executable formula columns")
        def safe(value):
            # Numeric negatives remain numeric; text with formula/control prefixes is literal.
            if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
                return "'" + value
            return value
        with output.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([safe(c.get("label", c["key"])) for c in columns])
            writer.writerows([[safe(v) for v in row] for row in rows])
        return
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([c.get("label", c["key"]) for c in columns] + (["Variance"] if template == "xlsx.budget" else []))
    for cell in ws[1]:
        cell.data_type = "s"
    for n, values in enumerate(rows, 2):
        ws.append(values)
        for j, col in enumerate(columns, 1):
            cell = ws.cell(n, j)
            if col.get("type", "text") == "text" and cell.value is not None:
                cell.data_type = "s"  # an ID/string beginning '=' is not a formula
                cell.number_format = "@"
            elif col.get("type") == "date":
                cell.number_format = "yyyy-mm-dd"
            elif col.get("type") in ("number", "formula"):
                cell.number_format = '#,##0.00;[Red](#,##0.00);"–"'
        if template == "xlsx.budget":
            ws.cell(n, 4, f"=B{n}-C{n}").number_format = '#,##0.00;[Red](#,##0.00)'
    if rows:
        tab = Table(displayName="OfficeData", ref=f"A1:{get_column_letter(ws.max_column)}{ws.max_row}")
        tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        ws.add_table(tab)
    if template == "xlsx.budget" and rows:
        end = ws.max_row
        ws.append(["Total", f"=SUM(B2:B{end})", f"=SUM(C2:C{end})", f"=B{end + 1}-C{end + 1}"])
        for cell in ws[ws.max_row][1:]:
            cell.number_format = '#,##0.00;[Red](#,##0.00)'
    for row in ws:
        for cell in row:
            cell.font = Font(name="Sarabun", size=11, bold=cell.row == 1 or (template == "xlsx.budget" and bool(rows) and cell.row == ws.max_row), color="FFFFFF" if cell.row == 1 else "172B4D")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.row == 1:
                cell.fill = PatternFill("solid", fgColor="16324F")
    for j in range(1, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(j)].width = min(45, max(16, max(len(str(ws.cell(i, j).value or "")) for i in range(1, min(ws.max_row, 200) + 1)) + 2))
    ws.freeze_panes = "A2"
    ws.print_title_rows = "1:1"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.print_options.horizontalCentered = True
    ws.oddFooter.center.text = "Page &P of &N"
    ws.oddHeader.center.text = data["title"].replace("&", "&&")
    wb.properties.title = data["title"]
    wb.save(output)


def inspect_file(path) -> dict:
    path = Path(path)
    kind = path.suffix.lower()
    report = {"filename": path.name, "bytes": path.stat().st_size, "warnings": []}
    if kind == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=False)
        cached = load_workbook(path, data_only=True)
        formulas, errors, missing = [], [], []
        for sheet in wb:
            for row in sheet:
                for cell in row:
                    value = cached[sheet.title][cell.coordinate]
                    loc = f"{sheet.title}!{cell.coordinate}"
                    if cell.data_type == "f":
                        formulas.append(loc)
                        if value.value is None:
                            missing.append(loc)
                    if value.data_type == "e":
                        errors.append({"cell": loc, "error": value.value})
        report.update(sheets=wb.sheetnames, formulas=len(formulas), missing_cached_values=len(missing), formula_errors=errors[:100])
        if missing:
            report["warnings"].append("Formula caches missing: recalculate before reporting evaluated values")
        wb.close()
        cached.close()
    elif kind == ".docx":
        from docx import Document
        doc = Document(path)
        report.update(paragraphs=len(doc.paragraphs), tables=len(doc.tables))
    elif kind == ".pptx":
        from pptx import Presentation
        prs = Presentation(path)
        report["slides"] = len(prs.slides)
        for i, slide in enumerate(prs.slides, 1):
            for shape in slide.shapes:
                if shape.left < 0 or shape.top < 0 or shape.left + shape.width > prs.slide_width or shape.top + shape.height > prs.slide_height:
                    report["warnings"].append(f"Slide {i}: shape outside canvas")
    elif kind == ".pdf":
        from pypdf import PdfReader
        pdf = PdfReader(path)
        report.update(pages=len(pdf.pages), text_characters=sum(len(p.extract_text() or "") for p in pdf.pages))
    elif kind == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            headers = next(reader, [])
            count = 0
            for row in reader:
                count += 1
                if len(row) != len(headers):
                    raise ValueError(f"CSV row {count + 1} does not match header width")
        report.update(columns=headers, rows=count, encoding="utf-8-sig")
    else:
        raise ValueError("Unsupported inspection format")
    return report


def create(template_id: str, data: dict, output_path) -> dict:
    if template_id not in TEMPLATES:
        raise ValueError("Unknown template; call catalog()")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    kind = TEMPLATES[template_id][0]
    output = Path(output_path)
    if output.suffix.lower() != "." + kind:
        raise ValueError("Output extension must match the template format")
    if output.exists():
        raise FileExistsError("Choose a new output path; templates do not overwrite originals")
    if kind != "csv":
        _text(data.get("title"), "title", True)
    try:
        if kind == "pptx":
            _pptx(data, output, template_id)
        elif kind == "docx":
            _docx(data, output)
        elif kind == "pdf":
            _pdf(data, output, invoice=template_id == "pdf.invoice")
        else:
            _spreadsheet(template_id, data, output)
        return {"template": template_id, **inspect_file(output)}
    except Exception:
        output.unlink(missing_ok=True)
        raise
