"""The matching result as .xlsx — the working paper an accounts-payable clerk hands to an approver.

Sheets: สรุป (how many passed, how many are blocked and why, the value in each state, and how
the lines were tested: three-way or two-way — on the face, so that nobody reads a two-way result
as a three-way one) · ผลตรวจใบแจ้งหนี้ (one row per invoice: status, reason codes, match depth) ·
ผลตรวจรายบรรทัด (one row per line: the PO line and the receipts it was tested against, with the
file, sheet and row of each; the cumulative figures; every tolerance that was used) · ยอดสะสมตาม PO
· GR-IR (received not yet billed, billed not yet received — figures, nothing is posted) ·
ค่าใช้จ่ายที่ไม่ได้สั่งซื้อ · ใบลดหนี้และใบเพิ่มหนี้ · การจับคู่ (every link: the pass, the tier, who
confirmed it) · ข้อยกเว้น · หลักฐาน · เอกสารที่ยังไม่ได้รวม (when a file was not used).

AM10: the wide tables print one page across. Each is one table (H_*) written as a main sheet — the
row's identity, its amounts, its status and reason — and companion sheets right after it
("<sheet> · หลักฐาน", "ผลตรวจรายบรรทัด · เกณฑ์ที่ใช้") that repeat the identity columns and hold the
evidence, one row per row, read side by side by row number (``LAYOUT``: which columns, how wide).

Thai labels; amounts and quantities are numbers, not text; the quality gate reopens the file and
checks it against computed.json.
"""

from __future__ import annotations

import os
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ....deliverables import workpaper
from ....deliverables.reread import Tally, sheets, table
from ....engine.e3 import cards as E3C
from .match import CONFIRMED_TH, DEPTH_SHORT, DEPTH_TH, PASS, REASON_TH, STATUS_TH, THREE, TWO

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
QTY = '#,##0.######;[Red](#,##0.######);"-"'
# The wide tables' columns in the order the data is built and read back (the checks read by name, from the
# sheets of the table together — ``table(parts(wb, sheet), H_*)``)
H_INVOICES = ["ลำดับ", "เลขที่", "วันที่", "ชนิดเอกสาร", "ผู้ขาย", "เลขประจำตัวผู้เสียภาษี", "PO ที่อ้างถึง", "สกุลเงิน", "ยอดก่อนภาษี",
              "ภาษีมูลค่าเพิ่ม", "ยอดรวม", "สถานะ", "เหตุผล", "วิธีตรวจ", "ขอคืนภาษีซื้อ", "หมายเหตุ", "หลักฐาน"]
H_LINES = ["ลำดับ", "ใบแจ้งหนี้", "บรรทัด", "รายการ", "จำนวน", "หน่วย", "ราคาต่อหน่วย", "จำนวนเงิน", "วิธีตรวจ", "เหตุที่ตรวจแบบนี้",
           "PO", "บรรทัดของ PO", "รายการใน PO", "ราคาต่อหน่วยใน PO", "หลักฐาน (PO)", "ใบรับของที่ใช้ตรวจ", "สั่งซื้อ", "รับจริงสะสม",
           "เรียกเก็บก่อนหน้า", "เรียกเก็บสะสม", "หน่วยที่เทียบ", "ส่วนต่างราคารวม", "สถานะ", "เหตุผล", "ข้อที่ไม่ผ่าน",
           "ค่าที่ยอมรับได้ที่ใช้", "วิธีจับคู่", "ขั้น", "ผู้ยืนยัน", "หมายเหตุ", "หลักฐาน (ใบแจ้งหนี้)"]
H_PO = ["PO", "บรรทัด", "รหัสสินค้า", "รายการ", "ประเภท", "สถานะ PO", "หน่วย", "ราคาต่อหน่วย", "สั่งซื้อ", "รับจริงสะสม",
        "เรียกเก็บสะสม", "มูลค่าที่เรียกเก็บสะสม", "คงเหลือให้เรียกเก็บ", "เรียกเก็บเกิน", "วิธีตรวจ", "ใบแจ้งหนี้ที่เรียกเก็บ", "ใบรับของ",
        "หลักฐาน (PO)"]
H_LINKS = ["ลำดับ", "เรื่อง", "เอกสาร", "สถานะ", "วิธีจับคู่", "ขั้น", "ผู้ยืนยัน", "รายการ (ซ้าย)", "หลักฐาน (ซ้าย)", "รายการ (ขวา)",
           "หลักฐาน (ขวา)", "ความคล้าย"]
S_SUMMARY, S_INVOICES, S_LINES, S_PO, S_GRIR = "สรุป", "ผลตรวจใบแจ้งหนี้", "ผลตรวจรายบรรทัด", "ยอดสะสมตาม PO", "ค้างระหว่างรับของกับเรียกเก็บ"
S_UNPLANNED, S_NOTES, S_LINKS, S_EXC, S_EVIDENCE, S_LEFT = ("ค่าใช้จ่ายที่ไม่ได้สั่งซื้อ", "ใบลดหนี้และใบเพิ่มหนี้", "การจับคู่", "ข้อยกเว้น",
                                                           "หลักฐาน", "เอกสารที่ยังไม่ได้รวม")
# AM10: the wide tables' companion sheets — the same identity columns, then the evidence, read beside the main
# sheet by row number; every one prints one page across
S_INVOICES_EV, S_LINES_EV, S_LINES_CRIT = "ผลตรวจใบแจ้งหนี้ · หลักฐาน", "ผลตรวจรายบรรทัด · หลักฐาน", "ผลตรวจรายบรรทัด · เกณฑ์ที่ใช้"
S_PO_EV, S_LINKS_EV = "ยอดสะสมตาม PO · หลักฐาน", "การจับคู่ · หลักฐาน"
PARTS = {S_INVOICES: (S_INVOICES, S_INVOICES_EV), S_LINES: (S_LINES, S_LINES_EV, S_LINES_CRIT), S_PO: (S_PO, S_PO_EV),
         S_LINKS: (S_LINKS, S_LINKS_EV)}
