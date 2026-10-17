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

from openpyxl import load_workbook

from ....deliverables import workpaper
from ....deliverables.look import commentary
from ....deliverables.look.xlsx_styles import Book
from ....deliverables.reread import Tally, sheets, table
from ....engine import words
from ....engine.e3 import cards as E3C
from ..words import W
from .match import CONFIRMED_TH, DEPTH_SHORT, DEPTH_TH, PASS, REASON_TH, STATUS_TH, THREE, TWO, never_written

# The wide tables' columns in the order the data is built and read back (the checks read by name, from the
# sheets of the table together — ``table(parts(wb, sheet), H_*)``)
H_INVOICES = W.heads(["doc.col.no", "doc.col.number", "doc.col.date", "doc.col.doc_type", "doc.col.seller", "doc.col.tax_id", "doc.col.po_ref", "doc.col.currency", "doc.col.subtotal",
              "doc.col.vat", "doc.col.total", "doc.col.status", "doc.col.reason", "doc.col.depth", "doc.col.vat_claim", "doc.col.note", "doc.col.evidence"])
H_LINES = W.heads(["doc.col.no", "doc.col.invoice", "doc.col.line", "doc.col.item", "doc.col.quantity", "doc.col.unit", "doc.col.unit_price", "doc.col.amount", "doc.col.depth", "doc.col.why_depth",
           "doc.col.po", "doc.col.po_line", "doc.col.po_item", "doc.col.po_unit_price", "doc.col.evidence_po", "doc.col.gr_used", "doc.col.ordered", "doc.col.received",
           "doc.col.billed_before", "doc.col.billed", "doc.col.unit_compared", "doc.col.variance", "doc.col.status", "doc.col.reason", "doc.col.failed_on",
           "doc.col.tolerance_used", "doc.col.pass", "doc.col.tier", "doc.col.confirmed_by", "doc.col.note", "doc.col.evidence_invoice"])
H_PO = W.heads(["doc.col.po", "doc.col.line", "doc.col.item_code", "doc.col.item", "doc.col.kind", "doc.col.po_status", "doc.col.unit", "doc.col.unit_price", "doc.col.ordered", "doc.col.received",
        "doc.col.billed", "doc.col.billed_value", "doc.col.left_to_bill", "doc.col.overbilled", "doc.col.depth", "doc.col.invoices_billing", "doc.col.gr",
        "doc.col.evidence_po"])
H_LINKS = W.heads(["doc.col.no", "doc.col.subject", "doc.col.document", "doc.col.status", "doc.col.pass", "doc.col.tier", "doc.col.confirmed_by", "doc.col.item_left", "doc.col.evidence_left", "doc.col.item_right",
           "doc.col.evidence_right", "doc.col.similarity"])
S_SUMMARY, S_INVOICES, S_LINES, S_PO, S_GRIR = "doc.sheet.summary", "doc.sheet.invoices", "doc.sheet.lines", "doc.sheet.po", "doc.sheet.grir"
S_UNPLANNED, S_NOTES, S_LINKS, S_EXC, S_EVIDENCE, S_LEFT = ("doc.sheet.unplanned", "doc.sheet.notes", "doc.sheet.links", "doc.sheet.exceptions",
                                                           "doc.sheet.evidence", "doc.sheet.left_out")
# AM10: the wide tables' companion sheets — the same identity columns, then the evidence, read beside the main
# sheet by row number; every one prints one page across
S_INVOICES_EV, S_LINES_EV, S_LINES_CRIT = "doc.sheet.invoices_ev", "doc.sheet.lines_ev", "doc.sheet.lines_crit"
S_PO_EV, S_LINKS_EV = "doc.sheet.po_ev", "doc.sheet.links_ev"
PARTS = {S_INVOICES: (S_INVOICES, S_INVOICES_EV), S_LINES: (S_LINES, S_LINES_EV, S_LINES_CRIT), S_PO: (S_PO, S_PO_EV),
         S_LINKS: (S_LINKS, S_LINKS_EV)}
