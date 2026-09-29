"""Which PO line a line belongs to — on the E3 engine, with this agent's own passes.

Three questions, each of them "two tables in, four groups out" (engine/e3):

    a goods receipt's lines   ↔  the lines of the purchase orders      (one run per goods receipt)
    an invoice's lines        ↔  the lines of the purchase orders      (one run per invoice)
    an invoice                ↔  the invoices presented before it      (one run per invoice)

One run per document, because one PO line can be delivered and billed many times: within a
document a PO line is taken once, across documents it is compared cumulatively (scripts/match.py).

The engine's rule holds here as everywhere: a pass of the ``exact`` tier — the document names the
PO and the line, or the PO and the item code, or the PO and the very same description — links by
itself; every other pass only proposes, and a person confirms (a card, engine/e3/cards.py keys).
No pass reads a price list, a clock or a model.

    pass                 tier          links by itself
    po_line              exact         yes   the PO number and the PO's line number
    po_item              exact         yes   the PO number and the item code
    po_description       exact         yes   the PO number and the same description
    po_only_line         exact         yes   (goods receipts) the PO has a single line
    po_price             amount_date   no    the same PO, the unit price within tolerance
    supplier_item        amount_date   no    no PO named: the supplier and the item code
    po_similar           fuzzy         no    the same PO, a description that resembles
    supplier_similar     fuzzy         no    no PO named: the supplier, a description that resembles

    same_number          exact         yes   (duplicates) the supplier and the invoice number
    same_amount_date     amount_date   no    the supplier, the total, a date within the window
    similar_number       fuzzy         no    the supplier, a number that resembles, the same total
    same_number_other_total  fuzzy     no    the supplier and the number, another total
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from difflib import SequenceMatcher

from ....engine import e3
from ....engine.e2 import thai_text as T
from ....engine.e3 import cards as E3C
from ....engine.e3.rows import Row, name_key, ref_key, similarity
from . import book as B
from . import parties as P
from .settings import Tolerances

ZERO = Decimal("0.00")
NAMES_INV = {"left": "ใบสั่งซื้อ", "right": "ใบแจ้งหนี้"}
NAMES_GR = {"left": "ใบสั่งซื้อ", "right": "ใบรับของ"}
NAMES_DUP = {"left": "ใบแจ้งหนี้ที่ส่งมาก่อน", "right": "ใบแจ้งหนี้ฉบับนี้"}
LABEL = {
    "po_line": "เลขที่ PO และบรรทัดของ PO ตรงกัน",
    "po_item": "เลขที่ PO และรหัสสินค้าตรงกัน",
    "po_description": "เลขที่ PO และชื่อรายการตรงกัน",
    "po_only_line": "PO มีรายการเดียว",
    "po_price": "PO เดียวกัน ราคาต่อหน่วยอยู่ในช่วงที่ตั้งไว้",
    "supplier_item": "ผู้ขายเดียวกัน รหัสสินค้าตรงกัน (เอกสารไม่ระบุ PO)",
    "po_similar": "PO เดียวกัน ชื่อรายการคล้ายกัน",
    "supplier_similar": "ผู้ขายเดียวกัน ชื่อรายการคล้ายกัน (เอกสารไม่ระบุ PO)",
    "same_number": "ผู้ขายและเลขที่ใบแจ้งหนี้ตรงกัน",
    "same_amount_date": "ผู้ขายและยอดรวมตรงกัน วันที่อยู่ในช่วงที่ตั้งไว้",
    "similar_number": "ผู้ขายเดียวกัน เลขที่คล้ายกัน ยอดรวมตรงกัน",
    "same_number_other_total": "ผู้ขายและเลขที่ตรงกัน แต่ยอดรวมไม่ตรงกัน",
}


def desc_key(text) -> str:
    """A description as the exact pass compares it: without legal forms, spaces and punctuation
    (name_key) and without the Thai marks a PDF's text layer loses ("แฟมเอกสาร 3 นิว" is
    "แฟ้มเอกสาร 3 นิ้ว"). Two PO lines that differ in nothing but marks share a key, and a key
    that names two PO lines links nothing by rule."""
    return T.skel(name_key(text))


def _money(v) -> Decimal:
    return B.q2(v if v is not None else ZERO)


def po_row(pl: B.PoLine) -> Row:
    amount = pl.amount if pl.amount is not None else pl.limit
    ref = " · ".join(x for x in (pl.po, f"บรรทัด {pl.line_no}" if pl.line_no else "", pl.item_code) if x)
    return Row(id=pl.id, side="left", amount=_money(amount), date=pl.date, ref=ref, name=pl.description,
               source=pl.source.out() if pl.source else {})


def po_keys(pl: B.PoLine) -> dict:
    return {"po": {pl.po_key} if pl.po_key else set(), "line": B.ref_key_short(pl.line_no) if pl.line_no else "",
            "item": B.code_key(pl.item_code), "desc": desc_key(pl.description), "text": pl.description,
            "price": pl.unit_price, "supplier": pl.supplier, "only": False}


# ── the passes ───────────────────────────────────────────────────────────

def _same_po(kl: dict, kr: dict) -> bool:
    return bool(kl["po"] & kr["po"])


def _items_agree(kl: dict, kr: dict) -> bool:
    """Two lines that both carry an item code carry the same one."""
    return not (kl["item"] and kr["item"] and kl["item"] != kr["item"])


def _exact(pid: str, keys: dict, key_of, guard=_items_agree) -> e3.Pass:
    """Lines that share a key naming one PO line. Several lines of the document under one key
    (the same item billed on two lines) are one group with that PO line."""
    def find(L, R, tol, refused):
        left: dict = {}
        for l in L:
            for k in key_of(keys[l.id]):
                left.setdefault(k, []).append(l)
        groups, order = {}, []
        for r in R:
            hits = {}
            for k in key_of(keys[r.id]):
                for l in left.get(k, ()):
                    hits[l.id] = l
            if len(hits) != 1:
                continue            # the key names no PO line, or more than one
            l = next(iter(hits.values()))
            if not guard(keys[l.id], keys[r.id]):
                continue
            if l.id not in groups:
                groups[l.id] = (l, [])
                order.append(l.id)
            groups[l.id][1].append(r)
        return [e3.Proposal((groups[i][0],), tuple(groups[i][1])) for i in order], {}
    return e3.Pass(pid, "exact", LABEL[pid], find, auto=True)


def _proposed(pid: str, tier: str, keys: dict, score, *, batch: bool = False) -> e3.Pass:
    """``score(left keys, right keys)`` → (rank, similarity) or None when the two are no pair."""
    def find(L, R, tol, refused):
        found = []
        for l in L:
            for r in R:
                if e3.pair_id((l,), (r,)) in refused:
                    continue
                s = score(keys[l.id], keys[r.id])
                if s is None:
                    continue
                rank, sim = s
                found.append((*rank, l.order, r.order, l, r, sim))
        return e3.best_pairs(found, L, R), {}
    return e3.Pass(pid, tier, LABEL[pid], find, batch=batch)


def _k_line(k):
    return [(po, k["line"]) for po in sorted(k["po"])] if k["line"] else []


def _k_item(k):
    return [(po, k["item"]) for po in sorted(k["po"])] if k["item"] else []


def _k_desc(k):
    return [(po, k["desc"]) for po in sorted(k["po"])] if k["desc"] else []


def _k_po(k):
    return [(po,) for po in sorted(k["po"])] if k["po"] and (k["only"] or k.get("right")) else []


def line_passes(keys: dict, tol: Tolerances, *, receipts: bool = False) -> tuple:
    """The passes for a document's lines against the PO lines, in order."""
    def within_price(a: Decimal | None, b: Decimal | None) -> bool:
        if a is None or b is None or a <= 0:
            return False
        diff = abs(b - a)
        return diff * 100 / a <= tol.price_pct

    def price(kl, kr):
        if not _same_po(kl, kr) or not _items_agree(kl, kr) or not within_price(kl["price"], kr["price"]):
            return None
        return (abs(kr["price"] - kl["price"]),), None

    def supplier_item(kl, kr):
        if kr["po"] or not kr["item"] or kl["item"] != kr["item"] or P.compare(kl["supplier"], kr["supplier"])[0] != P.SAME:
            return None
        return (Decimal(0),), None

    def similar(same_po: bool):
        def score(kl, kr):
            if same_po and not _same_po(kl, kr):
                return None
            if not same_po and (kr["po"] or P.compare(kl["supplier"], kr["supplier"])[0] != P.SAME):
                return None
            if not _items_agree(kl, kr):
                return None
            s = similarity(kl["text"], kr["text"])
            if s < tol.fuzzy:
                return None
            return (-s,), s
        return score

    exact = [_exact("po_line", keys, _k_line), _exact("po_item", keys, _k_item), _exact("po_description", keys, _k_desc)]
    if receipts:
        return tuple(exact + [_exact("po_only_line", keys, _k_po),
                              _proposed("po_similar", "fuzzy", keys, similar(True)),
                              _proposed("supplier_similar", "fuzzy", keys, similar(False))])
    return tuple(exact + [_proposed("po_price", "amount_date", keys, price, batch=True),
                          _proposed("supplier_item", "amount_date", keys, supplier_item, batch=True),
                          _proposed("po_similar", "fuzzy", keys, similar(True)),
                          _proposed("supplier_similar", "fuzzy", keys, similar(False))])


