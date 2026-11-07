"""AM11 — customers' forms with nothing in them that tells a filler where to write.

No {{marker}}, no named range, no content control, no named shape: only what a
person reading the form sees — a label, and a blank beside it or under it. Four
forms of different shapes, in two languages, none of them carrying a word of
this product or its owner:

* ``monthly_memo_docx``   Word, Thai. A table of label | blank, then a table
  whose headings sit over a row of blanks, then two things the reader does not
  take for blanks: a line of underscores after a label, and a signature line.
  Every blank is something the report agent has a value for.
* ``board_cover_xlsx``    Excel, English. Labels with a colon and a bordered
  blank beside them, a row of headings over bordered blanks, a title and a note
  with nothing beside them, and a formula of the form's own. Two of its blanks
  ("Headcount", "Cash at bank") are things the report agent has no value for.
* ``purchase_request_docx``  Word, Thai. Twelve blanks of a purchase request;
  the report agent has a value for one.
* ``plain_letter_docx``   Word. Paragraphs only: nothing a filler can take for
  a blank.

Built by code so that a test, the runner's integration lane and the walk on the
built apps all use the same bytes of the same four forms.
"""

from __future__ import annotations

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

MEMO_BESIDE = ["ชื่อบริษัท", "งวดบัญชี", "รายได้จากการขาย (บาท)", "กำไรขั้นต้น (บาท)"]
MEMO_OVER = ["ค่าใช้จ่ายรวม (บาท)", "กำไรสุทธิ (บาท)"]
MEMO_LINE = "ผู้จัดทำ: ______________________"
MEMO_SIGN = "ลงชื่อ ........................................ ผู้อนุมัติ"

# what each blank of the memo takes from the report agent, by the blank's own address
MEMO_MAPPING = {
    "table0.r0c1": "company", "table0.r1c1": "period", "table0.r2c1": "revenue", "table0.r3c1": "gross_profit",
    "table1.r1c0": "total_expenses", "table1.r1c1": "net_profit",
}

COVER_BESIDE = [("B4", "Company:"), ("B5", "Period:"), ("B6", "Revenue (THB):"), ("B7", "Gross profit (THB):")]
COVER_OVER = [("B9", "Total expenses (THB)"), ("C9", "Net profit (THB)"), ("D9", "Headcount")]
COVER_LAST = ("B12", "Cash at bank (THB):")
COVER_MAPPING = {
    "Cover!C4": "company", "Cover!C5": "period", "Cover!C6": "revenue", "Cover!C7": "gross_profit",
    "Cover!B10": "total_expenses", "Cover!C10": "net_profit",
}
COVER_NO_VALUE = {"Cover!D10": "Headcount", "Cover!C12": "Cash at bank (THB)"}

REQUEST_LABELS = [
    "เลขที่ใบขอซื้อ", "วันที่ขอซื้อ", "ผู้ขอซื้อ", "แผนก", "ชื่อบริษัท", "ผู้ขายที่เสนอ",
    "เลขประจำตัวผู้เสียภาษีของผู้ขาย", "วันที่ต้องการของ", "สถานที่ส่งของ", "เงื่อนไขการชำระเงิน",
    "งบประมาณที่อนุมัติ (บาท)", "เหตุผลที่ขอซื้อ",
]

# the report agent's fields with a run's values, as its template_values gives them
VALUES = {
    "company": "บริษัท ตัวอย่างการค้า จำกัด", "period": "กันยายน 2569", "revenue": "12,480,300",
    "gross_profit": "4,992,120", "total_expenses": "10,733,058", "net_profit": "1,747,242",
}

SPEC = {"fields": {
    "company": {"label": "ชื่อบริษัท"}, "period": {"label": "งวด"},
    "revenue": {"label": "รายได้จากการขายและบริการ งวดนี้ (บาท)"}, "gross_profit": {"label": "กำไรขั้นต้น งวดนี้ (บาท)"},
    "total_expenses": {"label": "ค่าใช้จ่ายรวม งวดนี้ (บาท)"}, "net_profit": {"label": "กำไรสุทธิ งวดนี้ (บาท)"},
}}


