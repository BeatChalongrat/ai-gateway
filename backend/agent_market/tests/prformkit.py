"""AM12 — two organisations' purchase-request forms, of different shapes, with nothing in them that tells a
filler where to write, and not a word of this product or its owner.

* ``request_form_docx``  Word, Thai. A table of label | blank; a table of items — six headings over four
  empty rows — with three rows of totals under them in the same table; a blank drawn as a line after its
  label; a signature line.
* ``request_form_xlsx``  Excel, English. Labels with a colon and a bordered blank beside them, in two
  columns; a bordered grid of items whose rows carry their own numbers; a subtotal the form works out
  itself (a formula: never written over), tax and total blanks; a blank for an approver.

* ``request_form_pdf``   PDF, Thai. Eleven fields to type in, each named by the words printed to its
  left (the fields' own names are "f1" … "f11" and say nothing), one named only by its description, and
  a check box, which is not written.
* ``printed_form_pdf``   the same page with no fields: a picture of a form. Nothing in it takes a value.

``other_form_docx`` is a form for something else altogether (a leave request): the agent has a value for
one of its blanks, and it is refused before any work.

Built by code so that a unit test, the runner's integration lane and the walk on the built apps all use
the same bytes of the same forms.
"""

from __future__ import annotations

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

DOCX_BESIDE = ["เลขที่ใบขอซื้อ", "วันที่ขอซื้อ", "ผู้ขอซื้อ", "แผนก", "ผู้ขายที่เสนอ", "เลขประจำตัวผู้เสียภาษีของผู้ขาย",
               "สถานที่ส่งของ", "วันที่ต้องการของ"]
DOCX_HEADS = ["ลำดับ", "รายการ", "จำนวน", "หน่วย", "ราคาต่อหน่วย (บาท)", "จำนวนเงิน (บาท)"]
DOCX_ROWS = 4
DOCX_TOTALS = ["รวมเป็นเงิน", "ภาษีมูลค่าเพิ่ม", "รวมทั้งสิ้น"]
DOCX_LINE = "เงื่อนไขการชำระเงิน: ______________________"
DOCX_NOTE = "หมายเหตุ: ______________________"
DOCX_SIGN = "ลงชื่อ ........................................ ผู้อนุมัติ"
DOCX_ITEMS = "rows@t1r1"

# what each place of the Word form takes from the procurement agent, by the place's own address — what a
# careful reader of the form's labels proposes, and a person confirms
DOCX_MAPPING = {
    "table0.r1c1": "request_date", "table0.r2c1": "requester", "table0.r4c1": "seller_as_shown", "table0.r5c1": "seller_tax_id",
    "table0.r6c1": "delivery_place", "table0.r7c1": "needed_by",
    DOCX_ITEMS: "lines", f"{DOCX_ITEMS}.c0": "lines.no", f"{DOCX_ITEMS}.c1": "lines.description", f"{DOCX_ITEMS}.c2": "lines.quantity",
    f"{DOCX_ITEMS}.c3": "lines.unit", f"{DOCX_ITEMS}.c4": "lines.unit_price", f"{DOCX_ITEMS}.c5": "lines.amount",
    "table1.r5c5": "subtotal", "table1.r6c5": "vat_amount", "table1.r7c5": "total",
}
# the blanks of the Word form the agent has nothing for, whatever the round: they stay empty and are named
DOCX_NEVER = {"table0.r0c1": "เลขที่ใบขอซื้อ", "table0.r3c1": "แผนก"}

XLSX_SHEET = "PR"
XLSX_BESIDE = [("B4", "Requested by:"), ("B5", "Date:"), ("B6", "Deliver to:"), ("E4", "Supplier:"), ("E5", "Supplier tax ID:"),
               ("E6", "Payment terms:")]
XLSX_HEADS = ["No.", "Description", "Qty", "Unit", "Unit price", "Amount"]   # B8 … G8
XLSX_ROWS = 6                                                                 # rows 9 … 14, numbered 1 … 6
XLSX_ITEMS = "rows@s0r9"
XLSX_MAPPING = {
    "PR!C4": "requester", "PR!C5": "request_date", "PR!C6": "delivery_place", "PR!F4": "seller_as_shown", "PR!F5": "seller_tax_id",
    XLSX_ITEMS: "lines", f"{XLSX_ITEMS}.C": "lines.description", f"{XLSX_ITEMS}.D": "lines.quantity", f"{XLSX_ITEMS}.E": "lines.unit",
    f"{XLSX_ITEMS}.F": "lines.unit_price", f"{XLSX_ITEMS}.G": "lines.amount",
    "PR!G16": "vat_amount", "PR!G17": "total",
}
XLSX_NEVER = {"PR!F6": "Payment terms", "PR!C19": "Approved by"}

OTHER_LABELS = ["ชื่อพนักงาน", "รหัสพนักงาน", "แผนก", "ประเภทการลา", "ลาตั้งแต่วันที่", "ถึงวันที่", "จำนวนวันลา", "เหตุผลการลา",
                "ผู้ปฏิบัติงานแทน", "วันที่ยื่นใบลา"]


def _doc() -> Document:
    d = Document()
    d.styles["Normal"].font.name = "Sarabun"
    d.styles["Normal"].font.size = Pt(14)
    return d


