"""The audit as .xlsx — what an approver works from.

Sheets: สรุป · รายการเบิก (one row per claim: the row as written, the outcome, a column per rule) ·
ผลตรวจแต่ละกฎ (every fact with its evidence and the table entry it used) · ข้อยกเว้น (the run
exceptions: reason code, evidence, proposed action, choices — nothing decided) · ใบเสร็จ (every
receipt as read: page, box, printed text, confidence, who decided the total) · นโยบายที่ใช้ (the
policy *table* the rules used, with its source, the holidays, and the KB document's paragraphs as
context — marked as never a limit). Thai labels; amounts are numbers, not text; the quality gate
reopens the file and checks it against computed.json.

AM10: the wide tables (claims, exceptions, receipts) print one page across. Each is one table (H_*)
written as a main sheet — the row's identity, its amounts, its outcome and reason — and companion
sheets right after it ("<sheet> · หลักฐาน", "รายการเบิก · ผลตามกฎ") that repeat the identity columns
and hold the rest, one row per row, read side by side by row number (``LAYOUT``: which columns, how wide).
"""

from __future__ import annotations

import os
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ....deliverables import workpaper
from ....deliverables.reread import Tally, sheets, table
from ....engine import words
from ..words import W
from .audit import CHOICES, OUTCOME_TH, REASON_TH, RESOLUTION_TH

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
S_CLAIMS, S_FACTS, S_EXC, S_RECEIPTS, S_POLICY = "รายการเบิก", "ผลตรวจแต่ละกฎ", "ข้อยกเว้น", "ใบเสร็จ", "นโยบายที่ใช้"
# AM10: the wide tables' companion sheets — the same identity columns, then the rest, read beside the main sheet
# by row number; every one prints one page across
S_CLAIMS_EV, S_CLAIMS_RULES, S_EXC_EV, S_RECEIPTS_EV = ("รายการเบิก · หลักฐาน", "รายการเบิก · ผลตามกฎ", "ข้อยกเว้น · หลักฐาน",
                                                         "ใบเสร็จ · หลักฐาน")
PARTS = {S_CLAIMS: (S_CLAIMS, S_CLAIMS_EV, S_CLAIMS_RULES), S_EXC: (S_EXC, S_EXC_EV), S_RECEIPTS: (S_RECEIPTS, S_RECEIPTS_EV)}
SHEETS = ("สรุป", S_CLAIMS, S_CLAIMS_EV, S_CLAIMS_RULES, S_FACTS, S_EXC, S_EXC_EV, S_RECEIPTS, S_RECEIPTS_EV, S_POLICY)
FACT_TH = {"claim_data": "ข้อมูลในแถว", "category": "หมวดในตาราง", "within_claim_limit": "วงเงินต่อครั้ง",
           "within_period_limit": "วงเงินต่อเดือน", "receipt": "มีใบเสร็จ", "receipt_amount": "ยอดตรงใบเสร็จ",
           "duplicate": "ไม่ซ้ำ", "split_purchase": "ไม่แยกบิล", "prohibited_merchant": "ร้านไม่ต้องห้าม",
           "vat_document": "เอกสารขอคืน VAT", "wht": "หัก ณ ที่จ่าย", "weekend_holiday": "วันทำงาน", "purpose": "วัตถุประสงค์"}
# The wide tables' columns in the order the data is built and read back (the checks read by name, from the sheets
# of a table together — ``table(parts(wb, sheet), H_*)``)
H_CLAIMS = ["ลำดับ", "เลขที่เบิก", "แถวในไฟล์", "รหัสพนักงาน", "ชื่อพนักงาน", "วันที่", "วัน", "หมวด", "ร้านค้า/ผู้รับเงิน",
            "จำนวนเงิน", "ใบเสร็จ", "ยอดในใบเสร็จ", "ขอคืนภาษีซื้อ", "หัก ณ ที่จ่าย", "รายละเอียด", "ผลการตรวจ",
            "รหัสเหตุผล", "คำตอบในการ์ด"] + [FACT_TH[f] for f in FACT_TH]
H_EXC = ["ลำดับ", "รายการ", "รหัสเหตุผล", "เหตุผล", "สถานะ", "รายละเอียด", "สิ่งที่เสนอให้ทำ", "ทางเลือกของผู้ตัดสิน",
         "ที่มา", "อ้างอิงนโยบาย", "วงเงิน/เกณฑ์จากตาราง", "ตั้งข้อสังเกตด้วย", "บริบทจากเอกสารระเบียบ (ไม่ใช่วงเงิน)"]
H_RECEIPTS = ["ไฟล์", "ชนิดไฟล์", "อ่านด้วย", "ชนิดเอกสาร", "ผู้ขาย", "เลขผู้เสียภาษี", "วันที่", "ยอดรวม", "หน้า",
              "ตำแหน่ง", "ข้อความในเอกสาร", "ความมั่นใจ", "ผู้กำหนดยอด", "สถานะ", "รายการเบิก", "หมายเหตุ"]
