"""The E2 extraction contract (schema ``e2.extraction.v1``).

Every later agent (Invoice Processing, Expense Audit) reads this shape, so
it is fixed here and validated on the way out:

    {"schema": "e2.extraction.v1",
     "document": {"name", "sha256", "pages", "kind": pdf|image|xml, "tier": digital|scan|photo|etax,
                  "triage_only": bool, "page_quality": [{page, method, ocr_confidence, dpi}]},
     "document_type": Field,
     "method": pdf_text | ocr | etax_xml | hybrid,
     "fields": {name: Field},
     "line_items": [{"row": int, "page": int, "description": Field, "quantity": Field,
                     "unit_price": Field, "amount": Field}],
     "deterministic_checks": [{"code", "status": pass|fail|skip, "expected", "actual", "fields", "message"}],
     "questions": [{"key", "code", "field", "subject", "reason", "blocking", "proposed", "evidence"}]}

    Field = {"value": as printed | None, "normalized": canonical | None, "confidence": 0..1,
             "source": {"page", "bbox", "span", "method", "raw_text"} | None,
             "candidates": [{"value", "normalized", "method", "confidence"}],   # other readings
             "checks": [codes of the deterministic checks this field passed]}

Rule (AM2): a model may *propose* a field (a candidate); it never finalises a
financial number. Money, VAT, WHT, tax IDs and dates are final only when a
deterministic source read them and the checks they take part in pass —
otherwise a question is raised instead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = "e2.extraction.v1"

# fields and their kinds
MONEY_FIELDS = ("sum_lines", "discount", "subtotal", "vat_amount", "total", "wht_amount", "net_payable")
FIELD_KINDS = {
    "seller_name": "party", "seller_tax_id": "tax_id", "seller_branch": "code", "seller_address": "text",
    "buyer_name": "party", "buyer_tax_id": "tax_id", "buyer_branch": "code", "buyer_address": "text",
    "document_number": "code", "document_date": "date", "due_date": "date", "currency": "code",
    "vat_rate": "rate", "wht_rate": "rate", "amount_in_words": "text", "bank_account": "bank_account",
    **{f: "money" for f in MONEY_FIELDS},
}
# the card's thresholds: money, VAT, WHT, tax ID and bank account need 0.95; everything else 0.85
STRICT_KINDS = ("money", "tax_id", "bank_account")
STRICT_THRESHOLD = 0.95
ORDINARY_THRESHOLD = 0.85
PAGE_OCR_THRESHOLD = 70.0

# what each document type must carry (a missing one is a question, not a failure of the type)
REQUIRED = {
    # a tax invoice must show the seller's branch (สำนักงานใหญ่ / สาขาที่ …) since the 2012 rules
    "full_tax_invoice": ("seller_name", "seller_tax_id", "seller_branch", "document_number", "document_date", "buyer_name", "total"),
    "abbreviated_tax_invoice": ("seller_name", "seller_tax_id", "seller_branch", "document_number", "document_date", "total"),
    "receipt": ("seller_name", "document_number", "document_date", "total"),
    "invoice": ("seller_name", "document_number", "document_date", "total"),
    "credit_note": ("seller_name", "seller_tax_id", "document_number", "document_date", "total"),
    "debit_note": ("seller_name", "seller_tax_id", "document_number", "document_date", "total"),
}
# photos are triage only: these five and nothing else (AM2 scope line)
TRIAGE_FIELDS = ("seller_name", "seller_tax_id", "document_date", "total", "vat_amount")


@dataclass
class Source:
    page: int | None
    bbox: list[float] | None
    span: list[int] | None
    method: str
    raw_text: str
    line: int | None = None      # index into the document's line list (internal, dropped on output)


@dataclass
class Field:
    value: Any = None
    normalized: Any = None
    confidence: float = 0.0
    source: Source | None = None
    candidates: list[dict] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return self.normalized is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["source"]:
            d["source"].pop("line", None)
        d["confidence"] = round(float(d["confidence"]), 3)
        d["normalized"] = _json(d["normalized"])
        d["value"] = _json(d["value"])
        for c in d["candidates"]:
            c["normalized"] = _json(c.get("normalized"))
            c["value"] = _json(c.get("value"))
        return d


def _json(v):
    from decimal import Decimal
    import datetime as _dt

    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    return v


@dataclass
class Check:
    code: str
    status: str            # pass | fail | skip
    expected: Any = None
    actual: Any = None
    fields: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "status": self.status, "expected": _json(self.expected), "actual": _json(self.actual),
                "fields": self.fields, "message": self.message}


@dataclass
class Question:
    code: str              # field_missing | low_confidence | check_failed | source_conflict | document_type_unknown | duplicate_candidate | copy_not_original | page_quality
    subject: str = ""      # the field or check it is about
    fieldname: str | None = None   # the field it is about (output key "field")
    reason: str = ""
    blocking: bool = True
    proposed: Any = None   # the value an automatic reading proposes (never final)
    evidence: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.code}:{self.subject}" if self.subject else self.code

    def to_dict(self) -> dict:
        return {"key": self.key, "code": self.code, "subject": self.subject, "field": self.fieldname, "reason": self.reason,
                "blocking": self.blocking, "proposed": _json(self.proposed), "evidence": _json(self.evidence)}


def validate(out: dict) -> None:
    """Refuse a malformed extraction before any agent reads it."""
    assert out.get("schema") == SCHEMA, "schema"
    for k in ("document", "document_type", "method", "fields", "line_items", "deterministic_checks", "questions"):
        assert k in out, f"missing {k}"
    for name, f in out["fields"].items():
        assert name in FIELD_KINDS, f"unknown field {name}"
        assert 0.0 <= f["confidence"] <= 1.0, f"{name}: confidence {f['confidence']}"
        if f["normalized"] is not None:
            assert f["source"] is not None or f["candidates"], f"{name}: a value without a source"
    for c in out["deterministic_checks"]:
        assert c["status"] in ("pass", "fail", "skip"), c
    keys = [q["key"] for q in out["questions"]]
    assert len(keys) == len(set(keys)), f"duplicate question keys {keys}"
