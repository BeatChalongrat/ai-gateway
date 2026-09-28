"""A person's answers to the E2 cards → what judging a document again needs.

The runner hands the package {finding key: value}. An E2 key is
``e2_<code>:<doc_key>[:<subject>]`` (engine/e2/cards.py); the value is one of
the card's option values ("exclude", "absent", "accept_as_printed",
"include", "ok", "reupload", a document type, {"accept": value}) or the words
typed into the card's own box, {"typed": text}.

A typed value is parsed deterministically (engine/e2/typed.py) for the
field's kind; one that does not parse is asked once more
(``e2_typed_invalid``), and a second bad value leaves the document out. The
words are never handed to a model to interpret.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ....engine.e2 import thai_text as T
from ....engine.e2 import typed
from ....engine.e2.schema import FIELD_KINDS

DOC_TYPES = ("full_tax_invoice", "abbreviated_tax_invoice", "receipt", "invoice", "credit_note", "debit_note")

# why a document left the register, by the card that left it out
EXCLUDED_BY = {
    "e2_field_missing": "missing_required_field", "e2_low_confidence": "unconfirmed_value",
    "e2_source_conflict": "sources_disagree", "e2_check_failed": "check_failed", "e2_duplicate": "duplicate",
    "e2_copy": "copy_not_original", "e2_doc_type": "unknown_document_type", "e2_page_quality": "unreadable_page",
    "e2_typed_invalid": "typed_value_invalid",
}


# when several skipped cards leave one document out, the reason shown is the most fundamental one
PRIORITY = ("duplicate", "unknown_document_type", "missing_required_field", "check_failed", "sources_disagree",
            "unconfirmed_value", "unreadable_page", "typed_value_invalid", "copy_not_original", "excluded_by_person")


@dataclass
class Decisions:
    overrides: dict = field(default_factory=dict)        # field → value the person typed
    accepts: dict = field(default_factory=dict)          # field → a proposed value the person confirmed
    absent: set = field(default_factory=set)             # required fields the person says the document does not print
    accepted_checks: set = field(default_factory=set)    # failed checks the person accepted as printed
    doc_type: str | None = None
    excluded: dict | None = None                         # the main reason: {code, subject, key, reupload, codes}
    excluding: list = field(default_factory=list)        # AM5: every answer that leaves the document out [(key, value)]
    fx_rate: Decimal | None = None
    invalid: dict = field(default_factory=dict)          # field → (the card's key, the reason the typed value failed)
    notes: list = field(default_factory=list)            # exceptions recorded while the document stays in
    answered: int = 0

    def judge_kwargs(self, read_fields: dict) -> dict:
        """``read_fields``: the extraction's fields as first read. A confirmed value the document itself
        gave keeps its source (page, box) and is marked confirmed; one only a model proposed becomes the
        person's value, marked ``confirmed_by_person``."""
        overrides, methods, confirmed = dict(self.overrides), {}, set()
        for name, v in self.accepts.items():
            cur = (read_fields.get(name) or {}).get("normalized")
            src = (read_fields.get(name) or {}).get("source")
            if cur is not None and src is not None and _same(cur, v):
                confirmed.add(name)
            else:
                overrides[name], methods[name] = v, "confirmed_by_person"
        return {"overrides": overrides, "override_methods": methods, "confirmed": confirmed, "absent": set(self.absent),
                "accepted_checks": set(self.accepted_checks), "doc_type": self.doc_type}


def _same(stored, v) -> bool:
    if isinstance(v, dict):
        return isinstance(stored, dict) and stored.get("key") == v.get("key")
    return str(stored) == str(v)


def split_key(key: str) -> tuple[str, str, str]:
    """'e2_low_confidence:d1a2b3:total' → ('e2_low_confidence', 'd1a2b3', 'total')."""
    code, _, rest = key.partition(":")
    doc, _, subject = rest.partition(":")
    return code, doc, subject


def to_value(name: str, raw):
    """A typed or confirmed value for a field → (normalised value, None) or (None, reason)."""
    kind = FIELD_KINDS.get(name)
    text = raw if isinstance(raw, str) else str(raw)
    if kind in ("money", "tax_id", "date", "rate"):
        return typed.parse(kind, text)
    t = T.arabic(text).strip()
    if not t:
        return None, "ไม่ได้พิมพ์ค่า"
    if kind == "party":
        return (T.legal_form(t) or {"legal_form": "unknown", "core": t, "key": T.core_key(t)}), None
    if name.endswith("_branch"):
        b = T.parse_branch(t) or ("".join(ch for ch in t if ch.isdigit()).zfill(5) if any(ch.isdigit() for ch in t) else None)
        return (b, None) if b else (None, "พิมพ์ 'สำนักงานใหญ่' หรือเลขสาขา 5 หลัก")
    if kind == "bank_account":
        d = "".join(ch for ch in t if ch.isdigit())
        return (d, None) if 10 <= len(d) <= 15 else (None, "เลขบัญชีต้องมี 10–15 หลัก")
    return t[:200], None


def for_document(doc_key: str, answers: dict) -> Decisions:
    d = Decisions()
    mine = [(k, v) for k, v in (answers or {}).items() if k.startswith("e2_") and split_key(k)[1] == doc_key]
    # a re-asked typed value is read after the answer it corrects
    mine.sort(key=lambda kv: (split_key(kv[0])[0] == "e2_typed_invalid", kv[0]))
    for key, val in mine:
        d.answered += 1
        _apply(d, key, val)
    return d


