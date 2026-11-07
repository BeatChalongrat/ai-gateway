"""AM22 §2.3 — three organisations' own forms for the three agents that had none: a bank reconciliation
statement (ใบกระทบยอดธนาคาร), an expense claim (ใบเบิกค่าใช้จ่าย) and a PO–invoice match sheet (ใบเทียบ PO กับ
ใบกำกับ), each in Excel and in Word, and the expense claim also as a PDF with fields to type in.

They are made up — the first real customer form will find something they do not. They are made to be unkind:
Thai labels; a Thai date (วว/ดด/ปปปป, Buddhist era); a 13-digit tax-ID blank; a VAT line; a signature block; a
merged title over the sheet; long blanks that are merged ranges; the form's own formula; and at least one blank
nothing in the round can fill (a reviewer, an approver, a department), so a form where everything works is never
the only case (CARD-RULES §6c). Each has a place the agent has no concept of for the round (an exchange rate, a
cash advance, a credit-note section with no credit note) — taken off the face, not printed as a gap (§2.2).

The refusal variants (§2.4) are built here too: two labels that could take one balance; a blank inside a merged
range that is not its top-left; a blank that is the form's own formula; a locked blank on a protected sheet; and
a new version of a form whose cell addresses are the same and whose labels moved.

Built by code so that the unit tests, the runner's integration lane and the walk use the same bytes; the files are
committed under each package's evals/fixtures/forms (python -m agent_market.tests.am22forms writes them there).
"""

from __future__ import annotations

import os

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Protection, Side

HERE = os.path.dirname(os.path.abspath(__file__))
PKGS = os.path.join(os.path.dirname(HERE), "packages")

THIN = Side(style="thin", color="808080")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
DATE_TH = "วันที่จัดทำ (วว/ดด/ปปปป)"
TAX_ID = "เลขประจำตัวผู้เสียภาษี (13 หลัก)"

