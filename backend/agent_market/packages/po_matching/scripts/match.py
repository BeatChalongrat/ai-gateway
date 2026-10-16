"""compute — every invoice line tested against the PO line it belongs to, cumulatively.

Deterministic, Decimal only. The same review as preflight (checks/review.py) with the answers in
it; the links come from the E3 engine, the rest is arithmetic:

    for every PO line:   ordered  ≥  received so far  ≥  billed so far
                         (with the install's over-delivery and quantity allowances)

"So far" is the point. One PO line can be delivered three times and billed four times, in this
round and in earlier ones. An invoice is tested with everything that was billed on its PO line
before it — the invoices of earlier rounds (the install's history) and the earlier invoices of
this round, in the order they are dated — not by itself: each of two invoices for 60 of 100
units passes alone, and together they bill 120.

MATCH DEPTH is decided per line, from what the files hold, and written on the line, in the
summary and on the face of the working paper:

    three_way   the PO line, a receipt of it (a goods receipt, or a receipt a person confirmed in
                this system) and the invoice line
    two_way     the PO line and the invoice line — a service, or a company that does not issue
                goods receipts, or a receipt that has not come yet

A two-way result never reads as a three-way one: its quantity was tested against what was
ordered, and nobody checked that anything arrived.

OUTCOMES — three, and none of them is an approval:

    pass      "ผ่าน · พร้อมเสนออนุมัติ"   within tolerance on every line
    blocked   "ติดเงื่อนไข"               with its reason codes
    person    "ต้องให้คนตัดสิน"            what the rules cannot settle

Approving and paying stay with people. Nothing is posted anywhere: the GR/IR positions are
figures for the accountant, the entries are theirs to make.

computed.json (schema ``ap.match.v1``):
  invoices[]        every invoice: status, reasons, match depth of its lines, VAT, what it links to
  lines[]           every invoice line: the PO line and the receipts it was tested against (file,
                    sheet, row), the cumulative figures, every check with the tolerance it used
  po_lines[]        every PO line that was billed or received: ordered, received, billed, open
  grir{}            received not yet billed · billed not yet received, per PO line and in total
  unplanned[]       freight, insurance, handling, surcharges: billed, never ordered
  notes[]           credit notes and debit notes and the invoice each corrects
  summary{}         counts and value by status and by reason code, by match depth
  run_exceptions[]  what a person must decide or look at
  index_entries[]   what this round adds to the install's history
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from decimal import Decimal

from ....engine import thai as TH
from ....engine import words
from ....engine.e3 import cards as E3C
from ....engine.e3.rows import ref_key
from ..checks import book as B
from ..checks import links as L
from ..checks import parties as P
from ..checks import settings as S
from ..checks import units as U
from ..checks.review import COUNTED, Review, is_duplicate, review, unit_factor
from ..words import W

SCHEMA = "ap.match.v1"
SCRIPT = "packages/po_matching/scripts/match.py"
ZERO = Decimal("0")
HUNDRED = Decimal("100")
PASS, BLOCKED, PERSON = "pass", "blocked", "person"
THREE, TWO, NONE = "three_way", "two_way", "none"
# What a person reads of a status, a depth, a reason and a choice: keys of the manifest's sentences, each said in
# the language of the run when it is read (engine/words.py).
STATUS_TH = W.table({PASS: "status.pass", BLOCKED: "status.blocked", PERSON: "status.person"})
DEPTH_TH = W.table({THREE: "depth.three", TWO: "depth.two", NONE: "depth.none"})
DEPTH_SHORT = W.table({THREE: "depth.short.three", TWO: "depth.short.two", NONE: "depth.short.none"})
DEPTH_WHY = W.table({
    "gr": "depth.why.gr",
    "confirmed": "depth.why.confirmed",
    "service": "depth.why.service",
    "no_practice": "depth.why.no_practice",
    "not_yet": "depth.why.not_yet",
    "blanket": "depth.why.blanket",
    "unlinked": "depth.why.unlinked",
})
# reason code → (the key of what a person reads, the outcome it gives)
REASONS = {
    "price_above_po": ("reason.price_above_po", BLOCKED),
    "qty_over_received": ("reason.qty_over_received", BLOCKED),
    "qty_over_po": ("reason.qty_over_po", BLOCKED),
    "no_goods_receipt": ("reason.no_goods_receipt", BLOCKED),
    "item_not_on_po": ("reason.item_not_on_po", BLOCKED),
    "po_not_found": ("reason.po_not_found", BLOCKED),
    "po_closed": ("reason.po_closed", BLOCKED),
    "duplicate_invoice": ("reason.duplicate_invoice", BLOCKED),
    "supplier_mismatch": ("reason.supplier_mismatch", BLOCKED),
    "currency_mismatch": ("reason.currency_mismatch", BLOCKED),
    "vat_mismatch": ("reason.vat_mismatch", BLOCKED),
    "tax_invoice_incomplete": ("reason.tax_invoice_incomplete", BLOCKED),
    "unplanned_cost": ("reason.unplanned_cost", BLOCKED),
    "line_amount_mismatch": ("reason.line_amount_mismatch", BLOCKED),
    "total_mismatch": ("reason.total_mismatch", BLOCKED),
    "date_outside_po": ("reason.date_outside_po", BLOCKED),
    "blanket_limit_exceeded": ("reason.blanket_limit_exceeded", BLOCKED),
    "credit_over_invoice": ("reason.note_over_invoice", BLOCKED),
    "buyer_mismatch": ("reason.buyer_mismatch", BLOCKED),
    "link_unconfirmed": ("reason.link_unconfirmed", PERSON),
    "duplicate_unconfirmed": ("reason.duplicate_unconfirmed", PERSON),
    "supplier_unsure": ("reason.supplier_unsure", PERSON),
    "unit_unknown": ("reason.unit_unknown", PERSON),
    "no_line_detail": ("reason.no_line_detail", PERSON),
    "note_without_invoice": ("reason.note_without_invoice", PERSON),
    "cannot_test": ("reason.cannot_test", PERSON),
}
REASON_TH = W.table({k: v[0] for k, v in REASONS.items()}, then=words.cap)
REASON_ORDER = list(REASONS)
# reason code → [(the key of an answer, its value)]
CHOICES = {
    "price_above_po": [("choice.accept_price", "accept"), ("choice.credit_note", "credit_note"), ("choice.purchasing", "purchasing")],
    "qty_over_received": [("choice.wait_rest", "wait"), ("choice.credit_note", "credit_note"), ("choice.recount", "recount")],
    "qty_over_po": [("choice.amend_qty", "amend_po"), ("choice.credit_note", "credit_note"), ("choice.purchasing", "purchasing")],
    "no_goods_receipt": [("choice.wait", "wait"), ("choice.service", "service"), ("choice.confirm_receipt", "confirm")],
    "item_not_on_po": [("choice.purchasing", "purchasing"), ("choice.amend_item", "amend_po"), ("choice.reject_item", "reject")],
    "po_not_found": [("choice.send_po", "send_po"), ("choice.purchasing", "purchasing")],
    "po_closed": [("choice.reopen", "reopen"), ("choice.purchasing", "purchasing"), ("choice.reject_invoice", "reject")],
    "duplicate_invoice": [("choice.duplicate", "duplicate"), ("choice.distinct", "distinct")],
    "supplier_mismatch": [("choice.purchasing", "purchasing"), ("choice.reject_invoice", "reject")],
    "currency_mismatch": [("choice.purchasing", "purchasing"), ("choice.new_invoice", "reissue")],
    "vat_mismatch": [("choice.corrected_tax_invoice", "reissue"), ("choice.accept_as_shown", "accept")],
    "tax_invoice_incomplete": [("choice.complete_tax_invoice", "reissue"), ("choice.no_claim", "no_claim")],
    "unplanned_cost": [("choice.accept_cost", "accept"), ("choice.purchasing", "purchasing"), ("choice.reject_cost", "reject")],
    "line_amount_mismatch": [("choice.corrected_invoice", "reissue"), ("choice.accept_as_shown", "accept")],
    "total_mismatch": [("choice.corrected_invoice", "reissue"), ("choice.accept_as_shown", "accept")],
    "date_outside_po": [("choice.accept", "accept"), ("choice.purchasing", "purchasing")],
    "blanket_limit_exceeded": [("choice.raise_limit", "amend_po"), ("choice.purchasing", "purchasing")],
    "credit_over_invoice": [("choice.corrected_credit_note", "reissue"), ("choice.review", "review")],
    "buyer_mismatch": [("choice.company_tax_invoice", "reissue"), ("choice.reject_invoice", "reject")],
    "link_unconfirmed": [("opt.same", "match"), ("opt.separate", "separate")],
    "duplicate_unconfirmed": [("opt.dup.same", "duplicate"), ("opt.dup.separate", "distinct")],
    "supplier_unsure": [("opt.supplier.same", "same"), ("opt.supplier.different", "different")],
    "unit_unknown": [("choice.set_conversion", "set_conversion"), ("choice.review", "review")],
    "no_line_detail": [("choice.send_detail", "send_detail"), ("choice.review", "review")],
    "note_without_invoice": [("choice.send_invoice", "send_invoice"), ("choice.review", "review")],
    "cannot_test": [("choice.send_qty_price", "send_detail"), ("choice.review", "review")],
}
ELEMENT_TH = W.table({"seller_name": "element.seller_name", "seller_tax_id": "element.seller_tax_id", "seller_branch": "element.seller_branch",
              "document_number": "element.document_number", "document_date": "element.document_date", "buyer_name": "element.buyer_name",
              "buyer_tax_id": "element.buyer_tax_id", "vat_amount": "element.vat_amount",
              "lines": "element.lines", "tax_id_check": "element.tax_id_check"})
DOC_TYPE_TH = W.table({"full_tax_invoice": "doctype.full_tax_invoice", "abbreviated_tax_invoice": "doctype.abbreviated_tax_invoice", "receipt": "doctype.receipt",
               "invoice": "name.invoice", "credit_note": "doctype.credit_note", "debit_note": "doctype.debit_note", "": "doctype.unknown",
               "unknown": "doctype.unknown"}, then=words.cap)
CONFIRMED_TH = W.table({"rule": "confirmed.rule", "person": "confirmed.person"})


def f2(v) -> str | None:
    return None if v is None else f"{B.q2(Decimal(v)):.2f}"


def qs(v) -> str | None:
    return None if v is None else B.qty_text(Decimal(v)).replace(",", "")


def money(v) -> str:
    return TH.fmt_money(B.q2(Decimal(v)))


def name_of(answered_by: dict | None, key: str | None) -> str:
    who = (answered_by or {}).get(key or "")
    return " ".join(str((who or {}).get("name") or "").split())[:120]


class Cumulative:
    """What has been received and billed on every PO line, as the round walks through its invoices."""

    def __init__(self, rv: Review):
        self.rv = rv
        self.lines = {p.id: p for p in rv.po}
        self.by_akey = {p.akey: p for p in rv.po}
        self.received: dict = {}     # PO line id → [(GrLine, quantity in the PO line's unit, link)]
        self.unconverted: dict = {}  # PO line id → [GrLine] whose unit nothing converts
        self.billed: dict = {}       # PO line id → [event]
        self.alloc: dict = {}        # (supplier key, invoice number key) → {PO line id: [quantity, amount]}
        for pid, got in rv.gr_of.items():
            pl = self.lines[pid]
            for g, link in got:
                f, _how = U.factor(g.unit, pl.unit, pl.item_code, rv.settings.conversions)
                if f is None:
                    self.unconverted.setdefault(pid, []).append(g)
                    continue
                self.received.setdefault(pid, []).append((g, g.qty * f, link))
        for e in rv.known:
            if e["outcome"] not in COUNTED:
                continue
            who = P.Party(e.get("seller_name") or "", str(e.get("seller_tax_id") or ""))
            for ln in e["detail"].get("lines") or []:
                pl = self.by_akey.get(str(ln.get("akey") or ""))
                if pl is None:
                    continue
                q, a = B.D(ln.get("quantity")) or ZERO, B.D(ln.get("amount")) or ZERO
                self._add(pl.id, {"invoice": str(e.get("document_number") or ""), "date": str(e.get("document_date") or "")[:10],
                                  "quantity": q, "amount": a, "from": "history", "file": e.get("name") or "",
                                  "outcome": e["outcome"], "kind": e["kind"]}, (who.key, ref_key(e.get("document_number"))))

    def _add(self, pid: str, event: dict, owner: tuple) -> None:
        self.billed.setdefault(pid, []).append(event)
        a = self.alloc.setdefault(owner, {}).setdefault(pid, [ZERO, ZERO])
        a[0] += event["quantity"]
        a[1] += event["amount"]

    def received_qty(self, pid: str) -> Decimal:
        return sum((q for _g, q, _l in self.received.get(pid, [])), ZERO)

    def billed_qty(self, pid: str) -> Decimal:
        return sum((e["quantity"] for e in self.billed.get(pid, [])), ZERO)

    def billed_amount(self, pid: str) -> Decimal:
        return sum((e["amount"] for e in self.billed.get(pid, [])), ZERO)

    def bill(self, pid: str, inv: B.Invoice, qty: Decimal, amount: Decimal) -> None:
        self._add(pid, {"invoice": inv.number, "date": inv.date.isoformat() if inv.date else "", "quantity": qty, "amount": amount,
                        "from": "round", "file": inv.name, "outcome": "", "kind": inv.doc_type or "invoice", "key": inv.key},
                  (inv.supplier.key, inv.number_key))

    def of_invoice(self, supplier: P.Party, number: str) -> dict:
        return self.alloc.get((supplier.key, ref_key(number)), {})


# ── one line ─────────────────────────────────────────────────────────────

def _check(code: str, ok: bool, label: str, *, expected=None, actual=None, tolerance: dict | None = None, detail: str = "") -> dict:
    return {"code": code, "ok": bool(ok), "label": label, "expected": expected, "actual": actual, "tolerance": tolerance or {},
            "detail": detail}


def _depth(rv: Review, cum: Cumulative, pl: B.PoLine) -> tuple[str, str, list]:
    """(three_way | two_way, why, reasons) for a line linked to ``pl``."""
    got = cum.received.get(pl.id, [])
    if got:
        in_system = all(g.in_system for g, _q, _l in got)
        return THREE, "confirmed" if in_system else "gr", []
    if pl.kind == B.SERVICE:
        return TWO, "service", []
    if rv.settings.gr_practice == S.GR_NO:
        return TWO, "no_practice", []
    if pl.kind == B.BLANKET and pl.qty is None:
        return TWO, "blanket", []
    return TWO, "not_yet", ["no_goods_receipt"]


def _line(rv: Review, cum: Cumulative, inv: B.Invoice, l: B.InvLine, counts: bool, answered_by: dict | None) -> dict:
    tol = rv.settings.tolerances
    used = {}
    out = {**l.out(), "invoice": inv.key, "invoice_number": inv.number, "po_line": None, "link": None, "depth": NONE,
           "depth_label": DEPTH_TH[NONE], "depth_why": DEPTH_WHY["unlinked"], "receipts": [], "cumulative": None, "checks": [],
           "reasons": [], "notes": [], "counted": False, "unplanned": None}
    reasons, notes, checks = [], [], []
    sign = inv.sign
    hit = rv.po_of.get(l.id)
    if hit is None:
        waiting = rv.waiting_of.get(l.id)
        if waiting is not None:
            pl = rv.po_by_id(waiting["left"][0]["id"])
            reasons.append("link_unconfirmed")
            notes.append(W("note.proposed", po=pl.label if pl else "", pass_label=waiting["pass_label"]))
            out["proposed"] = {"po_line": pl.out() if pl else None, "pass": waiting["pass"], "pass_label": waiting["pass_label"],
                               "tier": waiting["tier"], "pair": waiting["pair"], "decision": waiting.get("decision")}
        else:
            kind = U.unplanned_kind(l.description, rv.settings.unplanned_words)
            if kind is not None:
                out["unplanned"] = {"kind": kind[0], "label": kind[1], "amount": f2(l.amount or ZERO)}
            else:
                reasons.append("item_not_on_po")
        return _close(out, reasons, notes, checks, used)
    pl, m = hit
    out["po_line"] = pl.out()
    out["link"] = {"pass": m["pass"], "pass_label": m["pass_label"], "tier": m["tier"], "tier_label": m["tier_label"],
                   "confirmed": m["confirmed"], "confirmed_label": CONFIRMED_TH[m["confirmed"]],
                   "confirmed_by": name_of(answered_by, m.get("decision_key")) if m["confirmed"] == "person" else "",
                   "pair": m["pair"], "decision_key": m.get("decision_key")}
    depth, why, more = _depth(rv, cum, pl)
    out.update(depth=depth, depth_label=DEPTH_TH[depth], depth_why=DEPTH_WHY[why])
    reasons += more
    out["receipts"] = [{**g.out(), "quantity_in_po_unit": qs(q), "pass": lk["pass"], "pass_label": lk["pass_label"],
                        "confirmed": lk["confirmed"], "confirmed_by": lk.get("confirmed_by") or g.confirmed_by}
                       for g, q, lk in cum.received.get(pl.id, [])]
    if cum.unconverted.get(pl.id):
        notes.append(W("note.unconverted", n=len(cum.unconverted[pl.id])))
    # the PO itself
    if pl.status == B.CLOSED:
        reasons.append("po_closed")
        open_qty = cum.received_qty(pl.id) - cum.billed_qty(pl.id)
        if pl.qty is not None and cum.received_qty(pl.id) < pl.qty and open_qty > ZERO:
            notes.append(W("note.closed_early", qty=B.qty_text(pl.qty), received=B.qty_text(cum.received_qty(pl.id)), open_qty=B.qty_text(open_qty), unit=pl.unit))
        checks.append(_check("po_open", False, W("test.po_open"), expected=W("postatus.open"), actual=pl.status_raw or W("postatus.closed")))
    if inv.currency and pl.currency and inv.currency != pl.currency:
        reasons.append("currency_mismatch")
        checks.append(_check("currency", False, W("test.currency"), expected=pl.currency, actual=inv.currency))
    else:
        checks.append(_check("currency", True, W("test.currency"), expected=pl.currency, actual=inv.currency or pl.currency))
    if inv.date is not None:
        first = pl.valid_from if pl.kind == B.BLANKET and pl.valid_from else pl.date
        last = pl.valid_to if pl.kind == B.BLANKET else None
        ok = True
        if first is not None and inv.date < first - _dt.timedelta(days=tol.days):
            ok = False
        if last is not None and inv.date > last + _dt.timedelta(days=tol.days):
            ok = False
        if first is not None or last is not None:
            used["date_window_days"] = tol.days
            span = E3C.date_th(first.isoformat()) if first else ""
            if last is not None:
                span += " " + W("span.to", last=E3C.date_th(last.isoformat()))
            checks.append(_check("date", ok, W("test.date"), expected=span, actual=E3C.date_th(inv.date.isoformat()),
                                 tolerance={"date_window_days": tol.days}))
            if not ok:
                reasons.append("date_outside_po")
    # the line's own arithmetic
    if l.qty is not None and l.unit_price is not None and l.amount is not None:
        calc = B.q2(l.qty * l.unit_price)
        ok = abs(calc - l.amount) <= tol.small
        used["small_difference"] = f2(tol.small)
        checks.append(_check("line_amount", ok, W("test.line_amount"), expected=f2(calc), actual=f2(l.amount),
                             tolerance={"small_difference": f2(tol.small)}))
        if not ok:
            reasons.append("line_amount_mismatch")
    # units
    factor, how = (Decimal("1"), "not_stated")
    if l.unit and pl.unit:
        factor, how = unit_factor(rv, l.unit, pl)
    elif not l.unit and pl.unit:
        notes.append(W("note.unit_from_po", unit=pl.unit))
    out["unit_conversion"] = {"from": U.canonical(l.unit) if l.unit else "", "to": U.canonical(pl.unit) if pl.unit else "",
                              "factor": None if factor is None else str(factor.normalize()), "source": how}
    if factor is None:
        reasons.append("unit_unknown")
        checks.append(_check("unit", False, W("test.unit"), expected=pl.unit, actual=l.unit))
        return _close(out, reasons, notes, checks, used)
    if how in ("universal", "settings"):
        notes.append(W("note.unit_converted", frm=U.said(l.unit), factor=B.qty_text(factor), to=U.said(pl.unit)))
    amount = (l.amount if l.amount is not None else (B.q2(l.qty * l.unit_price) if l.qty is not None and l.unit_price is not None else None))
    if amount is None and l.qty is None:
        reasons.append("cannot_test")
        return _close(out, reasons, notes, checks, used)
    by_amount = l.qty is None or (l.unit_price is None and l.amount is None) or pl.qty is None
    qty_po = None if l.qty is None else l.qty * factor
    price_po = None
    if l.unit_price is not None:
        price_po = l.unit_price / factor
    elif amount is not None and qty_po not in (None, ZERO):
        price_po = amount / qty_po
    before_q, before_a = cum.billed_qty(pl.id), cum.billed_amount(pl.id)
    if not counts:
        # an invoice that repeats one already presented: its quantities are in the figures once, under the
        # first; testing them again would call every line of it over-billed
        out["cumulative"] = {"unit": pl.unit, "ordered": qs(pl.qty), "received": qs(cum.received_qty(pl.id)) if depth == THREE else None,
                             "billed_before": qs(before_q), "this_line": "0", "billed_after": qs(before_q), "not_counted": True,
                             "earlier": []}
        notes.append(W("note.not_counted_again"))
        out["quantity_in_po_unit"] = qs(qty_po)
        return _close(out, reasons, notes, checks, used)
    # price
    if price_po is not None and pl.unit_price not in (None, ZERO) and sign > 0:
        var = price_po - pl.unit_price
        ext = B.q2(var * (qty_po or ZERO))
        pct = (var * HUNDRED / pl.unit_price).quantize(Decimal("0.01"))
        ok = var <= ZERO or ext <= tol.small or (pct <= tol.price_pct and ext <= tol.price_abs)
        t = {"price_tolerance_pct": f2(tol.price_pct), "price_tolerance_abs": f2(tol.price_abs), "small_difference": f2(tol.small)}
        used.update(t)
        checks.append(_check("price", ok, W("test.price"), expected=str(pl.unit_price), actual=str(price_po.normalize()
                             if price_po == price_po.to_integral() else price_po.quantize(Decimal("0.000001")).normalize()),
                             tolerance=t, detail=W("test.price.detail", var=money(var), pct=pct, ext=money(ext))))
        out["price_variance"] = {"per_unit": str(var.quantize(Decimal("0.000001")).normalize()), "pct": f"{pct:.2f}",
                                 "extended": f2(ext)}
        if not ok:
            reasons.append("price_above_po")
        elif var < ZERO:
            notes.append(W("note.price_below", var=money(-var)))
    # quantity, cumulatively
    if not by_amount:
        after_q = before_q + sign * qty_po
        received = cum.received_qty(pl.id)
        out["cumulative"] = {"unit": pl.unit, "ordered": qs(pl.qty), "received": qs(received) if depth == THREE else None,
                             "billed_before": qs(before_q), "this_line": qs(sign * qty_po), "billed_after": qs(after_q),
                             "earlier": [{"invoice": e["invoice"], "date": e["date"], "quantity": qs(e["quantity"]), "from": e["from"],
                                          "file": e["file"]} for e in cum.billed.get(pl.id, [])]}
        if sign > 0:
            ordered_max = pl.qty * (1 + tol.over_pct / HUNDRED)
            if depth == THREE:
                allowed = received * (1 + tol.qty_pct / HUNDRED)
                ok = after_q <= allowed
                t = {"qty_tolerance_pct": f2(tol.qty_pct)}
                used.update(t)
                checks.append(_check("qty_received", ok, W("test.qty_received"), expected=qs(received), actual=qs(after_q),
                                     tolerance=t, detail=W("test.qty_received.detail", before_q=B.qty_text(before_q), qty_po=B.qty_text(qty_po), after_q=B.qty_text(after_q), unit=pl.unit, received=B.qty_text(received))))
                if not ok:
                    reasons.append("qty_over_received")
                ok2 = received <= ordered_max
                t2 = {"over_delivery_pct": f2(tol.over_pct)}
                used.update(t2)
                checks.append(_check("qty_ordered", ok2, W("test.received_ordered"), expected=qs(pl.qty), actual=qs(received),
                                     tolerance=t2))
                if not ok2 and after_q > ordered_max:
                    reasons.append("qty_over_po")
            else:
                allowed = ordered_max * (1 + tol.qty_pct / HUNDRED)
                ok = after_q <= allowed
                t = {"qty_tolerance_pct": f2(tol.qty_pct), "over_delivery_pct": f2(tol.over_pct)}
                used.update(t)
                checks.append(_check("qty_ordered", ok, W("test.qty_ordered"), expected=qs(pl.qty), actual=qs(after_q),
                                     tolerance=t, detail=W("test.qty_ordered.detail", before_q=B.qty_text(before_q), qty_po=B.qty_text(qty_po), after_q=B.qty_text(after_q), unit=pl.unit, qty=B.qty_text(pl.qty))))
                if not ok:
                    reasons.append("qty_over_po")
    else:
        # no quantity to compare: the amounts, cumulatively
        limit = pl.limit if pl.kind == B.BLANKET and pl.limit is not None else pl.amount
        after_a = before_a + sign * (amount or ZERO)
        out["cumulative"] = {"unit": W("word.baht"), "ordered": f2(limit), "received": None, "billed_before": f2(before_a),
                             "this_line": f2(sign * (amount or ZERO)), "billed_after": f2(after_a), "by_amount": True,
                             "earlier": [{"invoice": e["invoice"], "date": e["date"], "quantity": f2(e["amount"]), "from": e["from"],
                                          "file": e["file"]} for e in cum.billed.get(pl.id, [])]}
        notes.append(W("note.by_amount"))
        if limit is not None and sign > 0:
            if pl.kind == B.BLANKET:
                ok = after_a <= limit + tol.small
                checks.append(_check("blanket_limit", ok, W("test.blanket_limit"), expected=f2(limit), actual=f2(after_a),
                                     tolerance={"small_difference": f2(tol.small)}))
                used["small_difference"] = f2(tol.small)
                if not ok:
                    reasons.append("blanket_limit_exceeded")
            else:
                over = after_a - limit
                ok = over <= tol.small or (over * HUNDRED / limit <= tol.price_pct and over <= tol.price_abs) if limit > 0 else over <= tol.small
                t = {"price_tolerance_pct": f2(tol.price_pct), "price_tolerance_abs": f2(tol.price_abs), "small_difference": f2(tol.small)}
                used.update(t)
                checks.append(_check("amount_ordered", ok, W("test.amount_ordered"), expected=f2(limit),
                                     actual=f2(after_a), tolerance=t))
                if not ok:
                    reasons.append("price_above_po")
    if pl.kind == B.BLANKET and pl.limit is not None and not by_amount and sign > 0:
        after_a = before_a + (amount or ZERO)
        ok = after_a <= pl.limit + tol.small
        checks.append(_check("blanket_limit", ok, W("test.blanket_limit"), expected=f2(pl.limit), actual=f2(after_a),
                             tolerance={"small_difference": f2(tol.small)}))
        if not ok:
            reasons.append("blanket_limit_exceeded")
    # a credit note takes back no more than was billed
    if sign < 0:
        was = cum.of_invoice(inv.supplier, inv.ref_invoice).get(pl.id)
        if was is not None:
            ok = (qty_po or ZERO) <= was[0] + Decimal("0.000001") and (amount or ZERO) <= was[1] + tol.small
            checks.append(_check("credit", ok, W("test.credit_note"), expected=f"{qs(was[0])} · {f2(was[1])}",
                                 actual=f"{qs(qty_po or ZERO)} · {f2(amount or ZERO)}", tolerance={"small_difference": f2(tol.small)}))
            if not ok:
                reasons.append("credit_over_invoice")
    if counts:
        cum.bill(pl.id, inv, sign * (qty_po or ZERO), sign * (amount or ZERO))
        out["counted"] = True
    out["quantity_in_po_unit"] = qs(qty_po)
    return _close(out, reasons, notes, checks, used)


def _close(out: dict, reasons: list, notes: list, checks: list, used: dict) -> dict:
    reasons = sorted(set(reasons), key=REASON_ORDER.index)
    status = PASS
    if any(REASONS[r][1] == BLOCKED for r in reasons):
        status = BLOCKED
    elif reasons:
        status = PERSON
    if out.get("unplanned") is not None:
        status = PASS          # judged with its invoice: what was not ordered is the invoice's matter
    out.update(reasons=reasons, reason_labels=[REASON_TH[r] for r in reasons], notes=notes, checks=checks, tolerances=used,
               status=status, status_label=STATUS_TH[status])
    return out


# ── one invoice ──────────────────────────────────────────────────────────

def _elements(rv: Review, inv: B.Invoice, doc) -> list:
    """What a full tax invoice must show and this one does not."""
    if inv.doc_type != "full_tax_invoice":
        return []
    missing = []
    if not inv.supplier.name:
        missing.append("seller_name")
    if not inv.supplier.valid_tax_id:
        missing.append("seller_tax_id")
    elif not P.T.tax_id_check_ok(inv.supplier.tax_id):
        missing.append("tax_id_check")
    if not inv.number:
        missing.append("document_number")
    if inv.date is None:
        missing.append("document_date")
    if inv.vat_amount is None:
        missing.append("vat_amount")
    if not inv.lines and doc is not None:
        missing.append("lines")          # a register's row has no lines to show; the document is judged when it is read
    if doc is not None:
        f = doc.out.get("fields") or {}
        for n in ("seller_branch", "buyer_name"):
            if (f.get(n) or {}).get("normalized") in (None, "") and n not in missing:
                missing.append(n)
        for n in sorted(doc.dec.absent):
            if n in ELEMENT_TH and n not in missing:
                missing.append(n)
    return missing


def _invoice(rv: Review, cum: Cumulative, inv: B.Invoice, docs: dict, answered_by: dict | None) -> tuple[dict, list]:
    tol = rv.settings.tolerances
    reasons, notes, checks = [], [], []
    dup = is_duplicate(rv, inv)
    waiting_dup = [c for c in (rv.dup.get(inv.key) or {"groups": {"suspect": []}})["groups"]["suspect"] if c["kind"] == "candidate"]
    duplicate_of = None
    if dup is not None:
        e = dup["left"][0]
        reasons.append("duplicate_invoice")
        duplicate_of = {"number": e.get("ref"), "date": e.get("date"), "total": e.get("amount"), "file": e.get("file"),
                        "pass": dup["pass"], "pass_label": dup["pass_label"], "tier": dup["tier"], "confirmed": dup["confirmed"],
                        "confirmed_by": name_of(answered_by, dup.get("decision_key")) if dup["confirmed"] == "person" else ""}
    elif waiting_dup:
        reasons.append("duplicate_unconfirmed")
    counts = dup is None
    # a note names the invoice it corrects
    ref = None
    if inv.is_note:
        if inv.ref_invoice and cum.of_invoice(inv.supplier, inv.ref_invoice):
            ref = inv.ref_invoice
        elif inv.ref_invoice and any(i.number_key == ref_key(inv.ref_invoice) and i.supplier.key == inv.supplier.key
                                     for i in rv.invoices if i.key != inv.key):
            ref = inv.ref_invoice
        else:
            reasons.append("note_without_invoice")
    if inv.header_only:
        reasons.append("no_line_detail")
    lr = rv.links_inv.get(inv.key) or {}
    lines = [_line(rv, cum, inv, l, counts, answered_by) for l in inv.lines]
    linked = [x for x in lines if x["po_line"] is not None]
    named, known = lr.get("named") or [], lr.get("known") or []
    if not inv.header_only and not linked and not any(x.get("proposed") for x in lines):
        if named and not known:
            reasons.append("po_not_found")
            notes.append(W("note.po_named", pos=", ".join(inv.po_numbers or [l.po for l in inv.lines if l.po][:3])))
        elif not named:
            reasons.append("po_not_found")
            how = {"none": W("note.no_po.image"), "text": W("note.no_po.doc"),
                   "xml": W("note.no_po.doc"), "xml+text": W("note.no_po.doc"), "table": W("note.no_po.table")}
            notes.append(how.get(inv.refs_read, W("note.no_po.doc")))
    # the supplier
    suppliers = rv.supplier_of.get(inv.key) or {}
    for k, s in sorted(suppliers.items()):
        ok = s["verdict"] == P.SAME
        checks.append(_check("supplier", ok, W("test.seller"), expected=s["po_supplier"]["tax_id"] or s["po_supplier"]["name"],
                             actual=inv.supplier.tax_id or inv.supplier.name, detail=f"PO {s['po']}: {s['how_label']}"))
        if s["verdict"] == P.DIFFERENT:
            reasons.append("supplier_mismatch")
        elif s["verdict"] == P.UNSURE:
            reasons.append("supplier_unsure")
        elif s["how"] == "name":
            notes.append(W("note.seller_by_name", po=s["po"]))
    # tax
    base = inv.base
    rates = {x["po_line"]["vat_rate"] for x in linked if x["po_line"].get("vat_rate") is not None}
    if base is not None and inv.vat_amount is not None and base != ZERO:
        expected = B.q2(base * tol.vat_rate / HUNDRED)
        ok = abs(inv.vat_amount - expected) <= tol.small or inv.vat_amount == ZERO
        t = {"vat_rate": f2(tol.vat_rate), "small_difference": f2(tol.small)}
        if inv.vat_amount == ZERO:
            ok = not any(Decimal(r) > ZERO for r in rates)
            detail = W("test.vat.none") if ok else W("test.vat.none_but_po")
        else:
            detail = W("test.vat.detail", base=money(base), vat_rate=money(tol.vat_rate), expected=money(expected), vat_amount=money(inv.vat_amount))
            if ok and rates and all(Decimal(r) == ZERO for r in rates):
                ok, detail = False, W("test.vat.po_exempt")
        checks.append(_check("vat", ok, W("test.vat"), expected=f2(expected), actual=f2(inv.vat_amount),
                             tolerance=t, detail=detail))
        if not ok:
            reasons.append("vat_mismatch")
    if base is not None and inv.total is not None:
        expected = B.q2(base + (inv.vat_amount or ZERO))
        ok = abs(inv.total - expected) <= tol.small
        checks.append(_check("total", ok, W("test.total"), expected=f2(expected), actual=f2(inv.total),
                             tolerance={"small_difference": f2(tol.small)}))
        if not ok:
            reasons.append("total_mismatch")
    doc = docs.get(inv.key)
    missing = _elements(rv, inv, doc)
    if missing:
        reasons.append("tax_invoice_incomplete")
        notes.append(W("note.missing", what=" · ".join(ELEMENT_TH[m] for m in missing)))
    if rv.settings.company_tax_id and inv.buyer_tax_id and inv.buyer_tax_id != rv.settings.company_tax_id:
        reasons.append("buyer_mismatch")
        notes.append(W("note.buyer_other", buyer_tax_id=inv.buyer_tax_id))
    claim = inv.doc_type == "full_tax_invoice" and not missing and "buyer_mismatch" not in reasons
    why = W("vat.why.claimable") if claim else (
        W("vat.why.incomplete") if inv.doc_type == "full_tax_invoice" else
        W("vat.why.not_full", doc_type=DOC_TYPE_TH.get(inv.doc_type, DOC_TYPE_TH[""])))
    if inv.is_note:
        why = W("vat.why.note", doc_type=DOC_TYPE_TH[inv.doc_type])
    # what was billed and never ordered
    unplanned = [x for x in lines if x["unplanned"] is not None]
    total_unplanned = sum((Decimal(x["unplanned"]["amount"]) for x in unplanned), ZERO)
    footer = None
    amounts = [l.amount for l in inv.lines if l.amount is not None]
    if amounts and inv.subtotal is not None:
        discount = ZERO
        if doc is not None:
            discount = B.D(((doc.out.get("fields") or {}).get("discount") or {}).get("normalized")) or ZERO
        gap = inv.subtotal - (sum(amounts, ZERO) - discount)
        if gap > tol.small:
            footer = {"kind": "footer", "label": W("unplanned.footer"), "amount": f2(gap)}
            total_unplanned += gap
        elif gap < -tol.small and "total_mismatch" not in reasons:
            reasons.append("total_mismatch")
            notes.append(W("note.lines_over_subtotal", amounts=money(sum(amounts, ZERO)), subtotal=money(inv.subtotal)))
    if total_unplanned > ZERO:
        if total_unplanned > tol.unplanned:
            reasons.append("unplanned_cost")
        else:
            notes.append(W("note.unplanned_within", total_unplanned=money(total_unplanned), unplanned=money(tol.unplanned)))
    for x in lines:
        for r in x["reasons"]:
            if r not in reasons:
                reasons.append(r)
    reasons = sorted(set(reasons), key=REASON_ORDER.index)
    status = PASS
    if any(REASONS[r][1] == BLOCKED for r in reasons):
        status = BLOCKED
    elif reasons:
        status = PERSON
    depths = {d: sum(1 for x in lines if x["depth"] == d and x["unplanned"] is None) for d in (THREE, TWO, NONE)}
    thb = None
    if inv.total is not None:
        if inv.currency == rv.settings.currency:
            thb = inv.sign * inv.total
        elif inv.fx_rate is not None:
            thb = inv.sign * B.q2(inv.total * inv.fx_rate)
    if inv.currency != rv.settings.currency:
        notes.append(W("note.fx.rate", currency=inv.currency, fx_rate=inv.fx_rate, fx_from=inv.fx_from) if inv.fx_rate is not None else
                     W("note.fx.none", currency=inv.currency))
    again = rv.resubmitted.get(inv.key)
    if again is not None:
        notes.append(W("note.presented_again", name=again.get("name") or ""))
    depth_text = " · ".join(W("depth.count", depth=DEPTH_SHORT[d], n=n) for d, n in depths.items() if n) or W("depth.no_lines")
    out = {**inv.out(), "doc_type_label": DOC_TYPE_TH.get(inv.doc_type, DOC_TYPE_TH[""]), "status": status,
           "status_label": STATUS_TH[status], "reasons": reasons, "reason_labels": [REASON_TH[r] for r in reasons], "notes": notes,
           "checks": checks, "depths": depths, "depth_text": depth_text, "two_way_only": depths[THREE] == 0 and depths[TWO] > 0,
           "duplicate_of": duplicate_of, "corrects": ref, "counted": counts,
           "vat": {"claimable": bool(claim), "why": why, "rate": f2(tol.vat_rate), "missing": missing,
                   "missing_labels": [words.cap(ELEMENT_TH[m]) for m in missing]},
           "unplanned": [x["unplanned"] for x in unplanned] + ([footer] if footer else []),
           "unplanned_total": f2(total_unplanned), "value_thb": f2(thb), "presented_before": again is not None,
           "po_named": inv.po_numbers, "po_known": [p for p in inv.po_numbers if ref_key(p) in known],
           "suppliers": [{**s, "po_key": k} for k, s in sorted(suppliers.items())], "lines": [x["id"] for x in lines]}
    return out, lines


# ── the round ────────────────────────────────────────────────────────────

def _po_lines(rv: Review, cum: Cumulative) -> tuple[list, dict]:
    tol = rv.settings.tolerances
    rows, accrued, prepaid = [], ZERO, ZERO
    for pl in rv.po:
        got = cum.received.get(pl.id, [])
        billed = cum.billed.get(pl.id, [])
        if not got and not billed:
            continue
        received, q, a = cum.received_qty(pl.id), cum.billed_qty(pl.id), cum.billed_amount(pl.id)
        has_receipts = bool(got)
        price = pl.unit_price or ZERO
        not_billed = max(received - q, ZERO) if has_receipts else None
        not_received = max(q - received, ZERO) if has_receipts else None
        over_po = pl.qty is not None and q > pl.qty * (1 + tol.over_pct / HUNDRED) * (1 + tol.qty_pct / HUNDRED)
        over_received = has_receipts and q > received * (1 + tol.qty_pct / HUNDRED)
        r = {**pl.out(), "received": qs(received) if has_receipts else None, "billed": qs(q), "billed_amount": f2(a),
             "open_to_bill": None if pl.qty is None else qs(max(pl.qty - q, ZERO)),
             "open_to_receive": None if pl.qty is None or not has_receipts else qs(max(pl.qty - received, ZERO)),
             "received_not_billed": None if not_billed is None else qs(not_billed),
             "received_not_billed_amount": None if not_billed is None else f2(B.q2(not_billed * price)),
             "billed_not_received": None if not_received is None else qs(not_received),
             "billed_not_received_amount": None if not_received is None else f2(B.q2(not_received * price)),
             "over_billed": bool(over_po or over_received),
             "over_billed_why": W("over.received") if over_received else (W("over.ordered") if over_po else ""),
             "receipts": [{"gr": g.gr, "date": g.date.isoformat() if g.date else None, "quantity": qs(x), "in_system": g.in_system,
                           "confirmed_by": g.confirmed_by, **(g.source.out() if g.source else {"file": None, "sheet": None, "row": None})}
                          for g, x, _l in got],
             "invoices": [{"invoice": e["invoice"], "date": e["date"], "quantity": qs(e["quantity"]), "amount": f2(e["amount"]),
                           "from": e["from"], "file": e["file"], "kind": e["kind"]} for e in billed],
             "depth": THREE if has_receipts else TWO}
        if not_billed is not None:
            accrued += B.q2(not_billed * price)
            prepaid += B.q2(not_received * price)
        rows.append(r)
    grir = {"received_not_billed": f2(accrued), "billed_not_received": f2(prepaid),
            "lines": [r["id"] for r in rows if r["received"] is not None and (Decimal(r["received_not_billed"]) > 0
                                                                                or Decimal(r["billed_not_received"]) > 0)],
            "note": W("grir.note"),
            "covers": W("grir.covers")}
    return rows, grir


def _exceptions(rv: Review, invoices: list, lines: list) -> list:
    out = []
    by_inv = {}
    for x in lines:
        by_inv.setdefault(x["invoice"], []).append(x)

    def add(code, subject, source, detail, action):
        severity = "review"
        out.append({"reason_code": code, "reason": REASON_TH[code], "severity": severity, "subject": subject, "source_ref": source,
                    "detail": detail, "proposed_action": action,
                    "choices": [{"label": W(l), "value": v} for l, v in CHOICES.get(code, [])]})

    for inv in invoices:
        src = {"sheet_name": " · ".join(str(x) for x in (inv.get("file"), inv.get("sheet")) if x), "row": inv.get("row"),
               "cell": None, "rows": [{"file": inv.get("file"), "sheet": inv.get("sheet"), "row": inv.get("row"),
                                       "cells": inv.get("cells") or {}}]}
        label = W("ex.invoice", number=inv["number"] or inv["name"], seller=E3C.safe(inv["supplier"]["name"])[:40])
        mine = by_inv.get(inv["key"], [])
        for code in inv["reasons"]:
            on_lines = [x for x in mine if code in x["reasons"]]
            if on_lines:
                for x in on_lines:
                    chk = next((c for c in x["checks"] if not c["ok"] and c["detail"]), None)
                    po = x["po_line"] or (x.get("proposed") or {}).get("po_line") or {}
                    rows = [{"file": x.get("file"), "sheet": x.get("sheet"), "row": x.get("row"), "cells": x.get("cells") or {}}]
                    if po:
                        rows.append({"file": po.get("file"), "sheet": po.get("sheet"), "row": po.get("row"), "cells": po.get("cells") or {}})
                    detail = W("ex.line", label=label, n=x["n"], description=E3C.safe(x["description"])[:50])
                    if chk is not None:
                        detail += f": {chk['detail']}"
                    elif x["notes"]:
                        detail += f": {x['notes'][0]}"
                    add(code, f"{inv['key']}:{x['id']}", {**src, "rows": rows, "row": x.get("row")}, detail,
                        W(CHOICES[code][0][0]) if CHOICES.get(code) else W("choice.review"))
            else:
                chk = next((c for c in inv["checks"] if not c["ok"] and c["detail"]), None)
                detail = label
                if code == "duplicate_invoice" and inv["duplicate_of"]:
                    d = inv["duplicate_of"]
                    detail += ": " + W("ex.duplicate_of", number=d["number"], date=E3C.date_th(d["date"]), pass_label=d["pass_label"])
                elif code in ("vat_mismatch", "total_mismatch", "supplier_mismatch", "supplier_unsure") and chk is not None:
                    detail += f": {chk['detail']}"
                elif inv["notes"]:
                    detail += f": {inv['notes'][0]}"
                add(code, f"{inv['key']}:{code}", src, detail, W(CHOICES[code][0][0]) if CHOICES.get(code) else W("choice.review"))
    return out


def _summary(rv: Review, invoices: list, lines: list, grir: dict) -> dict:
    by_status = {s: {"status": s, "label": STATUS_TH[s], "count": 0, "value": ZERO, "without_value": 0} for s in (PASS, BLOCKED, PERSON)}
    by_reason: dict = {}
    other: dict = {}
    for inv in invoices:
        s = by_status[inv["status"]]
        s["count"] += 1
        if inv["value_thb"] is not None:
            s["value"] += Decimal(inv["value_thb"])
        else:
            s["without_value"] += 1
            if inv["total"] is not None:
                o = other.setdefault(inv["currency"], {"currency": inv["currency"], "count": 0, "total": ZERO})
                o["count"] += 1
                o["total"] += Decimal(inv["total"])
        for r in inv["reasons"]:
            b = by_reason.setdefault(r, {"code": r, "label": REASON_TH[r], "outcome": REASONS[r][1], "invoices": 0, "lines": 0,
                                         "value": ZERO})
            b["invoices"] += 1
            if inv["value_thb"] is not None:
                b["value"] += Decimal(inv["value_thb"])
    for x in lines:
        for r in x["reasons"]:
            if r in by_reason:
                by_reason[r]["lines"] += 1
    tested = [x for x in lines if x["unplanned"] is None]
    depth = {d: {"depth": d, "label": DEPTH_TH[d], "lines": sum(1 for x in tested if x["depth"] == d),
                 "amount": f2(sum((Decimal(x["amount"]) for x in tested if x["depth"] == d and x["amount"] is not None), ZERO))}
             for d in (THREE, TWO, NONE)}
    why: dict = {}
    for x in tested:
        if x["depth"] == TWO:
            why[x["depth_why"]] = why.get(x["depth_why"], 0) + 1
    return {
        "invoices": len(invoices), "lines": len(tested),
        "by_status": [{**v, "value": f2(v["value"])} for v in by_status.values()],
        "by_reason": [{**by_reason[r], "value": f2(by_reason[r]["value"])} for r in REASON_ORDER if r in by_reason],
        "by_depth": list(depth.values()), "two_way_why": [{"why": k, "lines": v} for k, v in sorted(why.items())],
        "other_currencies": [{**o, "total": f2(o["total"])} for o in sorted(other.values(), key=lambda o: o["currency"])],
        "currency": rv.settings.currency, "received_not_billed": grir["received_not_billed"],
        "billed_not_received": grir["billed_not_received"],
        "approval": W("summary.approval"),
    }


def _not_included(rv: Review) -> list:
    out = []
    for d in rv.docs:
        if d.status != "excluded":
            continue
        ex = d.exclusion
        rs = ex.get("restore")
        out.append({"doc_key": d.upload.key, "name": d.upload.name, "code": ex["code"], "reason": EXCLUDED_TH.get(ex["code"], W("excluded.default")),
                    "detail": ex.get("detail") or "", "how": _how(rs), "can_restore": bool(rs and (rs["set"] or rs["drop"])),
                    "restore": rs or {"set": {}, "drop": []}})
    for u, why, detail in rv.others:
        if why == "set_aside":
            key = next((f"pomatch_kind:{u.key}.{t['sheet_index']}" for t in u.data["tables"]
                        if rv.answers.get(f"pomatch_kind:{u.key}.{t['sheet_index']}") == "unused"), None)
            out.append({"doc_key": u.key, "name": u.name, "code": "set_aside", "reason": W("left.set_aside"),
                        "detail": detail, "how": W("left.set_aside.how"), "can_restore": key is not None,
                        "restore": {"set": {}, "drop": [key] if key else []}})
        else:
            out.append({"doc_key": u.key, "name": u.name, "code": why,
                        "reason": {"same_file": W("left.same_file")}.get(why, detail or W("left.not_used")),
                        "detail": detail if why == "same_file" else W("left.other.detail"),
                        "how": W("left.same_file.how") if why == "same_file" else W("left.other.how"),
                        "can_restore": False, "restore": {"set": {}, "drop": []}})
    return out


EXCLUDED_TH = W.table({
    "missing_required_field": "excluded.missing_required_field", "unconfirmed_value": "excluded.unconfirmed_value",
    "sources_disagree": "excluded.sources_disagree", "check_failed": "excluded.check_failed",
    "duplicate": "excluded.duplicate", "copy_not_original": "excluded.copy_not_original", "unknown_document_type": "excluded.unknown_document_type",
    "unreadable_page": "excluded.unreadable_page", "typed_value_invalid": "excluded.typed_value_invalid", "excluded_by_person": "excluded.excluded_by_person",
    "not_an_invoice": "excluded.not_an_invoice",
})


def _how(rs: dict | None) -> str:
    if not rs or not (rs["set"] or rs["drop"]):
        return W("restore.none")
    if rs["drop"]:
        return W("restore.ask")
    return W("restore.record")


def _index(rv: Review, invoices: list, lines: list) -> list:
    by_inv: dict = {}
    for x in lines:
        by_inv.setdefault(x["invoice"], []).append(x)
    out = []
    for inv in invoices:
        if inv["duplicate_of"] is not None:
            continue
        alloc = []
        for x in by_inv.get(inv["key"], []):
            if not x["counted"] or x["po_line"] is None:
                continue
            sign = -1 if inv["doc_type"] == "credit_note" else 1
            q = Decimal(x["quantity_in_po_unit"]) if x.get("quantity_in_po_unit") not in (None, "") else ZERO
            a = Decimal(x["amount"]) if x["amount"] is not None else ZERO
            alloc.append({"akey": x["po_line"]["akey"], "po": x["po_line"]["po"], "line_no": x["po_line"]["line_no"],
                          "quantity": qs(sign * q), "amount": f2(sign * a), "unit": x["po_line"]["unit"], "depth": x["depth"]})
        out.append({"doc_key": inv["key"], "name": inv["name"], "sha256": inv["sha256"],
                    "seller_tax_id": inv["supplier"]["tax_id"] or None, "document_number": inv["number"] or None,
                    "document_date": inv["date"], "total": inv["total"],
                    "detail": {"kind": inv["doc_type"] if inv["doc_type"] in B.NOTE_TYPES else "invoice", "outcome": inv["status"],
                               "reasons": inv["reasons"], "seller_name": inv["supplier"]["name"], "lines": alloc,
                               "po_numbers": inv["po_named"], "ref_invoice": inv["ref_invoice"]}})
    for k in sorted(rv.answers):
        if not k.startswith("pomatch_receipt:"):
            continue
        akey = k[len("pomatch_receipt:"):]
        pl = next((p for p in rv.po if p.akey == akey), None)
        got = next((g for p, g in rv.confirmed if p.akey == akey and g.id.startswith("c")), None)
        if pl is None or got is None:
            continue
        sha = hashlib.sha256(f"{akey}|{got.qty}|{rv.today.isoformat()}".encode()).hexdigest()
        out.append({"doc_key": "receipt:" + hashlib.sha256(akey.encode()).hexdigest()[:16], "name": W("index.receipt"), "sha256": sha,
                    "seller_tax_id": pl.supplier.tax_id or None, "document_number": pl.po, "document_date": rv.today.isoformat(),
                    "total": None, "detail": {"kind": "receipt", "akey": akey, "po": pl.po, "line_no": pl.line_no,
                                              "quantity": qs(got.qty), "unit": pl.unit, "confirmed_by": got.confirmed_by,
                                              "decision_key": k}})
    return out


def _offers(rv: Review, po_rows: list, lines: list) -> list:
    """The PO lines a person may confirm the receipt of, in this system — never required: goods
    lines that were tested two ways because no receipt of them exists."""
    seen, out = set(), []
    for x in lines:
        pl = x["po_line"]
        if pl is None or x["depth"] != TWO or pl["kind"] != B.GOODS or pl["id"] in seen or pl["quantity"] is None:
            continue
        seen.add(pl["id"])
        out.append({"akey": pl["akey"], "key": f"pomatch_receipt:{pl['akey']}", "po": pl["po"], "line_no": pl["line_no"],
                    "item_code": pl["item_code"], "description": pl["description"], "ordered": pl["quantity"], "unit": pl["unit"],
                    "billed": x["cumulative"]["billed_after"] if x["cumulative"] else None, "file": pl.get("file"),
                    "sheet": pl.get("sheet"), "row": pl.get("row")})
    return out


def compute(uploads, roles, period, *, answers=None, defaults=None, today=None, history=None, proposals=None,
            answered_by=None) -> dict:
    rv = review(uploads, defaults=defaults, answers=answers, today=today, history=history, proposals=proposals, period=period)
    if not rv.ready:
        raise ValueError("the round has no purchase order lines or no invoice to test")
    cum = Cumulative(rv)
    docs = {d.upload.key: d for d in rv.docs}
    invoices, lines = [], []
    for inv in rv.invoices:
        o, ls = _invoice(rv, cum, inv, docs, answered_by)
        invoices.append(o)
        lines += ls
    for g_list in cum.received.values():
        for _g, _q, lk in g_list:
            if lk.get("confirmed") == "person" and lk.get("decision_key") and not lk.get("confirmed_by"):
                lk["confirmed_by"] = name_of(answered_by, lk["decision_key"])
    for p, g in rv.confirmed:
        if not g.confirmed_by:
            g.confirmed_by = name_of(answered_by, f"pomatch_receipt:{p.akey}")
    po_rows, grir = _po_lines(rv, cum)
    run_ex = _exceptions(rv, invoices, lines)
    summary = _summary(rv, invoices, lines, grir)
    label = words.period(period)
    tiers, passes = _tiers(rv)
    by = {s["status"]: s for s in summary["by_status"]}
    head = (W("narr.summary", n=len(invoices), passed=by[PASS]["count"], blocked=by[BLOCKED]["count"], person=by[PERSON]["count"]))
    d3, d2 = summary["by_depth"][0]["lines"], summary["by_depth"][1]["lines"]
    depth_line = W("narr.depth.no_gr", d3=d3, d2=d2) if d2 and not d3 else W("narr.depth", d3=d3, d2=d2)
    # AM22: every reason, up to the commentary sheet's ceiling and a line that says how many more (all of them
    # are on the summary and the exceptions sheets) — no longer the first six and nothing said of the rest
    watch = [W("narr.reason", label=r["label"], invoices=r["invoices"]) for r in summary["by_reason"]]
    over = [p for p in po_rows if p["over_billed"]]
    if over:
        watch.insert(0, W("narr.over", n=len(over)))
    watch = _capped(watch)
    fallback = {"summary": head, "movements": [depth_line,
                                               W("narr.grir", received_not_billed=money(grir["received_not_billed"]), billed_not_received=money(grir["billed_not_received"]))],
                "watch": watch, "next_steps": [W("narr.next.exceptions"),
                                               W("narr.next.approval")]}
    quotable = {"invoices": str(len(invoices)), "passed": str(by[PASS]["count"]), "blocked": str(by[BLOCKED]["count"]),
                "person": str(by[PERSON]["count"]), "value_passed": by[PASS]["value"] or "", "value_blocked": by[BLOCKED]["value"] or "",
                "received_not_billed": grir["received_not_billed"], "billed_not_received": grir["billed_not_received"]}
    # AM22 §1.1: the figures a sentence of the commentary may name, each with its own citation (the counts and
    # values by status: s1 … v3 in _citations; the invoices and the GR/IR amounts: n1, g1, g2); the facts a
    # model is shown, with the figures by key; the layouts' limits for the words, in the run's language
    from ....deliverables.look.catalogue import writing_limits
    from ....engine import figures as F
    at = {s["status"]: n for n, s in enumerate(summary["by_status"], start=1)}
    figures = F.table([
        ("invoices", quotable["invoices"], "n1", W("cite.invoices.label")),
        ("passed", quotable["passed"], f"s{at[PASS]}", W("cite.count", label=STATUS_TH[PASS])),
        ("blocked", quotable["blocked"], f"s{at[BLOCKED]}", W("cite.count", label=STATUS_TH[BLOCKED])),
        ("person", quotable["person"], f"s{at[PERSON]}", W("cite.count", label=STATUS_TH[PERSON])),
        ("value_passed", money(by[PASS]["value"]) if by[PASS]["value"] else "", f"v{at[PASS]}", W("cite.value", label=STATUS_TH[PASS], currency=summary["currency"])),
        ("value_blocked", money(by[BLOCKED]["value"]) if by[BLOCKED]["value"] else "", f"v{at[BLOCKED]}", W("cite.value", label=STATUS_TH[BLOCKED], currency=summary["currency"])),
        ("received_not_billed", money(grir["received_not_billed"]), "g1", W("cite.grir.received.label")),
        ("billed_not_received", money(grir["billed_not_received"]), "g2", W("cite.grir.billed.label"))])
    facts = F.facts(
        (W("fact.h.result"), [W("fact.po.counts", invoices="{invoices}", passed="{passed}", blocked="{blocked}", person="{person}"),
                              W("fact.po.values", value_passed="{value_passed}", value_blocked="{value_blocked}"), depth_line,
                              W("fact.po.grir", received_not_billed="{received_not_billed}", billed_not_received="{billed_not_received}")]),
        (W("fact.h.reasons"), [W("narr.reason", label=r["label"], invoices=r["invoices"]) for r in summary["by_reason"]]
         + ([W("narr.over", n=len(over))] if over else [])))
    return {
        "figures": figures, "narrative_facts": facts, "writing_limits": writing_limits("xlsx"),
        "schema": SCHEMA, "period": period, "period_label": label, "period_source": rv.period_source,
        "prepared_on": (today or rv.today).isoformat(), "company": rv.settings.company,
        "company_tax_id": rv.settings.company_tax_id, "currency": rv.settings.currency,
        "practice": {"gr": rv.settings.gr_practice, "label": S.GR_PRACTICE_VALUE_TH[rv.settings.gr_practice],
                     "from": rv.settings.gr_from, "question": W(S.GR_PRACTICE_TH)},
        "invoices": invoices, "lines": lines, "po_lines": po_rows, "grir": grir,
        "unplanned": [{"invoice": i["key"], "invoice_number": i["number"], "supplier": i["supplier"]["name"], **u,
                       "within_limit": Decimal(i["unplanned_total"]) <= rv.settings.tolerances.unplanned}
                      for i in invoices for u in i["unplanned"]],
        "notes": [{"key": i["key"], "number": i["number"], "kind": i["doc_type"], "kind_label": i["doc_type_label"],
                   "supplier": i["supplier"]["name"], "date": i["date"], "total": i["total"], "ref_invoice": i["ref_invoice"],
                   "corrects": i["corrects"], "status": i["status"], "status_label": i["status_label"],
                   "reasons": i["reason_labels"]} for i in invoices if i["doc_type"] in B.NOTE_TYPES],
        "summary": summary, "match": {"tiers": tiers, "passes": passes, "names": dict(L.NAMES_INV),
                                      "links": _links(rv, answered_by)},
        "receipt_offers": _offers(rv, po_rows, lines),
        "run_exceptions": run_ex, "reason_codes": dict(REASON_TH), "not_included": _not_included(rv), "assumptions": rv.assumptions,
        "totals": {"invoices": len(invoices), "lines": summary["lines"], "passed": by[PASS]["count"], "blocked": by[BLOCKED]["count"],
                   "person": by[PERSON]["count"], "three_way": d3, "two_way": d2, "unlinked": summary["by_depth"][2]["lines"],
                   "po_lines": len(po_rows), "over_billed": len(over), "run_exceptions": len(run_ex), "review": len(run_ex),
                   "value_passed": by[PASS]["value"], "value_blocked": by[BLOCKED]["value"], "value_person": by[PERSON]["value"],
                   "received_not_billed": grir["received_not_billed"], "billed_not_received": grir["billed_not_received"],
                   "by_rule": sum(t["by_rule"] for t in tiers), "by_person": sum(t["by_person"] for t in tiers),
                   "waiting": sum(t["waiting"] for t in tiers)},
        "files": _files(rv), "settings": {"tolerances": rv.settings.tolerances.to_dict(), "unusable": rv.settings.unusable,
                                          "conversions": rv.settings.conversions, "labels": dict(S.SETTING_TH)},
        "index_entries": _index(rv, invoices, lines), "models": [],
        "quotable": quotable, "quotable_ids": [], "fallback_narrative": fallback,
        # the runner's generic view of computed.json
        "exceptions": run_ex, "excluded": [], "ratios": [], "top_movements": [],
        "citations": _citations(invoices, lines, summary, grir),
        "provenance": _citations(invoices, lines, summary, grir),
        "source": {"po": _named(rv, "po"), "gr": _named(rv, "gr"), "invoice": _named(rv, "invoice")},
        "script": SCRIPT,
    }


def _named(rv: Review, kind: str) -> list:
    out = sorted(({"name": u.name, "sha256": u.sha256} for u, _t, k in rv.tables if k == kind), key=lambda x: x["name"])
    if kind == "invoice":
        out += [{"name": d.upload.name, "sha256": d.upload.sha256} for d in rv.docs if d.status == "included"]
    seen, uniq = set(), []
    for x in out:
        if x["sha256"] not in seen:
            seen.add(x["sha256"])
            uniq.append(x)
    return uniq


def _files(rv: Review) -> list:
    out = []
    for u, t, kind in sorted(rv.tables, key=lambda x: (("po", "gr", "invoice").index(x[2]), x[0].name, x[1]["sheet_index"])):
        out.append({"name": u.name, "sha256": u.sha256, "kind": kind, "sheet": t["sheet"], "rows": len(t["rows"]),
                    "header_row": t["header_row"], "columns": t["columns"], "skipped": len(t["skipped"]),
                    "said_by": [s for s in (t.get("signs") or {}).get(kind, [])][:3]})
    for d in rv.docs:
        if d.status == "included":
            x = d.out["document"]
            out.append({"name": d.upload.name, "sha256": d.upload.sha256, "kind": "invoice", "sheet": None,
                        "rows": len(d.out.get("line_items") or []), "header_row": None, "columns": {}, "skipped": 0,
                        "said_by": [W("doc.file.read", tier=x["tier"], method=d.out["method"])]})
    return out


def _tiers(rv: Review) -> tuple[list, list]:
    """Every link of the round by the engine's tier and pass: what a rule accepted, what a person did."""
    tiers = {t: {"tier": t, "label": lab, "groups": 0, "by_rule": 0, "by_person": 0, "waiting": 0}
             for t, lab in (("exact", W("tier.exact")), ("amount_date", W("tier.amount_date")),
                            ("fuzzy", W("tier.fuzzy")))}
    passes: dict = {}
    for res in list(rv.links_gr.values()) + list(rv.links_inv.values()) + list(rv.dup.values()):
        for p in res["passes"]:
            q = passes.setdefault(p["id"], {"id": p["id"], "label": p["label"], "tier": p["tier"], "auto": p["auto"], "by_rule": 0,
                                            "by_person": 0, "waiting": 0})
            for k in ("by_rule", "by_person", "waiting"):
                q[k] += p[k]
        for t in res["tiers"]:
            for k in ("groups", "by_rule", "by_person", "waiting"):
                tiers[t["tier"]][k] += t[k]
    order = list(L.LABEL)
    return list(tiers.values()), sorted(passes.values(), key=lambda p: order.index(p["id"]) if p["id"] in order else 99)


