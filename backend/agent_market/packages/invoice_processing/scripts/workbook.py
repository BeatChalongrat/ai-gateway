"""The register as .xlsx — the deliverable a bookkeeper checks and keys from.

Sheets: สรุป · ทะเบียน (one row per recorded document) · เอกสารที่ยังไม่ได้รวม (AM5: every document
left out of the register, why, and how it comes back) · รายการสินค้า · ที่มา (every value: page,
box, method, printed text, confidence, who decided it) · ข้อยกเว้น (reason-coded) · การตรวจ (each
document's deterministic checks). Amounts are numbers, not text, so a reader can sum them again;
the quality gate reopens the file and checks the register adds up to computed.json.

AM10: the register prints one page across. It is one table (H_REGISTER) written as the main sheet —
the row's number, date, document number, the seller and every amount — and a companion right after it
("ทะเบียน · หลักฐาน": the same three identity columns, then the branch, the buyer, how it was read and the
file), one row per row, read side by side by row number (``LAYOUT``: which columns, how wide).
"""

from __future__ import annotations

import ast
import os
import re
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ....deliverables import workpaper
from ....deliverables.reread import sheets, table
from ....engine import words
from ....engine.e2.cards import CHECK_TH, FIELD_TH
from ..words import W
from .register import DOC_TYPE_TH, REASON_TH

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
NOT_INCLUDED = "เอกสารที่ยังไม่ได้รวม"
S_REGISTER, S_REGISTER_EV = "ทะเบียน", "ทะเบียน · หลักฐาน"
SHEETS = ("สรุป", S_REGISTER, S_REGISTER_EV, NOT_INCLUDED, "รายการสินค้า", "ที่มา", "ข้อยกเว้น", "การตรวจ")
# The register's columns in the order the data is built and read back (the gate reads by name, from both of its
# sheets together — ``table(parts(wb, S_REGISTER), H_REGISTER)``)
H_REGISTER = ["ลำดับ", "วันที่", "เลขที่", "ชนิดเอกสาร", "ผู้ขาย", "เลขผู้เสียภาษีผู้ขาย", "สาขา", "ผู้ซื้อ", "เลขผู้เสียภาษีผู้ซื้อ", "สกุลเงิน",
              "ยอดก่อน VAT", "VAT", "ยอดรวม", "หัก ณ ที่จ่าย", "ยอดที่ต้องชำระ", "อัตราแลกเปลี่ยน", "ยอดก่อน VAT (บาท)", "VAT (บาท)",
              "ยอดรวม (บาท)", "ที่มา", "ไฟล์"]
PARTS = {S_REGISTER: (S_REGISTER, S_REGISTER_EV)}
# AM10: what prints on each sheet of the register, in print order, with the column's width: the main sheet leads
# with the row's identity (ID_COLS of them, repeated on the companion and on every page), then the seller and
# every amount; the companion repeats the identity and holds the rest. Every column of H_REGISTER is on one.
LAYOUT = {
    S_REGISTER: [("ลำดับ", 6), ("วันที่", 11), ("เลขที่", 15), ("ผู้ขาย", 24), ("เลขผู้เสียภาษีผู้ขาย", 15), ("ชนิดเอกสาร", 14), ("สกุลเงิน", 7),
                 ("ยอดก่อน VAT", 12), ("VAT", 12), ("ยอดรวม", 12), ("หัก ณ ที่จ่าย", 12), ("ยอดที่ต้องชำระ", 12), ("อัตราแลกเปลี่ยน", 9),
                 ("ยอดก่อน VAT (บาท)", 13), ("VAT (บาท)", 12), ("ยอดรวม (บาท)", 13)],
    S_REGISTER_EV: [("ลำดับ", 6), ("วันที่", 11), ("เลขที่", 15), ("สาขา", 7), ("ผู้ซื้อ", 26), ("เลขผู้เสียภาษีผู้ซื้อ", 15), ("ที่มา", 15),
                    ("ไฟล์", 30)],
}
ID_COLS = {S_REGISTER: 3}
DECIDED_TH = {"document": "อ่านจากเอกสาร", "typed_by_person": "ผู้ใช้พิมพ์", "confirmed_by_person": "ผู้ใช้ยืนยัน",
              "computed": "คำนวณจากค่าในเอกสาร", "model_proposal": "โมเดลเสนอ (ยังไม่ยืนยัน)"}
