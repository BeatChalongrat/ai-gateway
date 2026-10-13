"""The register as .xlsx — the deliverable a bookkeeper checks and keys from.

Sheets: summary · register (one row per recorded document) · documents not included (AM5: every document
left out of the register, why, and how it comes back) · line items · source (every value: page, box,
method, printed text, confidence, who decided it) · exceptions (reason-coded) · checks (each document's
deterministic checks). AM18: every word the register itself says has a key and is said in the run's language
(the manifest holds the Thai and the English; the module keeps keys, W says them when read). Amounts are numbers, not text, so a reader can sum them again;
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
NOT_INCLUDED = "doc.sheet.not_included"
S_REGISTER, S_REGISTER_EV = "doc.sheet.register", "doc.sheet.register_ev"
SHEETS = ("doc.sheet.summary", S_REGISTER, S_REGISTER_EV, NOT_INCLUDED, "doc.sheet.items", "doc.sheet.provenance", "doc.sheet.exceptions", "doc.sheet.checks")
# The register's columns in the order the data is built and read back (the gate reads by name, from both of its
# sheets together — ``table(parts(wb, S_REGISTER), H_REGISTER)``)
H_REGISTER = W.heads(["doc.col.no", "doc.col.date", "doc.col.number", "doc.col.doc_type", "doc.col.seller", "doc.col.seller_tax_id", "doc.col.branch", "doc.col.buyer", "doc.col.buyer_tax_id", "doc.col.currency",
              "doc.col.subtotal", "doc.col.vat", "doc.col.total", "doc.col.wht", "doc.col.payable", "doc.col.fx_rate", "doc.col.subtotal_thb", "doc.col.vat_thb",
              "doc.col.total_thb", "doc.sheet.provenance", "doc.col.file"])
PARTS = {S_REGISTER: (S_REGISTER, S_REGISTER_EV)}
# AM10: what prints on each sheet of the register, in print order, with the column's width: the main sheet leads
# with the row's identity (ID_COLS of them, repeated on the companion and on every page), then the seller and
# every amount; the companion repeats the identity and holds the rest. Every column of H_REGISTER is on one.
LAYOUT = {
    S_REGISTER: [("doc.col.no", 6), ("doc.col.date", 11), ("doc.col.number", 15), ("doc.col.seller", 24), ("doc.col.seller_tax_id", 15), ("doc.col.doc_type", 14), ("doc.col.currency", 7),
                 ("doc.col.subtotal", 12), ("doc.col.vat", 12), ("doc.col.total", 12), ("doc.col.wht", 12), ("doc.col.payable", 12), ("doc.col.fx_rate", 9),
                 ("doc.col.subtotal_thb", 13), ("doc.col.vat_thb", 12), ("doc.col.total_thb", 13)],
    S_REGISTER_EV: [("doc.col.no", 6), ("doc.col.date", 11), ("doc.col.number", 15), ("doc.col.branch", 7), ("doc.col.buyer", 26), ("doc.col.buyer_tax_id", 15), ("doc.sheet.provenance", 15),
                    ("doc.col.file", 30)],
}
ID_COLS = {S_REGISTER: 3}
DECIDED_TH = W.table({"document": "doc.decided.document", "typed_by_person": "doc.decided.typed", "confirmed_by_person": "doc.decided.confirmed",
                        "computed": "doc.decided.computed", "model_proposal": "doc.decided.model"})
METHOD_TH = W.table({"pdf_text": "doc.method.pdf_text", "ocr": "doc.method.ocr", "etax_xml": "doc.method.etax", "typed_by_person": "doc.decided.typed",
                     "confirmed_by_person": "doc.decided.confirmed", "chosen_by_person": "doc.method.chosen",
                     # AM4: the rest a person could meet in the source sheet
                     "document_scan": "doc.method.document_scan", "default": "doc.method.default", "hybrid": "doc.method.hybrid"})


def method_th(method) -> str:
    """How a value was read, in Thai ("computed:sum_of_lines" → คำนวณจากรายการในเอกสาร)."""
    m = str(method or "")
    if m in METHOD_TH:
        return METHOD_TH[m]   # (a table of keys, said when read)
    if m.startswith("computed:"):
        return W("doc.method.computed_lines") if "lines" in m else W("doc.decided.computed")
    if m.startswith("vision:"):
        return W("doc.method.vision", v=m.split(":", 1)[1])
    return m
STATUS_TH = W.table({"pass": "doc.status.pass", "fail": "doc.status.fail", "skip": "doc.status.skip"})

# AM3: what each check looks at, in words that do not sound like a finding (a stranger read the
# card headers "VAT ไม่ตรง" / "duplicate" beside "ผ่าน" as problems with their documents).
CHECK_NAME_TH = W.table({
    "seller_tax_id_format": "doc.check.seller_tax_id_format", "buyer_tax_id_format": "doc.check.buyer_tax_id_format",
    "document_date": "doc.check.document_date", "line_arithmetic": "doc.check.line_arithmetic",
    "subtotal_sum": "doc.check.subtotal_sum", "discount_arithmetic": "doc.check.discount_arithmetic",
    "vat_7_percent": "doc.check.vat_7_percent", "grand_total": "doc.check.grand_total", "wht": "doc.check.wht",
    "currency": "doc.check.currency", "required_fields": "doc.check.required_fields", "buyer_match": "doc.check.buyer_match",
    "duplicate": "doc.check.duplicate", "original_copy": "doc.check.original_copy",
    "amount_in_words": "doc.check.amount_in_words", "document_type": "doc.check.document_type",
})
# an engine check's message → the key of the sentence the register prints for it (said in the run's language)
_MSG_TH = {
    "not printed": "doc.msg.not_printed", "no date found": "doc.msg.no_date",
    "a date in the future or implausibly old": "doc.msg.date_implausible",
    "no quantity × price rows": "doc.msg.no_qty_rows", "no line amounts": "doc.msg.no_line_amounts",
    "no subtotal to compare the lines with": "doc.msg.no_subtotal",
    "no VAT on this document": "doc.msg.no_vat", "no base to check the VAT against": "doc.msg.no_vat_base",
    "no withholding tax on this document": "doc.msg.no_wht",
    "the 13th digit does not match the other twelve (mod-11 check digit)": "doc.msg.check_digit",
    "only a full tax invoice with a buyer tax ID is matched against the organisation":
        "doc.msg.only_full_invoice",
    "made out to another tax ID: input VAT cannot be claimed on it": "doc.msg.another_tax_id",
    "the amount in words is not the total": "doc.msg.words_not_total",
    "13 digits with a valid check digit": "doc.msg.valid_tax_id", "not seen before": "doc.msg.not_seen",
    "a known document type": "doc.msg.known_type", "a date": "doc.msg.a_date", "a VAT base": "doc.msg.a_vat_base",
    "subtotal + VAT": "doc.msg.subtotal_plus_vat", "THB": "doc.msg.thb", "original": "doc.msg.original", "copy": "doc.msg.copy", "unmarked": "doc.msg.unmarked",
    "a withholding amount (the net payable is not the total)": "doc.msg.wht_amount",
    "rate × base = WHT; total − WHT = net": "doc.msg.wht_rule",
    "no net payable": "doc.msg.no_net", "quantity × unit price = amount": "doc.msg.qty_rule",
}
_KEY_TH = W.table({"net": "doc.key.net", "total": "doc.col.total", "wht": "doc.col.wht", "rate": "doc.key.rate", "match": "doc.key.match",
                   "name": "doc.key.name", "row": "doc.key.row", "expected": "doc.key.expected", "printed": "doc.key.printed"})
_MATCH_TH = W.table({"document number": "doc.match.number", "date and total": "doc.match.date_total"})
_FORM_TH = W.table({"company_limited": "doc.form.company_limited", "public_company": "doc.form.public_company", "limited_partnership": "doc.form.limited_partnership",
                    "sole_proprietor": "doc.form.sole_proprietor"})


def msg_th(text) -> str | None:
    """An engine check's message or value, as the register prints it in the run's language (numbers kept as they are).
    The engine says its messages in English; each has a key here, said in Thai on a Thai paper and as the
    manifest's English on an English one (the name is from before a paper could be English)."""
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
    if s.startswith("marked ") and s.endswith("/ copy"):
        return W("doc.msg.copy_marked")   # the engine's own words for a copy mark name the Thai word on the page
    if s in _MSG_TH:
        return W(_MSG_TH[s])
    if s in DOC_TYPE_TH:
        return DOC_TYPE_TH[s]
    if s in _MATCH_TH:
        return _MATCH_TH[s]
    rules = (
        (r"^row (\S+): (\S+) printed, (\S+) computed$", lambda m: W("doc.msg.row_printed_computed", v=m[1], v2=m[2], v3=m[3])),
        (r"^VAT should be (\S+) at (\S+)% of the base$", lambda m: W("doc.msg.vat_should_be", v=m[1], v2=m[2])),
        (r"^net payable (\S+) is not the total (\S+), and no withholding amount was read$",
         lambda m: W("doc.msg.net_not_total", v=m[1], v2=m[2])),
        (r"^amounts are in (\S+): the THB amount needs an exchange-rate source$",
         lambda m: W("doc.msg.fx_needed", v=m[1])),
        (r"^missing for a (\S+): (.+)$",
         lambda m: W("doc.msg.missing_for") + " " + ", ".join(FIELD_TH.get(x.strip(), x.strip()) for x in m[2].split(","))),
        (r"^same supplier and (.+) as (.+)$", lambda m: W("doc.msg.same_supplier", v=_MATCH_TH.get(m[1], m[1]), v2=m[2])),
        (r"^(\w+):(\S+) read (.+), the document text (.+)$",
         lambda m: (W("doc.msg.model", v=m[2]) if m[1] == "vision" else m[1]) + " " + W("doc.msg.read_vs_text", v=_val(m[3]), v2=_val(m[4]))),
        (r"^not after (\S+)$", lambda m: W("doc.msg.not_after", v=m[1])),
        (r"^(\S+)% of (\S+) is (\S+), printed (\S+)$", lambda m: W("doc.msg.pct_of", v=m[1], v2=m[2], v3=m[3], v4=m[4])),
        (r"^total − WHT = (\S+), net printed (\S+)$", lambda m: W("doc.msg.total_minus_wht", v=m[1], v2=m[2])),
    )
    parts = []
    for part in s.split("; "):
        for pat, fn in rules:
            m = re.match(pat, part)
            if m:
                parts.append(fn(m))
                break
        else:
            parts.append(W(_MSG_TH[part]) if part in _MSG_TH else part)
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
        return W("doc.check.source_agreement", f=FIELD_TH.get(f, f))
    return CHECK_NAME_TH.get(code) or CHECK_TH.get(code, (code,))[0]