def _links(rv: Review, answered_by: dict | None) -> list:
    """Every link the engine made or proposed: which document, which pass, which tier, who confirmed."""
    out = []

    def rows(kind: str, doc: str, res: dict):
        for group, state in (("matched", "linked"), ("suspect", "waiting")):
            for m in res["groups"][group]:
                if m.get("kind") == "possible_duplicate":
                    continue
                who = m.get("confirmed") or ""
                out.append({"kind": kind, "document": doc, "state": state, "pair": m["pair"], "pass": m["pass"],
                            "pass_label": m["pass_label"], "tier": m["tier"], "tier_label": m["tier_label"], "shape": m["shape"],
                            "confirmed": who, "confirmed_by": name_of(answered_by, m.get("decision_key")) if who == "person" else "",
                            "decision": m.get("decision"), "left": m["left"], "right": m["right"],
                            "similarity": m.get("similarity")})
    for k in sorted(rv.links_gr):
        rows("receipt", k.split("|")[-1], rv.links_gr[k])
    for inv in rv.invoices:
        rows("duplicate", inv.number or inv.name, rv.dup[inv.key])
        rows("invoice", inv.number or inv.name, rv.links_inv[inv.key])
    return out


def _citations(invoices: list, lines: list, summary: dict, grir: dict | None = None) -> list:
    """How each figure of the summary was made: the invoices a count or a value adds up. AM22: with ``grir``, the
    count of every invoice and the two GR/IR amounts too, so a commentary can point to them."""
    def c(cid, name, value, formula, sources):
        # name() says the figure's name in the language being spoken: a question may also ask about a figure by
        # the words the working paper prints (engine/words.py names)
        label, aliases = words.names(lambda: (name(), []))
        return {"id": cid, "label": label, "value": value, "formula": formula, "sources": sources, "left_out": [], "aliases": aliases,
                "script": SCRIPT}
    out = []
    for n, s in enumerate(summary["by_status"], start=1):
        mine = [i for i in invoices if i["status"] == s["status"]]
        out.append(c(f"s{n}", lambda status=s["status"]: W("cite.count", label=STATUS_TH[status]), str(s["count"]), W("cite.count.formula"),
                     [{"file": i["name"], "sheet": i.get("sheet") or "", "row": i.get("row"), "value": i["number"], "note": i["status_label"]}
                      for i in mine]))
        out.append(c(f"v{n}", lambda status=s["status"]: W("cite.value", label=STATUS_TH[status], currency=summary["currency"]),
                     s["value"], W("cite.value.formula"),
                     [{"file": i["name"], "sheet": i.get("sheet") or "", "row": i.get("row"), "value": i["value_thb"], "note": i["number"]}
                      for i in mine if i["value_thb"] is not None]))
    if grir is not None:
        out.append(c("n1", lambda: W("cite.invoices.label"), str(len(invoices)), W("cite.invoices.formula"),
                     [{"file": i["name"], "sheet": i.get("sheet") or "", "row": i.get("row"), "value": i["number"], "note": i["status_label"]} for i in invoices]))
        out.append(c("g1", lambda: W("cite.grir.received.label"), grir["received_not_billed"], W("cite.grir.received.formula"), []))
        out.append(c("g2", lambda: W("cite.grir.billed.label"), grir["billed_not_received"], W("cite.grir.billed.formula"), []))
    return out