# What prints on each sheet of a wide table, in print order, with the column's width: the main sheet leads with
# the row's identity (ID_COLS of them, repeated on the companions and on every page), then its amounts,
# status and reason; a companion repeats the identity and holds the rest. Every column of H_* is on one of them.
LAYOUT = {
    S_INVOICES: ["doc.col.no", "doc.col.number", "doc.col.seller", "doc.col.subtotal", "doc.col.vat", "doc.col.total", "doc.col.status",
                 "doc.col.date", "doc.col.doc_type", "doc.col.po_ref", "doc.col.currency", "doc.col.reason", "doc.col.depth"],
    S_INVOICES_EV: ["doc.col.no", "doc.col.number", "doc.col.seller", "doc.col.tax_id", "doc.col.vat_claim", "doc.col.note",
                    "doc.col.evidence"],
    S_LINES: ["doc.col.no", "doc.col.invoice", "doc.col.line", "doc.col.item", "doc.col.quantity", "doc.col.unit", "doc.col.unit_price",
              "doc.col.amount", "doc.col.po_unit_price", "doc.col.variance", "doc.col.status", "doc.col.depth", "doc.col.reason"],
    S_LINES_EV: ["doc.col.no", "doc.col.invoice", "doc.col.line", "doc.col.item", "doc.col.po", "doc.col.po_line", "doc.col.po_item",
                 "doc.col.evidence_po", "doc.col.gr_used", "doc.col.evidence_invoice", "doc.col.note"],
    S_LINES_CRIT: ["doc.col.no", "doc.col.invoice", "doc.col.line", "doc.col.item", "doc.col.why_depth", "doc.col.ordered",
                   "doc.col.received", "doc.col.billed_before", "doc.col.billed", "doc.col.unit_compared", "doc.col.failed_on",
                   "doc.col.tolerance_used", "doc.col.pass", "doc.col.tier", "doc.col.confirmed_by"],
    S_PO: ["doc.col.po", "doc.col.line", "doc.col.item", "doc.col.unit_price", "doc.col.billed_value", "doc.col.item_code", "doc.col.kind",
           "doc.col.po_status", "doc.col.unit", "doc.col.ordered", "doc.col.received", "doc.col.billed", "doc.col.left_to_bill",
           "doc.col.overbilled", "doc.col.depth"],
    S_PO_EV: ["doc.col.po", "doc.col.line", "doc.col.item", "doc.col.invoices_billing", "doc.col.gr", "doc.col.evidence_po"],
    S_LINKS: ["doc.col.no", "doc.col.subject", "doc.col.document", "doc.col.status", "doc.col.pass", "doc.col.tier", "doc.col.confirmed_by",
              "doc.col.item_left", "doc.col.item_right", "doc.col.similarity"],
    S_LINKS_EV: ["doc.col.no", "doc.col.subject", "doc.col.document", "doc.col.evidence_left", "doc.col.evidence_right"],
}
ID_COLS = {S_INVOICES: 3, S_LINES: 4, S_PO: 3, S_LINKS: 3}
SHEETS = (S_SUMMARY, S_INVOICES, S_INVOICES_EV, S_LINES, S_LINES_EV, S_LINES_CRIT, S_PO, S_PO_EV, S_GRIR, S_UNPLANNED, S_NOTES,
          S_LINKS, S_LINKS_EV, S_EXC, S_EVIDENCE)
L_COMPANY, L_PERIOD, L_PREPARER, L_PRACTICE, L_DEPTH, L_APPROVAL = ("doc.face.company", "doc.face.period", "doc.face.preparer", "doc.face.practice",
                                                                   "doc.face.depth", "doc.face.approval")
L_INVOICES, L_LINES = "doc.face.invoices", "doc.face.lines"
L_THREE, L_TWO, L_NONE = "doc.face.three_way", "doc.face.two_way", "doc.face.unlinked"
L_ACCRUED, L_PREPAID = "doc.face.accrued", "doc.face.prepaid"
TWO_WAY_WARNING = ("doc.note.two_way")
APPROVAL = "doc.note.approval"
# AM8: no placeholder stands in for the company or the preparer; a paper without one says it is a draft
MISSING = "doc.word.missing"
HEADER_DRAFT = "doc.draft.header"


def header_fields(c: dict, run: dict | None) -> list[tuple[str, str]]:
    run = run or {}
    return [(W(L_COMPANY), str(c.get("company") or run.get("organisation") or "").strip()),
            (W(L_PREPARER), str(run.get("preparer") or "").strip())]
