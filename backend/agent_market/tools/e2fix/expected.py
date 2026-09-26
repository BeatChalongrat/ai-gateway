"""Golden output for each fixture, from its spec alone (never from the engine).

The contract the E2 engine is scored against (engine/e2 eval):

* ``truth``: the value of every field as printed, normalised (tax ID 13 digits,
  dates ISO CE, money as 2-decimal strings, rates as percent strings).
* ``checks``: the deterministic checks and the status each must reach
  (pass | fail | skip). "skip" = the check does not apply to this document.
* ``questions.must``: question keys that must be asked (``<code>:<subject>``
  or the bare code); ``questions.max``: how many questions are allowed at
  most (0 for a clean document); ``questions.may``: the engine may ask
  about any field it could not read with enough confidence (scans, photos).
* ``rules``: ``no_silent_errors`` — every field in scope is either the true
  value or the subject of a question; ``zero_questions`` — nothing is asked.
* ``scope``: ``full`` or ``triage`` (photos: vendor / date / total / VAT /
  tax ID only).
* ``history``: the documents already seen when this one is judged (the
  duplicate check looks there only). Empty unless named: a scan of d01 run
  alone is not a duplicate; f03 is judged with d01 in its history.
* ``questions.allowed_extra``: codes an engine may raise beyond ``must``
  without being wrong (f07: P.P.36 self-assessment, section 70 WHT).
* Check semantics: ``wht`` = the printed WHT figures are consistent (base ×
  rate = amount, total − WHT = net); ``buyer_match`` applies to full tax
  invoices only (the input-VAT document); ``original_copy`` fails for a
  document marked สำเนา.
"""

from __future__ import annotations

from decimal import Decimal

from .html import seller_tax_printed
from .specs import SUPPLIERS

TRIAGE_FIELDS = ["seller_name", "seller_tax_id", "document_date", "total", "vat_amount"]
MONEY_FIELDS = ["sum_lines", "discount", "subtotal", "vat_amount", "total", "wht_amount", "net_payable"]


def _s(v):
    return None if v is None else str(v)


def _printed_seller_name(spec, seller) -> str:
    # an e-Tax document's source is its XML, which carries the full legal name
    if spec["layout"] in ("classic", "english", "xml") or spec.get("etax"):
        return seller["name"]
    return seller["print_name"]