def _num(v):
    return None if v in (None, "") else float(Decimal(str(v)))


def parts(wb, sheet: str) -> list:
    """The sheets a table prints on — the main sheet and the companion that is in the file — for ``table``."""
    return sheets(wb, *(W(k) for k in PARTS.get(sheet, (sheet,))))


def item_status(line: dict) -> str:
    """What a line item's status says (AM8): "ยืนยันแล้ว" is a person's word — the machine never says it.
    A reading a script made says it was read by the system, and whether it still needs a look."""
    if line.get("decided_by") == "person":
        return W("doc.status.person_confirmed")
    if line.get("left_out"):
        return W("doc.status.left_out", words=" ".join(line["left_out"])[:40])
    if line.get("verified"):
        return W("doc.status.system_passed")
    return W("doc.status.system_review")


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
        ws.title = W("doc.sheet.summary")
        c, t = self.c, self.c["totals"]
        self._title(ws, W("doc.title.summary", period_label=c["period_label"]),
                    " · ".join(x for x in (c.get("company"), W("doc.face.tax_id", company_tax_id=c["company_tax_id"]) if c.get("company_tax_id") else None,
                                            W("doc.face.prepared", prepared_on=c["prepared_on"])) if x))
        rows = [
            [W("doc.face.received"), t["documents"]], [W("doc.face.recorded"), t["included"]],
            [W("doc.face.not_included", not_included=W(NOT_INCLUDED)), t["excluded"]],
            [W("doc.face.exceptions"), t["exceptions"]], [W("doc.face.answered"), t["questions_answered"]],
            [W("doc.col.subtotal_thb"), _num(t["thb"]["subtotal"])], [W("doc.face.vat_thb"), _num(t["thb"]["vat_amount"])],
            [W("doc.col.total_thb"), _num(t["thb"]["total"])], [W("doc.face.wht_thb"), _num(t["thb"]["wht_amount"])],
            [W("doc.face.payable_thb"), _num(t["thb"].get("payable", t["thb"]["net_payable"]))],
        ]
        self._table(ws, 4, [W("doc.col.item"), W("doc.col.value")], rows, [36, 22])
        for r in range(10, 15):           # the five baht rows (the counts stay integers)
            ws.cell(row=r, column=2).number_format = MONEY
        r = 4 + len(rows) + 2
        notes = [W("doc.note.figures"),
                 W("doc.note.payable"),
                 W("doc.note.provenance")]
        if t["excluded"]:
            notes.insert(0, W("doc.note.excluded", not_included=W(NOT_INCLUDED), excluded=t["excluded"]))
        for m in self.c.get("models") or []:
            notes.append(W("doc.note.models", model=m["model"], place=W("doc.model.inside") if m["self_hosted"] else W("doc.model.outside"),
                           n=len(m["documents"])))
        if any(d["tier"] == "photo" for d in self.c["documents"]):
            notes.append(W("doc.note.photo"))
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
        for sheet, title, sub in ((S_REGISTER, W("doc.title.register"), W("doc.sub.register", s_register_ev=W(S_REGISTER_EV))),
                                  (S_REGISTER_EV, W(S_REGISTER_EV), W("doc.sub.register_ev", s_register=W(S_REGISTER)))):
            ws = self.wb.create_sheet(W(sheet))
            self._title(ws, title, sub)
            picked, prow, cols = workpaper.pick(list(H_REGISTER), rows, [W(h) for h, _ in LAYOUT[sheet]], money_cols=(11, 12, 13, 14, 15, 17, 18, 19),
                                                wrap_cols=(4, 5, 8, 21))
            self._table(ws, 4, picked, prow, [w for _, w in LAYOUT[sheet]], **cols)
            # a tax ID is text, never a number
            for j, h in enumerate(picked, start=1):
                if h in (W("doc.col.seller_tax_id"), W("doc.col.buyer_tax_id")):
                    for r in range(5, 5 + len(rows)):
                        ws.cell(row=r, column=j).number_format = "@"

    def not_included(self):
        ws = self.wb.create_sheet(W(NOT_INCLUDED))
        rows = [[e["name"], e["reason"], e.get("detail") or "", e["how"]] for e in self.c.get("not_included") or []]
        self._title(ws, W(NOT_INCLUDED),
                    W("doc.sub.not_included")
                    if rows else W("doc.sub.none_left_out"))
        self._table(ws, 4, [W("doc.col.file"), W("doc.col.reason"), W("doc.col.detail"), W("doc.col.how_back")], rows, [36, 30, 64, 44], wrap_cols=(3, 4))

    def line_items(self):
        ws = self.wb.create_sheet(W("doc.sheet.items"))
        self._title(ws, W("doc.title.items"), W("doc.sub.items"))
        rows = [[l["name"], l["row"], l["description"], _num(l["quantity"]), _num(l["unit_price"]), _num(l["amount"]),
                 l["confidence"], item_status(l)] for l in self.c["line_items"]]
        self._table(ws, 4, [W("doc.col.file"), W("doc.key.row"), W("doc.col.item"), W("doc.col.quantity"), W("doc.col.unit_price"), W("doc.col.amount"), W("doc.col.confidence"), W("doc.col.status")], rows,
                    [30, 8, 44, 10, 14, 14, 10, 30], money_cols=(5, 6), wrap_cols=(3, 8))

    def provenance(self):
        ws = self.wb.create_sheet(W("doc.sheet.provenance"))
        self._title(ws, W("doc.title.provenance"), W("doc.sub.provenance"))
        rows = []
        for p in self.c["provenance"]:
            # (a party the model read is shown as the name and its legal form, not as the engine wrote it down)
            vision = "; ".join(f"{c['method']}: {_val(str(c['value']))}" for c in p.get("candidates") or [])
            rows.append([p["name"], p["label"], p["value"] if not isinstance(p["value"], (int, float)) else str(p["value"]),
                         p["confidence"], method_th(p["method"]), p["page"], p["bbox"], p["raw_text"],
                         DECIDED_TH.get(p["decided_by"], p["decided_by"]),
                         ", ".join(CHECK_NAME_TH.get(c, c) for c in p["checks"] if c in CHECK_NAME_TH) or None, vision or None])
        self._table(ws, 4, [W("doc.col.file"), W("doc.col.field"), W("doc.col.value"), W("doc.col.confidence"), W("doc.col.read_by"), W("doc.col.page"), W("doc.col.position"), W("doc.col.doc_text"),
                            W("doc.col.decided_by"), W("doc.col.checks_passed"), W("doc.col.model_read")], rows,
                    [22, 15, 24, 9, 13, 6, 20, 28, 15, 28, 22], wrap_cols=(1, 2, 3, 5, 7, 8, 9, 10, 11))

    def exceptions(self):
        ws = self.wb.create_sheet(W("doc.sheet.exceptions"))
        self._title(ws, W("doc.sheet.exceptions"), W("doc.sub.exceptions"))
        who = {"person": W("doc.who.person"), "card": W("doc.who.card"), "rule": W("doc.who.rule")}
        rows = [[e["name"], e["code"], e["reason"], W("doc.result.excluded") if e["severity"] == "excluded" else W("doc.result.noted"),
                 e.get("detail") or "", who.get(e["decided_by"], e["decided_by"])] for e in self.c["exceptions"]]
        self._table(ws, 4, [W("doc.col.file"), W("doc.col.reason_code"), W("doc.col.reason"), W("doc.col.result"), W("doc.col.detail"), W("doc.col.decided")], rows,
                    [30, 24, 34, 18, 60, 14], wrap_cols=(5,))

    def checks(self):
        ws = self.wb.create_sheet(W("doc.sheet.checks"))
        self._title(ws, W("doc.title.checks"), W("doc.sub.checks"))
        def note(c):
            m = msg_th(c.get("message")) or ""
            try:
                differs = Decimal(str(c["expected"])) != Decimal(str(c["actual"]))
            except Exception:  # noqa: BLE001 — not two amounts
                differs = False
            if not m and c["status"] == "pass" and differs:
                m = W("doc.note.rounding")
            return m
        rows = [[c["name"], check_name(c["code"]), STATUS_TH.get(c["status"], c["status"]),
                 None if c.get("expected") is None else msg_th(c["expected"]), None if c.get("actual") is None else msg_th(c["actual"]),
                 note(c), c["code"]] for c in self.c["checks"]]
        self._table(ws, 4, [W("doc.col.file"), W("doc.col.what"), W("doc.col.result"), W("doc.key.expected"), W("doc.col.found"), W("doc.col.note"), W("doc.col.code")], rows,
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
                                 id_cols={W(S_REGISTER): ID_COLS[S_REGISTER], W(S_REGISTER_EV): ID_COLS[S_REGISTER], W("doc.sheet.provenance"): 2},
                                 lang=words.lang())
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
    out = [ck("xlsx.sheets", {W(k) for k in SHEETS} <= set(wb.sheetnames), ", ".join(wb.sheetnames), W("check.sheets"))]
    rows = table(parts(wb, S_REGISTER), H_REGISTER)
    want = computed["totals"]["included"]
    out.append(ck("xlsx.register_rows", len(rows) == want, W("check.rows.detail", n=len(rows), want=want), W("check.rows")))
    out.append(_figures(wb, rows, computed))
    pv = [r for r in wb[W("doc.sheet.provenance")].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.provenance", len(pv) == len(computed["provenance"]) and all(r[4] for r in pv),
                  W("check.n_values", n=len(pv)), W("check.provenance")))
    ex = [r for r in wb[W("doc.sheet.exceptions")].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    out.append(ck("xlsx.exceptions", len(ex) == len(computed["exceptions"]) and all(r[1] in REASON_TH for r in ex),
                  W("check.n_items", n=len(ex)), W("check.exceptions")))
    left = [r for r in wb[W(NOT_INCLUDED)].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
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
    (1, "doc.col.date", "document_date", False), (2, "doc.col.number", "document_number", False),
    (5, "doc.col.seller_tax_id", "seller_tax_id", False), (8, "doc.col.buyer_tax_id", "buyer_tax_id", False),
    (9, "doc.col.currency", "currency", False),
    (10, "doc.col.subtotal", "subtotal", True), (11, "doc.col.vat", "vat_amount", True), (12, "doc.col.total", "total", True),
    (13, "doc.col.wht", "wht_amount", True), (14, "doc.col.payable", "payable", True), (15, "doc.col.fx_rate", "fx_rate", True),
    (16, "doc.col.subtotal_thb", "thb_subtotal", True), (17, "doc.col.vat_thb", "thb_vat_amount", True),
    (18, "doc.col.total_thb", "thb_total", True),
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
                t.number(f"{where} {W(head)}", r[col], want)
            else:
                t.text(f"{where} {W(head)}", r[col], "" if want is None else want)
    face = labelled(wb[W("doc.sheet.summary")], min_row=5)
    tt = computed["totals"]
    for label, want in ((W("doc.face.received"), tt["documents"]), (W("doc.face.recorded"), tt["included"]),
                        (W("doc.face.exceptions"), tt["exceptions"]), (W("doc.face.answered"), tt["questions_answered"]),
                        (W("doc.col.subtotal_thb"), tt["thb"]["subtotal"]), (W("doc.face.vat_thb"), tt["thb"]["vat_amount"]),
                        (W("doc.col.total_thb"), tt["thb"]["total"]), (W("doc.face.wht_thb"), tt["thb"]["wht_amount"]),
                        (W("doc.face.payable_thb"), tt["thb"].get("payable", tt["thb"]["net_payable"]))):
        t.number(W("where.summary", label=label), face.get(label), want)
    items = [r for r in wb[W("doc.sheet.items")].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
    if len(items) != len(computed["line_items"]):
        t.fail(W("check.items.count", got=len(items), want=len(computed["line_items"])))
    for r, li in zip(items, computed["line_items"]):
        where = W("where.item", name=li["name"], row=li["row"])
        t.text(W("where.item.name", where=where), r[2], li["description"] or "")
        for col, head, key in ((3, W("doc.col.quantity"), "quantity"), (4, W("doc.col.unit_price"), "unit_price"), (5, W("doc.col.amount"), "amount")):
            t.number(f"{where} {head}", r[col], li[key])
        t.text(W("where.item.status", where=where), r[7], item_status(li))
    return t.result("xlsx.figures", W("check.figures"), W("noun.values"))