def summarize(computed: dict) -> dict:
    t, s = computed["totals"], computed["summary"]
    return {"period": computed["period"], "period_label": computed["period_label"], "kind": "po_matching",
            "invoices": t["invoices"], "lines": t["lines"], "passed": t["passed"], "blocked": t["blocked"], "person": t["person"],
            "three_way": t["three_way"], "two_way": t["two_way"], "unlinked": t["unlinked"], "over_billed": t["over_billed"],
            "value_passed": t["value_passed"], "value_blocked": t["value_blocked"], "value_person": t["value_person"],
            "received_not_billed": t["received_not_billed"], "billed_not_received": t["billed_not_received"],
            "by_rule": t["by_rule"], "by_person": t["by_person"], "waiting": t["waiting"],
            "run_exceptions": t["run_exceptions"], "review": t["review"],
            "by_reason": [{k: r[k] for k in ("code", "label", "outcome", "invoices", "lines", "value")} for r in s["by_reason"]],
            "by_depth": s["by_depth"], "practice": computed["practice"]["label"],
            "tiers": [{k: x[k] for k in ("tier", "label", "groups", "by_rule", "by_person", "waiting")} for x in computed["match"]["tiers"]],
            "receipt_offers": len(computed["receipt_offers"]),
            "models": [], "excluded_list": [],
            "not_included": [{k: e[k] for k in ("doc_key", "name", "reason", "detail", "how", "can_restore")}
                             for e in computed.get("not_included") or []]}


def _capped(lines: list) -> list:
    """AM22: the rules' list under one heading, at most the commentary sheet's ceiling; more is said, never cut."""
    from ....engine import figures as F

    return F.capped_rules(lines)