METHOD_TH = {"pdf_text": "ข้อความใน PDF", "ocr": "OCR", "etax_xml": "e-Tax XML", "typed_by_person": "ผู้ใช้พิมพ์",
             "confirmed_by_person": "ผู้ใช้ยืนยัน", "chosen_by_person": "ผู้ใช้เลือก",
             # AM4: the rest a person could meet in the ที่มา sheet
             "document_scan": "ตรวจทั้งเอกสาร", "default": "ค่าตั้งต้น", "hybrid": "ข้อความใน PDF และ OCR"}


def method_th(method) -> str:
    """How a value was read, in Thai ("computed:sum_of_lines" → คำนวณจากรายการในเอกสาร)."""
    m = str(method or "")
    if m in METHOD_TH:
        return METHOD_TH[m]
    if m.startswith("computed:"):
        return "คำนวณจากรายการในเอกสาร" if "lines" in m else "คำนวณจากค่าในเอกสาร"
    if m.startswith("vision:"):
        return f"โมเดล {m.split(':', 1)[1]} อ่านจากภาพ"
    return m
STATUS_TH = {"pass": "ผ่าน", "fail": "ไม่ผ่าน", "skip": "ไม่เกี่ยว"}

# AM3: what each check looks at, in words that do not sound like a finding (a stranger read the
# card headers "VAT ไม่ตรง" / "duplicate" beside "ผ่าน" as problems with their documents).
CHECK_NAME_TH = {
    "seller_tax_id_format": "เลขผู้เสียภาษีผู้ขายถูกรูปแบบ", "buyer_tax_id_format": "เลขผู้เสียภาษีผู้ซื้อถูกรูปแบบ",
    "document_date": "วันที่เอกสารสมเหตุสมผล", "line_arithmetic": "จำนวน × ราคา = ยอดรายการ",
    "subtotal_sum": "ผลรวมรายการ = ยอดก่อน VAT", "discount_arithmetic": "ส่วนลดคำนวณถูก",
    "vat_7_percent": "VAT = 7% ของฐาน", "grand_total": "ยอดก่อน VAT + VAT = ยอดรวม", "wht": "หัก ณ ที่จ่ายสอดคล้องกัน",
    "currency": "สกุลเงินบาท", "required_fields": "มีช่องที่เอกสารชนิดนี้ต้องมีครบ", "buyer_match": "ออกในนามบริษัทเรา",
    "duplicate": "ไม่ซ้ำกับเอกสารที่เคยบันทึก", "original_copy": "เป็นต้นฉบับ (ไม่ใช่สำเนา)",
    "amount_in_words": "จำนวนเงินตัวอักษร = ยอดรวม", "document_type": "ชนิดเอกสารชัดเจน",
}
_MSG_TH = {
    "not printed": "เอกสารไม่ได้พิมพ์ค่านี้", "no date found": "ไม่พบวันที่ในเอกสาร",
    "a date in the future or implausibly old": "วันที่อยู่ในอนาคตหรือเก่าผิดปกติ",
    "no quantity × price rows": "เอกสารไม่มีรายการแบบจำนวน × ราคา", "no line amounts": "เอกสารไม่มียอดรายการ",
    "no subtotal to compare the lines with": "ไม่มียอดก่อน VAT ให้เทียบกับผลรวมรายการ",
    "no VAT on this document": "เอกสารนี้ไม่มี VAT", "no base to check the VAT against": "ไม่มีฐานให้ตรวจ VAT",
    "no withholding tax on this document": "เอกสารนี้ไม่มีหัก ณ ที่จ่าย",
    "the 13th digit does not match the other twelve (mod-11 check digit)": "หลักที่ 13 ไม่ตรงกับ 12 หลักแรก (หลักตรวจสอบ)",
    "only a full tax invoice with a buyer tax ID is matched against the organisation":
        "ตรวจเฉพาะใบกำกับภาษีเต็มรูปที่มีเลขผู้เสียภาษีผู้ซื้อ",
    "made out to another tax ID: input VAT cannot be claimed on it": "ออกในนามเลขผู้เสียภาษีอื่น ขอคืนภาษีซื้อไม่ได้",
    "marked สำเนา / copy": "พิมพ์ว่า สำเนา", "the amount in words is not the total": "จำนวนเงินตัวอักษรไม่เท่ากับยอดรวม",
    "13 digits with a valid check digit": "13 หลัก หลักตรวจสอบถูกต้อง", "not seen before": "ไม่เคยพบมาก่อน",
    "a known document type": "ชนิดเอกสารที่รู้จัก", "a date": "มีวันที่", "a VAT base": "มีฐานภาษี",
    "subtotal + VAT": "ยอดก่อน VAT + VAT", "THB": "บาท", "original": "ต้นฉบับ", "copy": "สำเนา", "unmarked": "ไม่ได้พิมพ์ว่าสำเนา",
    "a withholding amount (the net payable is not the total)": "ยอดหัก ณ ที่จ่าย (เพราะยอดสุทธิไม่เท่ากับยอดรวม)",
    "rate × base = WHT; total − WHT = net": "อัตรา × ฐาน = หัก ณ ที่จ่าย; ยอดรวม − หัก ณ ที่จ่าย = ยอดสุทธิ",
    "no net payable": "เอกสารไม่ได้พิมพ์ยอดสุทธิ", "quantity × unit price = amount": "จำนวน × ราคาต่อหน่วย = ยอดรายการ",
}
_KEY_TH = {"net": "ยอดสุทธิ", "total": "ยอดรวม", "wht": "หัก ณ ที่จ่าย", "rate": "อัตรา (%)", "match": "ตรงกันที่",
           "name": "เอกสารเดิม", "row": "บรรทัด", "expected": "ควรเป็น", "printed": "พิมพ์"}
