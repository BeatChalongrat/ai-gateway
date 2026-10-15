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

from openpyxl import load_workbook

from ....deliverables import workpaper
from ....deliverables.look.xlsx_styles import Book
from ....deliverables.reread import Tally, sheets, table
from ....engine import words
from ..words import W
from .audit import CHOICES, OUTCOME_TH, REASON_TH, RESOLUTION_TH

S_CLAIMS, S_FACTS, S_EXC, S_RECEIPTS, S_POLICY = "doc.sheet.claims", "doc.sheet.facts", "doc.sheet.exceptions", "doc.sheet.receipts", "doc.sheet.policy"
# AM10: the wide tables' companion sheets — the same identity columns, then the rest, read beside the main sheet
# by row number; every one prints one page across
S_CLAIMS_EV, S_CLAIMS_RULES, S_EXC_EV, S_RECEIPTS_EV = ("doc.sheet.claims_ev", "doc.sheet.claims_rules", "doc.sheet.exceptions_ev",
                                                         "doc.sheet.receipts_ev")
PARTS = {S_CLAIMS: (S_CLAIMS, S_CLAIMS_EV, S_CLAIMS_RULES), S_EXC: (S_EXC, S_EXC_EV), S_RECEIPTS: (S_RECEIPTS, S_RECEIPTS_EV)}
SHEETS = ("doc.sheet.summary", S_CLAIMS, S_CLAIMS_EV, S_CLAIMS_RULES, S_FACTS, S_EXC, S_EXC_EV, S_RECEIPTS, S_RECEIPTS_EV, S_POLICY)
FACT_KEYS = {"claim_data": "doc.fact.claim_data", "category": "doc.fact.category", "within_claim_limit": "doc.fact.within_claim_limit",
           "within_period_limit": "doc.fact.within_period_limit", "receipt": "doc.fact.receipt", "receipt_amount": "doc.fact.receipt_amount",
           "duplicate": "doc.fact.duplicate", "split_purchase": "doc.fact.split_purchase", "prohibited_merchant": "doc.fact.prohibited_merchant",
           "vat_document": "doc.fact.vat_document", "wht": "doc.fact.wht", "weekend_holiday": "doc.fact.weekend_holiday", "purpose": "doc.fact.purpose"}
# The wide tables' columns in the order the data is built and read back (the checks read by name, from the sheets
# of a table together — ``table(parts(wb, sheet), H_*)``)
FACT_TH = W.table(FACT_KEYS)   # said when read
H_CLAIMS = W.heads(["doc.col.no", "doc.col.claim_no", "doc.col.row_in_file", "doc.col.employee_id", "doc.col.employee", "doc.col.date", "doc.col.day", "doc.col.category", "doc.col.merchant",
            "doc.col.amount", "doc.sheet.receipts", "doc.col.receipt_amount", "doc.col.vat_claim", "doc.fact.wht", "doc.col.description", "doc.col.outcome",
                        "doc.col.reason_code", "doc.col.card_answer"] + [FACT_KEYS[f] for f in FACT_KEYS])
H_EXC = W.heads(["doc.col.no", "doc.col.item", "doc.col.reason_code", "doc.col.reason", "doc.col.status", "doc.col.description", "doc.col.proposed", "doc.col.options",
         "doc.col.source", "doc.col.policy_ref", "doc.col.limit_from_table", "doc.col.also_flagged", "doc.col.kb_context"])
H_RECEIPTS = W.heads(["doc.col.file", "doc.col.file_type", "doc.col.read_by", "doc.col.doc_type", "doc.col.seller", "doc.col.tax_id", "doc.col.date", "doc.col.total", "doc.col.page",
              "doc.col.position", "doc.col.doc_text", "doc.col.confidence", "doc.col.amount_decided_by", "doc.col.status", "doc.sheet.claims", "doc.col.note"])