# What prints on each sheet of a wide table, in print order, with the column's width: the main sheet leads with
# the row's identity (ID_COLS of them, repeated on the companions and on every page), then its amounts,
# status and reason; a companion repeats the identity and holds the rest. Every column of H_* is on one of them.
LAYOUT = {
    S_INVOICES: [("ลำดับ", 7), ("เลขที่", 16), ("ผู้ขาย", 30), ("ยอดก่อนภาษี", 14), ("ภาษีมูลค่าเพิ่ม", 14), ("ยอดรวม", 14), ("สถานะ", 16),
                 ("วันที่", 13), ("ชนิดเอกสาร", 16), ("PO ที่อ้างถึง", 14), ("สกุลเงิน", 8), ("เหตุผล", 28), ("วิธีตรวจ", 14)],
    S_INVOICES_EV: [("ลำดับ", 7), ("เลขที่", 16), ("ผู้ขาย", 26), ("เลขประจำตัวผู้เสียภาษี", 15), ("ขอคืนภาษีซื้อ", 36), ("หมายเหตุ", 30),
                    ("หลักฐาน", 36)],
    S_LINES: [("ลำดับ", 7), ("ใบแจ้งหนี้", 16), ("บรรทัด", 8), ("รายการ", 30), ("จำนวน", 10), ("หน่วย", 8), ("ราคาต่อหน่วย", 12),
              ("จำนวนเงิน", 14), ("ราคาต่อหน่วยใน PO", 12), ("ส่วนต่างราคารวม", 12), ("สถานะ", 16), ("วิธีตรวจ", 18), ("เหตุผล", 26)],
    S_LINES_EV: [("ลำดับ", 6), ("ใบแจ้งหนี้", 14), ("บรรทัด", 7), ("รายการ", 20), ("PO", 13), ("บรรทัดของ PO", 8), ("รายการใน PO", 22),
                 ("หลักฐาน (PO)", 30), ("ใบรับของที่ใช้ตรวจ", 32), ("หลักฐาน (ใบแจ้งหนี้)", 30), ("หมายเหตุ", 22)],
    S_LINES_CRIT: [("ลำดับ", 6), ("ใบแจ้งหนี้", 14), ("บรรทัด", 7), ("รายการ", 20), ("เหตุที่ตรวจแบบนี้", 18), ("สั่งซื้อ", 9),
                   ("รับจริงสะสม", 10), ("เรียกเก็บก่อนหน้า", 11), ("เรียกเก็บสะสม", 11), ("หน่วยที่เทียบ", 9), ("ข้อที่ไม่ผ่าน", 24),
                   ("ค่าที่ยอมรับได้ที่ใช้", 30), ("วิธีจับคู่", 18), ("ขั้น", 12), ("ผู้ยืนยัน", 14)],
    S_PO: [("PO", 14), ("บรรทัด", 8), ("รายการ", 30), ("ราคาต่อหน่วย", 12), ("มูลค่าที่เรียกเก็บสะสม", 14), ("รหัสสินค้า", 12), ("ประเภท", 10),
           ("สถานะ PO", 10), ("หน่วย", 8), ("สั่งซื้อ", 10), ("รับจริงสะสม", 10), ("เรียกเก็บสะสม", 10), ("คงเหลือให้เรียกเก็บ", 12),
           ("เรียกเก็บเกิน", 22), ("วิธีตรวจ", 9)],
    S_PO_EV: [("PO", 14), ("บรรทัด", 8), ("รายการ", 26), ("ใบแจ้งหนี้ที่เรียกเก็บ", 40), ("ใบรับของ", 32), ("หลักฐาน (PO)", 40)],
    S_LINKS: [("ลำดับ", 6), ("เรื่อง", 16), ("เอกสาร", 16), ("สถานะ", 16), ("วิธีจับคู่", 26), ("ขั้น", 14), ("ผู้ยืนยัน", 16),
              ("รายการ (ซ้าย)", 30), ("รายการ (ขวา)", 30), ("ความคล้าย", 9)],
    S_LINKS_EV: [("ลำดับ", 6), ("เรื่อง", 16), ("เอกสาร", 16), ("หลักฐาน (ซ้าย)", 44), ("หลักฐาน (ขวา)", 44)],
}
ID_COLS = {S_INVOICES: 3, S_LINES: 4, S_PO: 3, S_LINKS: 3}
SHEETS = (S_SUMMARY, S_INVOICES, S_INVOICES_EV, S_LINES, S_LINES_EV, S_LINES_CRIT, S_PO, S_PO_EV, S_GRIR, S_UNPLANNED, S_NOTES,
          S_LINKS, S_LINKS_EV, S_EXC, S_EVIDENCE)
L_COMPANY, L_PERIOD, L_PREPARER, L_PRACTICE, L_DEPTH, L_APPROVAL = ("นิติบุคคล", "งวด", "ผู้จัดทำ", "บริษัทออกใบรับของหรือไม่",
                                                                   "วิธีตรวจของรอบนี้", "การอนุมัติจ่าย")
L_INVOICES, L_LINES = "ใบแจ้งหนี้ที่ตรวจ (ฉบับ)", "บรรทัดที่ตรวจ"
L_THREE, L_TWO, L_NONE = "บรรทัดที่ตรวจ 3 ทาง", "บรรทัดที่ตรวจ 2 ทาง", "บรรทัดที่ยังไม่ได้ตรวจกับ PO"
L_ACCRUED, L_PREPAID = "รับของแล้วยังไม่ถูกเรียกเก็บ (บาท)", "เรียกเก็บแล้วยังไม่ได้รับของ (บาท)"
TWO_WAY_WARNING = ("บรรทัดที่ตรวจ 2 ทางเทียบปริมาณกับที่สั่งซื้อเท่านั้น ไม่ได้ตรวจกับใบรับของ "
                   "จึงไม่ได้ยืนยันว่าได้รับของแล้ว")
APPROVAL = "agent ไม่ได้อนุมัติจ่ายใบแจ้งหนี้ใด “ผ่าน” หมายถึงพร้อมเสนออนุมัติ การอนุมัติและการจ่ายเป็นของคน"
# AM8: no placeholder stands in for the company or the preparer; a paper without one says it is a draft
MISSING = "— ยังไม่มี —"
HEADER_DRAFT = "ร่าง — หัวกระดาษทำการยังไม่ครบ ({}) ตั้งได้ที่การตั้งค่าของ agent นี้ ยังไม่ใช่กระดาษทำการที่ลงนามได้"


