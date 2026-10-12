"""Receipts as the E2 engine read them, judged in the run's context — only what the audit uses.

The reading is Agent 1's, shared rather than copied: the same E2 extraction (engine/e2), the same
cards (engine/e2/cards.py), the same answer rules (invoice_processing/checks/decisions.py: a typed
value is parsed deterministically, a skipped card leaves the reading out, never accepts a number) and
the same vision rule (a model's reading of a scan or photo is a candidate only).

What differs is which E2 questions are *asked*. The audit uses a receipt's total (compared with the
claimed amount) and — only when a claim asks for input VAT back — its document type. So a card is
raised only for a question about the total (low confidence, a source conflict, not found, a failed
check the total takes part in, the page it is on unreadable) or, for a VAT claim, the document type.
Everything else E2 would ask an invoice clerk (the document number of a shop's slip, a missing
branch) is not the audit's business and is not asked; it is still shown in the receipts sheet as read.

A receipt ends a pass as ``read``, ``pending`` (a card is open) or ``unconfirmed`` (a card left it out,
or compute found a question still open) — the rules then say the total could not be confirmed and a
person decides; the claim is never passed on an unconfirmed number.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal

from ....engine.e2 import cards as E2C
from ....engine.e2.extract import rejudge
from ...invoice_processing.checks import decisions as D
from ...invoice_processing.checks import review as IR
from ...invoice_processing.scripts.register import decided_by
from ..words import W
from .inputs import Upload
from .rules import ReceiptView

SCAN_TIERS = IR.SCAN_TIERS
AUDIT_FIELDS = ("total",)


@dataclass
class ReceiptReview:
    up: Upload
    out: dict
    dec: D.Decisions
    status: str                          # read | pending | unconfirmed
    findings: list = field(default_factory=list)
    exclusion: dict | None = None
    vat_claimed: bool = False
    names: list = field(default_factory=list)


def _total_page(out: dict):
    return (((out.get("fields") or {}).get("total") or {}).get("source") or {}).get("page")


def relevant(f, out: dict, vat_claimed: bool) -> bool:
    """Is this E2 card about something the audit uses?"""
    facts = f.facts or {}
    if f.code in ("e2_low_confidence", "e2_source_conflict", "e2_field_missing", "e2_typed_invalid"):
        return facts.get("field") in AUDIT_FIELDS
    if f.code == "e2_check_failed":
        c = next((c for c in out.get("deterministic_checks") or [] if c["code"] == facts.get("check")), None)
        return c is not None and "total" in (c.get("fields") or [])
    if f.code == "e2_page_quality":
        page = _total_page(out)
        return page is None or f.subject.endswith(f":p{page}")
    if f.code == "e2_doc_type":
        return vat_claimed
    return False


def review(receipts: list[Upload], *, vat_claimed: dict, aliases: dict, answers: dict | None, defaults: dict | None,
           today: _dt.date | None, proposals: dict | None) -> dict[str, ReceiptReview]:
    """``vat_claimed``: receipt key → a claim asks for input VAT on it; ``aliases``: receipt key → the
    other names the same bytes were uploaded under."""
    org = IR.org_from(defaults or {})
    out: dict[str, ReceiptReview] = {}
    for up in receipts:
        dec = D.for_document(up.key, answers or {})
        judged = rejudge(up.extraction, org=org, history=None, today=today, proposals=IR.proposals_for(up, proposals),
                         **dec.judge_kwargs(up.extraction.get("fields") or {}))
        vat = bool(vat_claimed.get(up.key))
        names = [up.name] + [n for n in aliases.get(up.key, []) if n != up.name]
        if dec.excluded:
            out[up.key] = ReceiptReview(up, judged, dec, "unconfirmed", vat_claimed=vat, names=names,
                                        exclusion={"code": dec.excluded["code"], "key": dec.excluded.get("key"),
                                                   "reupload": dec.excluded.get("reupload", False)})
            continue
        fs = [f for f in E2C.findings_for(up.key, judged, E2C.reader_of(proposals, up.key)) + IR._invalid_cards(up, judged, dec)
              if relevant(f, judged, vat)]
        out[up.key] = ReceiptReview(up, judged, dec, "pending" if fs else "read", findings=fs, vat_claimed=vat, names=names)
    return out


# why a receipt's amount is not confirmed: keys of the manifest's sentences, each said when it is read
REASON = W.table({k: f"receipt.{k}" for k in (
    "unconfirmed_value", "sources_disagree", "missing_required_field", "check_failed", "unreadable_page",
    "typed_value_invalid", "unknown_document_type", "excluded_by_person", "unresolved_question")})


def view(r: ReceiptReview, *, final: bool) -> ReceiptView:
    """The rules' view of one reviewed receipt. ``final`` (compute): an open question is unconfirmed."""
    f = r.out.get("fields") or {}

    def v(n):
        return (f.get(n) or {}).get("normalized")
    status = r.status if not (final and r.status == "pending") else "unconfirmed"
    why = ""
    if r.status == "unconfirmed":
        why = REASON.get((r.exclusion or {}).get("code"), W("receipt.excluded_by_person"))
    elif status == "unconfirmed":
        why = REASON["unresolved_question"]
    tf = f.get("total") or {}
    src = tf.get("source") or {}
    bbox = src.get("bbox")
    checks = {c["code"]: c for c in r.out.get("deterministic_checks") or []}
    req = checks.get("required_fields") or {}
    seller = f.get("seller_name") or {}
    return ReceiptView(
        key=r.up.key, names=r.names, sha256=r.up.sha256, status=status,
        total=Decimal(str(v("total"))) if v("total") is not None else None, currency=v("currency") or "THB",
        document_type=r.out["document_type"]["normalized"], seller_name=seller.get("value") or ((seller.get("normalized") or {}).get("core") if isinstance(seller.get("normalized"), dict) else None),
        seller_tax_id=v("seller_tax_id"), document_number=v("document_number"),
        document_date=str(v("document_date")) if v("document_date") else None,
        buyer_match=(checks.get("buyer_match") or {}).get("status", "skip"),
        missing_fields=list(req.get("actual") or []) if req.get("status") == "fail" else [],
        total_source={"page": src.get("page"), "bbox": ",".join(f"{x:.1f}" for x in bbox) if bbox else None,
                      "raw_text": src.get("raw_text"), "method": src.get("method") or next(
                          (c.get("method") for c in tf.get("candidates") or []), None),
                      "confidence": tf.get("confidence"), "decided_by": decided_by(tf) if tf.get("normalized") is not None else None},
        tier=r.out["document"]["tier"], unconfirmed_reason=why)


def index_entry(r: ReceiptReview, rv: ReceiptView) -> dict:
    """What the install's duplicate index keeps of a claimed receipt (the runner writes it after a
    successful run): its bytes' hash always, the E2 values only when the total was confirmed."""
    ok = rv.status == "read"
    return {"doc_key": r.up.key, "name": r.up.name, "sha256": r.up.sha256,
            "seller_tax_id": rv.seller_tax_id if ok else None, "document_number": rv.document_number if ok else None,
            "document_date": rv.document_date if ok else None, "total": f"{rv.total:.2f}" if ok and rv.total is not None else None}