FROM_TH = W.table({"history": "doc.from.history", "round": "doc.from.round"})
STATE_TH = W.table({"linked": "doc.state.linked", "waiting": "doc.state.waiting"})
KIND_TH = W.table({"receipt": "doc.kind.receipt", "invoice": "doc.kind.invoice", "duplicate": "doc.kind.duplicate"})


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def _where(s: dict | None) -> str:
    s = s or {}
    cells = ", ".join(sorted(set((s.get("cells") or {}).values()))) if isinstance(s.get("cells"), dict) else ""
    return " · ".join(str(x) for x in (s.get("file"), s.get("sheet"), W("doc.where.row", row=s["row"]) if s.get("row") else "",
                                       W("doc.where.page", page=s["page"]) if s.get("page") and not s.get("sheet") else "", cells) if x)


def who(kind: str, name: str) -> str:
    return f"{CONFIRMED_TH[kind]}: {name}" if kind == "person" and name else CONFIRMED_TH.get(kind, "")


def _tolerances(t: dict, labels: dict) -> str:
    return " · ".join(f"{labels.get(k, k)} = {v}" for k, v in sorted(t.items()))


def parts(wb, sheet: str) -> list:
    """The sheets a table prints on — the main sheet and the companions that are in the file — for ``table``."""
    return sheets(wb, *(W(k) for k in PARTS.get(sheet, (sheet,))))