# What prints on each sheet of a wide table, in print order, with the column's width: the main sheet leads with
# the row's identity (ID_COLS of them — the claim, the person, what it was for — repeated on the companions and
# on every page), then its amounts, outcome and reason; a companion repeats the identity and holds the rest.
# Every column of H_* is on one of them.
LAYOUT = {
    S_CLAIMS: [("ลำดับ", 6), ("เลขที่เบิก", 10), ("ชื่อพนักงาน", 16), ("รายละเอียด", 28), ("จำนวนเงิน", 13), ("ยอดในใบเสร็จ", 13),
               ("หัก ณ ที่จ่าย", 11), ("ผลการตรวจ", 18), ("วันที่", 11), ("หมวด", 16), ("ร้านค้า/ผู้รับเงิน", 22), ("ใบเสร็จ", 12),
               ("รหัสเหตุผล", 26)],
    S_CLAIMS_EV: [("ลำดับ", 6), ("เลขที่เบิก", 10), ("ชื่อพนักงาน", 16), ("รายละเอียด", 28), ("แถวในไฟล์", 8), ("รหัสพนักงาน", 11),
                  ("คำตอบในการ์ด", 40)],
    S_CLAIMS_RULES: [("ลำดับ", 6), ("เลขที่เบิก", 10), ("ชื่อพนักงาน", 16), ("รายละเอียด", 28), ("วัน", 9), ("ขอคืนภาษีซื้อ", 8)]
                    + [(FACT_TH[f], 9) for f in FACT_TH],
    S_EXC: [("ลำดับ", 6), ("รายการ", 10), ("เหตุผล", 24), ("วงเงิน/เกณฑ์จากตาราง", 13), ("สถานะ", 12), ("รายละเอียด", 40),
            ("สิ่งที่เสนอให้ทำ", 36), ("ทางเลือกของผู้ตัดสิน", 36), ("อ้างอิงนโยบาย", 26)],
    S_EXC_EV: [("ลำดับ", 6), ("รายการ", 10), ("เหตุผล", 24), ("รหัสเหตุผล", 22), ("ที่มา", 40), ("ตั้งข้อสังเกตด้วย", 24),
               ("บริบทจากเอกสารระเบียบ (ไม่ใช่วงเงิน)", 50)],
    S_RECEIPTS: [("ไฟล์", 20), ("ผู้ขาย", 26), ("ยอดรวม", 13), ("ชนิดไฟล์", 12), ("อ่านด้วย", 13), ("ชนิดเอกสาร", 16), ("เลขผู้เสียภาษี", 15),
                 ("วันที่", 11), ("สถานะ", 14), ("รายการเบิก", 13), ("ผู้กำหนดยอด", 16)],
    S_RECEIPTS_EV: [("ไฟล์", 20), ("ผู้ขาย", 26), ("หน้า", 6), ("ตำแหน่ง", 22), ("ข้อความในเอกสาร", 30), ("ความมั่นใจ", 10), ("หมายเหตุ", 36)],
}
ID_COLS = {S_CLAIMS: 4, S_EXC: 3, S_RECEIPTS: 2}
STATUS_TH = {"pass": "ผ่าน", "fail": "ไม่ผ่าน", "skip": "ไม่เกี่ยว"}
DISPOSITION_TH = {"exception": "ข้อยกเว้น (คนตัดสิน)", "question": "การ์ดคำถาม (วิจารณญาณ)", None: "บันทึกไว้"}
SEVERITY_TH = {"review": "รอคนตัดสิน", "info": "บันทึกไว้"}
DECIDED_TH = {"document": "อ่านจากเอกสาร", "typed_by_person": "ผู้ใช้พิมพ์", "confirmed_by_person": "ผู้ใช้ยืนยัน",
              "computed": "คำนวณจากค่าในเอกสาร", "model_proposal": "โมเดลเสนอ (ยังไม่ยืนยัน)"}
METHOD_TH = {"pdf_text": "ข้อความใน PDF", "ocr": "OCR", "etax_xml": "e-Tax XML", "hybrid": "ข้อความ + OCR",
             "typed_by_person": "ผู้ใช้พิมพ์", "confirmed_by_person": "ผู้ใช้ยืนยัน"}
KB_TITLE = "บริบทจากเอกสารระเบียบ — ไม่ใช่ที่มาของวงเงิน"
# AM4: where in the policy table a limit came from, in the words of the นโยบายที่ใช้ sheet
POLICY_FIELD_TH = {"per_claim": "วงเงินต่อครั้ง", "per_person_month": "วงเงินต่อคนต่อเดือน", "needs_purpose": "ต้องระบุวัตถุประสงค์",
                   "wht_rate": "หัก ณ ที่จ่าย (%)", "wht_from": "หักตั้งแต่ (บาท)",
                   "receipt_required_above": "ต้องมีใบเสร็จเมื่อยอดเกิน", "approval_threshold": "วงเงินอนุมัติ",
                   "weekend_holiday": "เบิกในวันหยุด", "vat_claim_requires": "ขอคืนภาษีซื้อต้องใช้",
                   "prohibited_merchants": "ร้านต้องห้าม"}