_MATCH_TH = {"document number": "เลขที่เอกสาร", "date and total": "วันที่และยอดรวม"}
_FORM_TH = {"company_limited": "บริษัทจำกัด", "public_company": "บริษัทมหาชน", "limited_partnership": "ห้างหุ้นส่วนจำกัด",
            "sole_proprietor": "บุคคลธรรมดา/ร้าน"}


def msg_th(text) -> str | None:
    """An engine check's English message or value, in Thai (numbers kept as they are)."""
    if text in (None, ""):
        return text
    if isinstance(text, dict) and "core" in text:  # a party name: the name as read, and its legal form
        form = _FORM_TH.get(str(text.get("legal_form") or ""), "")
        return " ".join(x for x in (str(text.get("core") or "").replace("\x00", ""), f"({form})" if form else "") if x)
    if isinstance(text, dict):
        return "; ".join(f"{_KEY_TH.get(k, FIELD_TH.get(k, k))} {msg_th(v) if v is not None else '—'}" for k, v in text.items())
    if isinstance(text, (list, tuple)):
        return ", ".join(FIELD_TH.get(str(x), msg_th(x) or "") for x in text)
    s = str(text)
    if s in _MSG_TH:
        return _MSG_TH[s]
    if s in DOC_TYPE_TH:
        return DOC_TYPE_TH[s]
    if s in _MATCH_TH:
        return _MATCH_TH[s]
    rules = (
        (r"^row (\S+): (\S+) printed, (\S+) computed$", lambda m: f"บรรทัด {m[1]}: พิมพ์ {m[2]} คำนวณได้ {m[3]}"),
        (r"^VAT should be (\S+) at (\S+)% of the base$", lambda m: f"VAT ควรเป็น {m[1]} ({m[2]}% ของฐาน)"),
        (r"^net payable (\S+) is not the total (\S+), and no withholding amount was read$",
         lambda m: f"ยอดสุทธิ {m[1]} ไม่เท่ากับยอดรวม {m[2]} และไม่พบยอดหัก ณ ที่จ่าย"),
        (r"^amounts are in (\S+): the THB amount needs an exchange-rate source$",
         lambda m: f"ยอดเป็นสกุล {m[1]} ต้องมีอัตราแลกเปลี่ยนก่อนบันทึกเป็นบาท"),
        (r"^missing for a (\S+): (.+)$",
         lambda m: "เอกสารชนิดนี้ขาด: " + ", ".join(FIELD_TH.get(x.strip(), x.strip()) for x in m[2].split(","))),
        (r"^same supplier and (.+) as (.+)$", lambda m: f"ผู้ขายเดียวกันและ{_MATCH_TH.get(m[1], m[1])}ตรงกับ {m[2]}"),
        (r"^(\w+):(\S+) read (.+), the document text (.+)$",
         lambda m: (f"โมเดล {m[2]}" if m[1] == "vision" else m[1]) + f" อ่านได้ {_val(m[3])} แต่ข้อความในเอกสารเป็น {_val(m[4])}"),
        (r"^not after (\S+)$", lambda m: f"ไม่เกิน {m[1]}"),
        (r"^(\S+)% of (\S+) is (\S+), printed (\S+)$", lambda m: f"{m[1]}% ของ {m[2]} = {m[3]} แต่พิมพ์ {m[4]}"),
        (r"^total − WHT = (\S+), net printed (\S+)$", lambda m: f"ยอดรวม − หัก ณ ที่จ่าย = {m[1]} แต่พิมพ์ยอดสุทธิ {m[2]}"),
    )
    parts = []
    for part in s.split("; "):
        for pat, fn in rules:
            m = re.match(pat, part)
            if m:
                parts.append(fn(m))
                break
        else:
            parts.append(_MSG_TH.get(part, part))
    return "; ".join(parts)