def header_fields(c: dict, run: dict | None) -> list[tuple[str, str]]:
    run = run or {}
    return [(L_COMPANY, str(c.get("company") or run.get("organisation") or "").strip()),
            (L_PREPARER, str(run.get("preparer") or "").strip())]
FROM_TH = {"history": "รอบก่อน", "round": "รอบนี้"}
STATE_TH = {"linked": "จับคู่แล้ว", "waiting": "รอคนยืนยัน"}
KIND_TH = {"receipt": "ใบรับของ ↔ PO", "invoice": "ใบแจ้งหนี้ ↔ PO", "duplicate": "ใบแจ้งหนี้ซ้ำ"}


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def _where(s: dict | None) -> str:
    s = s or {}
    cells = ", ".join(sorted(set((s.get("cells") or {}).values()))) if isinstance(s.get("cells"), dict) else ""
    return " · ".join(str(x) for x in (s.get("file"), s.get("sheet"), f"แถว {s['row']}" if s.get("row") else "",
                                       f"หน้า {s['page']}" if s.get("page") and not s.get("sheet") else "", cells) if x)


def who(kind: str, name: str) -> str:
    return f"{CONFIRMED_TH[kind]}: {name}" if kind == "person" and name else CONFIRMED_TH.get(kind, "")


def _tolerances(t: dict, labels: dict) -> str:
    return " · ".join(f"{labels.get(k, k)} = {v}" for k, v in sorted(t.items()))


def parts(wb, sheet: str) -> list:
    """The sheets a table prints on — the main sheet and the companions that are in the file — for ``table``."""
    return sheets(wb, *PARTS.get(sheet, (sheet,)))


