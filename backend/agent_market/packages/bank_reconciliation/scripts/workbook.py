"""The bank reconciliation statement as .xlsx — the working paper a preparer hands to a reviewer.

Sheets: งบกระทบยอด (whose account, which period, who prepared and who reviewed, the balances of
both sides, every adjustment line, the two adjusted balances and whether they tie) · รายการกระทบยอด
(every reconciling item with its evidence) · รายการที่จับคู่ (every pair: the pass, the tier, who
confirmed it) · รายการน่าสงสัย · ร่างรายการปรับปรุง (drafts — nothing is posted) · อายุรายการค้าง ·
รายการค้างยกมา · รายการค้างยกไป (next month's opening list: send this sheet back next month) ·
รายการข้ามงวด · ข้อยกเว้น · หลักฐาน · เอกสารที่ยังไม่ได้รวม (when a file was not used).

Thai labels; amounts are numbers, not text; the quality gate reopens the file and checks it against
computed.json — the adjusted balances, the difference and the verdict on the face of the statement
are the computed ones, to the satang.
"""

from __future__ import annotations

import os
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ....deliverables import workpaper
from ....deliverables.reread import Tally, table
from ....engine.e3 import cards as E3C
from ....engine.e3.match import SETTING_TH
from ..checks import classify as C
from ..checks.inputs import CARRIED_SHEET
from .reconcile import OUTSTANDING, REASON_TH

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
# The tables' columns in the order the data is built and read back (AM9: printed, the row's identity and
# its amounts come first — ``workpaper.lead`` — and the checks read by these names, not by position)
H_ITEMS = ["ลำดับ", "ปรับยอดฝั่ง", "ประเภท", "วันที่", "เลขที่อ้างอิง", "รายการ", "จำนวนเงินตามไฟล์", "ผลต่อยอดหลังปรับปรุง",
           "อายุ (วัน)", "ช่วงอายุ", "อยู่ใน", "หลักฐาน", "ผู้จัดประเภท", "หมายเหตุ"]
H_PAIRS = ["คู่ที่", "ขั้น", "วิธีจับคู่", "รูปแบบ", "ผู้ยืนยัน", "วันที่ (บัญชี)", "รายการ (บัญชี)", "เลขอ้างอิง (บัญชี)",
           "จำนวนเงิน (บัญชี)", "หลักฐาน (บัญชี)", "วันที่ (ธนาคาร)", "รายการ (ธนาคาร)", "เลขอ้างอิง (ธนาคาร)",
           "จำนวนเงิน (ธนาคาร)", "หลักฐาน (ธนาคาร)", "ผลต่าง", "ห่างกัน (วัน)"]
H_SUSPECT = ["ลำดับ"] + H_PAIRS[1:2] + ["เหตุที่สงสัย", "รูปแบบ", "สถานะ"] + H_PAIRS[5:]
H_JOURNAL = ["เลขที่ร่าง", "วันที่", "คำอธิบาย", "บัญชี", "รหัสบัญชี", "เดบิต", "เครดิต", "หมายเหตุ", "มาจากรายการกระทบยอด"]
H_CARRIED_IN = ["วันที่", "เลขที่อ้างอิง", "รายการ", "จำนวนเงิน", "ประเภท", "สถานะ", "ธนาคารลงวันที่", "วิธีจับคู่", "อายุ (วัน)", "หลักฐาน"]
# what prints first on each table that runs across pages: the row's number and description, then its amounts
LEAD = {
    "items": ["ลำดับ", "รายการ", "จำนวนเงินตามไฟล์", "ผลต่อยอดหลังปรับปรุง"],
    "pairs": ["คู่ที่", "รายการ (บัญชี)", "จำนวนเงิน (บัญชี)", "รายการ (ธนาคาร)", "จำนวนเงิน (ธนาคาร)", "ผลต่าง"],
    "suspect": ["ลำดับ", "รายการ (บัญชี)", "จำนวนเงิน (บัญชี)", "รายการ (ธนาคาร)", "จำนวนเงิน (ธนาคาร)", "ผลต่าง"],
    "journal": ["เลขที่ร่าง", "บัญชี", "เดบิต", "เครดิต"],
    "carried_in": ["เลขที่อ้างอิง", "รายการ", "จำนวนเงิน"],
}
S_STATEMENT, S_ITEMS, S_PAIRS, S_SUSPECT, S_JOURNAL = "งบกระทบยอด", "รายการกระทบยอด", "รายการที่จับคู่", "รายการน่าสงสัย", "ร่างรายการปรับปรุง"
S_AGE, S_IN, S_OUT, S_CUTOFF, S_EXC, S_EVIDENCE, S_LEFT = ("อายุรายการค้าง", "รายการค้างยกมา", CARRIED_SHEET, "รายการข้ามงวด",
                                                           "ข้อยกเว้น", "หลักฐาน", "เอกสารที่ยังไม่ได้รวม")