# ── what each form asks for: (label, the agent's field it takes — None: nothing in the round fills it) ──
BANK = {
    "package": "bank_reconciliation", "title": "ใบกระทบยอดเงินฝากธนาคาร", "sheet": "กระทบยอด",
    "beside": [("ชื่อบริษัท", "company"), (TAX_ID, None), ("ธนาคาร", "bank"), ("เลขที่บัญชี", "account_number"),
               ("งวด", "period"), (DATE_TH, "prepared_on")],
    "money": [("ยอดคงเหลือตามรายการเดินบัญชี", "bank_balance"), ("บวก เงินฝากระหว่างทาง", "deposit_in_transit"),
              ("หัก เช็คค้างจ่าย", "outstanding_cheque"), ("ยอดหลังปรับปรุงของธนาคาร", "adjusted_bank"),
              ("ยอดคงเหลือตามสมุดบัญชี", "book_balance"), ("บวก ดอกเบี้ยรับ", "interest"),
              ("หัก ค่าธรรมเนียมธนาคาร", "bank_fee"), ("ภาษีมูลค่าเพิ่มของค่าธรรมเนียม 7%", None),
              ("ยอดหลังปรับปรุงของบัญชี", "adjusted_book")],
    "formula": ("ผลต่างตามแบบฟอร์ม", "adjusted_bank", "adjusted_book"),   # the form works its difference out itself
    "na": [("อัตราแลกเปลี่ยน (บัญชีเงินตราต่างประเทศ)", "fx_rate")],
    "list": ("items", [("วันที่", "date"), ("เลขที่อ้างอิง", "ref"), ("รายละเอียด", "description"), ("ประเภท", "kind"),
                       ("จำนวนเงิน", "amount")], 8),
    "sign": [("ผู้จัดทำ", "prepared_by"), ("ผู้ตรวจสอบ", None)],
}
EXPENSE = {
    "package": "expense_audit", "title": "ใบเบิกค่าใช้จ่าย", "sheet": "ใบเบิก",
    "beside": [("ชื่อบริษัท", "company"), ("ชื่อผู้เบิก", "employee_name"), ("รหัสพนักงาน", "employee_id"), ("แผนก", None),
               ("งวด", "period"), (DATE_TH, "prepared_on"), ("เลขประจำตัวผู้เสียภาษีของผู้ขาย (13 หลัก)", None)],
    "money": [("ยอดเบิกรวม", "claimed_thb"), ("ยอดที่ผ่านการตรวจ", "pass_thb"), ("ยอดที่รอคนตัดสิน", "exception_thb"),
              ("ภาษีมูลค่าเพิ่ม 7%", None)],
    "formula": ("ยอดที่ต้องจ่ายตามแบบฟอร์ม", "pass_thb", None),
    "na": [("เงินทดรองจ่ายที่หักล้าง", "advance_cleared")],
    "list": ("lines", [("วันที่", "date"), ("รายละเอียด", "description"), ("หมวด", "category"), ("ใบเสร็จ", "receipt"),
                       ("จำนวนเงิน", "amount")], 12),
    "sign": [("ผู้เบิก", None), ("ผู้อนุมัติ", None)],
}
PO = {
    "package": "po_matching", "title": "ใบตรวจรับและเทียบใบสั่งซื้อกับใบกำกับภาษี", "sheet": "เทียบ PO",
    "beside": [("ชื่อบริษัท", "company"), ("ชื่อผู้ขาย", "supplier_name"), (TAX_ID, "supplier_tax_id"), ("งวด", "period"),
               (DATE_TH, "prepared_on"), ("เลขที่ใบตรวจรับ", None)],
    "money": [("จำนวนใบแจ้งหนี้ที่ตรวจ", "invoices"), ("มูลค่าที่ผ่าน", "value_passed"), ("มูลค่าที่ติดเงื่อนไข", "value_blocked"),
              ("ภาษีมูลค่าเพิ่มรวม 7%", None)],
    "formula": ("มูลค่ารวมตามแบบฟอร์ม", "value_passed", "value_blocked"),
    "na": [],
    "list": ("lines", [("เลขที่ใบแจ้งหนี้", "number"), ("วันที่", "date"), ("ผู้ขาย", "supplier"), ("ยอดรวม", "total"),
                       ("ผลตรวจ", "status")], 8),
    # a second table of items: the round's credit notes — a section with nothing in it when there are none
    "list2": ("credit_notes", [("เลขที่ใบลดหนี้", "number"), ("วันที่", "date"), ("ผู้ขาย", "supplier"), ("ยอดรวม", "total"),
                               ("หมายเหตุ", None)], 3),
    "sign": [("ผู้ตรวจรับ", None), ("ผู้อนุมัติจ่าย", None)],
}
SPECS = {"bank_reconciliation": BANK, "expense_audit": EXPENSE, "po_matching": PO}


