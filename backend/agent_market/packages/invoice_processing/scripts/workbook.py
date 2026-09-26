"""The register as .xlsx — the deliverable a bookkeeper checks and keys from.

Sheets: สรุป · ทะเบียน (one row per recorded document) · รายการสินค้า · ที่มา (every value: page,
box, method, printed text, confidence, who decided it) · ข้อยกเว้น (reason-coded) · การตรวจ (each
document's deterministic checks). Amounts are numbers, not text, so a reader can sum them again;
the quality gate reopens the file and checks the register adds up to computed.json.
"""

from __future__ import annotations

from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ....engine.e2.cards import CHECK_TH
from .register import DOC_TYPE_TH, REASON_TH

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
SHEETS = ("สรุป", "ทะเบียน", "รายการสินค้า", "ที่มา", "ข้อยกเว้น", "การตรวจ")
DECIDED_TH = {"document": "อ่านจากเอกสาร", "typed_by_person": "ผู้ใช้พิมพ์", "confirmed_by_person": "ผู้ใช้ยืนยัน",
              "computed": "คำนวณจากค่าในเอกสาร", "model_proposal": "โมเดลเสนอ (ยังไม่ยืนยัน)"}
METHOD_TH = {"pdf_text": "ข้อความใน PDF", "ocr": "OCR", "etax_xml": "e-Tax XML", "typed_by_person": "ผู้ใช้พิมพ์",
             "confirmed_by_person": "ผู้ใช้ยืนยัน", "chosen_by_person": "ผู้ใช้เลือก"}