def build(spec: dict, files: dict) -> dict:
    seller = SUPPLIERS[spec["seller"]]
    buyer = spec.get("buyer")
    # the receipt layout prints the payer's name and address, not their tax ID
    buyer_tid_printed = bool(buyer) and spec["layout"] != "receipt"
    t = spec["totals"]
    tid = seller_tax_printed(spec, seller)
    from .thai import tax_id_valid

    truth = {
        "document_type": spec["doc_type"],
        "seller_name": {"value": _printed_seller_name(spec, seller),
                        "normalized": {"legal_form": seller["legal_form"], "core": seller["core"]}},
        "seller_tax_id": None if tid is None else {"value": tid, "valid": tax_id_valid(tid)},
        "seller_branch": seller["branch"],
        "buyer_name": None if not buyer else {"value": buyer["name"],
                                              "normalized": {"legal_form": buyer["legal_form"], "core": buyer["core"]}},
        "buyer_tax_id": None if not buyer_tid_printed else {"value": buyer["tax_id"], "valid": tax_id_valid(buyer["tax_id"])},
        "buyer_branch": None if not buyer_tid_printed else buyer["branch"],
        "document_number": spec.get("number_value", spec["number"]),
        "document_date": spec["date"].isoformat(),
        "due_date": spec["due"].isoformat() if spec.get("due") and spec["layout"] not in ("pos", "receipt") else None,
        "currency": spec.get("currency", "THB"),
        "vat_mode": t["vat_mode"],
        "sum_lines": _s(t["sum_lines"]), "discount": _s(t["discount"]), "subtotal": _s(t["subtotal"]),
        "vat_rate": None if t["vat_rate"] is None else f"{t['vat_rate']:.0f}",
        "vat_amount": _s(t["vat_amount"]), "total": _s(t["total"]),
        "wht_rate": None if t.get("wht_rate") is None else f"{t['wht_rate']:.0f}",
        "wht_amount": _s(t.get("wht_amount")), "net_payable": _s(t.get("net_payable")),
        "line_items": [] if spec.get("no_line_table") else [
            {"description": l["description"],
             "quantity": str(int(l["quantity"])) if l["quantity"] == l["quantity"].to_integral_value() else str(l["quantity"]),
             "unit_price": _s(l["unit_price"]), "amount": _s(l["amount"])} for l in spec["lines"]],
    }
    # the deterministic checks
    doc_type = spec["doc_type"]
    full = doc_type == "full_tax_invoice"
    has_vat = t["vat_mode"] != "none"
    line_ok = all(l["amount"] == l["true_product"] for l in spec["lines"])
    checks = {
        "document_type": "pass",
        "seller_tax_id_format": "skip" if tid is None else ("pass" if tax_id_valid(tid) else "fail"),
        "buyer_tax_id_format": "skip" if not buyer_tid_printed else "pass",
        "document_date": "pass",
        "line_arithmetic": "skip" if spec.get("no_line_table") else ("pass" if line_ok else "fail"),
        "subtotal_sum": "skip" if spec.get("no_line_table") else "pass",
        "vat_7_percent": "skip" if not has_vat else (
            "pass" if abs(t["vat_amount"] - t["vat_true"]) <= Decimal("0.01") else "fail"),
        "grand_total": "skip" if not has_vat else "pass",
        "wht": "pass" if t.get("wht_amount") is not None else "skip",
        "currency": "pass" if spec.get("currency", "THB") == "THB" else "fail",
        "required_fields": "fail" if (full and tid is None) else "pass",
        "buyer_match": ("skip" if not full or not buyer else
                        ("pass" if buyer["tax_id"] == files["org"]["tax_id"] else "fail")),
        "duplicate": "pass",
    }
    # duplicate is judged against ``history`` only (the documents already seen: the same
    # batch before this one, or the install's index) — every fixture has an empty history
    # unless it names one, so a scan of d01 run on its own is not a duplicate
    if spec.get("duplicate_of"):
        checks["duplicate"] = "fail"
    # a document marked สำเนา (copy) cannot support an input-VAT claim on its own
    checks["original_copy"] = "fail" if spec.get("copy_mark") else "pass"
    must = list(spec["expect_questions"]) if isinstance(spec["expect_questions"], list) else []
    triage = bool(spec.get("triage"))
    may = spec["expect_questions"] == "may"
    rules = ["no_silent_errors"]
    if not must and not may:
        rules.append("zero_questions")
    cls = spec["class"]
    method = {"digital": "pdf_text", "etax_pdf": "etax_xml", "etax_xml": "etax_xml", "scan300": "ocr",
              "scan200": "ocr", "photo": "ocr"}[cls]
    return {
        "schema": "e2.expected.v1",
        "id": spec["id"],
        "files": files["files"],
        "class": cls,
        "purpose": spec["purpose"],
        "source_fixture": spec.get("source_fixture"),
        "duplicate_of": spec.get("duplicate_of"),
        "scope": "triage" if triage else "full",
        "scope_fields": TRIAGE_FIELDS if triage else None,
        "truth": truth,
        "checks": checks,
        "history": spec.get("history", []),
        "questions": {"must": must, "may": may, "allowed_extra": spec.get("allowed_extra", []),
                      "max": None if may else len(must) + len(spec.get("allowed_extra", []))},
        "rules": rules,
        "provenance": {"method": method, "pages": spec.get("pages", 1),
                       "totals_page": spec.get("pages", 1)},
        "confidence": {"money_min": 0.95} if cls in ("digital", "etax_pdf", "etax_xml") and not must else None,
        "ui": {"best_effort_notice": True} if triage else None,
    }