SHEETS = (S_STATEMENT, S_ITEMS, S_PAIRS, S_SUSPECT, S_JOURNAL, S_AGE, S_IN, S_OUT, S_CUTOFF, S_EXC, S_EVIDENCE)
L_BANK_BAL, L_BOOK_BAL = "ยอดคงเหลือตามรายการเดินบัญชีของธนาคาร", "ยอดคงเหลือตามสมุดบัญชีของบริษัท"
L_BANK_ADJ, L_BOOK_ADJ = "ยอดหลังปรับปรุงของธนาคาร", "ยอดหลังปรับปรุงของบัญชี"
L_DIFF, L_VERDICT = "ผลต่าง", "ผลการกระทบยอด"
L_PREPARER, L_REVIEWER = "ผู้จัดทำ", "ผู้สอบทาน"
TIED, NOT_TIED = "ลงตัว", "ยังไม่ลงตัว"
DRAFT_MARK = "ร่าง — ยังไม่ลงตัว ห้ามใช้เป็นงบกระทบยอดที่เสร็จแล้ว"
# AM8: a header field with no value says so — no placeholder stands in for a company, an account or a
# preparer — and a paper missing one of them says on its face that it is not yet one to sign
MISSING = "— ยังไม่มี —"
HEADER_DRAFT = "ร่าง — หัวกระดาษทำการยังไม่ครบ ({}) ตั้งได้ที่การตั้งค่าของ agent นี้ ยังไม่ใช่กระดาษทำการที่ลงนามได้"
L_COMPANY, L_BANK, L_ACC_NAME, L_ACC_NO, L_GL = "นิติบุคคล", "ธนาคาร", "ชื่อบัญชี", "เลขที่บัญชี", "รหัสบัญชีแยกประเภท"
# true before a sign-off and after it: the stamp adds the sheet, it rewrites no other
NOT_REVIEWED = "ชื่อและวันที่ของผู้สอบทานอยู่ในชีต “ตรวจทาน” ซึ่งเพิ่มเมื่อผู้สอบทานลงชื่อ (ต้องเป็นคนละคนกับผู้จัดทำ)"
CONFIRMED_TH = {"rule": "กฎ (ตรงกันทุกตัว)", "person": "คนยืนยัน"}
DECIDED_TH = {"rule": "กฎ", "person": "คนตัดสิน"}
SEVERITY_TH = {"review": "รอคนตัดสิน", "info": "บันทึกไว้"}
FROM_TH = {"book": "สมุดบัญชี", "bank": "รายการเดินบัญชี", "carried": "รายการค้างยกมา"}
SIDE_TH = {"bank": "ปรับยอดฝั่งธนาคาร", "book": "ปรับยอดฝั่งบัญชี", None: "ยังไม่นับ"}
BALANCE_FROM = {"row": "แถวยอดยกมา/ยกไปในไฟล์", "balance_column": "คอลัมน์ยอดคงเหลือในไฟล์", "movement": "คำนวณจากรายการของงวด",
                "typed": "ผู้ใช้พิมพ์", "zero": "ยอดยกมาเป็นศูนย์", "unknown": "ไม่ทราบ", "": ""}


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def who(kind: str, name: str, words: dict) -> str:
    """ "คนยืนยัน: สมหญิง ผู้ตรวจ" — the person by name when the runner knew who answered the card."""
    return f"{words[kind]}: {name}" if kind == "person" and name else words[kind]


def _where(s: dict) -> str:
    cells = ", ".join(sorted(set((s.get("cells") or {}).values()))) if isinstance(s.get("cells"), dict) else (s.get("cell") or "")
    return " · ".join(str(x) for x in (s.get("file"), s.get("sheet"), f"แถว {s['row']}" if s.get("row") else "", cells) if x)