STATUS_TH = {"pass": "ผ่าน", "fail": "ไม่ผ่าน", "skip": "ไม่เกี่ยว"}


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


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
            ["เอกสารที่ได้รับ", t["documents"]], ["บันทึกเข้าทะเบียน", t["included"]], ["ไม่ได้บันทึก (ดูข้อยกเว้น)", t["excluded"]],
            ["ข้อยกเว้นทั้งหมด", t["exceptions"]], ["คำถามที่ตอบแล้ว", t["questions_answered"]],
            ["ยอดก่อน VAT (บาท)", _num(t["thb"]["subtotal"])], ["ภาษีมูลค่าเพิ่ม (บาท)", _num(t["thb"]["vat_amount"])],
            ["ยอดรวม (บาท)", _num(t["thb"]["total"])], ["หัก ณ ที่จ่าย (บาท)", _num(t["thb"]["wht_amount"])],
            ["ยอดชำระสุทธิ (บาท)", _num(t["thb"]["net_payable"])],
        ]
        self._table(ws, 4, ["รายการ", "ค่า"], rows, [36, 22])
        for r in range(10, 15):           # the five baht rows (the counts stay integers)
            ws.cell(row=r, column=2).number_format = MONEY
        r = 4 + len(rows) + 2
        notes = ["ตัวเลขทุกตัวอ่านจากเอกสารด้วยสคริปต์ (ข้อความใน PDF / e-Tax XML / OCR) หรือผู้ใช้พิมพ์หรือยืนยัน "
                 "โมเดลไม่ได้เป็นผู้กำหนดตัวเลขใดในไฟล์นี้",
                 "ที่มาของทุกค่าอยู่ในชีต “ที่มา”: หน้า ตำแหน่งบนหน้า ข้อความที่พิมพ์ และความมั่นใจ"]
        for m in self.c.get("models") or []:
            notes.append(f"โมเดลที่เห็นเอกสาร: {m['model']} ({'รันในองค์กร' if m['self_hosted'] else 'ผู้ให้บริการภายนอก'}) "
                         f"อ่านซ้ำ {len(m['documents'])} ฉบับที่เป็นสแกนหรือรูปถ่าย เพื่อเทียบกับ OCR เท่านั้น")
        if any(d["tier"] == "photo" for d in self.c["documents"]):
            notes.append("รูปถ่ายใบเสร็จอ่านแบบพยายามที่สุด (best-effort): บันทึกเฉพาะผู้ขาย วันที่ ยอดรวม VAT และเลขผู้เสียภาษี")
        for n in notes:
            cell = ws.cell(row=r, column=1, value=n)
            cell.font = Font(name=self.font, size=9, color=self.p["muted"])
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.row_dimensions[r].height = 30
            r += 1

    def register(self):
        ws = self.wb.create_sheet("ทะเบียน")
        self._title(ws, "ทะเบียนเอกสาร", "เฉพาะเอกสารที่บันทึก ยอด “บาท” แปลงด้วยอัตราที่ผู้ใช้ระบุเมื่อเป็นเงินตราต่างประเทศ")
        heads = ["ลำดับ", "วันที่", "เลขที่", "ชนิดเอกสาร", "ผู้ขาย", "เลขผู้เสียภาษีผู้ขาย", "สาขา", "ผู้ซื้อ",
                 "เลขผู้เสียภาษีผู้ซื้อ", "สกุลเงิน", "ยอดก่อน VAT", "VAT", "ยอดรวม", "หัก ณ ที่จ่าย", "ยอดสุทธิ",
                 "อัตราแลกเปลี่ยน", "ยอดก่อน VAT (บาท)", "VAT (บาท)", "ยอดรวม (บาท)", "ที่มา", "ไฟล์"]
        rows = []
        for i, d in enumerate([d for d in self.c["documents"] if d["status"] == "included"], start=1):
            rows.append([i, d["document_date"], d["document_number"], d["document_type_label"], d["seller_name"],
                         d["seller_tax_id"], d["seller_branch"], d["buyer_name"], d["buyer_tax_id"], d["currency"],
                         _num(d["subtotal"]), _num(d["vat_amount"]), _num(d["total"]), _num(d["wht_amount"]),
                         _num(d["net_payable"]), _num(d["fx_rate"]), _num(d["thb_subtotal"]), _num(d["thb_vat_amount"]),
                         _num(d["thb_total"]), d["tier_label"], d["name"]])
        self._table(ws, 4, heads, rows, [6, 11, 16, 18, 30, 16, 8, 26, 16, 8, 14, 12, 14, 12, 14, 10, 16, 12, 16, 16, 30],
                    money_cols=(11, 12, 13, 14, 15, 17, 18, 19))
        for r in range(5, 5 + len(rows)):
            for j in (6, 9):
                ws.cell(row=r, column=j).number_format = "@"

    def line_items(self):
        ws = self.wb.create_sheet("รายการสินค้า")
        self._title(ws, "รายการสินค้า/บริการ", "ความมั่นใจต่ำกว่า 0.95 = ยังไม่ได้ยืนยัน (ยอดหัวเอกสารยังผ่านการตรวจ)")
        rows = [[l["name"], l["row"], l["description"], _num(l["quantity"]), _num(l["unit_price"]), _num(l["amount"]),
                 l["confidence"], "ยืนยันแล้ว" if l["verified"] else "ยังไม่ยืนยัน"] for l in self.c["line_items"]]
        self._table(ws, 4, ["ไฟล์", "บรรทัด", "รายการ", "จำนวน", "ราคาต่อหน่วย", "จำนวนเงิน", "ความมั่นใจ", "สถานะ"], rows,
                    [30, 8, 44, 10, 14, 14, 10, 14], money_cols=(5, 6), wrap_cols=(3,))

    def provenance(self):
        ws = self.wb.create_sheet("ที่มา")
        self._title(ws, "ที่มาของทุกค่า", "หน้า · ตำแหน่งบนหน้า (x0,y0,x1,y1 หน่วย pt; รูปถ่ายเป็น px) · ข้อความที่พิมพ์ · ใครเป็นผู้กำหนด")
        rows = []
        for p in self.c["provenance"]:
            vision = "; ".join(f"{c['method']}: {c['value']}" for c in p.get("candidates") or [])
            rows.append([p["name"], p["label"], p["value"] if not isinstance(p["value"], (int, float)) else str(p["value"]),
                         p["confidence"], METHOD_TH.get(p["method"], p["method"]), p["page"], p["bbox"], p["raw_text"],
                         DECIDED_TH.get(p["decided_by"], p["decided_by"]), ", ".join(p["checks"]), vision or None])
        self._table(ws, 4, ["ไฟล์", "ช่อง", "ค่า", "ความมั่นใจ", "อ่านด้วย", "หน้า", "ตำแหน่ง", "ข้อความในเอกสาร",
                            "ผู้กำหนด", "ผ่านการตรวจ", "โมเดลอ่านได้ (อ้างอิง)"], rows,
                    [28, 18, 28, 10, 14, 6, 24, 36, 18, 36, 28], wrap_cols=(8, 10))

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
        rows = [[c["name"], CHECK_TH.get(c["code"], (c["code"],))[0], c["code"], STATUS_TH.get(c["status"], c["status"]),
                 None if c.get("expected") is None else str(c["expected"]), None if c.get("actual") is None else str(c["actual"]),
                 c.get("message") or ""] for c in self.c["checks"]]
        self._table(ws, 4, ["ไฟล์", "การตรวจ", "รหัส", "ผล", "ควรเป็น", "ที่พบ", "หมายเหตุ"], rows,
                    [30, 18, 22, 10, 20, 20, 40], wrap_cols=(7,))

    def write(self, path: str) -> dict:
        self.summary()
        self.register()
        self.line_items()
        self.provenance()
        self.exceptions()
        self.checks()
        self.wb.properties.creator = "Agent Market · invoice processing"
        self.wb.save(path)
        return {"sheets": list(self.wb.sheetnames), "register_rows": self.c["totals"]["included"]}