# ── a document's lines against the PO lines ──────────────────────────────

def _e3_tolerance(tol: Tolerances) -> e3.Tolerance:
    # nothing is asked about a line for being large: what a line without a PO line is, is a reason code
    return e3.Tolerance(amount=tol.small, days=tol.days if tol.days <= 31 else 31, fuzzy=tol.fuzzy,
                        ask_above=Decimal("1000000000000.00"))


def candidates(lines: list, named: set, supplier: P.Party) -> list:
    """The PO lines a document's lines may belong to: the lines of the POs it names; and, for what
    it does not name, the lines of the same supplier's POs."""
    out = []
    for pl in lines:
        if pl.status == B.CANCELLED:
            continue
        if pl.po_key in named or P.compare(pl.supplier, supplier)[0] == P.SAME:
            out.append(pl)
    return out


def link_invoice(inv: B.Invoice, po_lines: list, tol: Tolerances, decisions: dict, keys_of: dict) -> dict:
    named = {ref_key(p) for p in inv.po_numbers if ref_key(p)}
    named |= {ref_key(l.po) for l in inv.lines if ref_key(l.po)}
    cands = candidates(po_lines, named, inv.supplier)
    keys = {pl.id: po_keys(pl) for pl in cands}
    rows_r = []
    for l in inv.lines:
        own = {ref_key(l.po)} if ref_key(l.po) else {ref_key(p) for p in inv.po_numbers if ref_key(p)}
        keys[l.id] = {"po": own, "line": B.ref_key_short(l.po_line) if l.po_line else "", "item": B.code_key(l.item_code),
                      "desc": desc_key(l.description), "text": l.description, "price": l.unit_price, "supplier": inv.supplier,
                      "only": False, "right": False}
        rows_r.append(Row(id=l.id, side="right", amount=_money(l.amount), date=inv.date, ref=inv.number, name=l.description,
                          source=l.source.out() if l.source else {}))
    res = e3.run([po_row(pl) for pl in cands], rows_r, passes=line_passes(keys, tol), tolerance=_e3_tolerance(tol),
                 decisions=decisions, keys=keys_of)
    res["named"] = sorted(named)
    res["known"] = sorted(named & {pl.po_key for pl in po_lines})
    return res