def policy_ref_th(ref, categories: dict) -> str | None:
    """"categories.meals.per_claim" → "นโยบาย › อาหาร › วงเงินต่อครั้ง" (the policy table, not a code)."""
    if not ref:
        return ref
    parts = str(ref).split(".")
    words = ["นโยบาย"]
    if parts[0] == "categories" and len(parts) > 1:
        cat = categories.get(parts[1]) or {}
        words.append(str(cat.get("label") or parts[1]))
        parts = parts[2:]
    words += [POLICY_FIELD_TH.get(p, p) for p in parts]
    return " › ".join(words)
NOT_A_LIMIT = "ไม่ใช้ (บริบทเท่านั้น)"


def _calendar_line(cal: dict) -> str:
    """Which holiday calendar the audit used — or that the holiday check was off (AM3: fail closed)."""
    if cal.get("active"):
        return f"ปฏิทินวันหยุด: {cal.get('note') or 'ปฏิทินวันหยุดของบริษัท'}"
    return (f"ปฏิทินวันหยุด: ไม่ได้ใช้ — {cal.get('note') or 'ยังไม่ได้ตั้งปฏิทินวันหยุดของบริษัท'} "
            "จึงยังไม่ได้ตรวจวันหยุดนักขัตฤกษ์ รอบนี้ตรวจเฉพาะวันเสาร์–อาทิตย์")


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def parts(wb, sheet: str) -> list:
    """The sheets a table prints on — the main sheet and the companions that are in the file — for ``table``."""
    return sheets(wb, *PARTS.get(sheet, (sheet,)))