def _val(text: str) -> str:
    """A value the engine printed with repr (a party name is a dict): shown the way msg_th shows it."""
    try:
        v = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text
    return msg_th(v) if isinstance(v, (dict, list, tuple)) else text


def check_name(code: str) -> str:
    if code.startswith("source_agreement:"):
        f = code.split(":", 1)[1]
        return f"{FIELD_TH.get(f, f)} อ่านจากสองแหล่งตรงกัน"
    return CHECK_NAME_TH.get(code) or CHECK_TH.get(code, (code,))[0]


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def parts(wb, sheet: str) -> list:
    """The sheets a table prints on — the main sheet and the companion that is in the file — for ``table``."""
    return sheets(wb, *PARTS.get(sheet, (sheet,)))


def item_status(line: dict) -> str:
    """What a line item's status says (AM8): "ยืนยันแล้ว" is a person's word — the machine never says it.
    A reading a script made says it was read by the system, and whether it still needs a look."""
    if line.get("decided_by") == "person":
        return "คนยืนยันแล้ว"
    if line.get("left_out"):
        return "ระบบอ่าน · ต้องตรวจ: ในแถวมีคำว่า “" + " ".join(line["left_out"])[:40] + "” ที่ไม่ได้อ่านเข้าชื่อรายการ"
    if line.get("verified"):
        return "ระบบอ่าน · ตัวเลขผ่านการตรวจ (ยังไม่มีคนตรวจ)"
    return "ระบบอ่าน · ต้องตรวจ"