def link_receipt(lines: list, po_lines: list, tol: Tolerances, decisions: dict, keys_of: dict) -> dict:
    """One goods receipt (its lines) against the PO lines."""
    named = {l.po_key for l in lines if l.po_key}
    cands = [pl for pl in po_lines if pl.po_key in named and pl.status != B.CANCELLED]
    by_po: dict = {}
    for pl in cands:
        by_po.setdefault(pl.po_key, []).append(pl)
    keys = {pl.id: {**po_keys(pl), "only": len(by_po[pl.po_key]) == 1} for pl in cands}
    rows_r = []
    for l in lines:
        keys[l.id] = {"po": {l.po_key} if l.po_key else set(), "line": B.ref_key_short(l.po_line) if l.po_line else "",
                      "item": B.code_key(l.item_code), "desc": desc_key(l.description), "text": l.description, "price": None,
                      "supplier": P.Party(), "only": False, "right": True}
        rows_r.append(Row(id=l.id, side="right", amount=_money(l.qty), date=l.date, ref=l.gr, name=l.description,
                          source=l.source.out() if l.source else {}))
    left = [Row(id=pl.id, side="left", amount=_money(pl.qty), date=pl.date, ref=po_row(pl).ref, name=pl.description,
                source=pl.source.out() if pl.source else {}) for pl in cands]
    return e3.run(left, rows_r, passes=line_passes(keys, tol, receipts=True), tolerance=_e3_tolerance(tol), decisions=decisions,
                  keys=keys_of)


