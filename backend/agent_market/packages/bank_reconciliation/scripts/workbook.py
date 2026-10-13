"""The bank reconciliation statement as .xlsx — the working paper a preparer hands to a reviewer.

Sheets: งบกระทบยอด (whose account, which period, who prepared and who reviewed, the balances of
both sides, every adjustment line, the two adjusted balances and whether they tie) · รายการกระทบยอด
(every reconciling item with its evidence) · รายการที่จับคู่ (every pair: the pass, the tier, who
confirmed it) · รายการน่าสงสัย · ร่างรายการปรับปรุง (drafts — nothing is posted) · อายุรายการค้าง ·
รายการค้างยกมา · รายการค้างยกไป (next month's opening list: send this sheet back next month) ·
รายการข้ามงวด · ข้อยกเว้น · หลักฐาน · เอกสารที่ยังไม่ได้รวม (when a file was not used).

AM10: the wide tables (items, pairs, suspects) print one page across. Each is one table (H_*) written
as a main sheet — the row's identity, its amounts, its status — and a companion sheet right after it
("<sheet> · หลักฐาน") that repeats the identity columns and holds the evidence, one row per row, read
side by side by row number (``LAYOUT``: which columns, how wide).

AM18: every word the paper itself says has a key and is said in the run's language (the manifest holds the Thai
and the English; the module keeps keys, W says them when read). Amounts are numbers, not text; the quality gate
reopens the file and checks it against computed.json — the adjusted balances, the difference and the verdict on the face of the statement
are the computed ones, to the satang.
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
from ....engine.e3 import cards as E3C
from ....engine.e3.match import SETTING_TH
from ..checks import classify as C
from ..checks.inputs import CARRIED_SHEET
from ..words import W
from .reconcile import OUTSTANDING, REASON_TH

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
# The tables' columns in the order the data is built and read back (the checks read by these names, not by
# position — from the sheets of a table together, ``table(parts(wb, sheet), H_*)``)
H_ITEMS = W.heads(["doc.col.no", "doc.col.side", "doc.col.type", "doc.col.date", "doc.col.ref", "doc.col.description", "doc.col.amount_per_file", "doc.col.effect",
           "doc.col.age", "doc.col.bucket", "doc.col.in", "doc.col.evidence", "doc.col.classified_by", "doc.col.note"])
H_PAIRS = W.heads(["doc.col.pair", "doc.col.tier", "doc.col.pass", "doc.col.shape", "doc.col.confirmed_by", "doc.col.date_book", "doc.col.description_book", "doc.col.ref_book",
           "doc.col.amount_book", "doc.col.evidence_book", "doc.col.date_bank", "doc.col.description_bank", "doc.col.ref_bank",
           "doc.col.amount_bank", "doc.col.evidence_bank", "doc.col.difference", "doc.col.days_apart"])
H_SUSPECT = W.heads(["doc.col.no"] + H_PAIRS.keys[1:2] + ["doc.col.why_suspect", "doc.col.shape", "doc.col.status"] + H_PAIRS.keys[5:])
H_JOURNAL = W.heads(["doc.col.draft_no", "doc.col.date", "doc.col.memo", "doc.col.account", "doc.col.account_code", "doc.col.debit", "doc.col.credit",
                     "doc.col.note", "doc.col.from_items"])
H_CARRIED_IN = W.heads(["doc.col.date", "doc.col.ref", "doc.col.description", "doc.col.amount", "doc.col.type", "doc.col.status", "doc.col.cleared_on",
                        "doc.col.pass", "doc.col.age", "doc.col.evidence"])
# what prints first on a table that is one sheet: the row's number and description, then its amounts (AM9) — by key
LEAD = {
    "journal": ["doc.col.draft_no", "doc.col.account", "doc.col.debit", "doc.col.credit"],
    "carried_in": ["doc.col.ref", "doc.col.description", "doc.col.amount"],
}
S_STATEMENT, S_ITEMS, S_PAIRS, S_SUSPECT, S_JOURNAL = "doc.sheet.statement", "doc.sheet.items", "doc.sheet.pairs", "doc.sheet.suspect", "doc.sheet.journal"
S_AGE, S_IN, S_OUT, S_CUTOFF, S_EXC, S_EVIDENCE, S_LEFT = ("doc.sheet.ageing", "doc.sheet.carried_in", CARRIED_SHEET, "doc.sheet.cutoff",
                                                           "doc.sheet.exceptions", "doc.sheet.evidence", "doc.sheet.left_out")
# AM10: the wide tables' companion sheets — the same identity columns, then the evidence, read beside the main
# sheet by row number; every one prints one page across
S_ITEMS_EV, S_PAIRS_EV, S_SUSPECT_EV = "doc.sheet.items_ev", "doc.sheet.pairs_ev", "doc.sheet.suspect_ev"
PARTS = {S_ITEMS: (S_ITEMS, S_ITEMS_EV), S_PAIRS: (S_PAIRS, S_PAIRS_EV), S_SUSPECT: (S_SUSPECT, S_SUSPECT_EV)}
# What prints on each sheet of a wide table, in print order, with the column's width: the main sheet leads with
# the row's identity (ID_COLS of them, repeated on the companion and on every page), then its amounts and
# status; the companion repeats the identity and holds the rest. Every column of H_* is on one of them.
LAYOUT = {
    S_ITEMS: [("doc.col.no", 6), ("doc.col.description", 34), ("doc.col.amount_per_file", 15), ("doc.col.effect", 15), ("doc.col.side", 15), ("doc.col.type", 22),
              ("doc.col.date", 13), ("doc.col.ref", 14), ("doc.col.age", 8), ("doc.col.bucket", 12), ("doc.col.in", 14)],
    S_ITEMS_EV: [("doc.col.no", 6), ("doc.col.description", 34), ("doc.col.evidence", 50), ("doc.col.classified_by", 14), ("doc.col.note", 40)],
    S_PAIRS: [("doc.col.pair", 6), ("doc.col.description_book", 30), ("doc.col.amount_book", 15), ("doc.col.description_bank", 30), ("doc.col.amount_bank", 15),
              ("doc.col.difference", 12), ("doc.col.tier", 14), ("doc.col.confirmed_by", 18), ("doc.col.date_book", 13), ("doc.col.date_bank", 13), ("doc.col.days_apart", 8)],
    S_PAIRS_EV: [("doc.col.pair", 6), ("doc.col.description_book", 30), ("doc.col.pass", 30), ("doc.col.shape", 9), ("doc.col.ref_book", 16), ("doc.col.evidence_book", 40),
                 ("doc.col.ref_bank", 16), ("doc.col.evidence_bank", 40)],
    S_SUSPECT: [("doc.col.no", 6), ("doc.col.description_book", 28), ("doc.col.amount_book", 14), ("doc.col.description_bank", 28), ("doc.col.amount_bank", 14),
                ("doc.col.difference", 12), ("doc.col.tier", 13), ("doc.col.why_suspect", 26), ("doc.col.status", 16), ("doc.col.date_book", 12), ("doc.col.date_bank", 12),
                ("doc.col.days_apart", 8)],
    S_SUSPECT_EV: [("doc.col.no", 6), ("doc.col.description_book", 28), ("doc.col.shape", 9), ("doc.col.ref_book", 16), ("doc.col.evidence_book", 40),
                   ("doc.col.ref_bank", 16), ("doc.col.evidence_bank", 40)],
}
ID_COLS = {S_ITEMS: 2, S_PAIRS: 2, S_SUSPECT: 2}
SHEETS = (S_STATEMENT, S_ITEMS, S_ITEMS_EV, S_PAIRS, S_PAIRS_EV, S_SUSPECT, S_SUSPECT_EV, S_JOURNAL, S_AGE, S_IN, S_OUT, S_CUTOFF,
          S_EXC, S_EVIDENCE)
L_BANK_BAL, L_BOOK_BAL = "doc.face.bank_balance", "doc.face.book_balance"
L_BANK_ADJ, L_BOOK_ADJ = "doc.face.bank_adjusted", "doc.face.book_adjusted"
L_DIFF, L_VERDICT = "doc.col.difference", "doc.face.verdict"
L_PREPARER, L_REVIEWER = "doc.face.preparer", "doc.face.reviewer"
TIED, NOT_TIED = "doc.face.tied", "doc.face.not_tied"
DRAFT_MARK = "doc.draft.not_tied"
# AM8: a header field with no value says so — no placeholder stands in for a company, an account or a
# preparer — and a paper missing one of them says on its face that it is not yet one to sign
MISSING = "doc.word.missing"
HEADER_DRAFT = "doc.draft.header"
L_COMPANY, L_BANK, L_ACC_NAME, L_ACC_NO, L_GL = "doc.face.company", "doc.face.bank", "doc.face.account_name", "doc.face.account_no", "doc.face.gl"
# true before a sign-off and after it: the stamp adds the sheet, it rewrites no other
NOT_REVIEWED = "doc.face.not_reviewed"
CONFIRMED_TH = W.table({"rule": "doc.who.rule_exact", "person": "doc.col.by_person"})
DECIDED_TH = W.table({"rule": "doc.who.rule", "person": "doc.who.person_decided"})
SEVERITY_TH = W.table({"review": "doc.status.review", "info": "doc.status.info"})
FROM_TH = W.table({"book": "doc.from.book", "bank": "doc.from.bank", "carried": "doc.sheet.carried_in"})
SIDE_TH = W.table({"bank": "doc.side.bank", "book": "doc.side.book", None: "doc.side.none"})
BALANCE_FROM = W.table({"row": "doc.balfrom.row", "balance_column": "doc.balfrom.column", "movement": "doc.balfrom.movement",
                        "typed": "doc.balfrom.typed", "zero": "doc.balfrom.zero", "unknown": "doc.word.unknown"})


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def who(kind: str, name: str, words: dict) -> str:
    """ "คนยืนยัน: สมหญิง ผู้ตรวจ" — the person by name when the runner knew who answered the card."""
    return f"{words[kind]}: {name}" if kind == "person" and name else words[kind]


def _where(s: dict) -> str:
    cells = ", ".join(sorted(set((s.get("cells") or {}).values()))) if isinstance(s.get("cells"), dict) else (s.get("cell") or "")
    return " · ".join(str(x) for x in (s.get("file"), s.get("sheet"), W("doc.where.row", row=s["row"]) if s.get("row") else "", cells) if x)


def parts(wb, sheet: str) -> list:
    """The sheets a table prints on — the main sheet and the companion that is in the file — for ``table``."""
    return sheets(wb, *(W(k) for k in PARTS.get(sheet, (sheet,))))


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

    def _part(self, ws, heads: list, rows: list, layout: list, **cols):
        """One sheet of a wide table (AM10): the columns ``layout`` names, at its widths, picked from the whole
        table; ``cols`` are the writer's column tuples against ``heads``."""
        picked, prow, cols = workpaper.pick(list(heads), rows, [W(h) for h, _ in layout], **cols)
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
        ws.title = W(S_STATEMENT)
        c, st, gate, acc = self.c, self.c["statement"], self.c["gate"], self.c["account"]
        self._title(ws, W("doc.title.statement", period_label=c["period_label"]),
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
        info = [(k, head[k] or W(MISSING)) for k in (W(L_COMPANY), W(L_BANK), W(L_ACC_NAME), W(L_ACC_NO))]
        info += [(W(L_GL), acc.get("gl_code") or W("doc.face.no_gl")), (W("doc.face.period"), c["period_label"]),
                 (W("doc.face.as_of"), E3C.date_th(c["as_of"])),
                 (W(L_PREPARER), (W("doc.face.prepared_line", v=head[W(L_PREPARER)], made=made, v2=self.run.get("agent_version") or "").rstrip(" ·"))
                  if head[W(L_PREPARER)] else W(MISSING)),
                 (W(L_REVIEWER), W(NOT_REVIEWED))]
        for label, value in info:
            self._pair(ws, r, label, value)
            r += 1
        r += 1
        self._section(ws, r, W("doc.section.balances"))
        r += 1
        b = c["balances"]
        for label, side, which in ((W("doc.face.opening_bank"), "bank", "opening"), (W("doc.face.opening_book"), "book", "opening"),
                                   (W("doc.face.closing_bank"), "bank", "closing"), (W("doc.face.closing_book"), "book", "closing")):
            self._pair(ws, r, label, _num(b[side][which]) if b[side][which] is not None else W("doc.word.unknown"), money=b[side][which] is not None)
            cell = b[side].get(f"{which}_cell") or {}
            src = ws.cell(row=r, column=4, value=" · ".join(x for x in (BALANCE_FROM.get(b[side][f"{which}_from"], ""), _where(cell)) if x))
            src.font = Font(name=self.font, size=9, color=self.p["muted"])
            # AM8: the evidence wraps inside its column — printed, it never runs past the page's edge
            src.alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
        for side, head, bal_label, adj_label in (("bank", W("doc.section.bank_side"), W(L_BANK_BAL), W(L_BANK_ADJ)),
                                                     ("book", W("doc.section.book_side"), W(L_BOOK_BAL), W(L_BOOK_ADJ))):
            r += 1
            self._section(ws, r, head)
            r += 1
            s = st[side]
            self._pair(ws, r, bal_label, _num(s["balance"]) if s["balance"] is not None else W("doc.word.unknown"), money=s["balance"] is not None,
                       bold=True)
            r += 1
            for ln in s["lines"]:
                self._pair(ws, r, f"{ln['sign']}: {ln['label']}", _num(ln["amount"]), money=True)
                ws.cell(row=r, column=3, value=W("doc.face.n_items", count=ln["count"])).font = Font(name=self.font, size=9, color=self.p["muted"])
                r += 1
            if not s["lines"]:
                self._pair(ws, r, W("doc.face.no_adjustments"), None)
                r += 1
            self._pair(ws, r, adj_label, _num(s["adjusted"]) if s["adjusted"] is not None else W("doc.word.unknown"), money=s["adjusted"] is not None,
                       bold=True, color=self.p["primary"])
            r += 1
        r += 1
        self._pair(ws, r, W(L_DIFF), _num(st["difference"]) if st["difference"] is not None else W("doc.word.unknown"),
                   money=st["difference"] is not None, bold=True)
        r += 1
        self._pair(ws, r, W(L_VERDICT), W(TIED) if gate["passed"] else gate["what"], bold=True,
                   color=self.p.get("positive", "067647") if gate["passed"] else self.p.get("negative", "B42318"))
        r += 2
        if not gate["passed"]:
            self._section(ws, r, W("doc.section.why"))
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
            W("doc.note.totals", matched=t["matched"], by_rule=t["by_rule"], by_person=t["by_person"], items=t["items"], suspect=t["suspect"]),
            W("doc.note.figures"),
            W("doc.note.drafts"),
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
        rows = self.item_rows(self.c["items"])
        for sheet, title, sub in ((S_ITEMS, W("doc.title.items"), W("doc.sub.items", s_items_ev=W(S_ITEMS_EV))),
                                  (S_ITEMS_EV, W(S_ITEMS_EV), W("doc.sub.items_ev", s_items=W(S_ITEMS)))):
            ws = self.wb.create_sheet(W(sheet))
            self._title(ws, title, sub)
            self._part(ws, H_ITEMS, rows, LAYOUT[sheet], money_cols=(7, 8), wrap_cols=(2, 3, 6, 12, 14))

    def pairs(self):
        rows = [self._pair_row(n, m, who(m["confirmed"], m.get("confirmed_by") or "", CONFIRMED_TH))
                for n, m in enumerate(self.c["match"]["groups"]["matched"], start=1)]
        for sheet, title, sub in ((S_PAIRS, W("doc.title.pairs"), W("doc.sub.pairs", s_pairs_ev=W(S_PAIRS_EV))),
                                  (S_PAIRS_EV, W(S_PAIRS_EV), W("doc.sub.pairs_ev", s_pairs=W(S_PAIRS)))):
            ws = self.wb.create_sheet(W(sheet))
            self._title(ws, title, sub)
            self._part(ws, H_PAIRS, rows, LAYOUT[sheet], money_cols=(9, 14, 16), wrap_cols=(2, 3, 5, 7, 10, 12, 15))

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
        rows = []
        for n, s in enumerate(self.c["match"]["groups"]["suspect"], start=1):
            if s["kind"] == "possible_duplicate":
                s = dict(s, tier_label="", pass_label=W("doc.suspect.duplicate"))
                who = W("doc.status.awaiting_review")
            else:
                who = W("doc.status.skipped") if s["decision"] == "undecided" else W("doc.status.not_asked")
            rows.append(self._pair_row(n, s, who))
        for sheet, title, sub in ((S_SUSPECT, W("doc.sheet.suspect"), W("doc.sub.suspect", s_suspect_ev=W(S_SUSPECT_EV))),
                                  (S_SUSPECT_EV, W(S_SUSPECT_EV), W("doc.sub.suspect_ev", s_suspect=W(S_SUSPECT)))):
            ws = self.wb.create_sheet(W(sheet))
            self._title(ws, title, sub)
            self._part(ws, H_SUSPECT, rows, LAYOUT[sheet], money_cols=(9, 14, 16), wrap_cols=(2, 3, 5, 7, 10, 12, 15))

    def journal(self):
        ws = self.wb.create_sheet(W(S_JOURNAL))
        self._title(ws, W("doc.title.journal"), W("doc.sub.journal"))
        rows = []
        for j in self.c["journal"]:
            for k, ln in enumerate(j["lines"]):
                rows.append([j["id"], E3C.date_th(j["date"]), j["memo"] if k == 0 else "", ln["account"], ln["code"],
                             _num(ln["debit"]) or None, _num(ln["credit"]) or None, j["note"] if k == 0 else "",
                             ", ".join(j["items"]) if k == 0 else ""])
        self._table(ws, 4, H_JOURNAL, rows, [10, 13, 36, 34, 12, 14, 14, 36, 18], money_cols=(6, 7), wrap_cols=(3, 4, 8, 9),
                    first=[W(k) for k in LEAD["journal"]])

    def ageing(self):
        ws = self.wb.create_sheet(W(S_AGE))
        a = self.c["ageing"]
        self._title(ws, W("doc.title.ageing"), W("doc.sub.ageing", as_of=E3C.date_th(self.c["as_of"]), stale_days=a["stale_days"]))
        self._table(ws, 4, [W("doc.col.bucket"), W("doc.col.count"), W("doc.col.amount")], [[b["label"], b["count"], _num(b["amount"])] for b in a["buckets"]],
                    [24, 16, 20], money_cols=(3,), freeze=False)
        r = 4 + len(a["buckets"]) + 2
        self._section(ws, r, W("doc.section.old"))
        old = [i for i in self.c["items"] if i["type"] in OUTSTANDING and i["age_days"] > 30]
        rows = [[C.TYPE_TH[i["type"]], E3C.date_th(i["date"]), i["ref"], i["description"], _num(abs(Decimal(i["amount"]))), i["age_days"],
                 i["bucket_label"], W("doc.status.stale") if i["id"] in a["stale"] else "", "; ".join(_where(s) for s in i["sources"])] for i in old]
        self._table(ws, r + 1, [W("doc.col.type"), W("doc.col.date"), W("doc.col.ref"), W("doc.col.description"), W("doc.col.amount"), W("doc.col.age"), W("doc.col.bucket"), W("doc.col.status"), W("doc.col.evidence")], rows,
                    [24, 16, 20, 44, 18, 12, 16, 16, 50], money_cols=(5,), wrap_cols=(4, 9), freeze=False)

    def carried_in(self):
        ws = self.wb.create_sheet(W(S_IN))
        self._title(ws, W("doc.title.carried_in"), W("doc.sub.carried_in"))
        rows = [[E3C.date_th(i["date"]), i["ref"], i["description"], _num(abs(Decimal(i["amount"]))), i["kind_label"],
                 W("doc.status.cleared") if i["cleared"] else W("doc.status.outstanding"), E3C.date_th(i["cleared_on"]) if i["cleared_on"] else "",
                 i["pass"] or "", i["age_days"], _where(i)] for i in self.c["carried_in"]]
        self._table(ws, 4, H_CARRIED_IN, rows, [13, 14, 32, 14, 18, 16, 13, 24, 8, 36], money_cols=(4,), wrap_cols=(3, 5, 6, 8, 10),
                    first=[W(k) for k in LEAD["carried_in"]])

    def carried_out(self):
        """Next month's opening list. Its columns are the ones checks/inputs.py reads back."""
        ws = self.wb.create_sheet(W(S_OUT))
        self._title(ws, W(CARRIED_SHEET), W("doc.sub.carried_out", as_of=E3C.date_th(self.c["as_of"])))
        rows = [[E3C.date_th(i["date"]), i["ref"], i["description"], _num(abs(Decimal(i["amount"]))), i["type_label"]]
                for i in (self.items[k] for k in self.c["carried_out"])]
        self._table(ws, 4, [W("doc.col.date"), W("doc.col.ref"), W("doc.col.description"), W("doc.col.amount"), W("doc.col.type")], rows, [16, 20, 50, 18, 34], money_cols=(4,),
                    wrap_cols=(3,))

    def cutoff(self):
        ws = self.wb.create_sheet(W(S_CUTOFF))
        c = self.c
        self._title(ws, W("doc.sheet.cutoff"), W("doc.sub.cutoff", period_label=c["period_label"]))
        rows = [[FROM_TH[x["side"]], E3C.date_th(x["date"]), x["ref"], x["description"], _num(x["amount"]),
                 W("doc.position.before") if x["date"] < c["period_start"] else W("doc.position.after"), _where(x)] for x in c["cutoff"]["rows"]]
        self._table(ws, 4, [W("doc.col.in"), W("doc.col.date"), W("doc.col.ref"), W("doc.col.description"), W("doc.col.amount"), W("doc.col.position"), W("doc.col.evidence")], rows,
                    [18, 14, 18, 44, 18, 16, 50], money_cols=(5,), wrap_cols=(4, 7), first=[W("doc.col.ref"), W("doc.col.description"), W("doc.col.amount")])

    def exceptions(self):
        ws = self.wb.create_sheet(W(S_EXC))
        self._title(ws, W("doc.sheet.exceptions"), W("doc.sub.exceptions"))
        rows = []
        for n, e in enumerate(self.c["run_exceptions"], start=1):
            src = "; ".join(_where(s) for s in (e["source_ref"].get("rows") or []))
            rows.append([n, e["reason"], SEVERITY_TH[e["severity"]], e["detail"], e["proposed_action"],
                         " / ".join(ch["label"] for ch in e["choices"]), src])
        self._table(ws, 4, [W("doc.col.no"), W("doc.col.subject"), W("doc.col.status"), W("doc.col.detail"), W("doc.col.proposed"), W("doc.col.options"), W("doc.col.evidence")], rows,
                    [6, 24, 12, 46, 32, 32, 36], wrap_cols=(2, 4, 5, 6, 7))

    def evidence(self):
        ws = self.wb.create_sheet(W(S_EVIDENCE))
        c = self.c
        self._title(ws, W("doc.col.evidence"), W("doc.sub.evidence"))
        rows = [[FROM_TH.get(f["side"], f["side"]), f["name"], f["sheet"], f["header_row"], f["rows"],
                 ", ".join(f"{k}={v}" for k, v in sorted((f["columns"] or {}).items())), f["sha256"]] for f in c["files"]]
        self._table(ws, 4, [W("doc.col.used_as"), W("doc.col.file"), W("doc.col.sheet"), W("doc.col.header_row"), W("doc.col.count"), W("doc.col.columns_read"), "SHA-256"], rows,
                    [18, 30, 16, 10, 10, 40, 36], wrap_cols=(2, 3, 6, 7), freeze=False)
        r = 4 + len(rows) + 2
        self._section(ws, r, W("doc.section.settings"))
        m = c["settings"]["matching"]
        srows = [[SETTING_TH[k], (W("doc.word.yes") if v else W("doc.word.no")) if isinstance(v, bool) else v,
                  W("doc.note.setting_unusable") if k in c["settings"]["unusable"] else ""] for k, v in m.items()]
        self._table(ws, r + 1, [W("doc.col.setting"), W("doc.col.used_this_run"), W("doc.col.note")], srows, freeze=False)
        r += len(srows) + 3
        self._section(ws, r, W("doc.section.tiers"))
        trows = [[t["label"], t["groups"], t["by_rule"], t["by_person"], t["waiting"]] for t in c["match"]["tiers"]]
        self._table(ws, r + 1, [W("doc.col.tier"), W("doc.col.pairs_matched"), W("doc.col.by_rule"), W("doc.col.by_person"), W("doc.col.waiting")], trows, freeze=False)
        r += len(trows) + 3
        prows = [[p["label"], p["tier_label"], W("doc.pass.auto") if p["auto"] else W("doc.pass.proposed"), p["by_rule"] + p["by_person"], p["waiting"]]
                 for p in c["match"]["passes"]]
        self._table(ws, r + 1, [W("doc.col.pass_in_order"), W("doc.col.tier"), W("doc.col.result"), W("doc.col.pairs_matched"), W("doc.col.waiting")], prows, freeze=False)

    def left_out(self):
        ws = self.wb.create_sheet(W(S_LEFT))
        self._title(ws, W(S_LEFT), W("doc.sub.left_out"))
        rows = [[e["name"], e["reason"], e["detail"], e["how"]] for e in self.c["not_included"]]
        self._table(ws, 4, [W("doc.col.file"), W("doc.col.reason"), W("doc.col.detail"), W("doc.col.how_back")], rows, [40, 40, 60, 50], wrap_cols=(2, 3, 4))

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
        # AM8: printable — the face on one page; a row's identifier on the page of its amounts (AM10: and
        # opening every companion sheet of a wide table)
        id_cols = {W(s): ID_COLS[main] for main, names in PARTS.items() for s in names}
        workpaper.setup_workbook(self.wb, doc_name=os.path.basename(path),
                                 id_cols={**id_cols, W(S_JOURNAL): 2, W(S_IN): 2, W(S_AGE): 1, W(S_OUT): 3, W(S_CUTOFF): 2, W(S_EXC): 2,
                                          W(S_EVIDENCE): 2, W(S_LEFT): 1}, lang=words.lang())
        self.wb.save(path)
        t = self.c["totals"]
        return {"sheets": list(self.wb.sheetnames), "tied": self.c["statement"]["tied"], "difference": self.c["statement"]["difference"],
                "matched": t["matched"], "items": t["items"], "run_exceptions": t["run_exceptions"], "draft": not self.c["gate"]["passed"]}


def header_fields(c: dict, run: dict | None) -> list[tuple[str, str]]:
    """The fields a working paper's header must hold, with their real values ("" when there is none):
    the company (the install's, else the organisation's name), the account, the preparer."""
    acc = c.get("account") or {}
    run = run or {}
    return [(W(L_COMPANY), str(c.get("company") or run.get("organisation") or "").strip()),
            (W(L_BANK), str(acc.get("bank") or "").strip()), (W(L_ACC_NAME), str(acc.get("name") or "").strip()),
            (W(L_ACC_NO), str(acc.get("number") or "").strip()), (W(L_PREPARER), str(run.get("preparer") or "").strip())]


def draft_marks(c: dict, run: dict | None) -> list[str]:
    marks = [] if c["gate"]["passed"] else [W(DRAFT_MARK)]
    missing = [k for k, v in header_fields(c, run) if not v]
    if missing:
        marks.append(W(HEADER_DRAFT).format(", ".join(missing)))
    return marks


def _labelled(ws) -> dict:
    out = {}
    for row in ws.iter_rows(min_row=1, max_col=2, values_only=True):
        if row and isinstance(row[0], str) and row[0] not in out:
            out[row[0]] = row[1]
    return out


def _same(cell, v) -> bool:
    if v is None:
        return cell == W("doc.word.unknown")
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
    out = [ck("xlsx.sheets", {W(k) for k in SHEETS} <= set(wb.sheetnames), ", ".join(wb.sheetnames), W("check.sheets"))]
    face = _labelled(wb[W(S_STATEMENT)])
    a3 = str(wb[W(S_STATEMENT)]["A3"].value or "")
    header_draft = W(HEADER_DRAFT).split("{}")[0]   # the mark on the face when a header field has no value
    t = Tally()
    for label, want in header_fields(computed, run):
        got = str(face.get(label) or "")
        if label == W(L_PREPARER):
            t.n += 1
            if want and not got.startswith(want):
                t.bad.append(W("check.header.differs", label=label, got=got[:40], want=want))
            elif not want and (got != W(MISSING) or header_draft not in a3):
                t.bad.append(W("check.header.no_preparer", label=label))
        elif want:
            t.text(label, got, want)
        else:
            t.n += 1
            if got != W(MISSING) or header_draft not in a3:
                t.bad.append(W("check.header.no_value", label=label, got=got))
    for k in (W("doc.face.period"), W("doc.face.as_of"), W(L_REVIEWER)):
        t.n += 1
        if not face.get(k):
            t.bad.append(W("check.header.empty", k=k))
    out.append(t.result("xlsx.header", W("check.header"), W("noun.header")))
    ok = (_same(face.get(W(L_BANK_BAL)), st["bank"]["balance"]) and _same(face.get(W(L_BOOK_BAL)), st["book"]["balance"])
          and _same(face.get(W(L_BANK_ADJ)), st["bank"]["adjusted"]) and _same(face.get(W(L_BOOK_ADJ)), st["book"]["adjusted"])
          and _same(face.get(W(L_DIFF)), st["difference"]))
    out.append(ck("xlsx.statement", ok, f"{face.get(W(L_BANK_ADJ))} / {face.get(W(L_BOOK_ADJ))}", W("check.statement")))
    verdict = str(face.get(W(L_VERDICT)) or "")
    says = verdict == W(TIED) if gate["passed"] else verdict.startswith(W(NOT_TIED))
    marked = W(DRAFT_MARK) in a3
    out.append(ck("xlsx.verdict", says and marked == (not gate["passed"]), verdict, W("check.verdict")))
    rows = table(parts(wb, S_ITEMS), H_ITEMS)
    items = computed["items"]
    same = len(rows) == len(items) and all(isinstance(r[6], (int, float)) and _same(r[6], i["amount"]) and r[11] for r, i in zip(rows, items))
    out.append(ck("xlsx.items", same, W("check.items.detail", rows=len(rows), items=len(items)), W("check.items")))
    for side, label in (("bank", W("word.bank_side")), ("book", W("word.book_side"))):
        got = sum((Decimal(str(r[7])) for r, i in zip(rows, items) if i["side"] == side and r[7] is not None), Decimal(0))
        out.append(ck(f"xlsx.adjustments_{side}", same and got.quantize(Decimal("0.01")) == Decimal(st[side]["adjustments"]),
                      f"{got} / {st[side]['adjustments']}", W("check.adjustments", label=label)))
    pairs = table(parts(wb, S_PAIRS), H_PAIRS)
    matched = computed["match"]["groups"]["matched"]
    ok = len(pairs) == len(matched) and all(r[1] and r[2] and r[4] == who(m["confirmed"], m.get("confirmed_by") or "", CONFIRMED_TH)
                                            for r, m in zip(pairs, matched))
    ok = ok and all(m["confirmed"] == "person" for m in matched if m["tier"] != "exact")
    out.append(ck("xlsx.pairs", ok, W("check.n_pairs", n=len(pairs)), W("check.pairs")))
    sus = table(parts(wb, S_SUSPECT), H_SUSPECT)
    out.append(ck("xlsx.suspect", len(sus) == len(computed["match"]["groups"]["suspect"]), W("check.n_items", n=len(sus)), W("check.suspect")))
    jr = table(wb[W(S_JOURNAL)], H_JOURNAL)
    by: dict = {}
    for r in jr:
        d = by.setdefault(r[0], [Decimal(0), Decimal(0)])
        d[0] += Decimal(str(r[5] or 0))
        d[1] += Decimal(str(r[6] or 0))
    ok = len(by) == len(computed["journal"]) and all(d[0].quantize(Decimal("0.01")) == d[1].quantize(Decimal("0.01")) and d[0] > 0
                                                      for d in by.values())
    out.append(ck("xlsx.journal_balances", ok, W("check.n_items", n=len(by)), W("check.journal")))
    co = [r for r in wb[W(S_OUT)].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.carried_out", len(co) == len(computed["carried_out"]) and all(isinstance(r[3], (int, float)) and r[4] for r in co),
                  W("check.n_items", n=len(co)), W("check.carried_out")))
    ex = [r for r in wb[W(S_EXC)].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    run_ex = computed["run_exceptions"]
    ok = len(ex) == len(run_ex) and all(e["reason_code"] in REASON_TH and r[1] == e["reason"] for r, e in zip(ex, run_ex)) and \
        all(r[5] for r, e in zip(ex, run_ex) if e["severity"] == "review")
    out.append(ck("xlsx.exceptions", ok, W("check.n_items", n=len(ex)), W("check.exceptions")))
    left = computed["not_included"]
    n = len([r for r in wb[W(S_LEFT)].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]) if W(S_LEFT) in wb.sheetnames else 0
    out.append(ck("xlsx.not_included", n == len(left), W("check.n_files", n=n), W("check.not_included")))
    out.append(_figures(wb, computed))
    out.extend(workpaper.checks(path, words.lang()))
    return out


def _figures(wb, computed: dict) -> dict:
    """Every amount of the statement, read back: balances, each adjustment line, each item's amount and
    effect, each pair's two totals (AM8)."""
    t = Tally()
    face = _labelled(wb[W(S_STATEMENT)])
    b, st = computed["balances"], computed["statement"]
    for label, side, which in ((W("doc.face.opening_bank"), "bank", "opening"), (W("doc.face.opening_book"), "book", "opening"),
                               (W("doc.face.closing_bank"), "bank", "closing"), (W("doc.face.closing_book"), "book", "closing")):
        want = b[side][which]
        if want is None:
            t.text(label, face.get(label), W("doc.word.unknown"))
        else:
            t.number(label, face.get(label), want)
    for side, adj in (("bank", W(L_BANK_ADJ)), ("book", W(L_BOOK_ADJ))):
        for ln in st[side]["lines"]:
            t.number(f"{ln['sign']}: {ln['label']}", face.get(f"{ln['sign']}: {ln['label']}"), ln["amount"])
        if st[side]["adjusted"] is not None:
            t.number(adj, face.get(adj), st[side]["adjusted"])
    rows = table(parts(wb, S_ITEMS), H_ITEMS)
    for r, i in zip(rows, computed["items"]):
        t.number(W("where.item.amount", no=r[0]), r[6], i["amount"])
        t.number(W("where.item.effect", no=r[0]), r[7], i["effect"])
    pairs = table(parts(wb, S_PAIRS), H_PAIRS)
    for r, m in zip(pairs, computed["match"]["groups"]["matched"]):
        t.number(W("where.pair.book", no=r[0]), r[8], m["left_total"])
        t.number(W("where.pair.bank", no=r[0]), r[13], m["right_total"])
    return t.result("xlsx.figures", W("check.figures"), W("noun.figures"))