class InvoiceWorkbook:
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

    def _table(self, ws, top: int, headers: list[str], rows: list[list], widths: list[int], money_cols=(), wrap_cols=()):
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
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = ws.cell(row=top + 1, column=1)
        if rows:
            ws.auto_filter.ref = f"A{top}:{get_column_letter(len(headers))}{top + len(rows)}"

    # ── sheets ─────────────────────────────────────────────────────────────
    def summary(self):
        ws = self.wb.active
        ws.title = "สรุป"
        c, t = self.c, self.c["totals"]
        self._title(ws, f"ทะเบียนเอกสารซื้อ · {c['period_label']}",
                    " · ".join(x for x in (c.get("company"), f"เลขผู้เสียภาษี {c['company_tax_id']}" if c.get("company_tax_id") else None,
                                            f"จัดทำ {c['prepared_on']}") if x))
        rows = [
            ["เอกสารที่ได้รับ", t["documents"]], ["บันทึกเข้าทะเบียน", t["included"]],
            [f"{NOT_INCLUDED} (ดูแผ่นชื่อเดียวกัน)", t["excluded"]],
            ["ข้อยกเว้นทั้งหมด", t["exceptions"]], ["คำถามที่ตอบแล้ว", t["questions_answered"]],
            ["ยอดก่อน VAT (บาท)", _num(t["thb"]["subtotal"])], ["ภาษีมูลค่าเพิ่ม (บาท)", _num(t["thb"]["vat_amount"])],
            ["ยอดรวม (บาท)", _num(t["thb"]["total"])], ["หัก ณ ที่จ่าย (บาท)", _num(t["thb"]["wht_amount"])],
            ["ยอดที่ต้องชำระ (บาท)", _num(t["thb"].get("payable", t["thb"]["net_payable"]))],
        ]
        self._table(ws, 4, ["รายการ", "ค่า"], rows, [36, 22])
        for r in range(10, 15):           # the five baht rows (the counts stay integers)
            ws.cell(row=r, column=2).number_format = MONEY
        r = 4 + len(rows) + 2
        notes = ["ตัวเลขทุกตัวอ่านจากเอกสารด้วยสคริปต์ (ข้อความใน PDF / e-Tax XML / OCR) หรือผู้ใช้พิมพ์หรือยืนยัน "
                 "โมเดลไม่ได้เป็นผู้กำหนดตัวเลขใดในไฟล์นี้",
                 "ยอดที่ต้องชำระ = ยอดสุทธิที่เอกสารพิมพ์ ถ้าไม่ได้พิมพ์ = ยอดรวม − หัก ณ ที่จ่าย (ไม่มีหัก ณ ที่จ่าย = ยอดรวม)",
                 "ที่มาของทุกค่าอยู่ในชีต “ที่มา”: หน้า ตำแหน่งบนหน้า ข้อความที่พิมพ์ และความมั่นใจ"]
        if t["excluded"]:
            notes.insert(0, f"มี{NOT_INCLUDED} {t['excluded']} ฉบับ ยอดในไฟล์นี้ยังไม่รวมเอกสารเหล่านั้น "
                            f"ดูเหตุผลและวิธีนำกลับมารวมในแผ่น “{NOT_INCLUDED}”")
        for m in self.c.get("models") or []:
            notes.append(f"โมเดลที่เห็นเอกสาร: {m['model']} ({'รันในองค์กร' if m['self_hosted'] else 'ผู้ให้บริการภายนอก'}) "
                         f"อ่านซ้ำ {len(m['documents'])} ฉบับที่เป็นสแกนหรือรูปถ่าย เพื่อเทียบกับ OCR เท่านั้น")
        if any(d["tier"] == "photo" for d in self.c["documents"]):
            notes.append("รูปถ่ายใบเสร็จอ่านได้เท่าที่ภาพชัด: บันทึกเฉพาะผู้ขาย วันที่ ยอดรวม VAT และเลขผู้เสียภาษี")
        for n in notes:
            cell = ws.cell(row=r, column=1, value=n)
            cell.font = Font(name=self.font, size=9, color=self.p["muted"])
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.row_dimensions[r].height = 30
            r += 1

    def register(self):
        rows = []
        for i, d in enumerate([d for d in self.c["documents"] if d["status"] == "included"], start=1):
            rows.append([i, d["document_date"], d["document_number"], d["document_type_label"], d["seller_name"],
                         d["seller_tax_id"], d["seller_branch"], d["buyer_name"], d["buyer_tax_id"], d["currency"],
                         _num(d["subtotal"]), _num(d["vat_amount"]), _num(d["total"]), _num(d["wht_amount"]),
                         _num(d.get("payable", d["net_payable"])), _num(d["fx_rate"]), _num(d["thb_subtotal"]),
                         _num(d["thb_vat_amount"]),
                         _num(d["thb_total"]), d["tier_label"], d["name"]])
        for sheet, title, sub in ((S_REGISTER, "ทะเบียนเอกสาร", "เฉพาะเอกสารที่บันทึก ยอด “บาท” แปลงด้วยอัตราที่ผู้ใช้ระบุเมื่อเป็นเงินตราต่างประเทศ "
                                                             f"· สาขา ผู้ซื้อ วิธีอ่าน และไฟล์อยู่ในชีต “{S_REGISTER_EV}” แถวเดียวกัน"),
                                  (S_REGISTER_EV, S_REGISTER_EV, f"หลักฐานของแต่ละแถวในชีต “{S_REGISTER}” อ่านเทียบกันตามลำดับ · สาขา ผู้ซื้อ "
                                                                 "ที่มา (วิธีอ่าน) และไฟล์")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, title, sub)
            picked, prow, cols = workpaper.pick(H_REGISTER, rows, [h for h, _ in LAYOUT[sheet]], money_cols=(11, 12, 13, 14, 15, 17, 18, 19),
                                                wrap_cols=(4, 5, 8, 21))
            self._table(ws, 4, picked, prow, [w for _, w in LAYOUT[sheet]], **cols)
            # a tax ID is text, never a number
            for j, h in enumerate(picked, start=1):
                if h in ("เลขผู้เสียภาษีผู้ขาย", "เลขผู้เสียภาษีผู้ซื้อ"):
                    for r in range(5, 5 + len(rows)):
                        ws.cell(row=r, column=j).number_format = "@"

    def not_included(self):
        ws = self.wb.create_sheet(NOT_INCLUDED)
        rows = [[e["name"], e["reason"], e.get("detail") or "", e["how"]] for e in self.c.get("not_included") or []]
        self._title(ws, NOT_INCLUDED,
                    "เอกสารในแผ่นนี้ไม่อยู่ในทะเบียน และไม่ได้นับในยอดรวมของรอบนี้ · นำกลับมารวมได้จากห้องทำงานของ agent"
                    if rows else "รอบนี้ไม่มีเอกสารที่ถูกตัดออก เอกสารที่ส่งมาทุกฉบับอยู่ในทะเบียน")
        self._table(ws, 4, ["ไฟล์", "เหตุผล", "รายละเอียด", "การนำกลับมารวม"], rows, [36, 30, 64, 44], wrap_cols=(3, 4))

    def line_items(self):
        ws = self.wb.create_sheet("รายการสินค้า")
        self._title(ws, "รายการสินค้า/บริการ", "“ระบบอ่าน” = อ่านด้วยสคริปต์ ยังไม่มีคนตรวจ · “คนยืนยันแล้ว” = มีคนตอบการ์ดของรายการนั้น · "
                                               "ความมั่นใจต่ำกว่า 0.95 = ต้องตรวจ (ยอดหัวเอกสารตรวจแยกไว้แล้ว)")
        rows = [[l["name"], l["row"], l["description"], _num(l["quantity"]), _num(l["unit_price"]), _num(l["amount"]),
                 l["confidence"], item_status(l)] for l in self.c["line_items"]]
        self._table(ws, 4, ["ไฟล์", "บรรทัด", "รายการ", "จำนวน", "ราคาต่อหน่วย", "จำนวนเงิน", "ความมั่นใจ", "สถานะ"], rows,
                    [30, 8, 44, 10, 14, 14, 10, 30], money_cols=(5, 6), wrap_cols=(3, 8))

    def provenance(self):
        ws = self.wb.create_sheet("ที่มา")
        self._title(ws, "ที่มาของทุกค่า", "หน้า · ตำแหน่งบนหน้า (x0,y0,x1,y1 หน่วย pt; รูปถ่ายเป็น px) · ข้อความที่พิมพ์ · ใครเป็นผู้กำหนด")
        rows = []
        for p in self.c["provenance"]:
            vision = "; ".join(f"{c['method']}: {c['value']}" for c in p.get("candidates") or [])
            rows.append([p["name"], p["label"], p["value"] if not isinstance(p["value"], (int, float)) else str(p["value"]),
                         p["confidence"], method_th(p["method"]), p["page"], p["bbox"], p["raw_text"],
                         DECIDED_TH.get(p["decided_by"], p["decided_by"]),
                         ", ".join(CHECK_NAME_TH.get(c, c) for c in p["checks"] if c in CHECK_NAME_TH) or None, vision or None])
        self._table(ws, 4, ["ไฟล์", "ช่อง", "ค่า", "ความมั่นใจ", "อ่านด้วย", "หน้า", "ตำแหน่ง", "ข้อความในเอกสาร",
                            "ผู้กำหนด", "ผ่านการตรวจ", "โมเดลอ่านได้ (อ้างอิง)"], rows,
                    [22, 15, 24, 9, 13, 6, 20, 28, 15, 28, 22], wrap_cols=(1, 2, 3, 5, 7, 8, 9, 10, 11))

    def exceptions(self):
        ws = self.wb.create_sheet("ข้อยกเว้น")
        self._title(ws, "ข้อยกเว้น", "“ไม่ได้บันทึก” = ไม่อยู่ในทะเบียน · “บันทึกพร้อมหมายเหตุ” = อยู่ในทะเบียน แต่มีสิ่งที่คนตรวจควรรู้")
        who = {"person": "ผู้ใช้", "card": "คำตอบในการ์ด", "rule": "กฎของระบบ"}
        rows = [[e["name"], e["code"], e["reason"], "ไม่ได้บันทึก" if e["severity"] == "excluded" else "บันทึกพร้อมหมายเหตุ",
                 e.get("detail") or "", who.get(e["decided_by"], e["decided_by"])] for e in self.c["exceptions"]]
        self._table(ws, 4, ["ไฟล์", "รหัสเหตุผล", "เหตุผล", "ผล", "รายละเอียด", "ตัดสินโดย"], rows,
                    [30, 24, 34, 18, 60, 14], wrap_cols=(5,))

    def checks(self):
        ws = self.wb.create_sheet("การตรวจ")
        self._title(ws, "การตรวจตัวเลขของแต่ละเอกสาร", "ตรวจด้วยกฎ ไม่ใช้โมเดล: เลขผู้เสียภาษี วันที่ จำนวน×ราคา ผลรวม VAT 7% ยอดรวม หัก ณ ที่จ่าย ฯลฯ")
        def note(c):
            m = msg_th(c.get("message")) or ""
            try:
                differs = Decimal(str(c["expected"])) != Decimal(str(c["actual"]))
            except Exception:  # noqa: BLE001 — not two amounts
                differs = False
            if not m and c["status"] == "pass" and differs:
                m = "ต่างกันไม่เกิน 1 สตางค์ จากการปัดเศษ ถือว่าตรง"
            return m
        rows = [[c["name"], check_name(c["code"]), STATUS_TH.get(c["status"], c["status"]),
                 None if c.get("expected") is None else msg_th(c["expected"]), None if c.get("actual") is None else msg_th(c["actual"]),
                 note(c), c["code"]] for c in self.c["checks"]]
        self._table(ws, 4, ["ไฟล์", "ตรวจอะไร", "ผล", "ควรเป็น", "ที่พบ", "หมายเหตุ", "รหัส (สำหรับผู้ดูแล)"], rows,
                    [30, 30, 10, 22, 22, 44, 20], wrap_cols=(6,))

    def write(self, path: str) -> dict:
        self.summary()
        self.register()
        self.not_included()
        self.line_items()
        self.provenance()
        self.exceptions()
        self.checks()
        self.wb.properties.creator = "Agent Market · invoice processing"
        # AM8: printable — the register's row number, date and document number repeat on every page (AM10: and
        # open its companion sheet)
        workpaper.setup_workbook(self.wb, doc_name=os.path.basename(path),
                                 id_cols={S_REGISTER: ID_COLS[S_REGISTER], S_REGISTER_EV: ID_COLS[S_REGISTER], "ที่มา": 2})
        self.wb.save(path)
        return {"sheets": list(self.wb.sheetnames), "register_rows": self.c["totals"]["included"]}