# What prints on each sheet of a wide table, in print order, with the column's width: the main sheet leads with
# the row's identity (ID_COLS of them — the claim, the person, what it was for — repeated on the companions and
# on every page), then its amounts, outcome and reason; a companion repeats the identity and holds the rest.
# Every column of H_* is on one of them.
LAYOUT = {
    S_CLAIMS: ["doc.col.no", "doc.col.claim_no", "doc.col.employee", "doc.col.description", "doc.col.amount", "doc.col.receipt_amount",
               "doc.fact.wht", "doc.col.outcome", "doc.col.date", "doc.col.category", "doc.col.merchant", "doc.sheet.receipts",
               "doc.col.reason_code"],
    S_CLAIMS_EV: ["doc.col.no", "doc.col.claim_no", "doc.col.employee", "doc.col.description", "doc.col.row_in_file", "doc.col.employee_id",
                  "doc.col.card_answer"],
    S_CLAIMS_RULES: ["doc.col.no", "doc.col.claim_no", "doc.col.employee", "doc.col.description", "doc.col.day", "doc.col.vat_claim"]
                    + [FACT_KEYS[f] for f in FACT_KEYS],
    S_EXC: ["doc.col.no", "doc.col.item", "doc.col.reason", "doc.col.limit_from_table", "doc.col.status", "doc.col.description",
            "doc.col.proposed", "doc.col.options", "doc.col.policy_ref"],
    S_EXC_EV: ["doc.col.no", "doc.col.item", "doc.col.reason", "doc.col.reason_code", "doc.col.source", "doc.col.also_flagged",
               "doc.col.kb_context"],
    S_RECEIPTS: ["doc.col.file", "doc.col.seller", "doc.col.total", "doc.col.file_type", "doc.col.read_by", "doc.col.doc_type", "doc.col.tax_id",
                 "doc.col.date", "doc.col.status", "doc.sheet.claims", "doc.col.amount_decided_by"],
    S_RECEIPTS_EV: ["doc.col.file", "doc.col.seller", "doc.col.page", "doc.col.position", "doc.col.doc_text", "doc.col.confidence", "doc.col.note"],
}
ID_COLS = {S_CLAIMS: 4, S_EXC: 3, S_RECEIPTS: 2}
STATUS_TH = W.table({"pass": "doc.status.pass", "fail": "doc.status.fail", "skip": "doc.status.skip"})
DISPOSITION_TH = W.table({"exception": "doc.disposition.exception", "question": "doc.disposition.question", None: "doc.disposition.noted"})
SEVERITY_TH = W.table({"review": "doc.severity.review", "info": "doc.disposition.noted"})
DECIDED_TH = W.table({"document": "doc.decided.document", "typed_by_person": "doc.decided.typed", "confirmed_by_person": "doc.decided.confirmed",
                        "computed": "doc.decided.computed", "model_proposal": "doc.decided.model"})
METHOD_TH = W.table({"pdf_text": "doc.method.pdf_text", "ocr": "doc.method.ocr", "etax_xml": "doc.method.etax", "hybrid": "doc.method.hybrid",
                     "typed_by_person": "doc.decided.typed", "confirmed_by_person": "doc.decided.confirmed"})
KB_TITLE = "doc.kb.title"
# AM4: where in the policy table a limit came from, in the words of the นโยบายที่ใช้ sheet
POLICY_FIELD_TH = W.table({"per_claim": "doc.fact.within_claim_limit", "per_person_month": "doc.policy.per_person_month", "needs_purpose": "doc.policy.needs_purpose",
                   "wht_rate": "doc.policy.wht_rate", "wht_from": "doc.policy.wht_from",
                   "receipt_required_above": "doc.policy.receipt_required_above", "approval_threshold": "doc.policy.approval_threshold",
                   "weekend_holiday": "doc.policy.weekend_holiday", "vat_claim_requires": "doc.policy.vat_claim_requires",
                           "prohibited_merchants": "doc.policy.prohibited_merchants"})


def policy_ref_th(ref, categories: dict) -> str | None:
    """"categories.meals.per_claim" → "นโยบาย › อาหาร › วงเงินต่อครั้ง" (the policy table, not a code)."""
    if not ref:
        return ref
    parts = str(ref).split(".")
    words = [W("doc.policy.root")]
    if parts[0] == "categories" and len(parts) > 1:
        cat = categories.get(parts[1]) or {}
        words.append(str(cat.get("label") or parts[1]))
        parts = parts[2:]
    words += [POLICY_FIELD_TH.get(p, p) for p in parts]
    return " › ".join(words)
NOT_A_LIMIT = "doc.policy.not_a_limit"


