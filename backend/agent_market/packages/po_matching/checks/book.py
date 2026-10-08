"""The three documents as lines: what was ordered, what arrived, what is billed.

    PoLine    a line of a purchase order — item, quantity, unit, unit price, currency, tax
              treatment, whether it is goods, a service or a blanket order, open or closed
    GrLine    a line of a goods receipt — what arrived, when, against which PO line; or a
              receipt a person confirmed in this system ("ของมาครบ")
    Invoice   what the supplier bills, with its lines — from a PDF or an e-Tax file (read by the
              E2 engine) or from a table; a credit note and a debit note are invoices of their
              own kind that name the invoice they correct

Matching is done line by line (links.py, scripts/match.py). A header is never matched to a
header: a total can agree while every line under it is wrong.

A line's id comes from where it was read (which file, which sheet, which row), never from its
position in a list: an answer about two lines lands on the same two lines in every later pass.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ....engine.e3.rows import name_key, ref_key
from . import inputs as I
from . import units as U
from ..words import W
from .parties import Party

ZERO = Decimal("0")
Q2 = Decimal("0.01")
GOODS, SERVICE, BLANKET = "goods", "service", "blanket"
OPEN, CLOSED, CANCELLED = "open", "closed", "cancelled"
INVOICE_TYPES = ("full_tax_invoice", "abbreviated_tax_invoice", "receipt", "invoice")
NOTE_TYPES = ("credit_note", "debit_note")
# (keys of the manifest's sentences, each said in the language of the run when it is read: engine/words.py)
KIND_TH = W.table({GOODS: "potype.goods", SERVICE: "potype.service", BLANKET: "potype.blanket"})
STATUS_TH = W.table({OPEN: "postatus.open", CLOSED: "postatus.closed", CANCELLED: "postatus.cancelled"})
_EXEMPT = ("ยกเว้น", "ยกเว้นภาษี", "ไม่มีvat", "ไม่มีภาษี", "ไม่คิดvat", "exempt", "novat", "nonvat", "zero", "zerorated", "none", "n/a")


def D(v) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


def q2(v: Decimal) -> Decimal:
    return v.quantize(Q2, rounding=ROUND_HALF_UP)


def date(v) -> _dt.date | None:
    return _dt.date.fromisoformat(v) if v else None


def qty_text(v: Decimal | None) -> str:
    """A quantity as a person reads it: 12 · 12.5 · 0.125."""
    if v is None:
        return ""
    s = f"{v:,.6f}".rstrip("0").rstrip(".")
    return s or "0"


def vat_rate_of(v) -> Decimal | None:
    """A PO's tax column → the rate it means: "7", "7%", "VAT 7", "V7" → 7; "ยกเว้น", "0%" → 0;
    a code nobody can read as a rate → None (the line's tax treatment is then not compared)."""
    k = U.key(v)
    if not k:
        return None
    if any(k == w or k.startswith(w) for w in _EXEMPT):
        return ZERO
    m = re.search(r"(\d+(?:\.\d+)?)%?$", k)
    if m:
        r = Decimal(m.group(1))
        if r < 1 and r > 0:            # 0.07 written as a fraction
            r = r * 100
        if ZERO <= r <= 30:
            return r.quantize(Q2)
    return None


@dataclass
class Source:
    file: str
    sheet: str | None = None
    row: int | None = None
    cells: dict = field(default_factory=dict)
    page: int | None = None

    def out(self) -> dict:
        d = {"file": self.file, "sheet": self.sheet, "row": self.row, "cells": self.cells}
        if self.page is not None:
            d["page"] = self.page
        return d


@dataclass
class PoLine:
    id: str
    po: str
    line_no: str
    item_code: str
    description: str
    qty: Decimal | None
    unit: str
    unit_price: Decimal | None
    amount: Decimal | None
    currency: str
    supplier: Party
    date: _dt.date | None
    kind: str = GOODS
    kind_from: str = ""
    status: str = OPEN
    status_raw: str = ""
    closed_on: _dt.date | None = None
    vat: str = ""
    vat_rate: Decimal | None = None
    valid_from: _dt.date | None = None
    valid_to: _dt.date | None = None
    limit: Decimal | None = None
    terms: str = ""
    delivery_date: _dt.date | None = None
    source: Source | None = None
    order: int = 0

    @property
    def po_key(self) -> str:
        return ref_key(self.po)

    @property
    def line_key(self) -> str:
        """What names this line within its PO, in every export of it: the line number, else the
        item code, else the description."""
        if self.line_no:
            return "l:" + ref_key_short(self.line_no)
        if self.item_code:
            return "i:" + code_key(self.item_code)
        return "d:" + name_key(self.description)

    @property
    def akey(self) -> str:
        return f"{self.po_key}#{self.line_key}"

    @property
    def label(self) -> str:
        n = W("po.line", line_no=self.line_no) if self.line_no else (W("po.code", item_code=self.item_code) if self.item_code else "")
        return " ".join(x for x in (f"PO {self.po}", n) if x)

    def out(self) -> dict:
        f2 = lambda v: None if v is None else f"{v:.2f}"   # noqa: E731
        return {"id": self.id, "po": self.po, "line_no": self.line_no, "item_code": self.item_code, "description": self.description,
                "quantity": None if self.qty is None else str(self.qty), "unit": self.unit,
                "unit_price": None if self.unit_price is None else str(self.unit_price), "amount": f2(self.amount),
                "currency": self.currency, "supplier": self.supplier.out(), "date": self.date.isoformat() if self.date else None,
                "kind": self.kind, "kind_label": KIND_TH[self.kind], "kind_from": self.kind_from, "status": self.status,
                "status_label": STATUS_TH[self.status], "status_raw": self.status_raw,
                "closed_on": self.closed_on.isoformat() if self.closed_on else None, "vat": self.vat,
                "vat_rate": None if self.vat_rate is None else f"{self.vat_rate:.2f}",
                "valid_from": self.valid_from.isoformat() if self.valid_from else None,
                "valid_to": self.valid_to.isoformat() if self.valid_to else None, "limit": f2(self.limit), "terms": self.terms,
                "delivery_date": self.delivery_date.isoformat() if self.delivery_date else None, "akey": self.akey,
                **(self.source.out() if self.source else {})}


@dataclass
class GrLine:
    id: str
    gr: str
    date: _dt.date | None
    po: str
    po_line: str
    item_code: str
    description: str
    qty: Decimal
    unit: str
    delivery_note: str = ""
    cancelled: bool = False
    confirmed_by: str = ""          # a receipt confirmed in this system names who confirmed it
    source: Source | None = None
    order: int = 0

    @property
    def po_key(self) -> str:
        return ref_key(self.po)

    @property
    def in_system(self) -> bool:
        return self.source is None

    def out(self) -> dict:
        return {"id": self.id, "gr": self.gr, "date": self.date.isoformat() if self.date else None, "po": self.po,
                "po_line": self.po_line, "item_code": self.item_code, "description": self.description, "quantity": str(self.qty),
                "unit": self.unit, "delivery_note": self.delivery_note, "cancelled": self.cancelled, "in_system": self.in_system,
                "confirmed_by": self.confirmed_by, **(self.source.out() if self.source else {"file": None, "sheet": None, "row": None,
                                                                                            "cells": {}})}


@dataclass
class InvLine:
    id: str
    n: int
    line_no: str
    po: str
    po_line: str
    item_code: str
    description: str
    qty: Decimal | None
    unit: str
    unit_price: Decimal | None
    amount: Decimal | None
    source: Source | None = None

    def out(self) -> dict:
        return {"id": self.id, "n": self.n, "line_no": self.line_no, "po": self.po, "po_line": self.po_line,
                "item_code": self.item_code, "description": self.description,
                "quantity": None if self.qty is None else str(self.qty), "unit": self.unit,
                "unit_price": None if self.unit_price is None else str(self.unit_price),
                "amount": None if self.amount is None else f"{self.amount:.2f}", **(self.source.out() if self.source else {})}


@dataclass
class Invoice:
    key: str                         # the document's own key in this round
    origin: str                      # document | table
    name: str                        # the file it came from
    sha256: str
    number: str
    date: _dt.date | None
    supplier: Party
    doc_type: str = ""               # full_tax_invoice | abbreviated_tax_invoice | receipt | invoice | credit_note | debit_note | ""
    currency: str = "THB"
    fx_rate: Decimal | None = None
    fx_from: str = ""
    subtotal: Decimal | None = None
    vat_amount: Decimal | None = None
    total: Decimal | None = None
    buyer_tax_id: str = ""
    po_numbers: list = field(default_factory=list)
    refs_read: str = ""              # how the PO reference was looked for: xml | text | table | none
    ref_invoice: str = ""
    lines: list = field(default_factory=list)
    header_only: bool = False
    tier: str = ""                   # a document's E2 tier: digital | scan | photo | etax
    missing: list = field(default_factory=list)      # elements a full tax invoice must show and this one does not
    failed_checks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    source: Source | None = None

    @property
    def number_key(self) -> str:
        return ref_key(self.number)

    @property
    def is_note(self) -> bool:
        return self.doc_type in NOTE_TYPES

    @property
    def sign(self) -> int:
        return -1 if self.doc_type == "credit_note" else 1

    @property
    def base(self) -> Decimal | None:
        """The taxable base: the amount before VAT the document states, else the sum of its lines."""
        if self.subtotal is not None:
            return self.subtotal
        amounts = [l.amount for l in self.lines if l.amount is not None]
        return q2(sum(amounts, ZERO)) if amounts else None

    @property
    def order(self) -> tuple:
        return (self.date.isoformat() if self.date else "9999-12-31", self.number_key, self.key)

    @property
    def label(self) -> str:
        who = f"“{self.supplier.name[:40]}”" if self.supplier.name else ""
        return " ".join(x for x in (W("inv.number", number=self.number) if self.number else "", W("inv.of", who=who) if who else "") if x) or self.name

    def out(self) -> dict:
        f2 = lambda v: None if v is None else f"{v:.2f}"   # noqa: E731
        return {"key": self.key, "origin": self.origin, "name": self.name, "sha256": self.sha256, "number": self.number,
                "date": self.date.isoformat() if self.date else None, "supplier": self.supplier.out(), "doc_type": self.doc_type,
                "currency": self.currency, "fx_rate": None if self.fx_rate is None else str(self.fx_rate), "fx_from": self.fx_from,
                "subtotal": f2(self.subtotal), "vat_amount": f2(self.vat_amount), "total": f2(self.total),
                "buyer_tax_id": self.buyer_tax_id, "po_numbers": self.po_numbers, "refs_read": self.refs_read,
                "ref_invoice": self.ref_invoice, "header_only": self.header_only, "tier": self.tier,
                "lines": [l.out() for l in self.lines], **(self.source.out() if self.source else {})}


def ref_key_short(v) -> str:
    """A line number as it is compared: "010", "10" and "10.0" name one line."""
    s = ref_key(v)
    if not s:
        s = re.sub(r"[^0-9A-Za-zก-๙]", "", str(v or "")).upper().lstrip("0")
    return s


def code_key(v) -> str:
    """An item code as it is compared: upper case, nothing but letters and digits."""
    return re.sub(r"[^0-9A-Za-zก-๙]", "", str(v or "")).upper()


def row_id(letter: str, sha: str, sheet_index: int, row: int) -> str:
    return f"{letter}{sha[:6]}.{sheet_index}.{row}"


# ── from the parsed uploads ──────────────────────────────────────────────

def _status(v) -> str:
    k = U.key(v)
    if not k:
        return OPEN
    if any(k == U.key(w) for w in U.CANCELLED_STATUS):
        return CANCELLED
    if any(k == U.key(w) or k.startswith(U.key(w)) for w in U.CLOSED_STATUS):
        return CLOSED
    return OPEN


def po_lines(upload, table: dict) -> list:
    out = []
    for n, r in enumerate(table["rows"]):
        qty, price, amount = D(r.get("quantity")), D(r.get("unit_price")), D(r.get("amount"))
        limit = D(r.get("limit"))
        if amount is None and qty is not None and price is not None:
            amount = q2(qty * price)
        if price is None and qty not in (None, ZERO) and amount is not None:
            price = (amount / qty).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        kind, how = GOODS, ""
        if I.blanket_type(r.get("type")) or (limit is not None and qty is None):
            kind, how = BLANKET, W("kind_from.limit")
        elif I.service_type(r.get("type")):
            kind, how = SERVICE, W("kind_from.type")
        elif U.is_service_unit(r.get("unit")):
            kind, how = SERVICE, W("kind_from.unit", unit=r.get("unit"))
        out.append(PoLine(
            id=row_id("p", upload.sha256, table["sheet_index"], r["row"]), po=r.get("po_number") or "",
            line_no=r.get("line_no") or "", item_code=r.get("item_code") or "", description=r.get("description") or "",
            qty=qty, unit=r.get("unit") or "", unit_price=price, amount=amount,
            currency=(r.get("currency") or "").upper(), supplier=Party(r.get("supplier_name") or "", r.get("supplier_tax_id") or "",
                                                                    r.get("supplier_branch") or ""),
            date=date(r.get("po_date")), kind=kind, kind_from=how, status=_status(r.get("status")),
            status_raw=r.get("status") or "", closed_on=date(r.get("closed_date")), vat=r.get("vat") or "",
            vat_rate=vat_rate_of(r.get("vat")), valid_from=date(r.get("valid_from")), valid_to=date(r.get("valid_to")),
            limit=limit if limit is not None else (amount if kind == BLANKET else None), terms=r.get("terms") or "",
            delivery_date=date(r.get("delivery_date")),
            source=Source(upload.name, table["sheet"], r["row"], r.get("cells") or {}), order=n))
    return out


def gr_lines(upload, table: dict) -> list:
    out = []
    for n, r in enumerate(table["rows"]):
        qty = D(r.get("quantity"))
        if qty is None:
            continue
        out.append(GrLine(
            id=row_id("g", upload.sha256, table["sheet_index"], r["row"]), gr=r.get("gr_number") or "", date=date(r.get("gr_date")),
            po=r.get("po_number") or "", po_line=r.get("po_line") or r.get("line_no") or "", item_code=r.get("item_code") or "",
            description=r.get("description") or "", qty=qty, unit=r.get("unit") or "",
            delivery_note=r.get("delivery_note") or "", cancelled=_status(r.get("status")) == CANCELLED,
            source=Source(upload.name, table["sheet"], r["row"], r.get("cells") or {}), order=n))
    return out


def table_invoices(upload, table: dict) -> list:
    """The invoices of a table: its rows grouped by the invoice's number and supplier, in the
    order the table holds them."""
    groups: dict = {}
    for r in table["rows"]:
        k = (ref_key(r.get("invoice_number")), r.get("supplier_tax_id") or name_key(r.get("supplier_name")))
        groups.setdefault(k, []).append(r)
    out = []
    for (num_key, _who), rows in groups.items():
        first = rows[0]
        lines, pos = [], []
        for n, r in enumerate([x for x in rows if not x.get("header_only")], start=1):
            qty, price, amount = D(r.get("quantity")), D(r.get("unit_price")), D(r.get("amount"))
            if amount is None and qty is not None and price is not None:
                amount = q2(qty * price)
            lines.append(InvLine(
                id=row_id("v", upload.sha256, table["sheet_index"], r["row"]), n=n, line_no=r.get("line_no") or "",
                po=r.get("po_number") or "", po_line=r.get("po_line") or "", item_code=r.get("item_code") or "",
                description=r.get("description") or "", qty=qty, unit=r.get("unit") or "", unit_price=price, amount=amount,
                source=Source(upload.name, table["sheet"], r["row"], r.get("cells") or {})))
        for r in rows:
            if r.get("po_number") and r["po_number"] not in pos:
                pos.append(r["po_number"])
        def one(f):
            return next((r[f] for r in rows if r.get(f) not in (None, "")), None)
        dtype = one("doc_type") or ""
        inv = Invoice(
            key=f"{upload.key}.{table['sheet_index']}.{num_key or first['row']}", origin="table", name=upload.name,
            sha256=upload.sha256, number=first.get("invoice_number") or "", date=date(one("invoice_date")),
            supplier=Party(one("supplier_name") or "", one("supplier_tax_id") or "", one("supplier_branch") or ""),
            doc_type=dtype, currency=(one("currency") or "").upper(), fx_rate=D(one("fx_rate")),
            fx_from=W("fx_from.column") if one("fx_rate") else "", subtotal=D(one("subtotal")),
            vat_amount=D(one("vat_amount")), total=D(one("total")), buyer_tax_id=one("buyer_tax_id") or "", po_numbers=pos,
            refs_read="table", ref_invoice=one("ref_invoice") or "", lines=lines, header_only=not lines,
            source=Source(upload.name, table["sheet"], first["row"], first.get("cells") or {}))
        out.append(inv)
    return out


def document_invoice(upload, out: dict, *, fx_rate: Decimal | None = None) -> Invoice:
    """An E2 extraction (as judged with the person's answers) as an invoice."""
    f = out["fields"]

    def v(n):
        return (f.get(n) or {}).get("normalized")

    def text(n):
        x = (f.get(n) or {}).get("value")
        if isinstance(x, dict):
            x = x.get("core")
        return " ".join(str(x or "").replace("\x00", "").split())

    def page(n):
        return ((f.get(n) or {}).get("source") or {}).get("page")
    lines = []
    for n, it in enumerate(out.get("line_items") or [], start=1):
        def lv(k):
            return (it.get(k) or {}).get("normalized")
        desc = (it.get("description") or {}).get("value") or ""
        lines.append(InvLine(id=f"v{upload.sha256[:6]}.l{n}", n=n, line_no=str(it.get("row") or n), po="", po_line="",
                             item_code="", description=" ".join(str(desc).split()), qty=D(lv("quantity")), unit="",
                             unit_price=D(lv("unit_price")), amount=D(lv("amount")),
                             source=Source(upload.name, None, it.get("row"), {}, page=it.get("page"))))
    refs = upload.data.get("refs") or {}
    seller = f.get("seller_name") or {}
    name = seller.get("value")
    if isinstance(seller.get("normalized"), dict) and not name:
        name = seller["normalized"].get("core")
    rate = fx_rate
    return Invoice(
        key=upload.key, origin="document", name=upload.name, sha256=upload.sha256, number=str(v("document_number") or ""),
        date=date(v("document_date")), supplier=Party(" ".join(str(name or "").replace("\x00", "").split()),
                                                       str(v("seller_tax_id") or ""), str(v("seller_branch") or "")),
        doc_type=str((out.get("document_type") or {}).get("normalized") or ""), currency=str(v("currency") or "THB").upper(),
        fx_rate=rate, fx_from=W("fx_from.typed") if rate is not None else "", subtotal=D(v("subtotal")),
        vat_amount=D(v("vat_amount")), total=D(v("total")), buyer_tax_id=str(v("buyer_tax_id") or ""),
        po_numbers=list(refs.get("po_numbers") or []), refs_read=refs.get("read") or "none",
        ref_invoice=(refs.get("ref_invoices") or [""])[0], lines=lines, header_only=not lines,
        tier=out["document"]["tier"], source=Source(upload.name, None, None, {}, page=page("document_number") or 1))