class BankWorkbook:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None):
        self.c, self.t, self.run = computed, theme, run or {}
        self.p = theme["palette"]
        self.font = theme["fonts"]["thai"]
        self.wb = Workbook()
        self.items = {i["id"]: i for i in computed["items"]}

    def header(self) -> list[tuple[str, str]]:
        """The face's header fields and their real values ("" when there is none)."""
        return header_fields(self.c, self.run)

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
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(name=self.font, bold=True, size=11, color=self.p["primary"])

    def _note(self, ws, r: int, text: str, cols: int = 4, height: int = 30):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(name=self.font, size=9, color=self.p["muted"])
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=cols)
        ws.row_dimensions[r].height = height

    # ── sheets ─────────────────────────────────────────────────────────────
    def statement(self):
        ws = self.wb.active
        ws.title = S_STATEMENT
        c, st, gate, acc = self.c, self.c["statement"], self.c["gate"], self.c["account"]
        self._title(ws, f"งบกระทบยอดเงินฝากธนาคาร · {c['period_label']}",
                    " · ".join(x for x in (c.get("company"), acc.get("bank"), acc.get("number")) if x))
        ws.column_dimensions["A"].width = 58
        ws.column_dimensions["B"].width = 44
        ws.column_dimensions["C"].width = 14
        ws.column_dimensions["D"].width = 40
        r = 4
        marks = draft_marks(self.c, self.run)
        if marks:
            m = ws.cell(row=3, column=1, value=" · ".join(marks))
            m.font = Font(name=self.font, bold=True, size=11, color=self.p.get("negative", "B42318"))
        made = E3C.date_th(self.run.get("date") or c["prepared_on"])
        head = dict(self.header())
        info = [(k, head[k] or MISSING) for k in (L_COMPANY, L_BANK, L_ACC_NAME, L_ACC_NO)]
        info += [(L_GL, acc.get("gl_code") or "ไม่มีรหัสบัญชีในไฟล์สมุดบัญชีและการตั้งค่า"), ("งวด", c["period_label"]),
                 ("ตัดยอด ณ วันที่", E3C.date_th(c["as_of"])),
                 (L_PREPARER, (f"{head[L_PREPARER]} · จัดทำวันที่ {made} · ด้วย agent รุ่น {self.run.get('agent_version') or ''}".rstrip(" ·"))
                  if head[L_PREPARER] else MISSING),
                 (L_REVIEWER, NOT_REVIEWED)]
        for label, value in info:
            self._pair(ws, r, label, value)
            r += 1
        r += 1
        self._section(ws, r, "ยอดยกมาและยอดคงเหลือ")
        r += 1
        b = c["balances"]
        for label, side, which in (("ยอดยกมาต้นงวดตามธนาคาร", "bank", "opening"), ("ยอดยกมาต้นงวดตามบัญชี", "book", "opening"),
                                   ("ยอดคงเหลือสิ้นงวดตามธนาคาร", "bank", "closing"), ("ยอดคงเหลือสิ้นงวดตามบัญชี", "book", "closing")):
            self._pair(ws, r, label, _num(b[side][which]) if b[side][which] is not None else "ไม่ทราบ", money=b[side][which] is not None)
            cell = b[side].get(f"{which}_cell") or {}
            src = ws.cell(row=r, column=4, value=" · ".join(x for x in (BALANCE_FROM.get(b[side][f"{which}_from"], ""), _where(cell)) if x))
            src.font = Font(name=self.font, size=9, color=self.p["muted"])
            # AM8: the evidence wraps inside its column — printed, it never runs past the page's edge
            src.alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
        for side, head, bal_label, adj_label in (("bank", "ฝั่งธนาคาร", L_BANK_BAL, L_BANK_ADJ), ("book", "ฝั่งบัญชี", L_BOOK_BAL, L_BOOK_ADJ)):
            r += 1
            self._section(ws, r, head)
            r += 1
            s = st[side]
            self._pair(ws, r, bal_label, _num(s["balance"]) if s["balance"] is not None else "ไม่ทราบ", money=s["balance"] is not None,
                       bold=True)
            r += 1
            for ln in s["lines"]:
                self._pair(ws, r, f"{ln['sign']}: {ln['label']}", _num(ln["amount"]), money=True)
                ws.cell(row=r, column=3, value=f"{ln['count']} รายการ").font = Font(name=self.font, size=9, color=self.p["muted"])
                r += 1
            if not s["lines"]:
                self._pair(ws, r, "ไม่มีรายการปรับปรุงฝั่งนี้", None)
                r += 1
            self._pair(ws, r, adj_label, _num(s["adjusted"]) if s["adjusted"] is not None else "ไม่ทราบ", money=s["adjusted"] is not None,
                       bold=True, color=self.p["primary"])
            r += 1
        r += 1
        self._pair(ws, r, L_DIFF, _num(st["difference"]) if st["difference"] is not None else "ไม่ทราบ",
                   money=st["difference"] is not None, bold=True)
        r += 1
        self._pair(ws, r, L_VERDICT, TIED if gate["passed"] else gate["what"], bold=True,
                   color=self.p.get("positive", "067647") if gate["passed"] else self.p.get("negative", "B42318"))
        r += 2
        if not gate["passed"]:
            self._section(ws, r, "ทำไมยังไม่ลงตัว และควรทำอะไรต่อ")
            r += 1
            for why in gate["reasons"]:
                self._note(ws, r, f"• {why}")
                r += 1
            for step in gate.get("next_steps") or [gate["next"]]:
                self._note(ws, r, f"→ {step}")
                r += 1
            r += 1
        t = c["totals"]
        notes = [
            f"จับคู่ได้ {t['matched']} คู่: ตามกฎ {t['by_rule']} คู่ (ตรงกันทุกตัว) · คนยืนยัน {t['by_person']} คู่ · "
            f"รายการกระทบยอด {t['items']} รายการ · น่าสงสัย {t['suspect']} รายการ",
            "ตัวเลขทุกตัวมาจากไฟล์ที่ส่งมาและคำนวณด้วยสคริปต์ การจับคู่ที่ไม่ตรงกันทุกตัวต้องมีคนยืนยัน "
            "ไม่มีโมเดลคำนวณตัวเลขหรือตัดสินการจับคู่ในไฟล์นี้",
            "ร่างรายการปรับปรุงเป็นร่างให้ตรวจ agent ไม่ได้บันทึกอะไรเข้าระบบบัญชี",
        ] + [a["text"] for a in c.get("assumptions") or [] if a["code"] in (
            "period", "gl_account", "tolerance", "cut_off", "rows_without_date", "no_carried_items", "setting_not_usable",
            "bank_closing_from_movement", "book_closing_from_movement", "bank_closing_typed", "book_closing_typed")]
        for n in notes:
            self._note(ws, r, n)
            r += 1

    def item_rows(self, items: list[dict]) -> list[list]:
        rows = []
        for n, i in enumerate(items, start=1):
            rows.append([n, SIDE_TH[i["side"]], i["type_label"], E3C.date_th(i["date"]), i["ref"], i["description"],
                         _num(i["amount"]), _num(i["effect"]), i["age_days"], i["bucket_label"], FROM_TH[i["from"]],
                         "; ".join(_where(s) for s in i["sources"]), who(i["decided_by"], i.get("decided_by_name") or "", DECIDED_TH),
                         i["note"]])
        return rows

    def reconciling(self):
        ws = self.wb.create_sheet(S_ITEMS)
        self._title(ws, "รายการกระทบยอดทุกรายการ", "รายการที่อยู่ฝั่งเดียว พร้อมหลักฐานว่าอ่านจากไฟล์ แผ่น และแถวใด")
        self._table(ws, 4, H_ITEMS, self.item_rows(self.c["items"]), [7, 18, 30, 14, 16, 44, 18, 20, 10, 14, 16, 50, 14, 40],
                    money_cols=(7, 8), wrap_cols=(6, 12, 14), first=LEAD["items"])

    def pairs(self):
        ws = self.wb.create_sheet(S_PAIRS)
        self._title(ws, "รายการที่จับคู่แล้ว", "ทุกคู่บอกขั้นที่จับได้และผู้ยืนยัน · ขั้นที่ไม่ใช่ “ตรงกันทุกตัว” ต้องมีคนยืนยันเสมอ")
        self._table(ws, 4, H_PAIRS, [self._pair_row(n, m, who(m["confirmed"], m.get("confirmed_by") or "", CONFIRMED_TH)) for n, m in
                                     enumerate(self.c["match"]["groups"]["matched"], start=1)],
                    [7, 18, 32, 9, 16, 14, 36, 16, 16, 40, 14, 36, 16, 16, 40, 12, 10], money_cols=(9, 14, 16), wrap_cols=(7, 10, 12, 15),
                    first=LEAD["pairs"])

    def _pair_row(self, n: int, m: dict, who: str) -> list:
        def join(rows, f):
            return "\n".join(str(f(r)) for r in rows)
        l, r = m["left"], m["right"]
        return [n, m.get("tier_label") or "", m.get("pass_label") or "", m.get("shape") or "", who,
                join(l, lambda x: E3C.date_th(x["date"])), join(l, lambda x: x["name"]), join(l, lambda x: x["ref"]),
                _num(m["left_total"]), join(l, _where),
                join(r, lambda x: E3C.date_th(x["date"])), join(r, lambda x: x["name"]), join(r, lambda x: x["ref"]),
                _num(m["right_total"]), join(r, _where),
                _num(m.get("difference")) if m.get("difference") is not None else None, m.get("days_apart")]

    def suspects(self):
        ws = self.wb.create_sheet(S_SUSPECT)
        self._title(ws, "รายการน่าสงสัย", "คู่ที่เสนอแต่ยังไม่มีคนยืนยัน และรายการที่อาจบันทึกซ้ำ · ยังไม่นับเป็นรายการกระทบยอดของฝั่งใด")
        rows = []
        for n, s in enumerate(self.c["match"]["groups"]["suspect"], start=1):
            if s["kind"] == "possible_duplicate":
                s = dict(s, tier_label="", pass_label="เลขอ้างอิงและยอดเดียวกับรายการที่จับคู่ไปแล้ว อาจบันทึกซ้ำ")
                who = "รอคนตรวจ"
            else:
                who = "ข้ามคำถามไว้ รอคนตัดสิน" if s["decision"] == "undecided" else "ยังไม่ได้ถาม"
            rows.append(self._pair_row(n, s, who))
        self._table(ws, 4, H_SUSPECT, rows, [7, 18, 36, 9, 22, 14, 36, 16, 16, 40, 14, 36, 16, 16, 40, 12, 10],
                    money_cols=(9, 14, 16), wrap_cols=(3, 7, 10, 12, 15), first=LEAD["suspect"])

    def journal(self):
        ws = self.wb.create_sheet(S_JOURNAL)
        self._title(ws, "ร่างรายการปรับปรุง (ฝั่งบัญชี)", "ร่างให้ตรวจและบันทึกเอง · agent ไม่ได้บันทึกอะไรเข้าระบบบัญชี · ทุกรายการเดบิตเท่ากับเครดิต")
        rows = []
        for j in self.c["journal"]:
            for k, ln in enumerate(j["lines"]):
                rows.append([j["id"], E3C.date_th(j["date"]), j["memo"] if k == 0 else "", ln["account"], ln["code"],
                             _num(ln["debit"]) or None, _num(ln["credit"]) or None, j["note"] if k == 0 else "",
                             ", ".join(j["items"]) if k == 0 else ""])
        self._table(ws, 4, H_JOURNAL, rows, [12, 14, 50, 44, 14, 16, 16, 50, 22], money_cols=(6, 7), wrap_cols=(3, 4, 8),
                    first=LEAD["journal"])

    def ageing(self):
        ws = self.wb.create_sheet(S_AGE)
        a = self.c["ageing"]
        self._title(ws, "อายุของรายการค้าง", f"นับถึงวันตัดยอด {E3C.date_th(self.c['as_of'])} · เช็คค้างเกิน {a['stale_days']} วันถือว่าหมดอายุ")
        self._table(ws, 4, ["ช่วงอายุ", "จำนวนรายการ", "จำนวนเงิน"], [[b["label"], b["count"], _num(b["amount"])] for b in a["buckets"]],
                    [24, 16, 20], money_cols=(3,), freeze=False)
        r = 4 + len(a["buckets"]) + 2
        self._section(ws, r, "รายการค้างเกิน 30 วัน และเช็คที่หมดอายุ")
        old = [i for i in self.c["items"] if i["type"] in OUTSTANDING and i["age_days"] > 30]
        rows = [[C.TYPE_TH[i["type"]], E3C.date_th(i["date"]), i["ref"], i["description"], _num(abs(Decimal(i["amount"]))), i["age_days"],
                 i["bucket_label"], "เช็คหมดอายุ" if i["id"] in a["stale"] else "", "; ".join(_where(s) for s in i["sources"])] for i in old]
        self._table(ws, r + 1, ["ประเภท", "วันที่", "เลขที่อ้างอิง", "รายการ", "จำนวนเงิน", "อายุ (วัน)", "ช่วงอายุ", "สถานะ", "หลักฐาน"], rows,
                    [24, 16, 20, 44, 18, 12, 16, 16, 50], money_cols=(5,), wrap_cols=(4, 9), freeze=False)

    def carried_in(self):
        ws = self.wb.create_sheet(S_IN)
        self._title(ws, "รายการค้างยกมาจากเดือนก่อน", "รายการที่ธนาคารลงแล้วในงวดนี้ และรายการที่ยังค้างอยู่")
        rows = [[E3C.date_th(i["date"]), i["ref"], i["description"], _num(abs(Decimal(i["amount"]))), i["kind_label"],
                 "ธนาคารลงรายการแล้ว" if i["cleared"] else "ยังค้างอยู่", E3C.date_th(i["cleared_on"]) if i["cleared_on"] else "",
                 i["pass"] or "", i["age_days"], _where(i)] for i in self.c["carried_in"]]
        self._table(ws, 4, H_CARRIED_IN, rows, [14, 18, 44, 18, 26, 22, 16, 32, 12, 50], money_cols=(4,), wrap_cols=(3, 10),
                    first=LEAD["carried_in"])

    def carried_out(self):
        """Next month's opening list. Its columns are the ones checks/inputs.py reads back."""
        ws = self.wb.create_sheet(S_OUT)
        self._title(ws, CARRIED_SHEET, f"รายการที่ยังค้าง ณ {E3C.date_th(self.c['as_of'])} · ส่งไฟล์นี้กลับมาพร้อมไฟล์ของเดือนถัดไป agent จะอ่านชีตนี้เอง")
        rows = [[E3C.date_th(i["date"]), i["ref"], i["description"], _num(abs(Decimal(i["amount"]))), i["type_label"]]
                for i in (self.items[k] for k in self.c["carried_out"])]
        self._table(ws, 4, ["วันที่", "เลขที่อ้างอิง", "รายการ", "จำนวนเงิน", "ประเภท"], rows, [16, 20, 50, 18, 34], money_cols=(4,),
                    wrap_cols=(3,))

    def cutoff(self):
        ws = self.wb.create_sheet(S_CUTOFF)
        c = self.c
        self._title(ws, "รายการข้ามงวด", f"รายการที่ลงวันที่นอกงวด {c['period_label']} ไม่นำมาจับคู่ · คู่ที่อยู่คนละฝั่งของวันตัดยอดอยู่ในชีตข้อยกเว้น")
        rows = [[FROM_TH[x["side"]], E3C.date_th(x["date"]), x["ref"], x["description"], _num(x["amount"]),
                 "ก่อนงวด" if x["date"] < c["period_start"] else "หลังวันตัดยอด", _where(x)] for x in c["cutoff"]["rows"]]
        self._table(ws, 4, ["อยู่ใน", "วันที่", "เลขที่อ้างอิง", "รายการ", "จำนวนเงิน", "ตำแหน่ง", "หลักฐาน"], rows,
                    [18, 14, 18, 44, 18, 16, 50], money_cols=(5,), wrap_cols=(4, 7), first=["เลขที่อ้างอิง", "รายการ", "จำนวนเงิน"])

    def exceptions(self):
        ws = self.wb.create_sheet(S_EXC)
        self._title(ws, "ข้อยกเว้น", "สิ่งที่คนต้องตัดสินหรือควรรู้ · ข้อที่รอคนตัดสินต้องตัดสินให้ครบก่อนสอบทาน")
        rows = []
        for n, e in enumerate(self.c["run_exceptions"], start=1):
            src = "; ".join(_where(s) for s in (e["source_ref"].get("rows") or []))
            rows.append([n, e["reason"], SEVERITY_TH[e["severity"]], e["detail"], e["proposed_action"],
                         " / ".join(ch["label"] for ch in e["choices"]), src])
        self._table(ws, 4, ["ลำดับ", "เรื่อง", "สถานะ", "รายละเอียด", "สิ่งที่เสนอให้ทำ", "ทางเลือก", "หลักฐาน"], rows,
                    [7, 32, 14, 60, 44, 40, 50], wrap_cols=(4, 5, 6, 7))

    def evidence(self):
        ws = self.wb.create_sheet(S_EVIDENCE)
        c = self.c
        self._title(ws, "หลักฐาน", "ไฟล์ที่ใช้ ลายนิ้วมือของไฟล์ และค่าที่ตั้งไว้ในการจับคู่")
        rows = [[FROM_TH.get(f["side"], f["side"]), f["name"], f["sheet"], f["header_row"], f["rows"],
                 ", ".join(f"{k}={v}" for k, v in sorted((f["columns"] or {}).items())), f["sha256"]] for f in c["files"]]
        self._table(ws, 4, ["ใช้เป็น", "ไฟล์", "แผ่น", "แถวหัวตาราง", "จำนวนรายการ", "คอลัมน์ที่อ่าน", "SHA-256"], rows,
                    [18, 40, 22, 12, 14, 60, 70], wrap_cols=(6,), freeze=False)
        r = 4 + len(rows) + 2
        self._section(ws, r, "ค่าที่ตั้งไว้ในการจับคู่")
        m = c["settings"]["matching"]
        srows = [[SETTING_TH[k], ("ใช่" if v else "ไม่") if isinstance(v, bool) else v,
                  "ค่าที่ตั้งไว้ใช้ไม่ได้ จึงใช้ค่าตั้งต้น" if k in c["settings"]["unusable"] else ""] for k, v in m.items()]
        self._table(ws, r + 1, ["ค่า", "ที่ใช้ในรอบนี้", "หมายเหตุ"], srows, freeze=False)
        r += len(srows) + 3
        self._section(ws, r, "ขั้นของการจับคู่")
        trows = [[t["label"], t["groups"], t["by_rule"], t["by_person"], t["waiting"]] for t in c["match"]["tiers"]]
        self._table(ws, r + 1, ["ขั้น", "คู่ที่จับได้", "ตามกฎ", "คนยืนยัน", "รอคนยืนยัน"], trows, freeze=False)
        r += len(trows) + 3
        prows = [[p["label"], p["tier_label"], "จับคู่เอง" if p["auto"] else "เสนอให้คนยืนยัน", p["by_rule"] + p["by_person"], p["waiting"]]
                 for p in c["match"]["passes"]]
        self._table(ws, r + 1, ["วิธีจับคู่ (ตามลำดับ)", "ขั้น", "ผล", "คู่ที่จับได้", "รอคนยืนยัน"], prows, freeze=False)

    def left_out(self):
        ws = self.wb.create_sheet(S_LEFT)
        self._title(ws, S_LEFT, "ไฟล์ของรอบนี้ที่ไม่ได้นำมากระทบยอด เหตุผล และวิธีนำกลับมารวม")
        rows = [[e["name"], e["reason"], e["detail"], e["how"]] for e in self.c["not_included"]]
        self._table(ws, 4, ["ไฟล์", "เหตุผล", "รายละเอียด", "นำกลับมารวมได้อย่างไร"], rows, [40, 40, 60, 50], wrap_cols=(2, 3, 4))

    def write(self, path: str) -> dict:
        self.statement()
        self.reconciling()
        self.pairs()
        self.suspects()
        self.journal()
        self.ageing()
        self.carried_in()
        self.carried_out()
        self.cutoff()
        self.exceptions()
        self.evidence()
        if self.c["not_included"]:
            self.left_out()
        self.wb.properties.creator = "Agent Market · bank reconciliation"
        # AM8: printable — the face on one page; a row's identifier on the page of its amounts
        workpaper.setup_workbook(self.wb, doc_name=os.path.basename(path),
                                 id_cols={S_ITEMS: 2, S_PAIRS: 2, S_SUSPECT: 2, S_JOURNAL: 2, S_IN: 2, S_AGE: 1, S_OUT: 3,
                                          S_CUTOFF: 2, S_EXC: 2, S_EVIDENCE: 2, S_LEFT: 1})
        self.wb.save(path)
        t = self.c["totals"]
        return {"sheets": list(self.wb.sheetnames), "tied": self.c["statement"]["tied"], "difference": self.c["statement"]["difference"],
                "matched": t["matched"], "items": t["items"], "run_exceptions": t["run_exceptions"], "draft": not self.c["gate"]["passed"]}