def _exclude(d: Decisions, code: str, subject: str, key: str, reupload: bool) -> None:
    codes = sorted({*(d.excluded or {}).get("codes", []), code}, key=lambda c: PRIORITY.index(c) if c in PRIORITY else 99)
    cur = d.excluded
    if cur is None or (PRIORITY.index(code) if code in PRIORITY else 99) < (PRIORITY.index(cur["code"]) if cur["code"] in PRIORITY else 99):
        cur = {"code": code, "subject": subject, "key": key, "reupload": reupload}
    d.excluded = {**cur, "codes": codes, "reupload": cur["reupload"] or reupload}


def _note(d: Decisions, code: str, subject: str, detail: str = "") -> None:
    d.notes.append({"code": code, "subject": subject, "detail": detail, "decided_by": "person"})


def _apply(d: Decisions, key: str, val) -> None:
    code, _, subject = split_key(key)
    if val in ("exclude", "reupload"):
        d.excluding.append((key, val))
        _exclude(d, EXCLUDED_BY.get(code, "excluded_by_person"), subject, key, val == "reupload")
        return
    if code in ("e2_low_confidence", "e2_source_conflict", "e2_field_missing", "e2_typed_invalid") and subject != "fx_rate":
        name = subject
        if name not in FIELD_KINDS:
            return
        if val == "absent":
            d.absent.add(name)
            d.invalid.pop(name, None)
            _note(d, "field_absent", name, "ผู้ใช้ยืนยันว่าเอกสารไม่ได้พิมพ์ค่านี้")
            return
        raw = None
        if isinstance(val, dict) and "typed" in val:
            raw = val["typed"]
        elif isinstance(val, dict) and "accept" in val:
            raw = val["accept"]
        if raw is None:
            return
        v, why = to_value(name, raw)
        if why:
            if code == "e2_typed_invalid":
                _exclude(d, "typed_value_invalid", name, key, False)
            else:
                d.invalid[name] = (key, why)
            return
        d.invalid.pop(name, None)
        if isinstance(val, dict) and "typed" in val:
            d.overrides[name] = v
            d.accepts.pop(name, None)
        else:
            d.accepts[name] = v
    elif code == "e2_check_failed" or (code == "e2_typed_invalid" and subject == "fx_rate"):
        if subject in ("currency", "fx_rate"):
            raw = val.get("typed") if isinstance(val, dict) else None
            rate, why = typed.parse("rate", raw) if raw is not None else (None, "ยังไม่ได้พิมพ์อัตราแลกเปลี่ยน")
            if why:
                if code == "e2_typed_invalid":
                    _exclude(d, "typed_value_invalid", "fx_rate", key, False)
                else:
                    d.invalid["fx_rate"] = (key, why)
                return
            d.invalid.pop("fx_rate", None)
            d.fx_rate = rate
            d.accepted_checks.add("currency")
            _note(d, "foreign_currency", "currency", f"อัตราแลกเปลี่ยนที่ผู้ใช้ระบุ {rate} บาทต่อหน่วย")
        elif val == "accept_as_printed":
            d.accepted_checks.add(subject)
            _note(d, "accepted_as_printed", subject, "ผู้ใช้ให้บันทึกตามที่พิมพ์ แม้ตรวจไม่ผ่าน")
    elif code == "e2_duplicate" and val == "include":
        d.accepted_checks.add("duplicate")
        _note(d, "not_duplicate_by_person", "duplicate", "ผู้ใช้ยืนยันว่าเป็นเอกสารคนละฉบับ")
    elif code == "e2_copy" and val == "ok":
        d.accepted_checks.add("original_copy")
        _note(d, "copy_not_original", "copy", "เอกสารเป็นสำเนา ขอคืนภาษีซื้อต้องใช้ต้นฉบับ")
    elif code == "e2_doc_type" and val in DOC_TYPES:
        d.doc_type = val


# AM5 — bringing a document back. An answer that left it out is replaced by
# the card's other answer when that one needs nothing more from the person
# (recorded as printed with an exception; "not a duplicate"; "a copy, record
# it"), and is taken back — so its question is asked again — when the
# document cannot be recorded without a value or a choice of theirs.
BRING_BACK = {"e2_check_failed": ("accept_as_printed", "บันทึกตามเอกสาร มีข้อยกเว้น"),
              "e2_duplicate": ("include", "ไม่ซ้ำ นับรวม"), "e2_copy": ("ok", "บันทึก พร้อมหมายเหตุ")}


def restore_of(dec: Decisions) -> dict:
    """{"set": {key: {value, label, code}}, "drop": [keys]} for a document a card left out."""
    out = {"set": {}, "drop": []}
    for key, _ in sorted(dec.excluding):
        code, _, subject = split_key(key)
        alt = BRING_BACK.get(code)
        if alt and not (code == "e2_check_failed" and subject in ("currency", "fx_rate", "buyer_match")):
            out["set"][key] = {"value": alt[0], "label": alt[1], "code": code}
        else:
            out["drop"].append(key)
    # a typed value that was asked again belongs to the question it corrects
    for key in list(out["drop"]):
        code, doc, subject = split_key(key)
        if code == "e2_typed_invalid":
            out["drop"] += [k for k in (f"e2_field_missing:{doc}:{subject}", f"e2_low_confidence:{doc}:{subject}",
                                        f"e2_source_conflict:{doc}:{subject}") if k not in out["drop"]]
    return out