def request_form_docx(path: str) -> str:
    d = _doc()
    d.add_heading("ใบขอซื้อ", level=1)
    t = d.add_table(rows=len(DOCX_BESIDE), cols=2)
    t.style = "Table Grid"
    for i, label in enumerate(DOCX_BESIDE):
        t.cell(i, 0).text = label
    d.add_paragraph("รายการที่ขอซื้อ")
    items = d.add_table(rows=1 + DOCX_ROWS + len(DOCX_TOTALS), cols=len(DOCX_HEADS))
    items.style = "Table Grid"
    for i, label in enumerate(DOCX_HEADS):
        items.cell(0, i).text = label
    for i, label in enumerate(DOCX_TOTALS):
        items.cell(1 + DOCX_ROWS + i, len(DOCX_HEADS) - 2).text = label
    d.add_paragraph("")
    d.add_paragraph(DOCX_LINE)
    d.add_paragraph(DOCX_NOTE)
    d.add_paragraph(DOCX_SIGN)
    d.save(path)
    return path


def request_form_xlsx(path: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = XLSX_SHEET
    thin = Side(style="thin", color="808080")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["B2"] = "Purchase requisition"
    ws["B2"].font = Font(bold=True, size=14)
    for ref, label in XLSX_BESIDE:
        ws[ref] = label
        ws.cell(row=ws[ref].row, column=ws[ref].column + 1).border = box
    for i, label in enumerate(XLSX_HEADS):
        c = ws.cell(row=8, column=2 + i, value=label)
        c.font, c.border, c.alignment = Font(bold=True), box, Alignment(horizontal="center")
    for r in range(XLSX_ROWS):
        for i in range(len(XLSX_HEADS)):
            ws.cell(row=9 + r, column=2 + i).border = box
        ws.cell(row=9 + r, column=2, value=r + 1)
    # the form's own arithmetic: kept as it is, recalculated when the file is opened
    ws["F15"], ws["G15"] = "Subtotal", f"=SUM(G9:G{8 + XLSX_ROWS})"
    ws["F16"], ws["F17"] = "VAT", "Total"
    for ref in ("G15", "G16", "G17"):
        ws[ref].border = box
        ws[ref].number_format = "#,##0.00"
    ws["B19"] = "Approved by:"
    ws["C19"].border = box
    for col, width in (("A", 3), ("B", 16), ("C", 44), ("D", 9), ("E", 16), ("F", 22), ("G", 18)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    return path


def other_form_docx(path: str) -> str:
    d = _doc()
    d.add_heading("ใบลา", level=1)
    t = d.add_table(rows=len(OTHER_LABELS), cols=2)
    t.style = "Table Grid"
    for i, label in enumerate(OTHER_LABELS):
        t.cell(i, 0).text = label
    d.add_paragraph("")
    d.add_paragraph("ลงชื่อ ........................................ ผู้ขอลา")
    d.save(path)
    return path


PDF_FIELDS = [("f1", "ผู้ขอซื้อ"), ("f2", "วันที่ขอซื้อ"), ("f3", "ผู้ขายที่เสนอ"), ("f4", "เลขประจำตัวผู้เสียภาษีของผู้ขาย"),
              ("f5", "สถานที่ส่งของ"), ("f6", "รายการที่ขอซื้อ"), ("f7", "จำนวน"), ("f8", "ราคาต่อหน่วย (บาท)"),
              ("f9", "รวมทั้งสิ้น (บาท)"), ("f10", "เงื่อนไขการชำระเงิน"), ("f11", "ผู้อนุมัติ")]
PDF_SAID = ("f12", "หมายเหตุถึงฝ่ายจัดซื้อ")     # no words printed beside it: its label is the field's own description
PDF_MAPPING = {"pdf:f1": "requester", "pdf:f2": "request_date", "pdf:f3": "seller_as_shown", "pdf:f4": "seller_tax_id",
               "pdf:f5": "delivery_place", "pdf:f6": "item_description", "pdf:f7": "quantity", "pdf:f8": "unit_price", "pdf:f9": "total"}
PDF_NEVER = {"pdf:f10": "เงื่อนไขการชำระเงิน", "pdf:f11": "ผู้อนุมัติ", "pdf:f12": "หมายเหตุถึงฝ่ายจัดซื้อ"}


def _pdf_page(path: str, fields: bool) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from agent_market.deliverables import pdf_form

    pdf_form._fonts()
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont(pdf_form.FONT_BOLD, 18)
    c.drawString(72, 770, "ใบขอซื้อ")
    c.setFont(pdf_form.FONT, 12)
    y = 725
    for name, label in PDF_FIELDS:
        c.drawString(72, y + 5, label + ":")
        if fields:
            c.acroForm.textfield(name=name, x=290, y=y, width=240, height=20, borderStyle="underlined", fontSize=11,
                                 fieldFlags="multiline" if name == "f6" else "")
        else:
            c.line(290, y, 530, y)
        y -= 34
    if fields:
        c.acroForm.textfield(name=PDF_SAID[0], tooltip=PDF_SAID[1], x=72, y=y - 30, width=458, height=40, borderStyle="underlined",
                             fontSize=11, fieldFlags="multiline")
        c.acroForm.checkbox(name="urgent", tooltip="ด่วน", x=72, y=y - 70, size=14)
        c.drawString(94, y - 67, "ด่วน")
    c.save()
    return path


def request_form_pdf(path: str) -> str:
    return _pdf_page(path, fields=True)


def printed_form_pdf(path: str) -> str:
    return _pdf_page(path, fields=False)

FORMS = {"request_form.docx": request_form_docx, "request_form.xlsx": request_form_xlsx, "other_form.docx": other_form_docx,
         "request_form.pdf": request_form_pdf, "printed_form.pdf": printed_form_pdf}


def write_all(folder: str) -> list[str]:
    """Every form, as a file in ``folder`` (the integration lane and the walk read them from disk)."""
    import os

    os.makedirs(folder, exist_ok=True)
    return [make(os.path.join(folder, name)) for name, make in FORMS.items()]


if __name__ == "__main__":
    import sys

    for p in write_all(sys.argv[1] if len(sys.argv) > 1 else "."):
        print(p)
