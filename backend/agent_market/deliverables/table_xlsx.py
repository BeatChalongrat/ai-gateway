"""The computed tables as .xlsx — so a person can check the work.

Sheets: สรุป (P&L lines) · รายบัญชี · รายการที่ใช้ (every row the numbers came
from, with its source row) · ตรวจสอบ (each account recomputed with SUMIFS
over รายการที่ใช้, next to the script's value and the difference) ·
รายการต้องตรวจ · ข้อสมมติ · อ้างอิง. The values are written as numbers, not
text, so a reader can sum them again. AM21: written through the look's Book —
one header style, one cell style, number formats by meaning, widths measured.
"""

from __future__ import annotations

from decimal import Decimal

from openpyxl.utils import get_column_letter

from ..engine import words
from ..engine.words import SHARED as S
from .look.xlsx_styles import Book


def _num(v):
    return None if v is None else float(Decimal(str(v)))


class XlsxWriter:
    def __init__(self, model: dict, computed: dict, theme: dict):
        self.m, self.c, self.t = model, computed, theme
        self.book = Book(theme, words.lang())
        self.wb = self.book.wb

    def summary(self):
        ws = self.book.sheet(S("docs.sheet.summary"))
        c = self.c
        self.book.title(ws, S("docs.xlsx.title", period_label=c["period_label"]), self.m["source_note"])
        heads = [S("docs.col.item"), S("docs.col.current"), S("docs.col.prior"), S("docs.col.change"), "%", S("docs.col.budget"), S("docs.col.var_budget"), S("docs.col.ref")]
        rows = []
        for l in c["lines"]:
            rows.append([l["label"], _num(l["current"]), _num(l["prior"]), _num(l["var_prior"]), _num(l["var_prior_pct"]),
                         _num(l["budget"]), _num(l["var_budget"]), l["cite"]])
        for rt in c["ratios"]:
            rows.append([rt["label"], _num(rt["current"]), _num(rt["prior"]), _num(rt["change_points"]), None, None, None, rt["cite"]])
        self.book.table(ws, 4, heads, rows, money_cols=(2, 3, 4, 6, 7), pct_cols=(5,), freeze=False, filter=False)
        n_lines = len(c["lines"])
        # AM23 Part 2: each figure as the report prints it — whole baht, a percentage to one decimal (the value stays exact)
        for i in range(n_lines):
            self._as(ws, 5 + i, (2, 3, 4, 6, 7), "baht")
            self._as(ws, 5 + i, (5,), "pct1")
        for i, rt in enumerate(c["ratios"]):
            r = 5 + n_lines + i
            self._as(ws, r, (2, 3), "pct1")
            self._as(ws, r, (4,), "points")
        for i, l in enumerate(c["lines"]):
            if l["key"] in ("gross_profit", "net_profit", "total_expenses"):
                r = 5 + i
                self.book.style(ws.cell(row=r, column=1), "AM Label Strong")
                self._as(ws, r, (2, 3, 4, 6, 7), "baht_strong")

    def accounts(self):
        ws = self.book.sheet(S("docs.sheet.accounts"))
        self.book.title(ws, S("docs.section.accounts"), S("docs.xlsx.accounts_sub"))
        heads = [S("docs.col.code"), S("docs.col.account_name"), S("docs.col.group"), S("docs.col.current"), S("docs.col.prior"), S("docs.col.change"), "%", S("docs.col.budget"), S("docs.col.var_budget"), S("docs.col.count"), S("docs.col.ref")]
        # an account's group is the key of the line it adds up to: named as that line is (AM4)
        group_name = {ln["key"]: ln["label"] for ln in self.c.get("lines") or []}
        rows = [[a["code"], a["name"], group_name.get(a["group"], a["group"]), _num(a["current"]), _num(a["prior"]), _num(a["var_prior"]),
                 _num(a["var_prior_pct"]), _num(a["budget"]), _num(a["var_budget"]), a["rows"], a["cite"]] for a in self.c["accounts"]]
        self.book.table(ws, 4, heads, rows, money_cols=(4, 5, 6, 8, 9), pct_cols=(7,), count_cols=(10,), text_cols=(1,))
        for i in range(len(rows)):   # (AM23 Part 2: as the report prints them)
            self._as(ws, 5 + i, (4, 5, 6, 8, 9), "baht")
            self._as(ws, 5 + i, (7,), "pct1")

    def _as(self, ws, r: int, cols, kind: str) -> None:
        """AM23 Part 2: these cells as the report prints their figures (the money and percentage meanings of the report)."""
        for j in cols:
            self.book.fmt(ws, r, j, kind)

    def ledger(self):
        ws = self.book.sheet(S("docs.sheet.ledger"))
        c = self.c
        self.book.title(ws, S("docs.xlsx.ledger_title"), S("docs.xlsx.ledger_sub"))
        heads = [S("docs.col.file"), S("docs.col.row_in_file"), S("docs.col.date"), S("docs.col.account_code"), S("docs.col.description"), S("docs.col.debit"), S("docs.col.credit"), S("docs.col.doc_no"), S("docs.col.cost_centre")]
        files = [f["file"] for f in c["source"]["files"]]
        rows = [[files[fidx] if fidx < len(files) else "", row, date, acct, desc, _num(dr), _num(cr), doc, cc]
                for fidx, row, date, acct, desc, dr, cr, doc, cc in c["ledger"]]
        self.book.table(ws, 4, heads, rows, money_cols=(6, 7), count_cols=(2,), text_cols=(4,), wrap_cols=(5,))
        last = 4 + len(rows)
        r = last + 1
        self.book.style(ws.cell(row=r, column=5, value=S("docs.word.total")), "AM Label Strong")
        for col in (6, 7):
            L = get_column_letter(col)
            self.book.style(ws.cell(row=r, column=col, value=f"=SUM({L}5:{L}{last})"), "AM Money Strong")
        return last

    def verify(self, ledger_last: int):
        ws = self.book.sheet(S("docs.sheet.verify"))
        self.book.title(ws, S("docs.xlsx.verify_title"), S("docs.xlsx.verify_sub"))
        heads = [S("docs.col.account_code"), S("docs.col.account_name"), S("docs.col.recomputed"), S("docs.col.from_script"), S("docs.col.difference")]
        ledger = S("docs.sheet.ledger")   # (the formulas name the sheet as the file names it)
        rng = f"'{ledger}'!$D$5:$D${ledger_last}"
        dr = f"'{ledger}'!$F$5:$F${ledger_last}"
        cr = f"'{ledger}'!$G$5:$G${ledger_last}"
        rows = []
        for i, a in enumerate(self.c["accounts"]):
            r = 5 + i
            sign = "-" if a["group"] in ("revenue", "other_income") else ""
            rows.append([a["code"], a["name"], f'={sign}(SUMIFS({dr},{rng},A{r})-SUMIFS({cr},{rng},A{r}))', _num(a["current"]), f"=ROUND(C{r}-D{r},2)"])
        self.book.table(ws, 4, heads, rows, money_cols=(3, 4, 5), text_cols=(1,))

    def lists(self):
        ws = self.book.sheet(S("docs.sheet.exceptions"))
        self.book.title(ws, S("docs.section.exceptions"), S("docs.xlsx.exceptions_sub"))
        heads = [S("docs.col.reason_code"), S("docs.col.item"), S("docs.col.detail"), S("docs.col.account"), S("docs.col.amount"), S("docs.col.source_row"), S("docs.col.ref")]
        rows = [[e["code"], e["title"], e["detail"], e.get("account"), _num(e.get("amount")), ", ".join(str(x) for x in e["rows"][:30]), e.get("cite")]
                for e in self.c["exceptions"]] or [[None, S("docs.exc.none"), None, None, None, None, None]]
        self.book.table(ws, 4, heads, rows, money_cols=(5,), wrap_cols=(2, 3, 6))
        for i in range(len(rows)):
            self._as(ws, 5 + i, (5,), "baht")

        ws = self.book.sheet(S("docs.sheet.assumptions"))
        self.book.title(ws, S("docs.section.assumptions"))
        src_words = {"default": S("docs.assume_src.default"), "remembered": S("docs.assume_src.remembered"), "answer": S("docs.assume_src.answer"),
                     "data": S("docs.assume_src.data"), "rule": S("docs.assume_src.rule"), "reprompt_default": S("docs.assume_src.reprompt"),
                     "skip": S("docs.assume_src.skip")}
        rows = [[a["text"], src_words.get(a.get("source"), a.get("source"))] for a in self.m["assumptions"]]
        self.book.table(ws, 4, [S("docs.sheet.assumptions"), S("docs.col.source")], rows, wrap_cols=(1,))

        ws = self.book.sheet(S("docs.sheet.citations"))
        self.book.title(ws, S("docs.xlsx.citations_title"))
        heads = [S("docs.col.ref"), S("docs.col.figure"), S("docs.col.value"), S("docs.col.file"), S("docs.col.sheet"), S("docs.col.cell"), S("docs.col.script"), S("docs.col.formula")]
        rows = []
        for cit in self.c["citations"]:
            src = cit["sources"][0] if cit["sources"] else {}
            rows.append([cit["id"], cit["label"], _num(cit["value"]) if cit["value"] and _isnum(cit["value"]) else cit["value"],
                         src.get("file") or (S("docs.cite.derived") + " " + ", ".join(cit.get("derived_from", [])[:10])),
                         src.get("sheet"), src.get("cells"), cit["script"], cit["formula"]])
        self.book.table(ws, 4, heads, rows, money_cols=(3,), wrap_cols=(2, 4, 6, 8))
        # each citation's value as the report's appendix prints it: a ratio to one decimal, a change in points, an amount
        # in whole baht (a count prints the same either way)
        for i, cit in enumerate(self.c["citations"]):
            self._as(ws, 5 + i, (3,), {"pct": "pct1", "points": "points"}.get(cit.get("unit") or "", "baht"))

    def write(self, path: str, organisation: str = "") -> dict:
        self.summary()
        self.accounts()
        last = self.ledger()
        self.verify(last)
        self.lists()
        # AM8: printable — the P&L face on one page; account and ledger rows keep their identifiers with their amounts
        self.book.finish(path, id_cols={S("docs.sheet.ledger"): 2}, author=self.m.get("company") or organisation,
                         title=f"{self.m['title']} {self.m['subtitle']}")
        return {"path": path, "sheets": self.wb.sheetnames, "ledger_rows": last - 4}


def _isnum(v) -> bool:
    try:
        Decimal(str(v))
        return True
    except Exception:  # noqa: BLE001
        return False