class MatchWorkbook:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None, narrative: dict | None = None,
                 req: dict | None = None):
        self.c, self.t, self.run = computed, theme, run or {}
        # AM22: the commentary (a model's or the rules', with who wrote it) printed on its own sheet after the face
        self.narrative = narrative or dict(computed.get("fallback_narrative") or {}, written_by="rules")
        self.req = req or {}  # AM23: the render request (the organisation's switch for the neutral line)
        self.commentary: dict = {}
        self.book = Book(theme, words.lang())
        self.wb = self.book.wb
        self.inv = {i["key"]: i for i in computed["invoices"]}
        self.labels = computed["settings"]["labels"]

    # ── sheets ─────────────────────────────────────────────────────────────
    def summary(self):
        ws = self.book.sheet(W(S_SUMMARY))
        c, s, t = self.c, self.c["summary"], self.c["totals"]
        head = dict(header_fields(c, self.run))
        self.book.title(ws, W("doc.title.summary", period_label=c["period_label"]), head[W(L_COMPANY)])
        missing = [k for k, v in head.items() if not v]
        if missing:
            self.book.mark(ws, 3, W(HEADER_DRAFT).format(", ".join(missing)))
        preparer = head[W(L_PREPARER)]
        made = E3C.date_th(self.run.get("date") or c["prepared_on"])
        depth = W("doc.face.depth_line", three_way=t["three_way"], two_way=t["two_way"])
        if t["unlinked"]:
            depth += " " + W("doc.face.unlinked_line", unlinked=t["unlinked"])
        r = 4
        for label, value in ((W(L_COMPANY), head[W(L_COMPANY)] or W(MISSING)), (W(L_PERIOD), c["period_label"]),
                             (W(L_PREPARER), W("doc.face.prepared_line", preparer=preparer, made=made, v=self.run.get("agent_version") or "").rstrip(" ·")
                              if preparer else W(MISSING)),
                             (W(L_PRACTICE), c["practice"]["label"]), (W(L_DEPTH), depth), (W(L_APPROVAL), W(APPROVAL))):
            self.book.pair(ws, r, label, value, strong=label == W(L_DEPTH))
            r += 1
        if t["two_way"]:
            self.book.note(ws, r, W(TWO_WAY_WARNING), bad=True)
            r += 1
        r += 1
        self.book.section(ws, r, W("doc.section.results"))
        rows = [[x["label"], x["count"], _num(x["value"]), W("doc.face.without_value", without_value=x["without_value"]) if x["without_value"] else ""]
                for x in s["by_status"]]
        self.book.table(ws, r + 1, [W("doc.col.status"), W("doc.col.count_docs"), W("doc.col.value_in", currency=s["currency"]), W("doc.col.note")], rows, money_cols=(3,), freeze=False)
        r += len(rows) + 3
        self.book.pair(ws, r, W(L_INVOICES), t["invoices"])
        self.book.pair(ws, r + 1, W(L_LINES), t["lines"])
        self.book.pair(ws, r + 2, W(L_THREE), t["three_way"])
        self.book.pair(ws, r + 3, W(L_TWO), t["two_way"])
        self.book.pair(ws, r + 4, W(L_NONE), t["unlinked"])
        self.book.pair(ws, r + 5, W(L_ACCRUED), _num(t["received_not_billed"]), money=True)
        self.book.pair(ws, r + 6, W(L_PREPAID), _num(t["billed_not_received"]), money=True)
        r += 8
        if s["two_way_why"]:
            self.book.section(ws, r, W("doc.section.why_two_way"))
            rows = [[x["why"], x["lines"]] for x in s["two_way_why"]]
            self.book.table(ws, r + 1, [W("doc.col.reason"), W("doc.col.count_lines")], rows, freeze=False)
            r += len(rows) + 3
        self.book.section(ws, r, W("doc.section.why_blocked"))
        rows = [[x["label"], STATUS_TH[x["outcome"]], x["invoices"], x["lines"], _num(x["value"])] for x in s["by_reason"]]
        self.book.table(ws, r + 1, [W("doc.col.reason"), W("doc.col.result"), W("doc.col.invoices_count"), W("doc.col.line"), W("doc.col.value_in", currency=s["currency"])],
                    rows or [[W("doc.word.none"), "", 0, 0, 0.0]], money_cols=(5,), freeze=False)
        r += max(len(rows), 1) + 3
        if s["other_currencies"]:
            self.book.section(ws, r, W("doc.section.foreign"))
            rows = [[x["currency"], x["count"], _num(x["total"])] for x in s["other_currencies"]]
            self.book.table(ws, r + 1, [W("doc.col.currency"), W("doc.col.count_docs"), W("doc.col.total_in_currency")], rows, money_cols=(3,), freeze=False)
            r += len(rows) + 3
        notes = [W("doc.note.lines"),
                 W("doc.note.figures"),
                 c["grir"]["note"]] + [a["text"] for a in c.get("assumptions") or []]
        for n in notes:
            self.book.note(ws, r, n)
            r += 1

    def invoices(self):
        rows = []
        for n, i in enumerate(self.c["invoices"], start=1):
            rows.append([n, i["number"], E3C.date_th(i["date"]), i["doc_type_label"], i["supplier"]["name"], i["supplier"]["tax_id"],
                         ", ".join(i["po_named"]), i["currency"], _num(i["subtotal"]), _num(i["vat_amount"]), _num(i["total"]),
                         i["status_label"], " · ".join(i["reason_labels"]), i["depth_text"],
                         (W("doc.vat.claimable") if i["vat"]["claimable"] else W("doc.vat.not_claimable")) + " " + i["vat"]["why"], "\n".join(i["notes"]), _where(i)])
        for sheet, sub in ((S_INVOICES, W("doc.sub.invoices")),
                           (S_INVOICES_EV, W("doc.sub.invoices_ev", s_invoices=W(S_INVOICES)))):
            ws = self.book.sheet(W(sheet))
            self.book.title(ws, W(sheet), sub)
            self.book.part(ws, H_INVOICES, rows, [W(h) for h in LAYOUT[sheet]], money_cols=(9, 10, 11), wrap_cols=(4, 5, 12, 13, 15, 16, 17))

    def line_rows(self) -> list:
        rows = []
        for n, x in enumerate(self.c["lines"], start=1):
            inv = self.inv[x["invoice"]]
            po = x["po_line"] or {}
            cum = x["cumulative"] or {}
            link = x["link"] or {}
            var = x.get("price_variance") or {}
            if x["unplanned"] is not None:
                depth, why = W("doc.sheet.unplanned"), x["unplanned"]["label"]
            else:
                depth, why = x["depth_label"], x["depth_why"]
            receipts = "\n".join(f"{g['gr']} {E3C.date_th(g['date'])} {g['quantity_in_po_unit']} · " +
                                 ((W("doc.gr.confirmed_by", confirmed_by=g["confirmed_by"]) if g["confirmed_by"] else W("doc.gr.confirmed")) if g["in_system"] else _where(g))
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
        for sheet, sub in ((S_LINES, W("doc.sub.lines", s_lines_ev=W(S_LINES_EV), s_lines_crit=W(S_LINES_CRIT))),
                           (S_LINES_EV, W("doc.sub.lines_ev", s_lines=W(S_LINES))),
                           (S_LINES_CRIT, W("doc.sub.lines_crit", s_lines=W(S_LINES)))):
            ws = self.book.sheet(W(sheet))
            self.book.title(ws, W(sheet), sub)
            self.book.part(ws, H_LINES, rows, [W(h) for h in LAYOUT[sheet]], money_cols=(7, 8, 14, 22), qty_cols=(5, 17, 18, 19, 20),
                       wrap_cols=(4, 9, 10, 13, 15, 16, 23, 24, 25, 26, 27, 29, 30, 31))

    def po(self):
        rows = []
        for p in self.c["po_lines"]:
            invs = "\n".join(f"{e['invoice']} {E3C.date_th(e['date'])} {e['quantity']} ({FROM_TH[e['from']]})" for e in p["invoices"])
            grs = "\n".join(f"{g['gr']} {E3C.date_th(g['date'])} {g['quantity']}" for g in p["receipts"])
            rows.append([p["po"], p["line_no"], p["item_code"], p["description"], p["kind_label"], p["status_label"], p["unit"],
                         _num(p["unit_price"]), _num(p["quantity"]), _num(p["received"]), _num(p["billed"]), _num(p["billed_amount"]),
                         _num(p["open_to_bill"]), p["over_billed_why"], DEPTH_SHORT[p["depth"]], invs, grs, _where(p)])
        for sheet, title, sub in ((S_PO, W("doc.title.po"), W("doc.sub.po")),
                                  (S_PO_EV, W(S_PO_EV), W("doc.sub.po_ev", s_po=W(S_PO)))):
            ws = self.book.sheet(W(sheet))
            self.book.title(ws, title, sub)
            self.book.part(ws, H_PO, rows, [W(h) for h in LAYOUT[sheet]], money_cols=(8, 12), qty_cols=(9, 10, 11, 13), wrap_cols=(4, 14, 16, 17, 18))

    def grir(self):
        ws = self.book.sheet(W(S_GRIR))
        g = self.c["grir"]
        self.book.title(ws, W("doc.title.grir"), f"{g['covers']} · {g['note']}")
        self.book.pair(ws, 4, W(L_ACCRUED), _num(g["received_not_billed"]), money=True, strong=True)
        self.book.pair(ws, 5, W(L_PREPAID), _num(g["billed_not_received"]), money=True, strong=True)
        rows = []
        for p in self.c["po_lines"]:
            if p["id"] not in g["lines"]:
                continue
            rows.append([p["po"], p["line_no"], p["description"], p["unit"], _num(p["unit_price"]), _num(p["received"]), _num(p["billed"]),
                         _num(p["received_not_billed"]), _num(p["received_not_billed_amount"]), _num(p["billed_not_received"]),
                         _num(p["billed_not_received_amount"]), _where(p)])
        self.book.table(ws, 7, ["PO", W("doc.col.line"), W("doc.col.item"), W("doc.col.unit"), W("doc.col.po_unit_price"), W("doc.col.received"), W("doc.col.billed"), W("doc.col.received_not_billed"),
                            W("doc.col.received_not_billed_value"), W("doc.col.billed_not_received"), W("doc.col.billed_not_received_value"), W("doc.col.evidence_po")], rows,
                    money_cols=(5, 9, 11), qty_cols=(6, 7, 8, 10), wrap_cols=(3, 12), freeze=False)

    def unplanned(self):
        ws = self.book.sheet(W(S_UNPLANNED))
        self.book.title(ws, W(S_UNPLANNED), W("doc.sub.unplanned"))
        rows = [[u["invoice_number"], u["supplier"], u["label"], _num(u["amount"]), W("doc.unplanned.within") if u["within_limit"] else W("doc.unplanned.over")]
                for u in self.c["unplanned"]]
        self.book.table(ws, 4, [W("doc.col.invoice"), W("doc.col.seller"), W("doc.col.charge_kind"), W("doc.col.amount"), W("doc.col.result")], rows, money_cols=(4,),
                    wrap_cols=(2, 3))

    def notes(self):
        ws = self.book.sheet(W(S_NOTES))
        self.book.title(ws, W(S_NOTES), W("doc.sub.notes"))
        rows = [[x["number"], E3C.date_th(x["date"]), x["kind_label"], x["supplier"], _num(x["total"]), x["ref_invoice"],
                 W("doc.word.found") if x["corrects"] else W("doc.word.not_found"), x["status_label"], " · ".join(x["reasons"])] for x in self.c["notes"]]
        self.book.table(ws, 4, [W("doc.col.number"), W("doc.col.date"), W("doc.col.note_kind"), W("doc.col.seller"), W("doc.col.total"), W("doc.col.refers_to"), W("doc.col.original_invoice"), W("doc.col.status"), W("doc.col.reason")], rows,
                    [18, 14, 16, 40, 16, 20, 14, 22, 44], money_cols=(5,), wrap_cols=(4, 9), first=[W("doc.col.number"), W("doc.col.seller"), W("doc.col.total")])

    def links(self):
        rows = []
        for n, m in enumerate(self.c["match"]["links"], start=1):
            def join(rs, f):
                return "\n".join(str(f(r)) for r in rs)
            state = STATE_TH[m["state"]] if m["state"] == "linked" or m["decision"] is None else W("doc.status.skipped")
            rows.append([n, KIND_TH[m["kind"]], m["document"], state, m["pass_label"], m["tier_label"],
                         who(m["confirmed"], m["confirmed_by"]) if m["state"] == "linked" else "",
                         join(m["left"], lambda r: f"{r['ref']} {r['name']}".strip()), join(m["left"], _where),
                         join(m["right"], lambda r: f"{r['ref']} {r['name']}".strip()), join(m["right"], _where),
                         _num(m["similarity"])])
        for sheet, sub in ((S_LINKS, W("doc.sub.links")),
                           (S_LINKS_EV, W("doc.sub.links_ev", s_links=W(S_LINKS)))):
            ws = self.book.sheet(W(sheet))
            self.book.title(ws, W(sheet), sub)
            self.book.part(ws, H_LINKS, rows, [W(h) for h in LAYOUT[sheet]], wrap_cols=(4, 5, 6, 7, 8, 9, 10, 11))

    def exceptions(self):
        ws = self.book.sheet(W(S_EXC))
        self.book.title(ws, W("doc.sheet.exceptions"), W("doc.sub.exceptions"))
        rows = []
        for n, e in enumerate(self.c["run_exceptions"], start=1):
            src = "; ".join(_where(s) for s in (e["source_ref"].get("rows") or []) if s)
            rows.append([n, e["reason"], e["detail"], e["proposed_action"], " / ".join(ch["label"] for ch in e["choices"]), src])
        self.book.table(ws, 4, [W("doc.col.no"), W("doc.col.reason"), W("doc.col.detail"), W("doc.col.proposed"), W("doc.col.options"), W("doc.col.evidence")], rows,
                    wrap_cols=(2, 3, 4, 5, 6))

    def evidence(self):
        ws = self.book.sheet(W(S_EVIDENCE))
        c = self.c
        self.book.title(ws, W("doc.col.evidence"), W("doc.sub.evidence"))
        kinds = {"po": W("doc.role.po"), "gr": W("doc.col.gr"), "invoice": W("doc.col.invoice")}
        rows = [[kinds.get(f["kind"], f["kind"]), f["name"], f["sheet"] or "", f["header_row"], f["rows"],
                 ", ".join(f"{k}={v}" for k, v in sorted((f["columns"] or {}).items())), " · ".join(f["said_by"]), f["sha256"]]
                for f in c["files"]]
        self.book.table(ws, 4, [W("doc.col.used_as"), W("doc.col.file"), W("doc.col.sheet"), W("doc.col.header_row"), W("doc.col.count"), W("doc.col.columns_read"), W("doc.col.known_by"), "SHA-256"], rows,
                    [18, 30, 16, 10, 10, 40, 30, 36], wrap_cols=(2, 3, 6, 7, 8), freeze=False)
        r = 4 + len(rows) + 2
        self.book.section(ws, r, W("doc.section.settings"))
        srows = [[c["practice"]["question"], c["practice"]["label"], ""]]
        srows += [[self.labels[k], v, W("doc.note.setting_unusable") if k in c["settings"]["unusable"] else ""]
                  for k, v in c["settings"]["tolerances"].items()]
        srows += [[W("doc.setting.unit_conversion"), f"1 {x['from']} = {x['factor']} {x['to']}" + (" " + W("doc.setting.only_item", item=x["item"]) if x.get("item") else ""),
                   W("doc.setting.answered") if x.get("answered") else W("doc.setting.in_agent")] for x in c["settings"]["conversions"]]
        self.book.table(ws, r + 1, [W("doc.col.setting"), W("doc.col.used_this_run"), W("doc.col.note")], srows, freeze=False)
        r += len(srows) + 3
        self.book.section(ws, r, W("doc.section.tiers"))
        trows = [[t["label"], t["groups"], t["by_rule"], t["by_person"], t["waiting"]] for t in c["match"]["tiers"]]
        self.book.table(ws, r + 1, [W("doc.col.tier"), W("doc.col.pairs_matched"), W("doc.col.by_rule"), W("doc.col.by_person"), W("doc.state.waiting")], trows, freeze=False)
        r += len(trows) + 3
        prows = [[p["label"], W("doc.pass.auto") if p["auto"] else W("doc.pass.proposed"), p["by_rule"] + p["by_person"], p["waiting"]]
                 for p in c["match"]["passes"]]
        self.book.table(ws, r + 1, [W("doc.col.pass_in_order"), W("doc.col.result"), W("doc.col.pairs_matched"), W("doc.state.waiting")], prows, freeze=False)

    def left_out(self):
        ws = self.book.sheet(W(S_LEFT))
        self.book.title(ws, W(S_LEFT), W("doc.sub.left_out"))
        rows = [[e["name"], e["reason"], e["detail"], e["how"]] for e in self.c["not_included"]]
        self.book.table(ws, 4, [W("doc.col.file"), W("doc.col.reason"), W("doc.col.detail"), W("doc.col.how_back")], rows, wrap_cols=(2, 3, 4))

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
        # AM8: printable — the face on one page; the columns that say which invoice and line a row is repeat on
        # every page beside its figures (AM10: and open every companion sheet of a wide table)
        id_cols = {W(s): ID_COLS[main] for main, names in PARTS.items() for s in names}
        # AM22 §1.1: the commentary sheet, right after the face, with the line that says who wrote it
        self.commentary = commentary.sheet(self.book, self.narrative, self.c, self.req)
        self.book.finish(path, id_cols={**id_cols, W(S_NOTES): 2, W(S_UNPLANNED): 2, W(S_GRIR): 3, W(S_EXC): 2, W(S_EVIDENCE): 2, W(S_LEFT): 1}, author=str(self.c.get("company") or self.run.get("organisation") or ""))
        t = self.c["totals"]
        return {"sheets": list(self.wb.sheetnames), "invoices": t["invoices"], "passed": t["passed"], "blocked": t["blocked"],
                "person": t["person"], "three_way": t["three_way"], "two_way": t["two_way"], "run_exceptions": t["run_exceptions"],
                "layouts": [self.commentary["layout"]]}


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
    out = [ck("xlsx.sheets", {W(k) for k in SHEETS} <= set(wb.sheetnames), ", ".join(wb.sheetnames), W("check.sheets"))]
    face = _labelled(wb[W(S_SUMMARY)])
    a3 = str(wb[W(S_SUMMARY)]["A3"].value or "")
    header_draft = W(HEADER_DRAFT).split("{}")[0]   # the mark on the face when a header field has no value
    h = Tally()
    for label, want in header_fields(computed, run):
        got = str(face.get(label) or "")
        h.n += 1
        if want and not got.startswith(want):
            h.bad.append(W("check.header.differs", label=label, got=got[:40], want=want))
        elif not want and (got != W(MISSING) or header_draft not in a3):
            h.bad.append(W("check.header.no_value", label=label, got=got))
    for k in (W(L_PERIOD), W(L_PRACTICE), W(L_DEPTH), W(L_APPROVAL)):
        h.n += 1
        if not face.get(k):
            h.bad.append(W("check.header.empty", k=k))
    out.append(h.result("xlsx.header", W("check.header"),
                        W("noun.header")))
    depth = str(face.get(W(L_DEPTH)) or "")
    says = W("doc.face.depth_line", three_way=t["three_way"], two_way=t["two_way"]) in depth
    warned = any(r and r[0] == W(TWO_WAY_WARNING) for r in wb[W(S_SUMMARY)].iter_rows(min_row=1, max_col=1, values_only=True))
    out.append(ck("xlsx.depth_on_face", says and warned == (t["two_way"] > 0), depth,
                  W("check.depth_on_face")))
    ok = (_same(face.get(W(L_THREE)), t["three_way"]) and _same(face.get(W(L_TWO)), t["two_way"]) and _same(face.get(W(L_INVOICES)), t["invoices"])
          and _same(face.get(W(L_ACCRUED)), t["received_not_billed"]) and _same(face.get(W(L_PREPAID)), t["billed_not_received"]))
    for x in s["by_status"]:
        ok = ok and _same(face.get(x["label"]), x["count"])
    out.append(ck("xlsx.summary", ok, W("check.n_invoices", n=face.get(W(L_INVOICES))), W("check.summary")))
    inv = table(parts(wb, S_INVOICES), H_INVOICES)
    invoices = computed["invoices"]
    ok = len(inv) == len(invoices) and all(r[11] == i["status_label"] and r[13] == i["depth_text"] and
                                           (i["status"] == PASS or r[12]) and (i["total"] is None or _same(r[10], i["total"]))
                                           for r, i in zip(inv, invoices))
    out.append(ck("xlsx.invoices", ok, W("check.invoices.detail", n=len(inv), want=len(invoices)), W("check.invoices")))
    ln = table(parts(wb, S_LINES), H_LINES)
    lines = computed["lines"]
    same = len(ln) == len(lines)
    depth_ok = same and all(r[8] == (x["depth_label"] if x["unplanned"] is None else W("doc.sheet.unplanned")) and r[9]
                            for r, x in zip(ln, lines))
    out.append(ck("xlsx.line_depth", depth_ok, W("check.lines.detail", n=len(ln), want=len(lines)), W("check.line_depth")))
    linked = same and all((x["po_line"] is None) or (r[10] and r[14]) for r, x in zip(ln, lines))
    out.append(ck("xlsx.line_links", linked, "", W("check.line_links")))
    three = same and all(r[15] for r, x in zip(ln, lines) if x["depth"] == THREE and x["unplanned"] is None)
    two = same and all(not r[15] for r, x in zip(ln, lines) if x["depth"] == TWO)
    out.append(ck("xlsx.line_receipts", three and two, "", W("check.line_receipts")))
    tol = same and all(r[25] for r, x in zip(ln, lines) if x["tolerances"])
    out.append(ck("xlsx.tolerances", tol, "", W("check.tolerances")))
    nums = same and all((x["amount"] is None or _same(r[7], x["amount"])) and (x["quantity"] is None or _same(r[4], x["quantity"]))
                        for r, x in zip(ln, lines))
    out.append(ck("xlsx.line_numbers", nums, "", W("check.line_numbers")))
    po = table(parts(wb, S_PO), H_PO)
    ok = len(po) == len(computed["po_lines"]) and all(_same(r[10], p["billed"]) and (p["received"] is None or _same(r[9], p["received"]))
                                                      and bool(r[13]) == p["over_billed"] for r, p in zip(po, computed["po_lines"]))
    out.append(ck("xlsx.cumulative", ok, W("check.n_po_lines", n=len(po)), W("check.cumulative")))
    ex = _rows(wb[W(S_EXC)])
    run_ex = computed["run_exceptions"]
    ok = len(ex) == len(run_ex) and all(e["reason_code"] in REASON_TH and r[1] == e["reason"] and r[4] for r, e in zip(ex, run_ex))
    out.append(ck("xlsx.exceptions", ok, W("check.n_items", n=len(ex)), W("check.exceptions")))
    lk = table(parts(wb, S_LINKS), H_LINKS)
    links = computed["match"]["links"]
    ok = len(lk) == len(links) and all(r[4] and r[5] and (m["state"] != "linked" or r[6]) for r, m in zip(lk, links))
    ok = ok and all(m["confirmed"] == "person" for m in links if m["state"] == "linked" and m["tier"] != "exact")
    out.append(ck("xlsx.links", ok, W("check.n_pairs", n=len(lk)), W("check.links")))
    # the words the paper must not say of any invoice, in either language (the same list the model is held to)
    forbidden = never_written()
    approved = any(w in str(c) for ws in wb.worksheets for r in ws.iter_rows(values_only=True) for c in r if c for w in forbidden)
    out.append(ck("xlsx.no_approval", not approved and face.get(W(L_APPROVAL)) == W(APPROVAL), "", W("check.no_approval")))
    left = computed["not_included"]
    n = len(_rows(wb[W(S_LEFT)])) if W(S_LEFT) in wb.sheetnames else 0
    out.append(ck("xlsx.not_included", n == len(left), W("check.n_files", n=n), W("check.not_included")))
    f = Tally()
    for r, i in zip(inv, invoices):
        for col, where, key in ((8, "where.invoice.subtotal", "subtotal"), (9, "where.invoice.vat", "vat_amount"),
                                (10, "where.invoice.total", "total")):
            f.number(W(where, number=i["number"]), r[col], i[key])
    for r, x in zip(ln, lines):
        for col, where, key in ((4, "where.line.qty", "quantity"), (6, "where.line.unit_price", "unit_price"), (7, "where.line.amount", "amount")):
            f.number(W(where, no=r[0], invoice=r[1]), r[col], x[key])
    out.append(f.result("xlsx.figures", W("check.figures"), W("noun.figures")))
    out.extend(workpaper.checks(path, words.lang()))
    return out