def check(path: str, computed: dict, run: dict | None = None) -> list[dict]:
    """The gate, on the file itself (AM8): every sheet there; the register holds exactly the recorded
    documents, and every field of every row — dates, numbers, tax IDs, every amount in its own currency
    and in baht — is computed.json's, read back cell by cell (an empty cell where a value belongs fails);
    the summary's counts and totals and every line item likewise; every provenance row says how its
    value was read."""
    def ck(name, ok, detail, label):
        return {"name": name, "ok": bool(ok), "detail": detail, "label": label}

    wb = load_workbook(path)
    out = [ck("xlsx.sheets", set(SHEETS) <= set(wb.sheetnames), ", ".join(wb.sheetnames), W("check.sheets"))]
    rows = table(parts(wb, S_REGISTER), H_REGISTER)
    want = computed["totals"]["included"]
    out.append(ck("xlsx.register_rows", len(rows) == want, W("check.rows.detail", n=len(rows), want=want), W("check.rows")))
    out.append(_figures(wb, rows, computed))
    pv = [r for r in wb["ที่มา"].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.provenance", len(pv) == len(computed["provenance"]) and all(r[4] for r in pv),
                  W("check.n_values", n=len(pv)), W("check.provenance")))
    ex = [r for r in wb["ข้อยกเว้น"].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.exceptions", len(ex) == len(computed["exceptions"]) and all(r[1] in REASON_TH for r in ex),
                  W("check.n_items", n=len(ex)), W("check.exceptions")))
    left = [r for r in wb[NOT_INCLUDED].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    want_left = sorted(e["name"] for e in computed.get("not_included") or [])
    out.append(ck("xlsx.not_included", sorted(r[0] for r in left) == want_left and all(r[1] and r[3] for r in left)
                  and len(want_left) == computed["totals"]["excluded"],
                  W("check.left.detail", n=len(left), excluded=computed["totals"]["excluded"]), W("check.left")))
    unknown = sorted({d["document_type"] for d in computed["documents"] if d["document_type"] not in DOC_TYPE_TH})
    out.append(ck("xlsx.doc_types", not unknown, ", ".join(unknown) or W("check.complete"), W("check.doc_types")))
    out.extend(workpaper.checks(path, words.lang()))
    return out


# The register's columns a value is read back from: (index in H_REGISTER, header, computed key, money?)
REGISTER_FIELDS = (
    (1, "วันที่", "document_date", False), (2, "เลขที่", "document_number", False),
    (5, "เลขผู้เสียภาษีผู้ขาย", "seller_tax_id", False), (8, "เลขผู้เสียภาษีผู้ซื้อ", "buyer_tax_id", False),
    (9, "สกุลเงิน", "currency", False),
    (10, "ยอดก่อน VAT", "subtotal", True), (11, "VAT", "vat_amount", True), (12, "ยอดรวม", "total", True),
    (13, "หัก ณ ที่จ่าย", "wht_amount", True), (14, "ยอดที่ต้องชำระ", "payable", True), (15, "อัตราแลกเปลี่ยน", "fx_rate", True),
    (16, "ยอดก่อน VAT (บาท)", "thb_subtotal", True), (17, "VAT (บาท)", "thb_vat_amount", True),
    (18, "ยอดรวม (บาท)", "thb_total", True),
)


def _figures(wb, rows: list, computed: dict) -> dict:
    """``rows`` is the register as ``table(parts(wb, S_REGISTER), H_REGISTER)`` read it: every row in H_REGISTER order."""
    from ....deliverables.reread import Tally, labelled

    t = Tally()
    docs = [d for d in computed["documents"] if d["status"] == "included"]
    for i, (r, d) in enumerate(zip(rows, docs), start=1):
        where = W("where.register", i=i, name=d.get("document_number") or d["name"])
        t.number(W("where.no", where=where), r[0], i)
        for col, head, key, money in REGISTER_FIELDS:
            want = d.get("payable", d.get("net_payable")) if key == "payable" else d.get(key)
            if money:
                t.number(f"{where} {head}", r[col], want)
            else:
                t.text(f"{where} {head}", r[col], "" if want is None else want)
    face = labelled(wb["สรุป"], min_row=5)
    tt = computed["totals"]
    for label, want in (("เอกสารที่ได้รับ", tt["documents"]), ("บันทึกเข้าทะเบียน", tt["included"]),
                        ("ข้อยกเว้นทั้งหมด", tt["exceptions"]), ("คำถามที่ตอบแล้ว", tt["questions_answered"]),
                        ("ยอดก่อน VAT (บาท)", tt["thb"]["subtotal"]), ("ภาษีมูลค่าเพิ่ม (บาท)", tt["thb"]["vat_amount"]),
                        ("ยอดรวม (บาท)", tt["thb"]["total"]), ("หัก ณ ที่จ่าย (บาท)", tt["thb"]["wht_amount"]),
                        ("ยอดที่ต้องชำระ (บาท)", tt["thb"].get("payable", tt["thb"]["net_payable"]))):
        t.number(W("where.summary", label=label), face.get(label), want)
    items = [r for r in wb["รายการสินค้า"].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    if len(items) != len(computed["line_items"]):
        t.fail(W("check.items.count", got=len(items), want=len(computed["line_items"])))
    for r, li in zip(items, computed["line_items"]):
        where = W("where.item", name=li["name"], row=li["row"])
        t.text(W("where.item.name", where=where), r[2], li["description"] or "")
        for col, head, key in ((3, "จำนวน", "quantity"), (4, "ราคาต่อหน่วย", "unit_price"), (5, "จำนวนเงิน", "amount")):
            t.number(f"{where} {head}", r[col], li[key])
        t.text(W("where.item.status", where=where), r[7], item_status(li))
    return t.result("xlsx.figures", W("check.figures"), W("noun.values"))