class ExpenseWorkbook:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None):
        self.c, self.t, self.run = computed, theme, run or {}
        self.p = theme["palette"]
        self.font = theme["fonts"]["thai"]
        self.wb = Workbook()

    # ── styling ────────────────────────────────────────────────────────────
    def _title(self, ws, text: str, sub: str = ""):
        ws["A1"] = text
        ws["A1"].font = Font(name=self.font, bold=True, size=14, color=self.p["primary"])
        if sub:
            ws["A2"] = sub
            ws["A2"].font = Font(name=self.font, size=9, color=self.p["muted"])

    def _table(self, ws, top: int, headers: list[str], rows: list[list], widths: list[int] | None = None, money_cols=(),
               wrap_cols=(), freeze: bool = True, first: list[str] | None = None):
        if first:
            headers, rows, widths, cols = workpaper.lead(headers, rows, widths, first, money_cols=money_cols, wrap_cols=wrap_cols)
            money_cols, wrap_cols = cols["money_cols"], cols["wrap_cols"]
        fill = PatternFill("solid", fgColor=self.p["primary"])
        thin = Side(style="thin", color=self.p["rule"])
        zebra = PatternFill("solid", fgColor=self.p["surface"])
        for j, h in enumerate(headers, start=1):
            c = ws.cell(row=top, column=j, value=h)
            c.font = Font(name=self.font, bold=True, color=self.p["on_primary"], size=10)
            c.fill = fill
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, row in enumerate(rows):
            r = top + 1 + i
            for j, v in enumerate(row, start=1):
                c = ws.cell(row=r, column=j, value=v)
                c.font = Font(name=self.font, size=10, color=self.p["ink"])
                c.border = Border(bottom=thin)
                if i % 2 == 1:
                    c.fill = zebra
                if j in money_cols:
                    c.number_format = MONEY
                if j in wrap_cols:
                    c.alignment = Alignment(wrap_text=True, vertical="top")
                elif isinstance(v, str):
                    c.alignment = Alignment(vertical="top")
        for j, w in enumerate(widths or [], start=1):
            ws.column_dimensions[get_column_letter(j)].width = w
        if freeze:
            ws.freeze_panes = ws.cell(row=top + 1, column=1)
            if rows:
                ws.auto_filter.ref = f"A{top}:{get_column_letter(len(headers))}{top + len(rows)}"

    def _part(self, ws, heads: list, rows: list, layout: list, **cols):
        """One sheet of a wide table (AM10): the columns ``layout`` names, at its widths, picked from the whole
        table; ``cols`` are the writer's column tuples against ``heads``."""
        picked, prow, cols = workpaper.pick(heads, rows, [h for h, _ in layout], **cols)
        self._table(ws, 4, picked, prow, [w for _, w in layout], **cols)

    def _note(self, ws, r: int, text: str, cols: int = 6, height: int = 30):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(name=self.font, size=9, color=self.p["muted"])
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=cols)
        ws.row_dimensions[r].height = height

    def _section(self, ws, r: int, text: str):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(name=self.font, bold=True, size=11, color=self.p["primary"])

    # ── sheets ─────────────────────────────────────────────────────────────
    def summary(self):
        ws = self.wb.active
        ws.title = "สรุป"
        c, t = self.c, self.c["totals"]
        pol = c["policy"]
        self._title(ws, f"ผลตรวจการเบิกค่าใช้จ่าย · {c['period_label']}",
                    " · ".join(x for x in (c.get("company"), f"จัดทำ {c['prepared_on']}") if x))
        by = t["claimed_by_outcome"]
        rows = [["รายการเบิกทั้งหมด", t["claims"]], ["ผ่านทุกกฎ", t["pass"]], ["ข้อยกเว้น รอคนตัดสิน", t["exception"]],
                ["ต้องใช้วิจารณญาณ (การ์ดคำถาม)", t["question"]], ["ยอดเบิกรวม (บาท)", _num(t["claimed"])],
                ["ยอดที่ผ่าน (บาท)", _num(by["pass"])], ["ยอดที่เป็นข้อยกเว้น (บาท)", _num(by["exception"])],
                ["ยอดที่ต้องใช้วิจารณญาณ (บาท)", _num(by["question"])], ["ข้อยกเว้นที่รอคนตัดสิน (แถว)", t["review"]],
                ["บันทึกไว้ (คนตัดสินแล้ว/ไม่ต้องตัดสิน)", t["info"]], ["ใบเสร็จที่อ่าน", t["receipts"]]]
        self._table(ws, 4, ["รายการ", "ค่า"], rows, [44, 22], freeze=False)
        for r in range(9, 13):
            ws.cell(row=r, column=2).number_format = MONEY
        r = 4 + len(rows) + 2
        src = ("ตารางตัวอย่างที่มากับ agent (ยังไม่ได้แก้เป็นของบริษัท)" if pol["source"] == "package_sample"
               else "ตารางนโยบายที่ตั้งไว้ใน agent")
        notes = [
            "กฎให้ข้อเท็จจริง คนเป็นผู้ตัดสิน: กฎตรวจวงเงิน ใบเสร็จ รายการซ้ำ ร้านต้องห้าม VAT หัก ณ ที่จ่าย และการแยกบิล "
            "แล้วส่งรายการที่ไม่ผ่านเป็นข้อยกเว้นพร้อมหลักฐานและทางเลือก เรื่องที่ต้องใช้วิจารณญาณ (ค่าใช้จ่ายวันหยุด วัตถุประสงค์ที่ไม่ชัด) "
            "ถามคนผ่านการ์ด ไม่มีรายการใดถูกปฏิเสธอัตโนมัติ",
            f"วงเงินทุกตัวมาจาก{src} (ชีต “นโยบายที่ใช้”) เอกสารระเบียบที่แนบมาใช้เป็นบริบทเท่านั้น ไม่เคยเป็นที่มาของวงเงิน",
            "ตัวเลขทุกตัวมาจากไฟล์รายการเบิก ตารางนโยบาย หรือใบเสร็จที่อ่านด้วยสคริปต์ (หรือผู้ใช้พิมพ์/ยืนยัน) "
            "โมเดลไม่ได้เป็นผู้กำหนดตัวเลขใดในไฟล์นี้",
            _calendar_line(pol["calendar"]),
        ]
        for m in c.get("models") or []:
            notes.append(f"โมเดลที่เห็นใบเสร็จ: {m['model']} ({'รันในองค์กร' if m['self_hosted'] else 'ผู้ให้บริการภายนอก'}) "
                         f"อ่านซ้ำ {len(m['documents'])} ฉบับที่เป็นสแกนหรือรูปถ่าย เพื่อเทียบกับ OCR เท่านั้น")
        for a in c.get("assumptions") or []:
            if a["code"] in ("kb_differs_from_table", "holiday_calendar_missing_year", "receipt_files_missing", "same_file_twice"):
                notes.append(a["text"])
        for n in notes:
            self._note(ws, r, n)
            r += 1

    def claims(self):
        rows = []
        for i, c in enumerate(self.c["claims"], start=1):
            answered = "; ".join(f"{REASON_TH[k]}: {RESOLUTION_TH.get(v, v)}" for k, v in c["resolution"].items())
            if c.get("purpose"):
                answered += f" — “{c['purpose']}”"
            rows.append([i, c["id"], c["row"], c["employee_id"], c["employee_name"], c["date"] or c["date_raw"], c["weekday"],
                         c["category_label"] or c["category_raw"], c["merchant"], _num(c["amount"]), c["receipt"] or c["receipt_ref"],
                         _num(c["receipt_total"]), "ใช่" if c["vat_claim"] else "", _num(c["wht_deducted"]), c["description"],
                         c["outcome_label"], ", ".join(c["codes"] + c["flags"] + [f"({x})" for x in c["also_flagged"]]),
                         answered or None]
                        + [STATUS_TH[c["facts"][f]["status"]] for f in FACT_TH])
        for sheet, title, sub in ((S_CLAIMS, "รายการเบิกและผลตรวจ", "หนึ่งแถวต่อหนึ่งรายการในไฟล์ · ผ่าน / ข้อยกเว้น (คนตัดสิน) / ต้องใช้วิจารณญาณ "
                                                                  f"— ไม่มีการปฏิเสธอัตโนมัติ · ผลของแต่ละกฎอยู่ในชีต “{S_CLAIMS_RULES}” "
                                                                  f"แถวในไฟล์ รหัสพนักงาน และคำตอบในการ์ดอยู่ในชีต “{S_CLAIMS_EV}” แถวเดียวกัน"),
                                  (S_CLAIMS_EV, S_CLAIMS_EV, f"หลักฐานของแต่ละรายการในชีต “{S_CLAIMS}” อ่านเทียบกันตามลำดับ · แถวในไฟล์ "
                                                             "รหัสพนักงาน และคำตอบที่ให้ไว้ในการ์ด"),
                                  (S_CLAIMS_RULES, S_CLAIMS_RULES, f"ผลของแต่ละกฎต่อรายการในชีต “{S_CLAIMS}” อ่านเทียบกันตามลำดับ · "
                                                                   "ผ่าน / ไม่ผ่าน / ไม่เกี่ยว")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, title, sub)
            self._part(ws, H_CLAIMS, rows, LAYOUT[sheet], money_cols=(10, 12, 14), wrap_cols=(8, 9, 15, 16, 17, 18))

    def facts(self):
        ws = self.wb.create_sheet(S_FACTS)
        self._title(ws, "ผลตรวจแต่ละกฎ (ข้อเท็จจริง)", "กฎตรวจด้วยสคริปต์ ไม่ใช้โมเดล · วงเงินอ้างอิงจากตารางนโยบายเท่านั้น (อ้างอิงนโยบาย = ช่องในแผ่น “นโยบายที่ใช้”)")
        rows = []
        cats = self.c["policy"]["table"].get("categories") or {}
        for c in self.c["claims"]:
            for f, fd in c["facts"].items():
                ev = fd.get("evidence") or {}
                rows.append([c["id"] or c["key"], FACT_TH[f], f, STATUS_TH[fd["status"]], fd.get("code"),
                             DISPOSITION_TH.get(fd.get("disposition")) if fd["status"] == "fail" else None, fd.get("detail") or None,
                             policy_ref_th(ev.get("policy_ref"), cats), _num(ev.get("limit")), _num(ev.get("amount"))])
        self._table(ws, 4, ["เลขที่เบิก", "กฎ", "รหัสกฎ", "ผล", "รหัสเหตุผล", "ผลที่ตามมา", "รายละเอียด", "อ้างอิงนโยบาย",
                            "วงเงิน/เกณฑ์จากตาราง", "ยอดที่ตรวจ"], rows,
                    [10, 16, 18, 9, 22, 20, 44, 30, 14, 13], money_cols=(9, 10), wrap_cols=(5, 7, 8))

    def exceptions(self):
        rows = []
        cats = self.c["policy"]["table"].get("categories") or {}
        for i, e in enumerate(self.c["run_exceptions"], start=1):
            s = e["source_ref"]
            where = " · ".join(str(x) for x in (s.get("sheet"), f"แถว {s['row']}" if s.get("row") else None, s.get("receipt"),
                                                  f"หน้า {s['page']}" if s.get("page") else None,
                                                  f"“{s['raw_text']}”" if s.get("raw_text") else None) if x)
            kb = " / ".join(f"{k['file']} ย่อหน้า {k['paragraph']}: {k['text']}" for k in s.get("kb_context") or [])
            also = ", ".join(a["reason"] for a in s.get("also_flagged") or [])
            rows.append([i, s.get("claim") or e["subject"], e["reason_code"], e["reason"], SEVERITY_TH[e["severity"]],
                         e.get("detail") or None, e["proposed_action"], " / ".join(ch["label"] for ch in e["choices"]) or None,
                         where or None, policy_ref_th(s.get("policy_ref"), cats), _num(s.get("limit")), also or None, kb or None])
        for sheet, title, sub in ((S_EXC, "ข้อยกเว้น", "“รอคนตัดสิน” = ยังไม่มีใครตัดสิน ทางเลือกทุกข้อเป็นของคน ไม่มีข้อใดเลือกไว้ให้ · “บันทึกไว้” = "
                                                     f"ผู้ใช้ตัดสินแล้วระหว่างตรวจ หรือไม่ต้องตัดสิน · ที่มาและบริบทอยู่ในชีต “{S_EXC_EV}” แถวเดียวกัน"),
                                  (S_EXC_EV, S_EXC_EV, f"หลักฐานของแต่ละข้อในชีต “{S_EXC}” อ่านเทียบกันตามลำดับ · รหัสเหตุผล ที่มา (ชีต แถว ใบเสร็จ "
                                                       "ข้อความ) และบริบทจากเอกสารระเบียบ ซึ่งไม่ใช่ที่มาของวงเงิน")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, title, sub)
            self._part(ws, H_EXC, rows, LAYOUT[sheet], money_cols=(11,), wrap_cols=(4, 6, 7, 8, 9, 10, 12, 13))

    def receipts(self):
        rows = []
        for r in self.c["receipts"]:
            ts = r["total_source"] or {}
            rows.append([r["name"], r["tier_label"], METHOD_TH.get(r["method"], r["method"]), r["document_type_label"],
                         r["seller_name"], r["seller_tax_id"], r["document_date"], _num(r["total"]), ts.get("page"), ts.get("bbox"),
                         ts.get("raw_text"), ts.get("confidence"), DECIDED_TH.get(ts.get("decided_by"), ts.get("decided_by")),
                         {"read": "ใช้ได้", "unconfirmed": "ยืนยันยอดไม่ได้", "pending": "รอคำตอบ"}.get(r["status"], r["status"]),
                         ", ".join(r["claims"]) or "ไม่มีรายการอ้างถึง", r["note"] or None])
        for sheet, title, sub in ((S_RECEIPTS, "ใบเสร็จที่อ่าน", "อ่านด้วยเครื่องอ่าน E2: ข้อความใน PDF / e-Tax XML / OCR · หน้า ตำแหน่ง และข้อความที่อ่าน "
                                                               f"อยู่ในชีต “{S_RECEIPTS_EV}” แถวเดียวกัน"),
                                  (S_RECEIPTS_EV, S_RECEIPTS_EV, f"หลักฐานของแต่ละใบเสร็จในชีต “{S_RECEIPTS}” อ่านเทียบกันตามลำดับ · หน้า "
                                                                 "ตำแหน่งบนหน้า (x0,y0,x1,y1 หน่วย pt; รูปถ่ายเป็น px) ข้อความที่พิมพ์ และความมั่นใจ")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, title, sub)
            self._part(ws, H_RECEIPTS, rows, LAYOUT[sheet], money_cols=(8,), wrap_cols=(4, 5, 11, 13, 14, 16))
        ws = self.wb[S_RECEIPTS]
        tax_col = [c.value for c in ws[4]].index("เลขผู้เสียภาษี") + 1
        for i in range(len(rows)):
            ws.cell(row=5 + i, column=tax_col).number_format = "@"

    def policy(self):
        ws = self.wb.create_sheet(S_POLICY)
        pol = self.c["policy"]
        table = pol["table"]
        src = ("ตารางตัวอย่างที่มากับ agent — ยังไม่ได้แก้เป็นของบริษัท" if pol["source"] == "package_sample"
               else "ตารางนโยบายที่ตั้งไว้ใน agent")
        self._title(ws, "นโยบายที่ใช้ตรวจ", f"ที่มาของวงเงินทุกตัว: {src} · ลายนิ้วมือ sha256 {pol['sha256'][:16]}…")
        cats = table["categories"]
        rows = [[c.get("label") or code, code, _num(c.get("per_claim")), _num(c.get("per_person_month")),
                 "ใช่" if c.get("needs_purpose") else None, _num(c.get("wht_rate")), _num(c.get("wht_from"))]
                for code, c in cats.items()]
        self._table(ws, 4, ["หมวด", "รหัส", "วงเงินต่อครั้ง", "วงเงินต่อคนต่อเดือน", "ต้องระบุวัตถุประสงค์", "หัก ณ ที่จ่าย (%)",
                            "หักตั้งแต่ (บาท)"], rows, [30, 18, 16, 20, 18, 16, 16], money_cols=(3, 4, 7), freeze=False)
        r = 4 + len(rows) + 2
        self._section(ws, r, "ค่าตั้งอื่นในตาราง")
        settings = [["ต้องมีใบเสร็จเมื่อยอดเกิน (บาท)", _num(table.get("receipt_required_above"))],
                    ["วงเงินอนุมัติ ใช้ตรวจการแยกบิล (บาท)", _num(table.get("approval_threshold"))],
                    ["เบิกในวันหยุด", {"flag": "ถามคน (การ์ด)", "exception": "เป็นข้อยกเว้น", "allow": "บันทึกไว้เท่านั้น"}.get(
                        table.get("weekend_holiday", "flag"), table.get("weekend_holiday"))],
                    ["ขอคืนภาษีซื้อต้องใช้", {"full_tax_invoice": "ใบกำกับภาษีเต็มรูป", "abbreviated_tax_invoice": "ใบกำกับภาษี (อย่างย่อได้)"}.get(
                        table.get("vat_claim_requires", "full_tax_invoice"))],
                    ["ร้านต้องห้าม (คำที่ตรวจ)", ", ".join(table.get("prohibited_merchants") or []) or "-"]]
        self._table(ws, r + 1, ["ค่าตั้ง", "ค่า"], settings, freeze=False)
        ws.cell(row=r + 2, column=2).number_format = MONEY
        ws.cell(row=r + 3, column=2).number_format = MONEY
        r = r + 1 + len(settings) + 2
        cal = pol["calendar"]
        if cal.get("active"):
            self._section(ws, r, "วันหยุดในเดือนที่ตรวจ · " + (cal.get("note") or "ปฏิทินวันหยุดของบริษัท"))
            hol = [[d, n] for d, n in cal["dates"].items()] or [["-", "ไม่มีวันหยุดในเดือนที่ตรวจ"]]
        else:
            self._section(ws, r, "ปฏิทินวันหยุด · ไม่ได้ใช้")
            hol = [["-", f"ไม่ได้ตรวจวันหยุดนักขัตฤกษ์: {cal.get('note') or 'ยังไม่ได้ตั้งปฏิทินวันหยุดของบริษัท'} "
                         "ตรวจเฉพาะวันเสาร์–อาทิตย์"]]
        self._table(ws, r + 1, ["วันที่", "วันหยุด"], hol, freeze=False)
        r = r + 1 + len(hol) + 2
        self._section(ws, r, KB_TITLE)
        ctx = [[k["file"], k["paragraph"], k["text"],
                ", ".join(str((cats.get(x) or {}).get("label") or x) for x in k["categories"]) or None, ", ".join(k["numbers"]) or None,
                NOT_A_LIMIT] for k in pol["context"]]
        self._table(ws, r + 1, ["ไฟล์", "ย่อหน้า", "ข้อความ", "หมวดที่กล่าวถึง", "ตัวเลขในข้อความ", "ใช้เป็นวงเงิน"],
                    ctx or [["-", None, "ไม่มีเอกสารระเบียบแนบมา", None, None, NOT_A_LIMIT]], freeze=False, wrap_cols=(3,))
        for j, w in enumerate([30, 18, 70, 20, 20, 22], start=1):
            ws.column_dimensions[get_column_letter(j)].width = max(ws.column_dimensions[get_column_letter(j)].width or 0, w)
        r = r + 2 + max(len(ctx), 1) + 1
        for cf in pol["conflicts"]:
            table_says = f"ตารางกำหนด {cf['table']} บาท" if cf["table"] else "ตารางไม่ได้กำหนดวงเงินนี้"
            self._note(ws, r, f"ต่างจากตาราง: {cf['file']} ย่อหน้า {cf['paragraph']} กล่าวถึง{cf['label']} {cf['document']} บาท "
                              f"แต่{table_says} ({policy_ref_th(cf['policy_ref'], cats)}) — การตรวจใช้ตาราง", cols=6, height=24)
            r += 1

    def write(self, path: str) -> dict:
        self.summary()
        self.claims()
        self.facts()
        self.exceptions()
        self.receipts()
        self.policy()
        self.wb.properties.creator = "Agent Market · expense audit"
        # AM8: printable — the claim's number and its person repeat on every page (AM10: and open every companion
        # sheet of a wide table)
        id_cols = {s: ID_COLS[main] for main, names in PARTS.items() for s in names}
        workpaper.setup_workbook(self.wb, doc_name=os.path.basename(path), id_cols={**id_cols, S_FACTS: 2})
        self.wb.save(path)
        return {"sheets": list(self.wb.sheetnames), "claims": self.c["totals"]["claims"],
                "run_exceptions": self.c["totals"]["run_exceptions"]}