def _doc() -> Document:
    d = Document()
    d.styles["Normal"].font.name = "Sarabun"
    d.styles["Normal"].font.size = Pt(14)
    return d


def monthly_memo_docx(path: str) -> str:
    d = _doc()
    d.add_heading("บันทึกสรุปผลประกอบการประจำเดือน", level=1)
    d.add_paragraph("เรียน คณะกรรมการบริษัท")
    t = d.add_table(rows=len(MEMO_BESIDE), cols=2)
    t.style = "Table Grid"
    for i, label in enumerate(MEMO_BESIDE):
        t.cell(i, 0).text = label
    d.add_paragraph("")
    t2 = d.add_table(rows=2, cols=len(MEMO_OVER))
    t2.style = "Table Grid"
    for i, label in enumerate(MEMO_OVER):
        t2.cell(0, i).text = label
    d.add_paragraph("")
    d.add_paragraph(MEMO_LINE)
    d.add_paragraph(MEMO_SIGN)
    d.save(path)
    return path


def board_cover_xlsx(path: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Cover"
    thin = Side(style="thin", color="808080")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["B2"] = "Monthly board pack — cover sheet"
    ws["B2"].font = Font(bold=True, size=14)
    for ref, label in COVER_BESIDE + [COVER_LAST]:
        ws[ref] = label
        ws.cell(row=ws[ref].row, column=ws[ref].column + 1).border = box
    for ref, label in COVER_OVER:
        ws[ref] = label
        ws[ref].font = Font(bold=True)
        ws[ref].alignment = Alignment(horizontal="center")
        ws.cell(row=ws[ref].row + 1, column=ws[ref].column).border = box
    # the form's own arithmetic: kept as it is, recalculated when the file is opened
    ws["B14"] = "Net margin"
    ws["C14"] = '=IF(N(C6)=0,"",N(C10)/N(C6))'
    ws["C14"].number_format = "0.0%"
    ws["B16"] = "Figures are in Thai baht."
    for col, width in (("A", 3), ("B", 26), ("C", 24), ("D", 18)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    return path


def purchase_request_docx(path: str) -> str:
    d = _doc()
    d.add_heading("ใบขอซื้อ", level=1)
    t = d.add_table(rows=len(REQUEST_LABELS), cols=2)
    t.style = "Table Grid"
    for i, label in enumerate(REQUEST_LABELS):
        t.cell(i, 0).text = label
    d.add_paragraph("")
    d.add_paragraph("ลงชื่อ ........................................ ผู้ขอซื้อ")
    d.save(path)
    return path


def plain_letter_docx(path: str) -> str:
    d = _doc()
    d.add_heading("หนังสือนำส่ง", level=1)
    d.add_paragraph("เรียน ผู้เกี่ยวข้อง")
    d.add_paragraph("ขอนำส่งรายงานผลประกอบการประจำเดือนตามเอกสารแนบ")
    d.add_paragraph("ขอแสดงความนับถือ")
    d.save(path)
    return path


FORMS = {
    "monthly_memo.docx": monthly_memo_docx,
    "board_cover.xlsx": board_cover_xlsx,
    "purchase_request.docx": purchase_request_docx,
    "plain_letter.docx": plain_letter_docx,
}


def write_all(folder: str) -> list[str]:
    """Every form, as a file in ``folder`` (the integration lane and the walk read them from disk)."""
    import os

    os.makedirs(folder, exist_ok=True)
    return [make(os.path.join(folder, name)) for name, make in FORMS.items()]


if __name__ == "__main__":
    import sys

    for p in write_all(sys.argv[1] if len(sys.argv) > 1 else "."):
        print(p)