# ── Excel ───────────────────────────────────────────────────────────────────
def _xlsx(spec: dict, path: str, *, swap: bool = False, merged_inside: bool = False, locked: bool = False,
          twin: bool = False) -> str:
    """The form as a workbook. Variants (§2.4): ``swap`` — the same cells, two money labels exchanged (a new version
    of the form whose layout moved under the same addresses); ``merged_inside`` — one blank is a cell inside a
    merged range that is not its top-left; ``locked`` — the sheet is protected and its blanks stay locked; ``twin`` —
    two labels that could take one balance."""
    wb = Workbook()
    ws = wb.active
    ws.title = spec["sheet"]
    ws["B1"] = spec["title"]
    ws["B1"].font = Font(bold=True, size=16)
    ws["B1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("B1:F1")                       # the merged header every Thai form has
    r = 3
    for label, _ in spec["beside"]:
        ws.cell(row=r, column=2, value=label + ":")
        ws.cell(row=r, column=3).border = BOX
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)   # a long blank: a merged range
        r += 1
    r += 1
    money = list(spec["money"])
    if swap:
        money[0], money[1] = money[1], money[0]
    if twin:
        money.insert(1, ("ยอดตามบัญชี" if spec is BANK else money[0][0] + " (ตามระบบ)", None))
        money[0] = ("ยอดตามธนาคาร" if spec is BANK else money[0][0], money[0][1])
    first_money = r

    def money_line(label: str) -> None:
        # a label drawn across three columns to its blank at the end of the row, as a printed form leads its dots
        ws.cell(row=r, column=2, value=label + ":")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.cell(row=r, column=5).border = BOX
        ws.cell(row=r, column=5).number_format = "#,##0.00"

    for label, _ in money:
        money_line(label)
        r += 1
    # the form's own arithmetic, beside its label — a formula, never written over
    flabel, a, b = spec["formula"]
    rows = {f: first_money + i for i, (_, f) in enumerate(money) if f}
    money_line(flabel)
    ws.cell(row=r, column=5, value=f"=E{rows[a]}+E{rows[b]}" if spec is PO else f"=E{rows[a]}" + (f"-E{rows[b]}" if b else ""))
    r += 1
    for label, _ in spec["na"]:
        money_line(label)
        r += 1
    r += 1
    for which in ("list", "list2"):
        if which not in spec:
            continue
        _, heads, n = spec[which]
        for i, (h, _) in enumerate(heads):
            c = ws.cell(row=r, column=2 + i, value=h)
            c.font, c.border, c.alignment = Font(bold=True), BOX, Alignment(horizontal="center")
        for k in range(1, n + 1):
            for i in range(len(heads)):
                ws.cell(row=r + k, column=2 + i).border = BOX
        r += n + 2
    for label, _ in spec["sign"]:
        ws.cell(row=r, column=2, value=label + ":")
        ws.cell(row=r, column=3).border = BOX
        ws.cell(row=r + 1, column=2, value="ลงชื่อ ........................................")
        r += 3
    if merged_inside:
        # the blank of the second line of the header is the lower half of a merge that starts on the row above
        ws.unmerge_cells(start_row=4, start_column=3, end_row=4, end_column=5)
        ws.unmerge_cells(start_row=3, start_column=3, end_row=3, end_column=5)
        ws.merge_cells(start_row=3, start_column=3, end_row=4, end_column=5)
    if locked:
        for row in ws.iter_rows():
            for c in row:
                c.protection = Protection(locked=True)
        ws.protection.sheet = True
    for col, width in (("A", 3), ("B", 40), ("C", 16), ("D", 22), ("E", 18), ("F", 20)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    wb.close()
    return path


def formula_ref(spec: dict) -> str:
    """The address of the form's own formula (what a mapping must never write into)."""
    r = 3 + len(spec["beside"]) + 1 + len(spec["money"])
    return f"{spec['sheet']}!E{r}"


# ── Word ────────────────────────────────────────────────────────────────────
def _doc() -> Document:
    d = Document()
    d.styles["Normal"].font.name = "Sarabun"
    d.styles["Normal"].font.size = Pt(14)
    return d


def _docx(spec: dict, path: str) -> str:
    d = _doc()
    d.add_heading(spec["title"], level=1)
    rows = list(spec["beside"]) + list(spec["money"]) + list(spec["na"])
    t = d.add_table(rows=len(rows), cols=2)
    t.style = "Table Grid"
    for i, (label, _) in enumerate(rows):
        t.cell(i, 0).text = label
    d.add_paragraph("")
    _, heads, n = spec["list"]
    regions = [spec["list"]] + ([spec["list2"]] if "list2" in spec else [])
    total = sum(1 + m for _, _, m in regions) + len(regions) - 1
    items = d.add_table(rows=total, cols=len(heads))
    items.style = "Table Grid"
    r = 0
    for k, (_, hs, m) in enumerate(regions):
        if k:
            items.cell(r, 0).text = "รวม"            # a row between the two tables of items: not a heading, not a blank
            r += 1
        for i, (h, _) in enumerate(hs):
            items.cell(r, i).text = h
        r += 1 + m
    d.add_paragraph("")
    for label, _ in spec["sign"]:
        d.add_paragraph(f"{label}: ______________________")
    d.add_paragraph("ลงชื่อ ........................................")
    d.save(path)
    return path


# ── PDF (the expense claim) ─────────────────────────────────────────────────
PDF_FIELDS = [("f1", "ชื่อผู้เบิก", "employee_name"), ("f2", "รหัสพนักงาน", "employee_id"), ("f3", "แผนก", None),
              ("f4", DATE_TH, "prepared_on"), ("f5", "ยอดเบิกรวม (บาท)", "claimed_thb"), ("f6", "ยอดที่ผ่านการตรวจ (บาท)", "pass_thb"),
              ("f7", "ภาษีมูลค่าเพิ่ม 7% (บาท)", None), ("f8", "เงินทดรองจ่ายที่หักล้าง (บาท)", "advance_cleared"),
              ("f9", "ผู้อนุมัติ", None)]


def expense_claim_pdf(path: str) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from agent_market.deliverables import pdf_form

    pdf_form._fonts()
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont(pdf_form.FONT_BOLD, 18)
    c.drawString(72, 770, EXPENSE["title"])
    c.setFont(pdf_form.FONT, 12)
    y = 725
    for name, label, _ in PDF_FIELDS:
        c.drawString(72, y + 5, label + ":")
        c.acroForm.textfield(name=name, x=300, y=y, width=230, height=20, borderStyle="underlined", fontSize=11)
        y -= 34
    c.save()
    return path


def forms(spec: dict) -> dict:
    """{file name: maker} of a package's forms and their refusal variants."""
    base = spec["package"]
    out = {f"{base}_form.xlsx": lambda p, s=spec: _xlsx(s, p), f"{base}_form.docx": lambda p, s=spec: _docx(s, p),
           f"{base}_moved.xlsx": lambda p, s=spec: _xlsx(s, p, swap=True),
           f"{base}_merged.xlsx": lambda p, s=spec: _xlsx(s, p, merged_inside=True),
           f"{base}_locked.xlsx": lambda p, s=spec: _xlsx(s, p, locked=True),
           f"{base}_twin.xlsx": lambda p, s=spec: _xlsx(s, p, twin=True)}
    if spec is EXPENSE:
        out[f"{base}_form.pdf"] = expense_claim_pdf
    return out


def folder(package: str) -> str:
    return os.path.join(PKGS, package, "evals", "fixtures", "forms")   # (the engine zip leaves fixtures out)


def write_all(root: str | None = None) -> list[str]:
    """Every form of every package, into each package's evals/fixtures/forms (or under ``root``/<package>)."""
    made = []
    for pkg, spec in SPECS.items():
        d = os.path.join(root, pkg) if root else folder(pkg)
        os.makedirs(d, exist_ok=True)
        for name, make in forms(spec).items():
            made.append(make(os.path.join(d, name)))
    return made


# ── the mapping a person confirms (by the form's own labels, as they read them) ──
def label_map(spec: dict) -> dict:
    """{a label as the form prints it: the agent's field} — what a careful reader of the form maps."""
    out = {}
    for group in ("beside", "money", "na", "sign"):
        for label, field in spec[group]:
            if field:
                out[label] = field
    return out


def mapping_for(spec: dict, slots: list[dict], pdf: bool = False) -> dict:
    """The mapping of a form's slots to the agent's fields, by label; the tables of items by their headings."""
    by_label = {k: v for k, v in label_map(spec).items()}
    if pdf:
        by_label = {label: field for _, label, field in PDF_FIELDS if field}
    out = {}
    lists = {spec[w][0]: spec[w][1] for w in ("list", "list2") if w in spec}
    for s in slots:
        if not s.get("fillable"):
            continue
        if s["kind"] == "table":
            for name, heads in lists.items():
                cols = {h: f for h, f in heads}
                labels = s.get("column_labels") or {}
                if labels and all(v in cols for v in labels.values()):
                    out[s["key"]] = name
                    for col, label in labels.items():
                        if cols.get(label):
                            out[f"{s['key']}.{col}"] = f"{name}.{cols[label]}"
            continue
        field = by_label.get(s.get("label") or "")
        if field:
            out[s["key"]] = field
    return out


if __name__ == "__main__":
    for p in write_all():
        print(p)