def check(path: str, computed: dict, run: dict | None = None) -> list[dict]:
    """The gate: every sheet there; one row per claim with its outcome, adding up to the claimed total;
    amounts are numbers; every run exception there with a documented reason code and, when a person
    must decide, the choices; the policy sheet shows the table's limits exactly and the KB only as
    context; every receipt with how it was read."""
    def ck(name, ok, detail, label):
        return {"name": name, "ok": bool(ok), "detail": detail, "label": label}

    wb = load_workbook(path)
    out = [ck("xlsx.sheets", set(SHEETS) <= set(wb.sheetnames), ", ".join(wb.sheetnames), W("check.sheets"))]
    rows = table(parts(wb, S_CLAIMS), H_CLAIMS)
    claims = computed["claims"]
    same = len(rows) == len(claims) and all(r[1] == c["id"] and r[15] == c["outcome_label"] for r, c in zip(rows, claims))
    out.append(ck("xlsx.claims", same, W("check.claims.detail", rows=len(rows), claims=len(claims)), W("check.claims")))
    with words.speaking(words.DOCUMENT):   # the outcomes as the workbook writes them …
        known = {v: k for k, v in OUTCOME_TH.items()}
    # … and what was read is said in the run's words (a cell that is none of them, as it is)
    read = sorted({str(r[15]) for r in rows})
    out.append(ck("xlsx.no_rejection", all(r[15] in known for r in rows),
                  ", ".join(OUTCOME_TH[known[v]] if v in known else v for v in read), W("check.no_rejection")))
    got = sum((Decimal(str(r[9])) for r in rows if r[9] is not None), Decimal(0)).quantize(Decimal("0.01"))
    exp = Decimal(computed["totals"]["claimed"])
    out.append(ck("xlsx.claims_total", got == exp, f"{got} / {exp}", W("check.claims_total")))
    numeric = all(isinstance(r[9], (int, float)) for r, c in zip(rows, claims) if c["amount"] is not None)
    out.append(ck("xlsx.amounts_are_numbers", numeric, "", W("check.numbers")))
    ex = table(parts(wb, S_EXC), H_EXC)
    run_ex = computed["run_exceptions"]
    ok = len(ex) == len(run_ex) and all(r[2] in REASON_TH and r[2] == e["reason_code"] for r, e in zip(ex, run_ex)) and \
        all(r[7] for r, e in zip(ex, run_ex) if e["severity"] == "review") and \
        all(e["reason_code"] in CHOICES or e["severity"] == "info" for e in run_ex)
    out.append(ck("xlsx.exceptions", ok, W("check.n_items", n=len(ex)), W("check.exceptions")))
    ws = wb["นโยบายที่ใช้"]
    cats = computed["policy"]["table"]["categories"]
    prow = [r for r in ws.iter_rows(min_row=5, max_row=4 + len(cats), values_only=True)]

    def same_limit(cell, v):
        return (cell is None and v in (None, "")) or (cell is not None and v not in (None, "")
                                                       and Decimal(str(cell)).quantize(Decimal("0.01")) == Decimal(str(v)))
    ok = len(prow) == len(cats) and all(r[1] == code and same_limit(r[2], c.get("per_claim")) and
                                        same_limit(r[3], c.get("per_person_month"))
                                        for r, (code, c) in zip(prow, cats.items()))
    kb_at = next((i for i, r in enumerate(ws.iter_rows(values_only=True), start=1) if r and r[0] == KB_TITLE), None)
    kb_rows = [r for r in ws.iter_rows(min_row=(kb_at or 0) + 2, values_only=True) if r and r[5] is not None] if kb_at else []
    ok = ok and kb_at is not None and all(r[5] == NOT_A_LIMIT for r in kb_rows)
    out.append(ck("xlsx.policy_is_the_table", ok, W("check.n_categories", n=len(prow)), W("check.policy")))
    rc = table(parts(wb, S_RECEIPTS), H_RECEIPTS)
    out.append(ck("xlsx.receipts", len(rc) == len(computed["receipts"]) and all(r[2] for r in rc), W("check.n_receipts", n=len(rc)),
                  W("check.receipts")))
    # AM8: every claim's own figures, not only their sum (two errors that cancel out passed before)
    t = Tally()
    for r, c in zip(rows, claims):
        where = W("where.claim", id=c["id"] or r[0])
        t.number(W("where.claim.amount", where=where), r[9], c["amount"])
        t.number(W("where.claim.receipt", where=where), r[11], c["receipt_total"])
        t.number(W("where.claim.wht", where=where), r[13], c["wht_deducted"])
        t.text(W("where.claim.outcome", where=where), r[15], c["outcome_label"])
    face = {}
    for row in wb["สรุป"].iter_rows(min_row=5, max_col=2, values_only=True):
        if row and isinstance(row[0], str):
            face.setdefault(row[0], row[1])
    tt = computed["totals"]
    by = tt["claimed_by_outcome"]
    for label, want in (("รายการเบิกทั้งหมด", tt["claims"]), ("ผ่านทุกกฎ", tt["pass"]), ("ข้อยกเว้น รอคนตัดสิน", tt["exception"]),
                        ("ยอดเบิกรวม (บาท)", tt["claimed"]), ("ยอดที่ผ่าน (บาท)", by["pass"]),
                        ("ยอดที่เป็นข้อยกเว้น (บาท)", by["exception"]), ("ยอดที่ต้องใช้วิจารณญาณ (บาท)", by["question"])):
        t.number(W("where.summary", label=label), face.get(label), want)
    for r, rec in zip(rc, computed["receipts"]):
        t.number(W("where.receipt", name=rec["name"]), r[7], rec["total"])
    out.append(t.result("xlsx.figures", W("check.figures"), W("noun.figures")))
    out.extend(workpaper.checks(path, words.lang()))
    return out
