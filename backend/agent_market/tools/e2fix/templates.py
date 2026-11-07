"""Customer-template fixtures (AM2 Phase A, input to Phase E).

    python -m agent_market.tools.e2fix.templates [out_dir]   (from backend/)

Each template comes with the values to fill and what must be true after the
fill (expected.json): which markers are filled, which are left alone and why,
what a formula cell recalculates to, what must survive (merges, chart,
protection), and — for templates the filler cannot use — what the user must
be told instead of a silent no-op.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.workbook.defined_name import DefinedName
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches
from pptx.util import Pt as PPt

VALUES = {
    "company_name": "บริษัท สรานเทค โซลูชั่นส์ จำกัด",
    "period": "กันยายน 2569",
    "invoice_count": "4",
    "total_amount": "168,000.70",
    "vat_amount": "10,990.70",
    "prepared_by": "ฝ่ายบัญชี",
    "lines": [
        {"description": "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด — IV6909-0015", "quantity": "1", "amount": "12,850.70"},
        {"description": "บริษัท เอเชีย คลาวด์ โซลูชั่นส์ จำกัด — INV2569/0912", "quantity": "1", "amount": "85,600.00"},
        {"description": "หจก. ช่างเหล็กการโยธา — CS-2569-0187", "quantity": "1", "amount": "53,500.00"},
        {"description": "บมจ. ไทยพาณิชย์ขนส่ง — ขส.69/0334", "quantity": "1", "amount": "16,050.00"},
    ],
}


def _thai_run(run, size=14):
    run.font.name = "TH Sarabun New"
    run.font.size = Pt(size)
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs"):
        fonts.set(qn(a), "TH Sarabun New")
    lang = OxmlElement("w:lang")
    lang.set(qn("w:bidi"), "th-TH")
    rpr.append(lang)


def docx_placeholders(out: Path) -> dict:
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "{{company_name}} — รายงานประจำเดือน"
    doc.sections[0].footer.paragraphs[0].text = "จัดทำโดย {{prepared_by}}"
    p = doc.add_paragraph()
    _thai_run(p.add_run("สรุปใบแจ้งหนี้ของ {{company_name}} ประจำงวด {{period}}"), 16)
    p = doc.add_paragraph()
    # a placeholder Word split across two runs (what editing in Word does)
    r1 = p.add_run("จำนวนเอกสาร {{invoice_")
    r2 = p.add_run("count}} ฉบับ")
    _thai_run(r1)
    _thai_run(r2)
    t = doc.add_table(rows=2, cols=2)
    t.style = "Table Grid"
    t.cell(0, 0).text, t.cell(0, 1).text = "ยอดรวม", "{{total_amount}}"
    t.cell(1, 0).text, t.cell(1, 1).text = "ภาษีมูลค่าเพิ่ม", "{{vat_amount}}"
    doc.save(out)
    return {"kind": "docx_placeholders", "markers": ["company_name", "period", "invoice_count", "total_amount",
                                                    "vat_amount", "prepared_by"],
            "expect": {"filled": ["company_name", "period", "invoice_count", "total_amount", "vat_amount", "prepared_by"],
                       "no_braces_left": True, "split_run_marker": "invoice_count",
                       "in_header": ["company_name"], "in_footer": ["prepared_by"], "in_table": ["total_amount", "vat_amount"]}}


def docx_repeating_rows(out: Path) -> dict:
    doc = Document()
    p = doc.add_paragraph()
    _thai_run(p.add_run("รายการใบแจ้งหนี้ งวด {{period}}"), 16)
    t = doc.add_table(rows=3, cols=3)
    t.style = "Table Grid"
    hdr = ["รายการ", "จำนวน", "จำนวนเงิน"]
    for i, h in enumerate(hdr):
        t.cell(0, i).text = h
    for i, m in enumerate(["{{line.description}}", "{{line.quantity}}", "{{line.amount}}"]):
        t.cell(1, i).text = m
        t.cell(1, i).paragraphs[0].runs[0].bold = False
    t.cell(2, 0).text, t.cell(2, 1).text, t.cell(2, 2).text = "รวม", "", "{{total_amount}}"
    doc.save(out)
    return {"kind": "docx_repeating_rows", "markers": ["period", "line.description", "line.quantity", "line.amount", "total_amount"],
            "expect": {"repeat_rows": len(VALUES["lines"]), "row_order": [l["description"] for l in VALUES["lines"]],
                       "header_row_kept": True, "total_row_after_lines": True, "no_braces_left": True}}


def _sdt(parent_p, tag: str, alias: str, text: str, kind: str):
    """A block of Word content-control XML inside a paragraph (w:sdt / w:sdtContent)."""
    sdt = OxmlElement("w:sdt")
    pr = OxmlElement("w:sdtPr")
    a = OxmlElement("w:alias")
    a.set(qn("w:val"), alias)
    tg = OxmlElement("w:tag")
    tg.set(qn("w:val"), tag)
    pr.append(a)
    pr.append(tg)
    if kind == "text":
        pr.append(OxmlElement("w:text"))
    elif kind == "richText":
        pr.append(OxmlElement("w:richText"))
    elif kind == "date":
        dt_ = OxmlElement("w:date")
        fmt = OxmlElement("w:dateFormat")
        fmt.set(qn("w:val"), "d/M/yyyy")
        dt_.append(fmt)
        pr.append(dt_)
    elif kind == "dropDownList":
        dd = OxmlElement("w:dropDownList")
        for v in ("อนุมัติ", "ไม่อนุมัติ"):
            li = OxmlElement("w:listItem")
            li.set(qn("w:displayText"), v)
            li.set(qn("w:value"), v)
            dd.append(li)
        pr.append(dd)
    ph = OxmlElement("w:showingPlcHdr")
    pr.append(ph)
    content = OxmlElement("w:sdtContent")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    content.append(r)
    sdt.append(pr)
    sdt.append(content)
    parent_p._p.append(sdt)


def docx_content_controls(out: Path) -> dict:
    doc = Document()
    doc.add_paragraph("แบบฟอร์มสรุปใบแจ้งหนี้")
    p = doc.add_paragraph("ชื่อบริษัท: ")
    _sdt(p, "CompanyName", "ชื่อบริษัท", "คลิกเพื่อพิมพ์ชื่อบริษัท", "text")
    p = doc.add_paragraph("ยอดรวม: ")
    _sdt(p, "TotalAmount", "ยอดรวม", "คลิกเพื่อพิมพ์ยอดรวม", "text")
    p = doc.add_paragraph("วันที่อนุมัติ: ")
    _sdt(p, "ApprovalDate", "วันที่อนุมัติ", "เลือกวันที่", "date")
    p = doc.add_paragraph("หมายเหตุ: ")
    _sdt(p, "Notes", "หมายเหตุ", "พิมพ์หมายเหตุ", "richText")
    p = doc.add_paragraph("สถานะ: ")
    _sdt(p, "Status", "สถานะ", "เลือกสถานะ", "dropDownList")
    doc.save(out)
    return {"kind": "docx_content_controls",
            "controls": [{"tag": "CompanyName", "type": "text"}, {"tag": "TotalAmount", "type": "text"},
                         {"tag": "ApprovalDate", "type": "date"}, {"tag": "Notes", "type": "richText"},
                         {"tag": "Status", "type": "dropDownList"}],
            "mapping": {"CompanyName": "company_name", "TotalAmount": "total_amount"},
            "expect": {"filled_plain_text": {"CompanyName": VALUES["company_name"], "TotalAmount": VALUES["total_amount"]},
                       "placeholder_flag_cleared": ["CompanyName", "TotalAmount"],
                       "not_filled_explained": ["ApprovalDate", "Notes", "Status"],
                       "fallback": "detect_and_explain_then_mapping_screen"}}


def docx_no_markers(out: Path) -> dict:
    doc = Document()
    doc.add_paragraph("บันทึกสรุปค่าใช้จ่ายประจำเดือน")
    t = doc.add_table(rows=3, cols=2)
    t.style = "Table Grid"
    for i, label in enumerate(["บริษัท", "งวด", "ยอดรวม"]):
        t.cell(i, 0).text = label
    doc.save(out)
    return {"kind": "docx_no_markers", "markers": [],
            "expect": {"mapping_screen": True, "targets": [{"cell": "table0.r0c1", "label": "บริษัท"},
                                                           {"cell": "table0.r1c1", "label": "งวด"},
                                                           {"cell": "table0.r2c1", "label": "ยอดรวม"}],
                       "silent_noop_forbidden": True}}


def xlsx_named(out: Path) -> dict:
    wb = Workbook()
    ws = wb.active
    ws.title = "สรุป"
    ws["A1"] = "สรุปใบแจ้งหนี้"
    ws.merge_cells("A1:C1")
    ws["A2"], ws["A3"] = "บริษัท", "งวด"
    ws["A5"], ws["A6"], ws["A7"] = "ยอดก่อนภาษี", "ภาษีมูลค่าเพิ่ม", "รวมทั้งสิ้น"
    ws["B5"], ws["B6"] = 0, 0
    ws["B7"] = "=B5+B6"
    ws["A9"], ws["B9"], ws["C9"] = "รายการ", "จำนวน", "จำนวนเงิน"
    ws["A10"] = None
    ws["C14"] = "=SUM(C10:C13)"
    ws["A14"] = "รวมรายการ"
    for name, ref in (("CompanyName", "$B$2"), ("Period", "$B$3"), ("Subtotal", "$B$5"), ("VatAmount", "$B$6"),
                      ("GrandTotal", "$B$7"), ("LinesTable", "$A$10:$C$13")):
        wb.defined_names[name] = DefinedName(name, attr_text=f"'สรุป'!{ref}")
    chart = BarChart()
    chart.title = "ยอดและภาษี"
    chart.add_data(Reference(ws, min_col=2, min_row=5, max_row=6))
    chart.set_categories(Reference(ws, min_col=1, min_row=5, max_row=6))
    ws.add_chart(chart, "E2")
    lock = wb.create_sheet("ข้อมูลอ้างอิง")
    lock["A1"], lock["B1"] = "อัตราภาษี", 0.07
    lock["A2"], lock["B2"] = "ยอดรวมจากสรุป", "=สรุป!B7"
    lock.protection.sheet = True
    lock.protection.password = "readonly"
    wb.defined_names["VatRate"] = DefinedName("VatRate", attr_text="'ข้อมูลอ้างอิง'!$B$1")
    wb.save(out)
    return {"kind": "xlsx_named_ranges",
            "names": {"CompanyName": "สรุป!B2", "Period": "สรุป!B3", "Subtotal": "สรุป!B5", "VatAmount": "สรุป!B6",
                      "GrandTotal": "สรุป!B7 (formula)", "LinesTable": "สรุป!A10:C13", "VatRate": "ข้อมูลอ้างอิง!B1 (protected sheet)"},
            "fill": {"CompanyName": VALUES["company_name"], "Period": VALUES["period"], "Subtotal": 157010.00,
                     "VatAmount": 10990.70, "GrandTotal": 168000.70, "LinesTable": VALUES["lines"], "VatRate": 0.07},
            "expect": {"written": ["CompanyName", "Period", "Subtotal", "VatAmount", "LinesTable"],
                       "refused": {"GrandTotal": "formula_cell", "VatRate": "protected_sheet"},
                       "formulas_kept": ["สรุป!B7", "สรุป!C14", "ข้อมูลอ้างอิง!B2"],
                       "recalculated": {"สรุป!B7": 168000.70, "สรุป!C14": 168000.70, "ข้อมูลอ้างอิง!B2": 168000.70},
                       "lines_written_as_numbers": True,
                       "merged_kept": ["A1:C1"], "chart_kept": True, "protection_kept": "ข้อมูลอ้างอิง"}}


def xlsx_no_names(out: Path) -> dict:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"], ws["A2"], ws["A3"] = "บริษัท", "งวด", "ยอดรวม"
    wb.save(out)
    return {"kind": "xlsx_no_markers", "expect": {"mapping_screen": True,
                                                   "targets": [{"cell": "Sheet1!B1", "label": "บริษัท"},
                                                               {"cell": "Sheet1!B2", "label": "งวด"},
                                                               {"cell": "Sheet1!B3", "label": "ยอดรวม"}],
                                                   "silent_noop_forbidden": True}}


def pptx_theme(out: Path) -> dict:
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "สรุปค่าใช้จ่าย {{period}}"
    s.shapes.title.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x1F, 0x6F, 0x8B)
    data = CategoryChartData()
    data.categories = ["ไทยออฟฟิศ", "เอเชีย คลาวด์", "ช่างเหล็ก", "ไทยพาณิชย์"]
    data.add_series("ยอด (บาท)", (12850.70, 85600.00, 53500.00, 16050.00))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.6), Inches(8.4), Inches(4.8), data)
    tb = s.shapes.add_textbox(Inches(0.8), Inches(6.5), Inches(8), Inches(0.6)).text_frame
    tb.text = "รวม {{total_amount}} บาท"
    tb.paragraphs[0].runs[0].font.size = PPt(18)
    prs.save(out)
    return {"kind": "pptx_theme_chart", "markers": ["period", "total_amount"],
            "expect": {"filled": ["period", "total_amount"], "chart_kept": True, "title_color": "1F6F8B"}}


PDF_EXPECT = {
    "docx_placeholders.docx": {"pages": 1, "text_contains": ["สรุปใบแจ้งหนี้ของ", "บริษัท สรานเทค โซลูชั่นส์ จำกัด", "ประจำงวด กันยายน 2569", "168,000.70"]},
    "docx_repeating_rows.docx": {"pages": 1, "text_contains": ["รายการใบแจ้งหนี้", "หจก. ช่างเหล็กการโยธา", "53,500.00"]},
    "xlsx_named_ranges.xlsx": {"pages_min": 1, "text_contains": ["สรุปใบแจ้งหนี้", "บริษัท สรานเทค โซลูชั่นส์ จำกัด"]},
    "pptx_theme_chart.pptx": {"pages": 1, "text_contains": ["สรุปค่าใช้จ่าย กันยายน 2569"]},
}


def main(argv) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "fixtures" / "templates"
    out.mkdir(parents=True, exist_ok=True)
    spec = {}
    for name, fn in (("docx_placeholders.docx", docx_placeholders), ("docx_repeating_rows.docx", docx_repeating_rows),
                     ("docx_content_controls.docx", docx_content_controls), ("docx_no_markers.docx", docx_no_markers),
                     ("xlsx_named_ranges.xlsx", xlsx_named), ("xlsx_no_markers.xlsx", xlsx_no_names),
                     ("pptx_theme_chart.pptx", pptx_theme)):
        spec[name] = fn(out / name)
    (out / "expected.json").write_text(json.dumps({"schema": "am2.templates.v1", "values": VALUES, "templates": spec,
                                                  "pdf": PDF_EXPECT}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"{len(spec)} templates -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