# ── an invoice against the invoices presented before it ──────────────────

def _number_resemblance(a: str, b: str) -> Decimal:
    if not a or not b:
        return Decimal("0.00")
    if a == b:
        return Decimal("1.00")
    return Decimal(str(SequenceMatcher(None, a, b, autojunk=False).ratio())).quantize(Decimal("0.01"))


def duplicate_passes(keys: dict, tol: Tolerances) -> tuple:
    def same_total(kl, kr) -> bool:
        return kl["total"] is not None and kr["total"] is not None and abs(kl["total"] - kr["total"]) <= tol.small

    def same_supplier(kl, kr) -> bool:
        return P.compare(kl["supplier"], kr["supplier"])[0] == P.SAME

    def number_key(k):
        s = k["supplier"].key
        return [(s, k["kind"], k["number"])] if s and k["number"] else []

    def number_guard(kl, kr) -> bool:
        # the same number with another total is not the same document by rule: a person decides
        return kl["total"] is None or kr["total"] is None or same_total(kl, kr)

    def amount_date(kl, kr):
        if kl["kind"] != kr["kind"] or not same_supplier(kl, kr) or not same_total(kl, kr) or kl["number"] == kr["number"]:
            return None
        if kl["date"] is None or kr["date"] is None or abs((kr["date"] - kl["date"]).days) > tol.dup_days:
            return None
        return (abs((kr["date"] - kl["date"]).days),), None

    def similar_number(kl, kr):
        if kl["kind"] != kr["kind"] or not same_supplier(kl, kr) or kl["number"] == kr["number"]:
            return None
        s = _number_resemblance(kl["number"], kr["number"])
        if s < Decimal("0.85") or not same_total(kl, kr):
            return None
        return (-s,), s

    def other_total(kl, kr):
        if kl["kind"] != kr["kind"] or not same_supplier(kl, kr) or not kl["number"] or kl["number"] != kr["number"]:
            return None
        return (Decimal(0),), Decimal("1.00")

    return (_exact("same_number", keys, number_key, guard=number_guard),
            _proposed("same_amount_date", "amount_date", keys, amount_date),
            _proposed("similar_number", "fuzzy", keys, similar_number),
            _proposed("same_number_other_total", "fuzzy", keys, other_total))


def known_row(entry: dict, n: int) -> tuple[Row, dict]:
    """An invoice presented before (a row of the install's history, or an earlier document of this
    round) as a row of the left table."""
    total = B.D(entry.get("total"))
    d = entry.get("document_date")
    supplier = P.Party(entry.get("seller_name") or "", str(entry.get("seller_tax_id") or ""))
    row = Row(id=entry["id"], side="left", amount=_money(total), date=_dt.date.fromisoformat(str(d)[:10]) if d else None,
              ref=str(entry.get("document_number") or ""), name=supplier.name or supplier.tax_id,
              source={"file": entry.get("name"), "sheet": None, "row": None, "cells": {}}, order=n)
    return row, {"supplier": supplier, "number": ref_key(entry.get("document_number")), "total": total, "date": row.date,
                 "kind": "note" if entry.get("kind") in B.NOTE_TYPES else "invoice"}


def find_duplicate(inv: B.Invoice, known: list, tol: Tolerances, decisions: dict, keys_of: dict) -> dict:
    keys, left = {}, []
    for n, e in enumerate(known):
        row, k = known_row(e, n)
        left.append(row)
        keys[row.id] = k
    rid = "n" + inv.key.replace("~", "-").replace("+", "-")
    keys[rid] = {"supplier": inv.supplier, "number": inv.number_key, "total": inv.total, "date": inv.date,
                 "kind": "note" if inv.is_note else "invoice"}
    right = [Row(id=rid, side="right", amount=_money(inv.total), date=inv.date, ref=inv.number,
                 name=inv.supplier.name or inv.supplier.tax_id, source=inv.source.out() if inv.source else {})]
    return e3.run(left, right, passes=duplicate_passes(keys, tol), tolerance=_e3_tolerance(tol), decisions=decisions, keys=keys_of)


def decisions_from(answers: dict | None) -> tuple[dict, dict]:
    d, k, _ = E3C.decisions_from(answers)
    return d, k