class MatchWorkbook:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None):
        self.c, self.t, self.run = computed, theme, run or {}
        self.p = theme["palette"]
        self.font = theme["fonts"]["thai"]
        self.wb = Workbook()
        self.inv = {i["key"]: i for i in computed["invoices"]}
        self.labels = computed["settings"]["labels"]

    # ── styling ────────────────────────────────────────────────────────────
    def _title(self, ws, text: str, sub: str = ""):
        ws["A1"] = text
        ws["A1"].font = Font(name=self.font, bold=True, size=14, color=self.p["primary"])
        if sub:
            ws["A2"] = sub
            ws["A2"].font = Font(name=self.font, size=9, color=self.p["muted"])

    def _table(self, ws, top: int, headers: list, rows: list, widths: list | None = None, money_cols=(), qty_cols=(),
               wrap_cols=(), freeze: bool = True, first: list | None = None):
        if first:
            headers, rows, widths, cols = workpaper.lead(headers, rows, widths, first, money_cols=money_cols, qty_cols=qty_cols,
                                                         wrap_cols=wrap_cols)
            money_cols, qty_cols, wrap_cols = cols["money_cols"], cols["qty_cols"], cols["wrap_cols"]
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
                elif j in qty_cols:
                    c.number_format = QTY
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

    def _pair(self, ws, r: int, label: str, value, *, money: bool = False, bold: bool = False, color: str | None = None):
        a = ws.cell(row=r, column=1, value=label)
        b = ws.cell(row=r, column=2, value=value)
        for c in (a, b):
            c.font = Font(name=self.font, size=10, bold=bold, color=color or self.p["ink"])
            c.border = Border(bottom=Side(style="thin", color=self.p["rule"]))
        b.alignment = Alignment(wrap_text=True, vertical="top", horizontal="right" if money else "left")
        if money:
            b.number_format = MONEY

    def _section(self, ws, r: int, text: str):
        ws.cell(row=r, column=1, value=text).font = Font(name=self.font, bold=True, size=11, color=self.p["primary"])

    def _note(self, ws, r: int, text: str, cols: int = 4, height: int = 30, color: str | None = None, bold: bool = False):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(name=self.font, size=9, color=color or self.p["muted"], bold=bold)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=cols)
        ws.row_dimensions[r].height = height

    # ── sheets ─────────────────────────────────────────────────────────────
    def summary(self):
        ws = self.wb.active
        ws.title = S_SUMMARY
        c, s, t = self.c, self.c["summary"], self.c["totals"]
        head = dict(header_fields(c, self.run))
        self._title(ws, f"ผลตรวจใบแจ้งหนี้กับใบสั่งซื้อ · {c['period_label']}", head[L_COMPANY])
        for col, w in zip("ABCD", (52, 40, 16, 22)):
            ws.column_dimensions[col].width = w
        missing = [k for k, v in head.items() if not v]
        if missing:
            m = ws.cell(row=3, column=1, value=HEADER_DRAFT.format(", ".join(missing)))
            m.font = Font(name=self.font, bold=True, size=11, color=self.p.get("negative", "B42318"))
        preparer = head[L_PREPARER]
        made = E3C.date_th(self.run.get("date") or c["prepared_on"])
        depth = f"ตรวจ 3 ทาง {t['three_way']} บรรทัด · ตรวจ 2 ทาง {t['two_way']} บรรทัด"
        if t["unlinked"]:
            depth += f" · ยังไม่ได้ตรวจกับ PO {t['unlinked']} บรรทัด"
        r = 4
        for label, value in ((L_COMPANY, head[L_COMPANY] or MISSING), (L_PERIOD, c["period_label"]),
                             (L_PREPARER, f"{preparer} · จัดทำวันที่ {made} · ด้วย agent รุ่น {self.run.get('agent_version') or ''}".rstrip(" ·")
                              if preparer else MISSING),
                             (L_PRACTICE, c["practice"]["label"]), (L_DEPTH, depth), (L_APPROVAL, APPROVAL)):
            self._pair(ws, r, label, value, bold=label == L_DEPTH)
            r += 1
        if t["two_way"]:
            self._note(ws, r, TWO_WAY_WARNING, color=self.p.get("negative", "B42318"), bold=True)
            r += 1
        r += 1
        self._section(ws, r, "ผลตรวจ")
        rows = [[x["label"], x["count"], _num(x["value"]), f"ไม่รวม {x['without_value']} ฉบับที่ไม่มีอัตราแลกเปลี่ยน" if x["without_value"] else ""]
                for x in s["by_status"]]
        self._table(ws, r + 1, ["สถานะ", "จำนวน (ฉบับ)", f"มูลค่า ({s['currency']})", "หมายเหตุ"], rows, money_cols=(3,), freeze=False)
        r += len(rows) + 3
        self._pair(ws, r, L_INVOICES, t["invoices"])
        self._pair(ws, r + 1, L_LINES, t["lines"])
        self._pair(ws, r + 2, L_THREE, t["three_way"])
        self._pair(ws, r + 3, L_TWO, t["two_way"])
        self._pair(ws, r + 4, L_NONE, t["unlinked"])
        self._pair(ws, r + 5, L_ACCRUED, _num(t["received_not_billed"]), money=True)
        self._pair(ws, r + 6, L_PREPAID, _num(t["billed_not_received"]), money=True)
        r += 8
        if s["two_way_why"]:
            self._section(ws, r, "ทำไมบรรทัดเหล่านี้ตรวจ 2 ทาง")
            rows = [[x["why"], x["lines"]] for x in s["two_way_why"]]
            self._table(ws, r + 1, ["เหตุผล", "จำนวนบรรทัด"], rows, freeze=False)
            r += len(rows) + 3
        self._section(ws, r, "เหตุที่ติดเงื่อนไขหรือต้องให้คนตัดสิน")
        rows = [[x["label"], STATUS_TH[x["outcome"]], x["invoices"], x["lines"], _num(x["value"])] for x in s["by_reason"]]
        self._table(ws, r + 1, ["เหตุผล", "ผล", "ใบแจ้งหนี้ (ฉบับ)", "บรรทัด", f"มูลค่า ({s['currency']})"],
                    rows or [["ไม่มี", "", 0, 0, 0.0]], money_cols=(5,), freeze=False)
        r += max(len(rows), 1) + 3
        if s["other_currencies"]:
            self._section(ws, r, "ใบแจ้งหนี้สกุลเงินอื่นที่ไม่มีอัตราแลกเปลี่ยน")
            rows = [[x["currency"], x["count"], _num(x["total"])] for x in s["other_currencies"]]
            self._table(ws, r + 1, ["สกุลเงิน", "จำนวน (ฉบับ)", "ยอดรวมในสกุลเงินนั้น"], rows, money_cols=(3,), freeze=False)
            r += len(rows) + 3
        notes = ["ทุกบรรทัดของใบแจ้งหนี้ตรวจกับบรรทัดของ PO ไม่ได้เทียบเพียงยอดรวมของเอกสาร และเทียบยอดสะสมของทุกใบแจ้งหนี้ที่เรียกเก็บบรรทัดเดียวกัน",
                 "ตัวเลขทุกตัวมาจากไฟล์ที่ส่งมาและคำนวณด้วยสคริปต์ การจับคู่ที่ไม่ตรงกันทุกตัวต้องมีคนยืนยัน ไม่มีโมเดลคำนวณตัวเลขหรือตัดสินการจับคู่ในไฟล์นี้",
                 c["grir"]["note"]] + [a["text"] for a in c.get("assumptions") or []]
        for n in notes:
            self._note(ws, r, n)
            r += 1

    def invoices(self):
        rows = []
        for n, i in enumerate(self.c["invoices"], start=1):
            rows.append([n, i["number"], E3C.date_th(i["date"]), i["doc_type_label"], i["supplier"]["name"], i["supplier"]["tax_id"],
                         ", ".join(i["po_named"]), i["currency"], _num(i["subtotal"]), _num(i["vat_amount"]), _num(i["total"]),
                         i["status_label"], " · ".join(i["reason_labels"]), i["depth_text"],
                         ("ได้: " if i["vat"]["claimable"] else "ไม่ได้: ") + i["vat"]["why"], "\n".join(i["notes"]), _where(i)])
        for sheet, sub in ((S_INVOICES, "หนึ่งแถวต่อหนึ่งเอกสาร · สถานะ เหตุผล และวิธีตรวจของบรรทัดในเอกสารนั้น"),
                           (S_INVOICES_EV, f"หลักฐานของแต่ละแถวในชีต “{S_INVOICES}” อ่านเทียบกันตามลำดับ · เลขผู้เสียภาษี ภาษีซื้อ หมายเหตุ "
                                           "และไฟล์ แผ่น แถวที่อ่าน")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, sheet, sub)
            self._part(ws, H_INVOICES, rows, LAYOUT[sheet], money_cols=(9, 10, 11), wrap_cols=(4, 5, 12, 13, 15, 16, 17))

    def line_rows(self) -> list:
        rows = []
        for n, x in enumerate(self.c["lines"], start=1):
            inv = self.inv[x["invoice"]]
            po = x["po_line"] or {}
            cum = x["cumulative"] or {}
            link = x["link"] or {}
            var = x.get("price_variance") or {}
            if x["unplanned"] is not None:
                depth, why = "ค่าใช้จ่ายที่ไม่ได้สั่งซื้อ", x["unplanned"]["label"]
            else:
                depth, why = x["depth_label"], x["depth_why"]
            receipts = "\n".join(f"{g['gr']} {E3C.date_th(g['date'])} {g['quantity_in_po_unit']} · " +
                                 (f"ยืนยันในระบบโดย {g['confirmed_by']}".rstrip("โดย ").strip() if g["in_system"] else _where(g))
                                 for g in x["receipts"])
            failed = "\n".join(f"{c['label']}: {c['detail'] or (str(c['actual']) + ' / ' + str(c['expected']))}"
                               for c in x["checks"] if not c["ok"])
            rows.append([n, inv["number"], x["n"], x["description"], _num(x["quantity"]), x["unit"], _num(x["unit_price"]),
                         _num(x["amount"]), depth, why, po.get("po") or "", po.get("line_no") or po.get("item_code") or "",
                         po.get("description") or "", _num(po.get("unit_price")), _where(po) if po else "", receipts,
                         _num(cum.get("ordered")), _num(cum.get("received")), _num(cum.get("billed_before")),
                         _num(cum.get("billed_after")), cum.get("unit") or "", _num(var.get("extended")),
                         x["status_label"], " · ".join(x["reason_labels"]), failed,
                         _tolerances(x["tolerances"], self.labels), link.get("pass_label") or "", link.get("tier_label") or "",
                         who(link.get("confirmed") or "", link.get("confirmed_by") or "") if link else "",
                         "\n".join(x["notes"]), _where(x)])
        return rows

    def lines(self):
        rows = self.line_rows()
        for sheet, sub in ((S_LINES, "ทุกบรรทัดบอกว่าตรวจ 3 ทางหรือ 2 ทาง สถานะ และเหตุผล · บรรทัดของ PO ใบรับของ และไฟล์ แผ่น แถวที่อ่าน "
                                     f"อยู่ในชีต “{S_LINES_EV}” ยอดสะสม ข้อที่ไม่ผ่าน และค่าที่ยอมรับได้อยู่ในชีต “{S_LINES_CRIT}” "
                                     "แถวเดียวกันคือบรรทัดเดียวกัน"),
                           (S_LINES_EV, f"หลักฐานของแต่ละบรรทัดในชีต “{S_LINES}” อ่านเทียบกันตามลำดับ · ตรวจกับบรรทัดใดของ PO และใบรับของใด "
                                        "พร้อมไฟล์ แผ่น และแถว"),
                           (S_LINES_CRIT, f"เกณฑ์ที่ใช้ตรวจแต่ละบรรทัดในชีต “{S_LINES}” อ่านเทียบกันตามลำดับ · เหตุที่ตรวจแบบนี้ ยอดสะสม "
                                          "ข้อที่ไม่ผ่าน ค่าที่ยอมรับได้ที่ใช้ และการจับคู่")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, sheet, sub)
            self._part(ws, H_LINES, rows, LAYOUT[sheet], money_cols=(7, 8, 14, 22), qty_cols=(5, 17, 18, 19, 20),
                       wrap_cols=(4, 9, 10, 13, 15, 16, 23, 24, 25, 26, 27, 29, 30, 31))

    def po(self):
        rows = []
        for p in self.c["po_lines"]:
            invs = "\n".join(f"{e['invoice']} {E3C.date_th(e['date'])} {e['quantity']} ({FROM_TH[e['from']]})" for e in p["invoices"])
            grs = "\n".join(f"{g['gr']} {E3C.date_th(g['date'])} {g['quantity']}" for g in p["receipts"])
            rows.append([p["po"], p["line_no"], p["item_code"], p["description"], p["kind_label"], p["status_label"], p["unit"],
                         _num(p["unit_price"]), _num(p["quantity"]), _num(p["received"]), _num(p["billed"]), _num(p["billed_amount"]),
                         _num(p["open_to_bill"]), p["over_billed_why"], DEPTH_SHORT[p["depth"]], invs, grs, _where(p)])
        for sheet, title, sub in ((S_PO, "ยอดสะสมตามบรรทัดของ PO", "สั่งซื้อ รับจริง และเรียกเก็บสะสมของทุกใบแจ้งหนี้ ทั้งรอบนี้และรอบก่อน"),
                                  (S_PO_EV, S_PO_EV, f"หลักฐานของแต่ละบรรทัดในชีต “{S_PO}” อ่านเทียบกันตามลำดับ · ใบแจ้งหนี้และใบรับของที่นับ "
                                                     "และไฟล์ แผ่น แถวของ PO")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, title, sub)
            self._part(ws, H_PO, rows, LAYOUT[sheet], money_cols=(8, 12), qty_cols=(9, 10, 11, 13), wrap_cols=(4, 14, 16, 17, 18))

    def grir(self):
        ws = self.wb.create_sheet(S_GRIR)
        g = self.c["grir"]
        self._title(ws, "รับของแล้วยังไม่ถูกเรียกเก็บ และเรียกเก็บแล้วยังไม่ได้รับของ", f"{g['covers']} · {g['note']}")
        self._pair(ws, 4, L_ACCRUED, _num(g["received_not_billed"]), money=True, bold=True)
        self._pair(ws, 5, L_PREPAID, _num(g["billed_not_received"]), money=True, bold=True)
        ws.column_dimensions["A"].width = 44
        ws.column_dimensions["B"].width = 20
        rows = []
        for p in self.c["po_lines"]:
            if p["id"] not in g["lines"]:
                continue
            rows.append([p["po"], p["line_no"], p["description"], p["unit"], _num(p["unit_price"]), _num(p["received"]), _num(p["billed"]),
                         _num(p["received_not_billed"]), _num(p["received_not_billed_amount"]), _num(p["billed_not_received"]),
                         _num(p["billed_not_received_amount"]), _where(p)])
        self._table(ws, 7, ["PO", "บรรทัด", "รายการ", "หน่วย", "ราคาต่อหน่วยใน PO", "รับจริงสะสม", "เรียกเก็บสะสม", "รับแล้วยังไม่เรียกเก็บ",
                            "มูลค่า (รับแล้วยังไม่เรียกเก็บ)", "เรียกเก็บแล้วยังไม่รับ", "มูลค่า (เรียกเก็บแล้วยังไม่รับ)", "หลักฐาน (PO)"], rows,
                    money_cols=(5, 9, 11), qty_cols=(6, 7, 8, 10), wrap_cols=(3, 12), freeze=False)

    def unplanned(self):
        ws = self.wb.create_sheet(S_UNPLANNED)
        self._title(ws, S_UNPLANNED, "ค่าขนส่ง ค่าประกันภัย ค่าดำเนินการ และค่าธรรมเนียมที่ถูกเรียกเก็บโดยไม่อยู่ใน PO · ไม่นำไปรวมกับส่วนต่างราคาของบรรทัดใด")
        rows = [[u["invoice_number"], u["supplier"], u["label"], _num(u["amount"]), "อยู่ในวงเงินที่ตั้งไว้" if u["within_limit"] else "เกินวงเงินที่ตั้งไว้"]
                for u in self.c["unplanned"]]
        self._table(ws, 4, ["ใบแจ้งหนี้", "ผู้ขาย", "ประเภทค่าใช้จ่าย", "จำนวนเงิน", "ผล"], rows, [20, 40, 40, 18, 28], money_cols=(4,),
                    wrap_cols=(2, 3))

    def notes(self):
        ws = self.wb.create_sheet(S_NOTES)
        self._title(ws, S_NOTES, "ใบลดหนี้ลดยอดที่เรียกเก็บสะสมของบรรทัดที่อ้างถึง ใบเพิ่มหนี้เพิ่มยอด · ทั้งสองต้องระบุใบแจ้งหนี้เดิม")
        rows = [[x["number"], E3C.date_th(x["date"]), x["kind_label"], x["supplier"], _num(x["total"]), x["ref_invoice"],
                 "พบ" if x["corrects"] else "ไม่พบ", x["status_label"], " · ".join(x["reasons"])] for x in self.c["notes"]]
        self._table(ws, 4, ["เลขที่", "วันที่", "ชนิด", "ผู้ขาย", "ยอดรวม", "อ้างถึงใบแจ้งหนี้", "ใบแจ้งหนี้เดิม", "สถานะ", "เหตุผล"], rows,
                    [18, 14, 16, 40, 16, 20, 14, 22, 44], money_cols=(5,), wrap_cols=(4, 9), first=["เลขที่", "ผู้ขาย", "ยอดรวม"])

    def links(self):
        rows = []
        for n, m in enumerate(self.c["match"]["links"], start=1):
            def join(rs, f):
                return "\n".join(str(f(r)) for r in rs)
            state = STATE_TH[m["state"]] if m["state"] == "linked" or m["decision"] is None else "ข้ามคำถามไว้ รอคนตัดสิน"
            rows.append([n, KIND_TH[m["kind"]], m["document"], state, m["pass_label"], m["tier_label"],
                         who(m["confirmed"], m["confirmed_by"]) if m["state"] == "linked" else "",
                         join(m["left"], lambda r: f"{r['ref']} {r['name']}".strip()), join(m["left"], _where),
                         join(m["right"], lambda r: f"{r['ref']} {r['name']}".strip()), join(m["right"], _where),
                         _num(m["similarity"])])
        for sheet, sub in ((S_LINKS, "ทุกคู่บอกวิธีที่จับได้ ขั้น และผู้ยืนยัน · ขั้นที่ไม่ใช่ “ตรงกันทุกตัว” ต้องมีคนยืนยันเสมอ"),
                           (S_LINKS_EV, f"หลักฐานของแต่ละคู่ในชีต “{S_LINKS}” อ่านเทียบกันตามลำดับ · ไฟล์ แผ่น และแถวของทั้งสองข้าง")):
            ws = self.wb.create_sheet(sheet)
            self._title(ws, sheet, sub)
            self._part(ws, H_LINKS, rows, LAYOUT[sheet], wrap_cols=(4, 5, 6, 7, 8, 9, 10, 11))

    def exceptions(self):
        ws = self.wb.create_sheet(S_EXC)
        self._title(ws, "ข้อยกเว้น", "สิ่งที่คนต้องตัดสิน · ทุกข้อมีทางเลือก และไม่มีข้อใดถูกตัดสินแทนคน")
        rows = []
        for n, e in enumerate(self.c["run_exceptions"], start=1):
            src = "; ".join(_where(s) for s in (e["source_ref"].get("rows") or []) if s)
            rows.append([n, e["reason"], e["detail"], e["proposed_action"], " / ".join(ch["label"] for ch in e["choices"]), src])
        self._table(ws, 4, ["ลำดับ", "เหตุผล", "รายละเอียด", "สิ่งที่เสนอให้ทำ", "ทางเลือก", "หลักฐาน"], rows, [6, 26, 50, 30, 40, 40],
                    wrap_cols=(2, 3, 4, 5, 6))

    def evidence(self):
        ws = self.wb.create_sheet(S_EVIDENCE)
        c = self.c
        self._title(ws, "หลักฐาน", "ไฟล์ที่ใช้ ลายนิ้วมือของไฟล์ ค่าที่ตั้งไว้ และขั้นของการจับคู่")
        kinds = {"po": "ใบสั่งซื้อ", "gr": "ใบรับของ", "invoice": "ใบแจ้งหนี้"}
        rows = [[kinds.get(f["kind"], f["kind"]), f["name"], f["sheet"] or "", f["header_row"], f["rows"],
                 ", ".join(f"{k}={v}" for k, v in sorted((f["columns"] or {}).items())), " · ".join(f["said_by"]), f["sha256"]]
                for f in c["files"]]
        self._table(ws, 4, ["ใช้เป็น", "ไฟล์", "แผ่น", "แถวหัวตาราง", "จำนวนรายการ", "คอลัมน์ที่อ่าน", "รู้ได้จาก", "SHA-256"], rows,
                    [18, 30, 16, 10, 10, 40, 30, 36], wrap_cols=(2, 3, 6, 7, 8), freeze=False)
        r = 4 + len(rows) + 2
        self._section(ws, r, "ค่าที่ตั้งไว้")
        srows = [[c["practice"]["question"], c["practice"]["label"], ""]]
        srows += [[self.labels[k], v, "ค่าที่ตั้งไว้ใช้ไม่ได้ จึงใช้ค่าตั้งต้น" if k in c["settings"]["unusable"] else ""]
                  for k, v in c["settings"]["tolerances"].items()]
        srows += [["การแปลงหน่วยนับ", f"1 {x['from']} = {x['factor']} {x['to']}" + (f" (เฉพาะ {x['item']})" if x.get("item") else ""),
                   "คุณตอบในรอบนี้" if x.get("answered") else "ตั้งไว้ใน agent"] for x in c["settings"]["conversions"]]
        self._table(ws, r + 1, ["ค่า", "ที่ใช้ในรอบนี้", "หมายเหตุ"], srows, freeze=False)
        r += len(srows) + 3
        self._section(ws, r, "ขั้นของการจับคู่")
        trows = [[t["label"], t["groups"], t["by_rule"], t["by_person"], t["waiting"]] for t in c["match"]["tiers"]]
        self._table(ws, r + 1, ["ขั้น", "คู่ที่จับได้", "ตามกฎ", "คนยืนยัน", "รอคนยืนยัน"], trows, freeze=False)
        r += len(trows) + 3
        prows = [[p["label"], "จับคู่เอง" if p["auto"] else "เสนอให้คนยืนยัน", p["by_rule"] + p["by_person"], p["waiting"]]
                 for p in c["match"]["passes"]]
        self._table(ws, r + 1, ["วิธีจับคู่ (ตามลำดับ)", "ผล", "คู่ที่จับได้", "รอคนยืนยัน"], prows, freeze=False)

    def left_out(self):
        ws = self.wb.create_sheet(S_LEFT)
        self._title(ws, S_LEFT, "ไฟล์ของรอบนี้ที่ไม่ได้นำมาตรวจ เหตุผล และวิธีนำกลับมารวม")
        rows = [[e["name"], e["reason"], e["detail"], e["how"]] for e in self.c["not_included"]]
        self._table(ws, 4, ["ไฟล์", "เหตุผล", "รายละเอียด", "นำกลับมารวมได้อย่างไร"], rows, [40, 40, 60, 50], wrap_cols=(2, 3, 4))

    def write(self, path: str) -> dict:
        self.summary()
        self.invoices()
        self.lines()
        self.po()
        self.grir()
        self.unplanned()
        self.notes()
        self.links()
        self.exceptions()
        self.evidence()
        if self.c["not_included"]:
            self.left_out()
        self.wb.properties.creator = "Agent Market · invoice and purchase order matching"
        # AM8: printable — the face on one page; the columns that say which invoice and line a row is repeat on
        # every page beside its figures (AM10: and open every companion sheet of a wide table)
        id_cols = {s: ID_COLS[main] for main, names in PARTS.items() for s in names}
        workpaper.setup_workbook(self.wb, doc_name=os.path.basename(path),
                                 id_cols={**id_cols, S_NOTES: 2, S_UNPLANNED: 2, S_GRIR: 3, S_EXC: 2, S_EVIDENCE: 2, S_LEFT: 1})
        self.wb.save(path)
        t = self.c["totals"]
        return {"sheets": list(self.wb.sheetnames), "invoices": t["invoices"], "passed": t["passed"], "blocked": t["blocked"],
                "person": t["person"], "three_way": t["three_way"], "two_way": t["two_way"], "run_exceptions": t["run_exceptions"]}