def header_fields(c: dict, run: dict | None) -> list[tuple[str, str]]:
    """The fields a working paper's header must hold, with their real values ("" when there is none):
    the company (the install's, else the organisation's name), the account, the preparer."""
    acc = c.get("account") or {}
    run = run or {}
    return [(L_COMPANY, str(c.get("company") or run.get("organisation") or "").strip()),
            (L_BANK, str(acc.get("bank") or "").strip()), (L_ACC_NAME, str(acc.get("name") or "").strip()),
            (L_ACC_NO, str(acc.get("number") or "").strip()), (L_PREPARER, str(run.get("preparer") or "").strip())]


def draft_marks(c: dict, run: dict | None) -> list[str]:
    marks = [] if c["gate"]["passed"] else [DRAFT_MARK]
    missing = [k for k, v in header_fields(c, run) if not v]
    if missing:
        marks.append(HEADER_DRAFT.format(", ".join(missing)))
    return marks


def _labelled(ws) -> dict:
    out = {}
    for row in ws.iter_rows(min_row=1, max_col=2, values_only=True):
        if row and isinstance(row[0], str) and row[0] not in out:
            out[row[0]] = row[1]
    return out


def _same(cell, v) -> bool:
    if v is None:
        return cell == "ไม่ทราบ"
    return isinstance(cell, (int, float)) and Decimal(str(cell)).quantize(Decimal("0.01")) == Decimal(v)