def _calendar_line(cal: dict) -> str:
    """Which holiday calendar the audit used — or that the holiday check was off (AM3: fail closed)."""
    if cal.get("active"):
        return W("doc.calendar.on", note=cal.get("note") or W("doc.calendar.company"))
    return W("doc.calendar.off", v=cal.get("note") or W("doc.calendar.unset"))


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def parts(wb, sheet: str) -> list:
    """The sheets a table prints on — the main sheet and the companions that are in the file — for ``table``."""
    return sheets(wb, *(W(k) for k in PARTS.get(sheet, (sheet,))))


class ExpenseWorkbook:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None):
        self.c, self.t, self.run = computed, theme, run or {}
        self.book = Book(theme, words.lang())
        self.wb = self.book.wb

    # ── sheets ─────────────────────────────────────────────────────────────
    def summary(self):
        ws = self.book.sheet(W("doc.sheet.summary"))
        c, t = self.c, self.c["totals"]
        pol = c["policy"]
        self.book.title(ws, W("doc.title.summary", period_label=c["period_label"]),
                    " · ".join(x for x in (c.get("company"), W("doc.face.prepared", prepared_on=c["prepared_on"])) if x))
        by = t["claimed_by_outcome"]
        rows = [[W("doc.face.all_claims"), t["claims"]], [W("doc.face.passed"), t["pass"]], [W("doc.face.exceptions_review"), t["exception"]],
                [W("doc.face.judgement"), t["question"]], [W("doc.face.total_claimed"), _num(t["claimed"])],
                [W("doc.face.total_passed"), _num(by["pass"])], [W("doc.face.total_exceptions"), _num(by["exception"])],
                [W("doc.face.total_judgement"), _num(by["question"])], [W("doc.face.exceptions_rows"), t["review"]],
                [W("doc.face.noted"), t["info"]], [W("doc.face.receipts_read"), t["receipts"]]]
        self.book.table(ws, 4, [W("doc.col.item"), W("doc.col.value")], rows, freeze=False)
        for r in range(9, 13):
            self.book.fmt(ws, r, 2, "money")
        r = 4 + len(rows) + 2
        src = (W("doc.policy.came_with_agent") if pol["source"] == "package_sample"
               else W("doc.policy.set"))
        notes = [
            W("doc.note.rules"),
            W("doc.note.limits", src=src),
            W("doc.note.figures"),
            _calendar_line(pol["calendar"]),
        ]
        for m in c.get("models") or []:
            notes.append(W("doc.note.models", model=m["model"], place=W("doc.model.inside") if m["self_hosted"] else W("doc.model.outside"),
                           n=len(m["documents"])))
        for a in c.get("assumptions") or []:
            if a["code"] in ("kb_differs_from_table", "holiday_calendar_missing_year", "receipt_files_missing", "same_file_twice"):
                notes.append(a["text"])
        for n in notes:
            self.book.note(ws, r, n)
            r += 1

    def claims(self):
        rows = []
        for i, c in enumerate(self.c["claims"], start=1):
            answered = "; ".join(f"{REASON_TH[k]}: {RESOLUTION_TH.get(v, v)}" for k, v in c["resolution"].items())
            if c.get("purpose"):
                answered += f" — “{c['purpose']}”"
            rows.append([i, c["id"], c["row"], c["employee_id"], c["employee_name"], c["date"] or c["date_raw"], c["weekday"],
                         c["category_label"] or c["category_raw"], c["merchant"], _num(c["amount"]), c["receipt"] or c["receipt_ref"],
                         _num(c["receipt_total"]), W("doc.word.yes") if c["vat_claim"] else "", _num(c["wht_deducted"]), c["description"],
                         c["outcome_label"], ", ".join(c["codes"] + c["flags"] + [f"({x})" for x in c["also_flagged"]]),
                         answered or None]
                        + [STATUS_TH[c["facts"][f]["status"]] for f in FACT_TH])
        for sheet, title, sub in ((S_CLAIMS, W("doc.title.claims"), W("doc.sub.claims", s_claims_rules=W(S_CLAIMS_RULES), s_claims_ev=W(S_CLAIMS_EV))),
                                  (S_CLAIMS_EV, W(S_CLAIMS_EV), W("doc.sub.claims_ev", s_claims=W(S_CLAIMS))),
                                  (S_CLAIMS_RULES, W(S_CLAIMS_RULES), W("doc.sub.claims_rules", s_claims=W(S_CLAIMS)))):
            ws = self.book.sheet(W(sheet))
            self.book.title(ws, title, sub)
            self.book.part(ws, H_CLAIMS, rows, [W(h) for h in LAYOUT[sheet]], money_cols=(10, 12, 14), wrap_cols=(8, 9, 15, 16, 17, 18))

    def facts(self):
        ws = self.book.sheet(W(S_FACTS))
        self.book.title(ws, W("doc.title.facts"), W("doc.sub.facts"))
        rows = []
        cats = self.c["policy"]["table"].get("categories") or {}
        for c in self.c["claims"]:
            for f, fd in c["facts"].items():
                ev = fd.get("evidence") or {}
                rows.append([c["id"] or c["key"], FACT_TH[f], f, STATUS_TH[fd["status"]], fd.get("code"),
                             DISPOSITION_TH.get(fd.get("disposition")) if fd["status"] == "fail" else None, fd.get("detail") or None,
                             policy_ref_th(ev.get("policy_ref"), cats), _num(ev.get("limit")), _num(ev.get("amount"))])
        self.book.table(ws, 4, [W("doc.col.claim_no"), W("doc.col.rule"), W("doc.col.rule_code"), W("doc.col.result"), W("doc.col.reason_code"), W("doc.col.consequence"), W("doc.col.description"), W("doc.col.policy_ref"),
                            W("doc.col.limit_from_table"), W("doc.col.amount_checked")], rows,
                    [10, 16, 18, 9, 22, 20, 44, 30, 14, 13], money_cols=(9, 10), wrap_cols=(5, 7, 8))

    def exceptions(self):
        rows = []
        cats = self.c["policy"]["table"].get("categories") or {}
        for i, e in enumerate(self.c["run_exceptions"], start=1):
            s = e["source_ref"]
            where = " · ".join(str(x) for x in (s.get("sheet"), W("doc.where.row", row=s["row"]) if s.get("row") else None, s.get("receipt"),
                                                  W("doc.where.page", page=s["page"]) if s.get("page") else None,
                                                  f"“{s['raw_text']}”" if s.get("raw_text") else None) if x)
            kb = " / ".join(W("doc.where.paragraph", file=k["file"], paragraph=k["paragraph"], text=k["text"]) for k in s.get("kb_context") or [])
            also = ", ".join(a["reason"] for a in s.get("also_flagged") or [])
            rows.append([i, s.get("claim") or e["subject"], e["reason_code"], e["reason"], SEVERITY_TH[e["severity"]],
                         e.get("detail") or None, e["proposed_action"], " / ".join(ch["label"] for ch in e["choices"]) or None,
                         where or None, policy_ref_th(s.get("policy_ref"), cats), _num(s.get("limit")), also or None, kb or None])
        for sheet, title, sub in ((S_EXC, W("doc.sheet.exceptions"), W("doc.sub.exceptions", s_exc_ev=W(S_EXC_EV))),
                                  (S_EXC_EV, W(S_EXC_EV), W("doc.sub.exceptions_ev", s_exc=W(S_EXC)))):
            ws = self.book.sheet(W(sheet))
            self.book.title(ws, title, sub)
            self.book.part(ws, H_EXC, rows, [W(h) for h in LAYOUT[sheet]], money_cols=(11,), wrap_cols=(4, 6, 7, 8, 9, 10, 12, 13))

    def receipts(self):
        rows = []
        for r in self.c["receipts"]:
            ts = r["total_source"] or {}
            rows.append([r["name"], r["tier_label"], METHOD_TH.get(r["method"], r["method"]), r["document_type_label"],
                         r["seller_name"], r["seller_tax_id"], r["document_date"], _num(r["total"]), ts.get("page"), ts.get("bbox"),
                         ts.get("raw_text"), ts.get("confidence"), DECIDED_TH.get(ts.get("decided_by"), ts.get("decided_by")),
                         {"read": W("doc.receipt.read"), "unconfirmed": W("doc.receipt.unconfirmed"), "pending": W("doc.receipt.pending")}.get(r["status"], r["status"]),
                         ", ".join(r["claims"]) or W("doc.receipt.unreferenced"), r["note"] or None])
        for sheet, title, sub in ((S_RECEIPTS, W("doc.face.receipts_read"), W("doc.sub.receipts", s_receipts_ev=W(S_RECEIPTS_EV))),
                                  (S_RECEIPTS_EV, W(S_RECEIPTS_EV), W("doc.sub.receipts_ev", s_receipts=W(S_RECEIPTS)))):
            ws = self.book.sheet(W(sheet))
            self.book.title(ws, title, sub)
            self.book.part(ws, H_RECEIPTS, rows, [W(h) for h in LAYOUT[sheet]], money_cols=(8,), wrap_cols=(4, 5, 11, 13, 14, 16))
        ws = self.wb[W(S_RECEIPTS)]
        tax_col = [c.value for c in ws[4]].index(W("doc.col.tax_id")) + 1
        for i in range(len(rows)):
            self.book.fmt(ws, 5 + i, tax_col, "text")

    def policy(self):
        ws = self.book.sheet(W(S_POLICY))
        pol = self.c["policy"]
        table = pol["table"]
        src = (W("doc.policy.sample_title") if pol["source"] == "package_sample"
               else W("doc.policy.set"))
        self.book.title(ws, W("doc.title.policy"), W("doc.sub.policy", src=src, v=pol["sha256"][:16]))
        cats = table["categories"]
        rows = [[c.get("label") or code, code, _num(c.get("per_claim")), _num(c.get("per_person_month")),
                 W("doc.word.yes") if c.get("needs_purpose") else None, _num(c.get("wht_rate")), _num(c.get("wht_from"))]
                for code, c in cats.items()]
        self.book.table(ws, 4, [W("doc.col.category"), W("doc.col.code"), W("doc.fact.within_claim_limit"), W("doc.policy.per_person_month"), W("doc.policy.needs_purpose"), W("doc.policy.wht_rate"),
                            W("doc.policy.wht_from")], rows, money_cols=(3, 4, 7), freeze=False)
        r = 4 + len(rows) + 2
        self.book.section(ws, r, W("doc.section.other_settings"))
        settings = [[W("doc.policy.receipt_required_above_thb"), _num(table.get("receipt_required_above"))],
                    [W("doc.policy.approval_threshold_thb"), _num(table.get("approval_threshold"))],
                    [W("doc.policy.weekend_holiday"), {"flag": W("doc.policy.flag"), "exception": W("doc.policy.exception"), "allow": W("doc.policy.allow")}.get(
                        table.get("weekend_holiday", "flag"), table.get("weekend_holiday"))],
                    [W("doc.policy.vat_claim_requires"), {"full_tax_invoice": W("doc.policy.full_tax_invoice"), "abbreviated_tax_invoice": W("doc.policy.abbreviated_ok")}.get(
                        table.get("vat_claim_requires", "full_tax_invoice"))],
                    [W("doc.policy.prohibited_words"), ", ".join(table.get("prohibited_merchants") or []) or "-"]]
        self.book.table(ws, r + 1, [W("doc.col.setting"), W("doc.col.value")], settings, freeze=False)
        self.book.fmt(ws, r + 2, 2, "money")
        self.book.fmt(ws, r + 3, 2, "money")
        r = r + 1 + len(settings) + 2
        cal = pol["calendar"]
        if cal.get("active"):
            self.book.section(ws, r, W("doc.section.holidays") + " " + (cal.get("note") or W("doc.calendar.company")))
            hol = [[d, n] for d, n in cal["dates"].items()] or [["-", W("doc.calendar.none")]]
        else:
            self.book.section(ws, r, W("doc.section.calendar_off"))
            hol = [["-", W("doc.calendar.not_checked", v=cal.get("note") or W("doc.calendar.unset"))]]
        self.book.table(ws, r + 1, [W("doc.col.date"), W("doc.col.holiday")], hol, freeze=False)
        r = r + 1 + len(hol) + 2
        self.book.section(ws, r, W(KB_TITLE))
        ctx = [[k["file"], k["paragraph"], k["text"],
                ", ".join(str((cats.get(x) or {}).get("label") or x) for x in k["categories"]) or None, ", ".join(k["numbers"]) or None,
                W(NOT_A_LIMIT)] for k in pol["context"]]
        self.book.table(ws, r + 1, [W("doc.col.file"), W("doc.col.paragraph"), W("doc.col.text"), W("doc.col.category_mentioned"), W("doc.col.numbers_in_text"), W("doc.col.used_as_limit")],
                    ctx or [["-", None, W("doc.kb.none"), None, None, W(NOT_A_LIMIT)]], freeze=False, wrap_cols=(3,))
        r = r + 2 + max(len(ctx), 1) + 1
        for cf in pol["conflicts"]:
            table_says = W("doc.kb.table_sets", table=cf["table"]) if cf["table"] else W("doc.kb.table_no_limit")
            self.book.note(ws, r, W("doc.kb.differs", file=cf["file"], paragraph=cf["paragraph"], label=cf["label"], document=cf["document"], table_says=table_says, policy_ref=policy_ref_th(cf["policy_ref"], cats)), cols=6)
            r += 1

    def write(self, path: str) -> dict:
        self.summary()
        self.claims()
        self.facts()
        self.exceptions()
        self.receipts()
        self.policy()
        # AM8: printable — the claim's number and its person repeat on every page (AM10: and open every companion
        # sheet of a wide table)
        id_cols = {W(s): ID_COLS[main] for main, names in PARTS.items() for s in names}
        self.book.finish(path, id_cols={**id_cols, W(S_FACTS): 2}, author=str(self.c.get("company") or self.run.get("organisation") or ""))
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
    out = [ck("xlsx.sheets", {W(k) for k in SHEETS} <= set(wb.sheetnames), ", ".join(wb.sheetnames), W("check.sheets"))]
    rows = table(parts(wb, S_CLAIMS), H_CLAIMS)
    claims = computed["claims"]
    same = len(rows) == len(claims) and all(r[1] == c["id"] and r[15] == c["outcome_label"] for r, c in zip(rows, claims))
    out.append(ck("xlsx.claims", same, W("check.claims.detail", rows=len(rows), claims=len(claims)), W("check.claims")))
    # the outcomes as the workbook writes them — in the run's language, like the rest of it (AM18)
    known = {v: k for k, v in OUTCOME_TH.items()}
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
    ws = wb[W("doc.sheet.policy")]
    cats = computed["policy"]["table"]["categories"]
    prow = [r for r in ws.iter_rows(min_row=5, max_row=4 + len(cats), values_only=True)]

    def same_limit(cell, v):
        return (cell is None and v in (None, "")) or (cell is not None and v not in (None, "")
                                                       and Decimal(str(cell)).quantize(Decimal("0.01")) == Decimal(str(v)))
    ok = len(prow) == len(cats) and all(r[1] == code and same_limit(r[2], c.get("per_claim")) and
                                        same_limit(r[3], c.get("per_person_month"))
                                        for r, (code, c) in zip(prow, cats.items()))
    kb_at = next((i for i, r in enumerate(ws.iter_rows(values_only=True), start=1) if r and r[0] == W(KB_TITLE)), None)
    kb_rows = [r for r in ws.iter_rows(min_row=(kb_at or 0) + 2, values_only=True) if r and r[5] is not None] if kb_at else []
    ok = ok and kb_at is not None and all(r[5] == W(NOT_A_LIMIT) for r in kb_rows)
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
    for row in wb[W("doc.sheet.summary")].iter_rows(min_row=5, max_col=2, values_only=True):
        if row and isinstance(row[0], str):
            face.setdefault(row[0], row[1])
    tt = computed["totals"]
    by = tt["claimed_by_outcome"]
    for label, want in ((W("doc.face.all_claims"), tt["claims"]), (W("doc.face.passed"), tt["pass"]), (W("doc.face.exceptions_review"), tt["exception"]),
                        (W("doc.face.total_claimed"), tt["claimed"]), (W("doc.face.total_passed"), by["pass"]),
                        (W("doc.face.total_exceptions"), by["exception"]), (W("doc.face.total_judgement"), by["question"])):
        t.number(W("where.summary", label=label), face.get(label), want)
    for r, rec in zip(rc, computed["receipts"]):
        t.number(W("where.receipt", name=rec["name"]), r[7], rec["total"])
    out.append(t.result("xlsx.figures", W("check.figures"), W("noun.figures")))
    out.extend(workpaper.checks(path, words.lang()))
    return out