def _labelled(ws) -> dict:
    out = {}
    for row in ws.iter_rows(min_row=1, max_col=2, values_only=True):
        if row and isinstance(row[0], str) and row[0] not in out:
            out[row[0]] = row[1]
    return out


def _rows(ws, top: int = 5) -> list:
    return [r for r in ws.iter_rows(min_row=top, values_only=True) if r and r[0] is not None]


def _same(cell, v) -> bool:
    if v is None:
        return cell is None
    return isinstance(cell, (int, float)) and abs(Decimal(str(cell)) - Decimal(str(v))) <= Decimal("0.000001")


def check(path: str, computed: dict, run: dict | None = None) -> list:
    """The gate on the file itself (AM8: read back): the header holds the real company and preparer (or the
    paper says it is a draft for want of them); what the working paper shows is what was computed, every
    invoice's amounts and every line's figures; the print setup is there."""
    def ck(name, ok, detail, label):
        return {"name": name, "ok": bool(ok), "detail": detail, "label": label}

    wb = load_workbook(path)
    t, s = computed["totals"], computed["summary"]
    out = [ck("xlsx.sheets", set(SHEETS) <= set(wb.sheetnames), ", ".join(wb.sheetnames), "ชีตครบ")]
    face = _labelled(wb[S_SUMMARY])
    a3 = str(wb[S_SUMMARY]["A3"].value or "")
    h = Tally()
    for label, want in header_fields(computed, run):
        got = str(face.get(label) or "")
        h.n += 1
        if want and not got.startswith(want):
            h.bad.append(f"{label}: ในไฟล์ “{got[:40]}” ควรเป็น {want}")
        elif not want and (got != MISSING or "หัวกระดาษทำการยังไม่ครบ" not in a3):
            h.bad.append(f"{label}: ไม่มีค่าจริง แต่ในไฟล์เขียนว่า “{got}” และไม่ได้บอกว่าเป็นร่าง")
    for k in (L_PERIOD, L_PRACTICE, L_DEPTH, L_APPROVAL):
        h.n += 1
        if not face.get(k):
            h.bad.append(f"{k}: ว่าง")
    out.append(h.result("xlsx.header", "หน้าสรุประบุนิติบุคคลและผู้จัดทำจริง (หรือบอกว่าเป็นร่าง) งวด ใบรับของ วิธีตรวจ และการอนุมัติจ่าย",
                        "หัวกระดาษทำการ"))
    depth = str(face.get(L_DEPTH) or "")
    says = f"ตรวจ 3 ทาง {t['three_way']} บรรทัด" in depth and f"ตรวจ 2 ทาง {t['two_way']} บรรทัด" in depth
    warned = any(r and r[0] == TWO_WAY_WARNING for r in wb[S_SUMMARY].iter_rows(min_row=1, max_col=1, values_only=True))
    out.append(ck("xlsx.depth_on_face", says and warned == (t["two_way"] > 0), depth,
                  "หน้าสรุปบอกจำนวนบรรทัดที่ตรวจ 3 ทางและ 2 ทาง และเตือนเมื่อมีบรรทัดที่ตรวจ 2 ทาง"))
    ok = (_same(face.get(L_THREE), t["three_way"]) and _same(face.get(L_TWO), t["two_way"]) and _same(face.get(L_INVOICES), t["invoices"])
          and _same(face.get(L_ACCRUED), t["received_not_billed"]) and _same(face.get(L_PREPAID), t["billed_not_received"]))
    for x in s["by_status"]:
        ok = ok and _same(face.get(x["label"]), x["count"])
    out.append(ck("xlsx.summary", ok, f"{face.get(L_INVOICES)} ฉบับ", "ตัวเลขในหน้าสรุปตรงกับที่คำนวณ"))
    inv = table(parts(wb, S_INVOICES), H_INVOICES)
    invoices = computed["invoices"]
    ok = len(inv) == len(invoices) and all(r[11] == i["status_label"] and r[13] == i["depth_text"] and
                                           (i["status"] == PASS or r[12]) and (i["total"] is None or _same(r[10], i["total"]))
                                           for r, i in zip(inv, invoices))
    out.append(ck("xlsx.invoices", ok, f"{len(inv)} แถว / {len(invoices)} ฉบับ", "ทุกใบแจ้งหนี้มีสถานะ วิธีตรวจ และเหตุผลเมื่อไม่ผ่าน"))
    ln = table(parts(wb, S_LINES), H_LINES)
    lines = computed["lines"]
    same = len(ln) == len(lines)
    depth_ok = same and all(r[8] == (x["depth_label"] if x["unplanned"] is None else "ค่าใช้จ่ายที่ไม่ได้สั่งซื้อ") and r[9]
                            for r, x in zip(ln, lines))
    out.append(ck("xlsx.line_depth", depth_ok, f"{len(ln)} แถว / {len(lines)} บรรทัด", "ทุกบรรทัดบอกว่าตรวจ 3 ทางหรือ 2 ทาง และเพราะอะไร"))
    linked = same and all((x["po_line"] is None) or (r[10] and r[14]) for r, x in zip(ln, lines))
    out.append(ck("xlsx.line_links", linked, "", "ทุกบรรทัดที่จับคู่แล้วชี้ไปที่บรรทัดของ PO พร้อมไฟล์ แผ่น และแถว"))
    three = same and all(r[15] for r, x in zip(ln, lines) if x["depth"] == THREE and x["unplanned"] is None)
    two = same and all(not r[15] for r, x in zip(ln, lines) if x["depth"] == TWO)
    out.append(ck("xlsx.line_receipts", three and two, "", "บรรทัดที่ตรวจ 3 ทางชี้ไปที่ใบรับของ บรรทัดที่ตรวจ 2 ทางไม่มีใบรับของ"))
    tol = same and all(r[25] for r, x in zip(ln, lines) if x["tolerances"])
    out.append(ck("xlsx.tolerances", tol, "", "ทุกบรรทัดบันทึกค่าที่ยอมรับได้ที่ใช้"))
    nums = same and all((x["amount"] is None or _same(r[7], x["amount"])) and (x["quantity"] is None or _same(r[4], x["quantity"]))
                        for r, x in zip(ln, lines))
    out.append(ck("xlsx.line_numbers", nums, "", "จำนวนและจำนวนเงินของทุกบรรทัดเป็นตัวเลขและตรงกับที่อ่านได้"))
    po = table(parts(wb, S_PO), H_PO)
    ok = len(po) == len(computed["po_lines"]) and all(_same(r[10], p["billed"]) and (p["received"] is None or _same(r[9], p["received"]))
                                                      and bool(r[13]) == p["over_billed"] for r, p in zip(po, computed["po_lines"]))
    out.append(ck("xlsx.cumulative", ok, f"{len(po)} บรรทัดของ PO", "ยอดสะสมของทุกบรรทัดของ PO ตรงกับที่คำนวณ และบอกบรรทัดที่เรียกเก็บเกิน"))
    ex = _rows(wb[S_EXC])
    run_ex = computed["run_exceptions"]
    ok = len(ex) == len(run_ex) and all(e["reason_code"] in REASON_TH and r[1] == e["reason"] and r[4] for r, e in zip(ex, run_ex))
    out.append(ck("xlsx.exceptions", ok, f"{len(ex)} รายการ", "ข้อยกเว้นครบ และทุกข้อมีทางเลือกให้คนตัดสิน"))
    lk = table(parts(wb, S_LINKS), H_LINKS)
    links = computed["match"]["links"]
    ok = len(lk) == len(links) and all(r[4] and r[5] and (m["state"] != "linked" or r[6]) for r, m in zip(lk, links))
    ok = ok and all(m["confirmed"] == "person" for m in links if m["state"] == "linked" and m["tier"] != "exact")
    out.append(ck("xlsx.links", ok, f"{len(lk)} คู่", "ทุกคู่บอกวิธีจับคู่ ขั้น และผู้ยืนยัน ขั้นที่ไม่ตรงกันทุกตัวมีคนยืนยัน"))
    approved = any("อนุมัติแล้ว" in str(c) or "จ่ายแล้ว" in str(c) for ws in wb.worksheets for r in ws.iter_rows(values_only=True) for c in r if c)
    out.append(ck("xlsx.no_approval", not approved and face.get(L_APPROVAL) == APPROVAL, "", "ไม่มีใบแจ้งหนี้ใดถูกระบุว่าอนุมัติหรือจ่ายแล้ว"))
    left = computed["not_included"]
    n = len(_rows(wb[S_LEFT])) if S_LEFT in wb.sheetnames else 0
    out.append(ck("xlsx.not_included", n == len(left), f"{n} ไฟล์", "ไฟล์ที่ไม่ได้ใช้อยู่ในรายการเอกสารที่ยังไม่ได้รวม"))
    f = Tally()
    for r, i in zip(inv, invoices):
        for col, head, key in ((8, "ยอดก่อนภาษี", "subtotal"), (9, "ภาษีมูลค่าเพิ่ม", "vat_amount"), (10, "ยอดรวม", "total")):
            f.number(f"ใบแจ้งหนี้ {i['number']} {head}", r[col], i[key])
    for r, x in zip(ln, lines):
        for col, head, key in ((4, "จำนวน", "quantity"), (6, "ราคาต่อหน่วย", "unit_price"), (7, "จำนวนเงิน", "amount")):
            f.number(f"บรรทัด {r[0]} ({r[1]}) {head}", r[col], x[key])
    out.append(f.result("xlsx.figures", "ยอดของทุกใบแจ้งหนี้และตัวเลขของทุกบรรทัด (อ่านจากไฟล์) ตรงกับที่คำนวณ", "ตัวเลข"))
    out.extend(workpaper.checks(path))
    return out
