"""The computed tables as .xlsx — so a person can check the work.

Sheets: สรุป (P&L lines) · รายบัญชี · รายการที่ใช้ (every row the numbers came
from, with its source row) · ตรวจสอบ (each account recomputed with SUMIFS
over รายการที่ใช้, next to the script's value and the difference) ·
รายการต้องตรวจ · ข้อสมมติ · อ้างอิง. The values are written as numbers, not
text, so a reader can sum them again.
"""

from __future__ import annotations

from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
PCT = '0.00"%";[Red]-0.00"%";"-"'


def _num(v):
    return None if v is None else float(Decimal(str(v)))


class XlsxWriter:
    def __init__(self, model: dict, computed: dict, theme: dict):
        self.m, self.c, self.t = model, computed, theme
        self.p = theme["palette"]
        self.font = theme["fonts"]["thai"]
        self.wb = Workbook()

    def _header(self, ws, row: int, headers: list[str]):
        fill = PatternFill("solid", fgColor=self.p["primary"])
        for j, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=j, value=h)
            c.font = Font(name=self.font, bold=True, color=self.p["on_primary"], size=10)
            c.fill = fill
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.freeze_panes = ws.cell(row=row + 1, column=1)

    def _title(self, ws, text: str, sub: str = ""):
        ws["A1"] = text
        ws["A1"].font = Font(name=self.font, bold=True, size=14, color=self.p["primary"])
        if sub:
            ws["A2"] = sub
            ws["A2"].font = Font(name=self.font, size=9, color=self.p["muted"])

    def _body_font(self, ws, first_row: int):
        thin = Side(style="thin", color=self.p["rule"])
        zebra = PatternFill("solid", fgColor=self.p["surface"])
        for r_i, row in enumerate(ws.iter_rows(min_row=first_row, max_row=ws.max_row)):
            for c in row:
                if c.font is None or not c.font.bold:
                    c.font = Font(name=self.font, size=10, color=self.p["ink"])
                c.border = Border(bottom=thin)
                if r_i % 2 == 1:
                    c.fill = zebra

    def _widths(self, ws, widths: list[int]):
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w

    def summary(self):
        ws = self.wb.active
        ws.title = "สรุป"
        c = self.c
        self._title(ws, f"งบกำไรขาดทุน · {c['period_label']}", self.m["source_note"])
        heads = ["รายการ", "งวดนี้", "งวดก่อน", "เปลี่ยนแปลง", "%", "งบประมาณ", "ต่างจากงบ", "อ้างอิง"]
        self._header(ws, 4, heads)
        r = 5
        for l in c["lines"]:
            vals = [l["label"], _num(l["current"]), _num(l["prior"]), _num(l["var_prior"]), _num(l["var_prior_pct"]),
                    _num(l["budget"]), _num(l["var_budget"]), l["cite"]]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            r += 1
        for rt in c["ratios"]:
            vals = [rt["label"], _num(rt["current"]), _num(rt["prior"]), _num(rt["change_points"]), None, None, None, rt["cite"]]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            for j in (2, 3):
                ws.cell(row=r, column=j).number_format = PCT
            r += 1
        self._body_font(ws, 5)
        for row in ws.iter_rows(min_row=5, max_row=5 + len(c["lines"]) - 1, min_col=2, max_col=7):
            for cell in row:
                cell.number_format = PCT if cell.column == 5 else MONEY
        for i, l in enumerate(c["lines"]):
            if l["key"] in ("gross_profit", "net_profit", "total_expenses"):
                for cell in ws[5 + i]:
                    cell.font = Font(name=self.font, size=10, bold=True, color=self.p["ink"])
        self._widths(ws, [34, 16, 16, 16, 9, 16, 16, 9])

    def accounts(self):
        ws = self.wb.create_sheet("รายบัญชี")
        self._title(ws, "รายละเอียดรายบัญชี", "ค่าตามบัญชีที่อ่านได้จากไฟล์ (รายได้แสดงเป็นบวก)")
        heads = ["รหัส", "ชื่อบัญชี", "หมวด", "งวดนี้", "งวดก่อน", "เปลี่ยนแปลง", "%", "งบประมาณ", "ต่างจากงบ", "จำนวนรายการ", "อ้างอิง"]
        self._header(ws, 4, heads)
        r = 5
        # an account's group is the key of the line it adds up to: named as that line is (AM4)
        group_name = {ln["key"]: ln["label"] for ln in self.c.get("lines") or []}
        for a in self.c["accounts"]:
            vals = [a["code"], a["name"], group_name.get(a["group"], a["group"]), _num(a["current"]), _num(a["prior"]),
                    _num(a["var_prior"]),
                    _num(a["var_prior_pct"]), _num(a["budget"]), _num(a["var_budget"]), a["rows"], a["cite"]]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            r += 1
        self._body_font(ws, 5)
        for row in ws.iter_rows(min_row=5, max_row=r - 1, min_col=4, max_col=9):
            for cell in row:
                cell.number_format = PCT if cell.column == 7 else MONEY
        self._widths(ws, [11, 30, 18, 15, 15, 15, 9, 15, 15, 10, 9])

    def ledger(self):
        ws = self.wb.create_sheet("รายการที่ใช้")
        c = self.c
        self._title(ws, "รายการที่ใช้คำนวณ", "ทุกแถวอ้างกลับไปที่แถวในไฟล์ต้นฉบับ (คอลัมน์ ไฟล์ และ แถวในไฟล์)")
        heads = ["ไฟล์", "แถวในไฟล์", "วันที่", "รหัสบัญชี", "คำอธิบาย", "เดบิต", "เครดิต", "เลขที่เอกสาร", "ศูนย์ต้นทุน"]
        self._header(ws, 4, heads)
        files = [f["file"] for f in c["source"]["files"]]
        r = 5
        for fidx, row, date, acct, desc, dr, cr, doc, cc in c["ledger"]:
            vals = [files[fidx] if fidx < len(files) else "", row, date, acct, desc, _num(dr), _num(cr), doc, cc]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            r += 1
        last = r - 1
        for row in ws.iter_rows(min_row=5, max_row=last, min_col=6, max_col=7):
            for cell in row:
                cell.number_format = MONEY
                cell.font = Font(name=self.font, size=10)
        for row in ws.iter_rows(min_row=5, max_row=last, min_col=1, max_col=5):
            for cell in row:
                cell.font = Font(name=self.font, size=10)
        ws.cell(row=r, column=5, value="รวม").font = Font(name=self.font, bold=True, size=10)
        for col in (6, 7):
            L = get_column_letter(col)
            cell = ws.cell(row=r, column=col, value=f"=SUM({L}5:{L}{last})")
            cell.number_format = MONEY
            cell.font = Font(name=self.font, bold=True, size=10)
        ws.auto_filter.ref = f"A4:I{last}"
        self._widths(ws, [22, 9, 11, 11, 44, 15, 15, 16, 12])
        return last

    def verify(self, ledger_last: int):
        ws = self.wb.create_sheet("ตรวจสอบ")
        self._title(ws, "ตรวจสอบการคำนวณ",
                    "คอลัมน์ C คำนวณใหม่ด้วยสูตร SUMIFS จากชีต รายการที่ใช้ · คอลัมน์ E ควรเป็นศูนย์ทุกแถว")
        heads = ["รหัสบัญชี", "ชื่อบัญชี", "คำนวณใหม่ (สูตร)", "ค่าจากสคริปต์", "ส่วนต่าง"]
        self._header(ws, 4, heads)
        rng = f"'รายการที่ใช้'!$D$5:$D${ledger_last}"
        dr = f"'รายการที่ใช้'!$F$5:$F${ledger_last}"
        cr = f"'รายการที่ใช้'!$G$5:$G${ledger_last}"
        r = 5
        for a in self.c["accounts"]:
            sign = "-" if a["group"] in ("revenue", "other_income") else ""
            ws.cell(row=r, column=1, value=a["code"])
            ws.cell(row=r, column=2, value=a["name"])
            ws.cell(row=r, column=3, value=f'={sign}(SUMIFS({dr},{rng},A{r})-SUMIFS({cr},{rng},A{r}))')
            ws.cell(row=r, column=4, value=_num(a["current"]))
            ws.cell(row=r, column=5, value=f"=ROUND(C{r}-D{r},2)")
            r += 1
        self._body_font(ws, 5)
        for row in ws.iter_rows(min_row=5, max_row=r - 1, min_col=3, max_col=5):
            for cell in row:
                cell.number_format = MONEY
        self._widths(ws, [12, 32, 18, 18, 12])

    def lists(self):
        ws = self.wb.create_sheet("รายการต้องตรวจ")
        self._title(ws, "รายการที่ต้องตรวจสอบ", "ระบบไม่แก้ตัวเลขเอง")
        self._header(ws, 4, ["รหัสเหตุผล", "รายการ", "รายละเอียด", "บัญชี", "จำนวนเงิน", "แถวต้นทาง", "อ้างอิง"])
        r = 5
        for e in self.c["exceptions"]:
            vals = [e["code"], e["title"], e["detail"], e.get("account"), _num(e.get("amount")),
                    ", ".join(str(x) for x in e["rows"][:30]), e.get("cite")]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            ws.cell(row=r, column=5).number_format = MONEY
            r += 1
        if r == 5:
            ws.cell(row=5, column=2, value="ไม่พบรายการผิดปกติตามกฎที่ตั้งไว้")
        self._body_font(ws, 5)
        self._widths(ws, [22, 44, 36, 11, 15, 24, 9])

        ws = self.wb.create_sheet("ข้อสมมติ")
        self._title(ws, "ข้อสมมติที่ใช้ในรอบนี้")
        self._header(ws, 4, ["ข้อสมมติ", "ที่มา"])
        for i, a in enumerate(self.m["assumptions"], start=5):
            ws.cell(row=i, column=1, value=a["text"])
            ws.cell(row=i, column=2, value={"default": "ค่าตั้งต้น", "remembered": "ค่าที่จำไว้", "answer": "คำตอบของคุณ",
                                            "data": "ข้อมูลที่มี", "rule": "กฎของ agent",
                                            "reprompt_default": "ค่าตั้งต้นหลังถามซ้ำ",
                                            "skip": "กดข้าม ใช้ค่าแนะนำ"}.get(a.get("source"), a.get("source")))
        self._body_font(ws, 5)
        self._widths(ws, [90, 18])

        ws = self.wb.create_sheet("อ้างอิง")
        self._title(ws, "ที่มาของตัวเลข")
        self._header(ws, 4, ["อ้างอิง", "ตัวเลข", "ค่า", "ไฟล์", "ชีต", "ช่อง", "สคริปต์", "สูตร"])
        r = 5
        for cit in self.c["citations"]:
            src = cit["sources"][0] if cit["sources"] else {}
            vals = [cit["id"], cit["label"], _num(cit["value"]) if cit["value"] and _isnum(cit["value"]) else cit["value"],
                    src.get("file") or ("คำนวณจาก " + ", ".join(cit.get("derived_from", [])[:10])),
                    src.get("sheet"), src.get("cells"), cit["script"], cit["formula"]]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            ws.cell(row=r, column=3).number_format = MONEY
            r += 1
        self._body_font(ws, 5)
        self._widths(ws, [9, 34, 16, 26, 12, 30, 18, 44])

    def write(self, path: str) -> dict:
        self.summary()
        self.accounts()
        last = self.ledger()
        self.verify(last)
        self.lists()
        self.wb.properties.title = f"{self.m['title']} {self.m['subtitle']}"
        # AM8: printable — the P&L face on one page; account and ledger rows keep their identifiers with their amounts
        import os

        from . import workpaper

        workpaper.setup_workbook(self.wb, doc_name=os.path.basename(path), id_cols={"รายการที่ใช้": 2})
        self.wb.save(path)
        return {"path": path, "sheets": self.wb.sheetnames, "ledger_rows": last - 4}


def _isnum(v) -> bool:
    try:
        Decimal(str(v))
        return True
    except Exception:  # noqa: BLE001
        return False