def check(path: str, computed: dict) -> list[dict]:
    """The gate: every sheet there, the register holds exactly the recorded documents and adds up to
    computed.json to the satang, every provenance row says how its value was read."""
    def ck(name, ok, detail, label):
        return {"name": name, "ok": bool(ok), "detail": detail, "label": label}

    wb = load_workbook(path)
    out = [ck("xlsx.sheets", set(SHEETS) <= set(wb.sheetnames), ", ".join(wb.sheetnames), "ชีตครบ")]
    ws = wb["ทะเบียน"]
    rows = [r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    want = computed["totals"]["included"]
    out.append(ck("xlsx.register_rows", len(rows) == want, f"{len(rows)} แถว / {want} ฉบับ", "จำนวนแถวตรงกับเอกสารที่บันทึก"))
    got = sum((Decimal(str(r[18])) for r in rows if r[18] is not None), Decimal(0)).quantize(Decimal("0.01"))
    exp = Decimal(computed["totals"]["thb"]["total"])
    out.append(ck("xlsx.register_total", got == exp, f"{got} / {exp}", "ยอดรวมในทะเบียนตรงกับที่คำนวณ"))
    pv = [r for r in wb["ที่มา"].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.provenance", len(pv) == len(computed["provenance"]) and all(r[4] for r in pv),
                  f"{len(pv)} ค่า", "ทุกค่ามีที่มา"))
    ex = [r for r in wb["ข้อยกเว้น"].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.exceptions", len(ex) == len(computed["exceptions"]) and all(r[1] in REASON_TH for r in ex),
                  f"{len(ex)} รายการ", "ข้อยกเว้นครบและมีรหัสเหตุผล"))
    unknown = sorted({d["document_type"] for d in computed["documents"] if d["document_type"] not in DOC_TYPE_TH})
    out.append(ck("xlsx.doc_types", not unknown, ", ".join(unknown) or "ครบ", "ชนิดเอกสารมีชื่อไทย"))
    return out