def check(path: str, computed: dict, run: dict | None = None) -> list[dict]:
    """The gate on the file itself (AM8: read back, never taken from the writer): the header holds the
    real company, account and preparer — or the paper says on its face that it is a draft for want of
    them; every balance, adjustment line, reconciling item and pair amount is the computed one; the
    print setup is there."""
    def ck(name, ok, detail, label):
        return {"name": name, "ok": bool(ok), "detail": detail, "label": label}

    wb = load_workbook(path)
    st, gate = computed["statement"], computed["gate"]
    out = [ck("xlsx.sheets", set(SHEETS) <= set(wb.sheetnames), ", ".join(wb.sheetnames), "ชีตครบ")]
    face = _labelled(wb[S_STATEMENT])
    a3 = str(wb[S_STATEMENT]["A3"].value or "")
    t = Tally()
    for label, want in header_fields(computed, run):
        got = str(face.get(label) or "")
        if label == L_PREPARER:
            t.n += 1
            if want and not got.startswith(want):
                t.bad.append(f"{label}: ในไฟล์ “{got[:40]}” ควรเป็น {want}")
            elif not want and (got != MISSING or "หัวกระดาษทำการยังไม่ครบ" not in a3):
                t.bad.append(f"{label}: ไม่มีชื่อผู้จัดทำ แต่กระดาษทำการไม่ได้บอกว่าเป็นร่าง")
        elif want:
            t.text(label, got, want)
        else:
            t.n += 1
            if got != MISSING or "หัวกระดาษทำการยังไม่ครบ" not in a3:
                t.bad.append(f"{label}: ไม่มีค่าจริง แต่ในไฟล์เขียนว่า “{got}” และไม่ได้บอกว่าเป็นร่าง")
    for k in ("งวด", "ตัดยอด ณ วันที่", L_REVIEWER):
        t.n += 1
        if not face.get(k):
            t.bad.append(f"{k}: ว่าง")
    out.append(t.result("xlsx.header", "หัวงบระบุนิติบุคคล บัญชี งวด และผู้จัดทำจริง (หรือบอกว่าเป็นร่าง)", "หัวกระดาษทำการ"))
    ok = (_same(face.get(L_BANK_BAL), st["bank"]["balance"]) and _same(face.get(L_BOOK_BAL), st["book"]["balance"])
          and _same(face.get(L_BANK_ADJ), st["bank"]["adjusted"]) and _same(face.get(L_BOOK_ADJ), st["book"]["adjusted"])
          and _same(face.get(L_DIFF), st["difference"]))
    out.append(ck("xlsx.statement", ok, f"{face.get(L_BANK_ADJ)} / {face.get(L_BOOK_ADJ)}", "ยอดในงบตรงกับที่คำนวณ"))
    verdict = str(face.get(L_VERDICT) or "")
    says = verdict == TIED if gate["passed"] else verdict.startswith(NOT_TIED)
    marked = DRAFT_MARK in a3
    out.append(ck("xlsx.verdict", says and marked == (not gate["passed"]), verdict, "งบบอกตรงตามจริงว่าลงตัวหรือยังไม่ลงตัว"))
    rows = table(wb[S_ITEMS], H_ITEMS)
    items = computed["items"]
    same = len(rows) == len(items) and all(isinstance(r[6], (int, float)) and _same(r[6], i["amount"]) and r[11] for r, i in zip(rows, items))
    out.append(ck("xlsx.items", same, f"{len(rows)} แถว / {len(items)} รายการ", "รายการกระทบยอดครบ เป็นตัวเลข และมีหลักฐานทุกรายการ"))
    for side, label in (("bank", "ฝั่งธนาคาร"), ("book", "ฝั่งบัญชี")):
        got = sum((Decimal(str(r[7])) for r, i in zip(rows, items) if i["side"] == side and r[7] is not None), Decimal(0))
        out.append(ck(f"xlsx.adjustments_{side}", same and got.quantize(Decimal("0.01")) == Decimal(st[side]["adjustments"]),
                      f"{got} / {st[side]['adjustments']}", f"รายการปรับปรุง{label}รวมแล้วเท่ากับในงบ"))
    pairs = table(wb[S_PAIRS], H_PAIRS)
    matched = computed["match"]["groups"]["matched"]
    ok = len(pairs) == len(matched) and all(r[1] and r[2] and r[4] == who(m["confirmed"], m.get("confirmed_by") or "", CONFIRMED_TH)
                                            for r, m in zip(pairs, matched))
    ok = ok and all(m["confirmed"] == "person" for m in matched if m["tier"] != "exact")
    out.append(ck("xlsx.pairs", ok, f"{len(pairs)} คู่", "ทุกคู่บอกขั้น วิธีจับคู่ และผู้ยืนยัน ขั้นที่ไม่ตรงกันทุกตัวมีคนยืนยัน"))
    sus = table(wb[S_SUSPECT], H_SUSPECT)
    out.append(ck("xlsx.suspect", len(sus) == len(computed["match"]["groups"]["suspect"]), f"{len(sus)} รายการ", "รายการน่าสงสัยครบ"))
    jr = table(wb[S_JOURNAL], H_JOURNAL)
    by: dict = {}
    for r in jr:
        d = by.setdefault(r[0], [Decimal(0), Decimal(0)])
        d[0] += Decimal(str(r[5] or 0))
        d[1] += Decimal(str(r[6] or 0))
    ok = len(by) == len(computed["journal"]) and all(d[0].quantize(Decimal("0.01")) == d[1].quantize(Decimal("0.01")) and d[0] > 0
                                                      for d in by.values())
    out.append(ck("xlsx.journal_balances", ok, f"{len(by)} รายการ", "ร่างรายการปรับปรุงทุกรายการ เดบิตเท่ากับเครดิต"))
    co = [r for r in wb[S_OUT].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.carried_out", len(co) == len(computed["carried_out"]) and all(isinstance(r[3], (int, float)) and r[4] for r in co),
                  f"{len(co)} รายการ", "รายการค้างยกไปครบ พร้อมใช้เป็นรายการค้างยกมาของเดือนหน้า"))
    ex = [r for r in wb[S_EXC].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    run_ex = computed["run_exceptions"]
    ok = len(ex) == len(run_ex) and all(e["reason_code"] in REASON_TH and r[1] == e["reason"] for r, e in zip(ex, run_ex)) and \
        all(r[5] for r, e in zip(ex, run_ex) if e["severity"] == "review")
    out.append(ck("xlsx.exceptions", ok, f"{len(ex)} รายการ", "ข้อยกเว้นครบ และข้อที่รอคนตัดสินมีทางเลือก"))
    left = computed["not_included"]
    n = len([r for r in wb[S_LEFT].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]) if S_LEFT in wb.sheetnames else 0
    out.append(ck("xlsx.not_included", n == len(left), f"{n} ไฟล์", "ไฟล์ที่ไม่ได้ใช้อยู่ในรายการเอกสารที่ยังไม่ได้รวม"))
    out.append(_figures(wb, computed))
    out.append(workpaper.check(path))
    return out


def _figures(wb, computed: dict) -> dict:
    """Every amount of the statement, read back: balances, each adjustment line, each item's amount and
    effect, each pair's two totals (AM8)."""
    t = Tally()
    face = _labelled(wb[S_STATEMENT])
    b, st = computed["balances"], computed["statement"]
    for label, side, which in (("ยอดยกมาต้นงวดตามธนาคาร", "bank", "opening"), ("ยอดยกมาต้นงวดตามบัญชี", "book", "opening"),
                               ("ยอดคงเหลือสิ้นงวดตามธนาคาร", "bank", "closing"), ("ยอดคงเหลือสิ้นงวดตามบัญชี", "book", "closing")):
        want = b[side][which]
        if want is None:
            t.text(label, face.get(label), "ไม่ทราบ")
        else:
            t.number(label, face.get(label), want)
    for side, adj in (("bank", L_BANK_ADJ), ("book", L_BOOK_ADJ)):
        for ln in st[side]["lines"]:
            t.number(f"{ln['sign']}: {ln['label']}", face.get(f"{ln['sign']}: {ln['label']}"), ln["amount"])
        if st[side]["adjusted"] is not None:
            t.number(adj, face.get(adj), st[side]["adjusted"])
    rows = table(wb[S_ITEMS], H_ITEMS)
    for r, i in zip(rows, computed["items"]):
        t.number(f"รายการกระทบยอด {r[0]} จำนวนเงิน", r[6], i["amount"])
        t.number(f"รายการกระทบยอด {r[0]} ผลต่อยอด", r[7], i["effect"])
    pairs = table(wb[S_PAIRS], H_PAIRS)
    for r, m in zip(pairs, computed["match"]["groups"]["matched"]):
        t.number(f"คู่ที่ {r[0]} จำนวนเงิน (บัญชี)", r[8], m["left_total"])
        t.number(f"คู่ที่ {r[0]} จำนวนเงิน (ธนาคาร)", r[13], m["right_total"])
    return t.result("xlsx.figures", "ทุกยอดในงบ รายการกระทบยอด และคู่ที่จับได้ (อ่านจากไฟล์) ตรงกับที่คำนวณ", "ยอด")
