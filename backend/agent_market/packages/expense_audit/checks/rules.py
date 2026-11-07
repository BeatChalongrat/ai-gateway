"""The deterministic facts of every claim — and the line between a rule and judgement.

RULES MAKE FACTS; PEOPLE MAKE DECISIONS. Every rule below is plain Python over the claims sheet,
the policy table (checks/policy.py) and the receipts as the E2 engine read them. Each produces a
*fact* per claim — ``pass``, ``fail`` or ``skip`` (the rule does not apply, or the table sets no
value for it) — with its evidence: the table entry it used (``policy_ref``, ``limit``,
``limit_source`` = "policy_table", never a KB document), the numbers it compared, the sheet row and
the receipt.

What a failed fact leads to is fixed per rule (``disposition``):
  * ``exception`` — a rule that can be checked exactly (over a limit, no receipt, a duplicate, a
    prohibited merchant, VAT without a full tax invoice, WHT not deducted, a split purchase, the
    receipt's total not the claimed amount…). It becomes a *run exception*: a person decides, with
    the evidence and the choices in front of them;
  * ``question`` — a trigger for judgement: the claim is on a weekend or holiday (policy
    ``weekend_holiday: "flag"``), or its description says nothing more than its category. A rule can
    see the trigger; only a person can judge the business reason. It becomes a *clarification card*
    (checks/cards.py); a skipped card becomes a run exception for a person — never a pass;
  * a claim that already fails a rule is going to a person anyway: its judgement triggers are not
    asked as separate cards, they ride with its exception (``also_flagged``).

There is no "reject". A claim's outcome is ``pass`` (every fact passed or did not apply),
``exception`` (a rule failed) or ``question`` (only judgement is needed). Nothing here refuses,
removes or shortens a claim: amounts are compared, never changed. Decimal only.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ....engine import words
from ....engine.e2 import thai_text as T
from ..words import W
from .policy import Calendar, Category, Policy

OUTCOMES = ("pass", "exception", "question")
FACTS = ("claim_data", "category", "within_claim_limit", "within_period_limit", "receipt", "receipt_amount",
         "duplicate", "split_purchase", "prohibited_merchant", "vat_document", "wht", "weekend_holiday", "purpose")
# a failed fact → a run exception for a person (a rule) …
EXCEPTION_CODES = ("claim_row_unreadable", "unknown_category", "over_claim_limit", "over_period_limit", "missing_receipt",
                   "receipt_amount_mismatch", "receipt_unconfirmed", "duplicate_claim", "split_purchase",
                   "prohibited_merchant", "vat_claim_without_full_tax_invoice", "wht_missing", "wht_mismatch",
                   "missing_purpose")
# … or a clarification card (judgement). weekend_or_holiday is an exception instead when the policy says so.
JUDGEMENT_CODES = ("weekend_or_holiday", "vague_purpose")
# (a table keeps the keys of its words — agent.json — and says each in the language of the run when it is read)
WEEKDAY_TH = W.table({i: f"weekday.{i}" for i in range(7)})
Q2 = Decimal("0.01")
# a description that says nothing about the business reason (compared without spaces, punctuation or ค่า)
GENERIC = ("ใช้จ่าย", "ใช้จ่ายทั่วไป", "ใช้จ่ายอื่นๆ", "ใช้จ่ายในงาน", "อื่นๆ", "อื่น", "ทั่วไป", "เบ็ดเตล็ด", "ตามใบเสร็จ",
           "ตามบิล", "ใช้ในงาน", "งาน", "ธุระ", "misc", "other", "others", "expense", "expenses", "general", "na", "n/a")
_PUNCT = re.compile(r"[\s\.,\-/\\()\[\]{}\"'“”‘’!?:;_*#]+")


def date_th(d: _dt.date | None) -> str:
    """A day in a sentence, in the language of the run: 2026-10-10 → '10 ต.ค. 2569', '10 Oct 2026' (the name is
    from before a run could be in English)."""
    return words.date(d) or W("date.none")


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Q2, rounding=ROUND_HALF_UP)


def _m(v) -> str:
    """An amount as a sentence prints it: 1,234.50."""
    return f"{v:,.2f}"


def _s(v) -> str | None:
    return None if v is None else f"{v:.2f}" if isinstance(v, Decimal) else str(v)


def _norm(s) -> str:
    """Merchant and name comparison: Arabic digits, no spaces, lower case."""
    return "".join(T.arabic(str(s or "")).split()).lower()


def _clean(s) -> str:
    return _PUNCT.sub("", T.arabic(str(s or ""))).lower()


def _strip_kha(s: str) -> str:
    return s[3:] if s.startswith("ค่า") and len(s) > 3 else s


def vague_purpose(description: str | None, category_label: str | None) -> str | None:
    """The trigger for the purpose card, or None. ``empty``: nothing written; ``restates_category``:
    the description is the category's own name (ค่าอาหาร for meals, with or without ค่า, or one part of
    a label written with "/"); ``generic``: a word that says nothing (ค่าใช้จ่าย, อื่นๆ, ตามใบเสร็จ).
    Whether a purpose is *good enough* is not decided here: that is the person's judgement."""
    d = _strip_kha(_clean(description))
    if not d:
        return "empty"
    label = str(category_label or "")
    forms = {_strip_kha(_clean(p)) for p in [label] + label.split("/") if _clean(p)}
    if d in forms:
        return "restates_category"
    if d in {_strip_kha(_clean(g)) for g in GENERIC}:
        return "generic"
    return None


@dataclass
class Claim:
    key: str                      # unique in the run: the claim id (or its sheet row)
    id: str | None
    row: int
    sheet: str                    # file name
    sheet_name: str
    employee_id: str | None
    employee_name: str | None
    date: _dt.date | None
    date_raw: str
    category_raw: str
    merchant: str
    amount: Decimal | None
    amount_raw: str
    receipt_ref: str | None
    vat_claim: bool
    wht_deducted: Decimal | None
    wht_raw: str
    description: str
    cells: dict = field(default_factory=dict)

    @property
    def employee(self) -> str:
        return _norm(self.employee_id) or _norm(self.employee_name)

    @property
    def label(self) -> str:
        return self.id or W("claim.row", row=self.row)

    @classmethod
    def from_row(cls, d: dict, *, sheet: str = "", sheet_name: str = "", key: str | None = None) -> "Claim":
        amount = Decimal(d["amount"]) if d.get("amount") not in (None, "") else None
        wht = Decimal(d["wht_deducted"]) if d.get("wht_deducted") not in (None, "") else None
        date = _dt.date.fromisoformat(d["date"]) if d.get("date") else None
        k = key or d.get("id") or f"r{d.get('row')}"
        return cls(key=re.sub(r"[\s:]+", "_", str(k)), id=d.get("id"), row=int(d.get("row") or 0), sheet=sheet,
                   sheet_name=sheet_name, employee_id=d.get("employee_id"), employee_name=d.get("employee_name"),
                   date=date, date_raw=str(d.get("date_raw") or ""), category_raw=str(d.get("category") or ""),
                   merchant=str(d.get("merchant") or ""), amount=amount, amount_raw=str(d.get("amount_raw") or ""),
                   receipt_ref=d.get("receipt") or None, vat_claim=bool(d.get("vat_claim")), wht_deducted=wht,
                   wht_raw=str(d.get("wht_raw") or ""), description=str(d.get("description") or ""),
                   cells=dict(d.get("cells") or {}))


@dataclass
class ReceiptView:
    """What the rules need from one receipt as E2 read it (checks/receipts.py builds it)."""
    key: str
    names: list                   # every uploaded file name with these bytes
    sha256: str
    status: str                   # read | pending (a card is open) | unconfirmed (left out by a card / unanswered)
    total: Decimal | None = None
    currency: str | None = "THB"
    document_type: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    document_number: str | None = None
    document_date: str | None = None
    buyer_match: str = "skip"
    missing_fields: list = field(default_factory=list)
    total_source: dict = field(default_factory=dict)
    tier: str = "digital"
    unconfirmed_reason: str = ""

    @property
    def name(self) -> str:
        return self.names[0]


@dataclass
class Fact:
    name: str
    status: str                   # pass | fail | skip
    code: str | None = None       # the reason code when it failed
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    disposition: str | None = None    # exception | question | None (recorded, no action)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClaimAudit:
    claim: Claim
    facts: dict                   # name → Fact, in FACTS order
    receipt: ReceiptView | None
    category: Category | None
    outcome: str
    codes: list                   # rule failures → run exceptions
    flags: list                   # judgement → clarification cards (only when no rule failed)
    also_flagged: list            # judgement on a claim already an exception: rides with it


def _lookup(receipts: list[ReceiptView]) -> dict[str, ReceiptView]:
    out: dict[str, ReceiptView] = {}
    for r in receipts:
        for n in r.names:
            low = n.lower()
            out.setdefault(low, r)
            out.setdefault(low.rsplit(".", 1)[0], r)
    return out


def _find(lookup: dict, ref: str | None) -> ReceiptView | None:
    if not ref:
        return None
    low = ref.strip().lower()
    return lookup.get(low) or lookup.get(low.rsplit(".", 1)[0])


def _limit_ev(ref: str, value: Decimal) -> dict:
    return {"policy_ref": ref, "limit": _s(value), "limit_source": "policy_table"}


def audit(claims: list[Claim], *, policy: Policy, calendar: Calendar, receipts: list[ReceiptView] | None = None,
          history: list[dict] | None = None) -> list[ClaimAudit]:
    lookup = _lookup(receipts or [])
    cats = {c.key: (policy.categories.get(policy.category_of(c.category_raw) or "")) for c in claims}
    linked = {c.key: _find(lookup, c.receipt_ref) for c in claims}
    order = {c.key: i for i, c in enumerate(claims)}
    dup = _duplicates(claims, linked, history or [])
    split = _splits(claims, dup, policy)
    period = _periods(claims, cats, order)
    out = []
    for c in claims:
        cat, rec = cats[c.key], linked[c.key]
        facts = {f.name: f for f in (
            _claim_data(c), _category(c, cat, policy), _claim_limit(c, cat), period.get(c.key) or _skip("within_period_limit", cat, "per_person_month", c),
            _receipt(c, rec, policy), _receipt_amount(c, rec), dup.get(c.key) or Fact("duplicate", "pass"),
            split.get(c.key) or _split_skip(c), _prohibited(c, rec, policy), _vat(c, rec, policy), _wht(c, cat),
            _weekend(c, calendar, policy), _purpose(c, cat))}
        exc = [f.code for f in facts.values() if f.status == "fail" and f.disposition == "exception"]
        judge = [f.code for f in facts.values() if f.status == "fail" and f.disposition == "question"]
        outcome = "exception" if exc else ("question" if judge else "pass")
        out.append(ClaimAudit(claim=c, facts=facts, receipt=rec, category=cat, outcome=outcome, codes=exc,
                              flags=[] if exc else judge, also_flagged=judge if exc else []))
    out.sort(key=lambda a: order[a.claim.key])
    return out


# ── the rules ─────────────────────────────────────────────────────────────────────

def _claim_data(c: Claim) -> Fact:
    problems = []
    if c.amount is None:
        problems.append(("amount", W("fact.amount_unreadable", amount_raw=c.amount_raw) if c.amount_raw else W("fact.no_amount")))
    elif c.amount <= 0:
        problems.append(("amount", W("fact.amount_not_positive", amount=_m(c.amount))))
    if c.date is None:
        problems.append(("date", W("fact.date_unreadable", date_raw=c.date_raw) if c.date_raw else W("fact.no_date")))
    if not c.employee:
        problems.append(("employee", W("fact.no_employee")))
    if problems:
        return Fact("claim_data", "fail", "claim_row_unreadable", "; ".join(p[1] for p in problems),
                    {"problems": [p[0] for p in problems], "amount_raw": c.amount_raw, "date_raw": c.date_raw},
                    "exception")
    return Fact("claim_data", "pass")


def _category(c: Claim, cat: Category | None, policy: Policy) -> Fact:
    if cat is None:
        return Fact("category", "fail", "unknown_category",
                    W("fact.unknown_category", category_raw=c.category_raw),
                    {"category_raw": c.category_raw, "policy_ref": "categories",
                     "known": [x.label for x in policy.categories.values()]}, "exception")
    return Fact("category", "pass", evidence={"code": cat.code, "label": cat.label, "policy_ref": f"categories.{cat.code}"})


def _skip(name: str, cat: Category | None, what: str, c: Claim) -> Fact:
    if cat is None or c.amount is None:
        return Fact(name, "skip", detail=W("fact.skip.no_category"))
    per = W("per.claim") if what == "per_claim" else W("per.month")
    if getattr(cat, what) is None:
        # the table sets no such limit: none is applied (a KB document never supplies one)
        return Fact(name, "skip", detail=W("fact.skip.no_limit", per=per, label=cat.label),
                    evidence={"policy_ref": f"categories.{cat.code}.{what}", "limit_source": "policy_table"})
    return Fact(name, "skip", detail=W("fact.skip.no_date_or_employee"))


def _claim_limit(c: Claim, cat: Category | None) -> Fact:
    if cat is None or c.amount is None or c.amount <= 0 or cat.per_claim is None:
        return _skip("within_claim_limit", cat, "per_claim", c)
    ev = {**_limit_ev(f"categories.{cat.code}.per_claim", cat.per_claim), "amount": _s(c.amount)}
    if c.amount > cat.per_claim:
        ev["excess"] = _s(c.amount - cat.per_claim)
        return Fact("within_claim_limit", "fail", "over_claim_limit",
                    W("fact.over_claim_limit", amount=_m(c.amount), label=cat.label, per_claim=_m(cat.per_claim),
                      excess=_m(c.amount - cat.per_claim)),
                    ev, "exception")
    return Fact("within_claim_limit", "pass", detail=W("fact.within_claim_limit", per_claim=_m(cat.per_claim)), evidence=ev)


def _periods(claims: list[Claim], cats: dict, order: dict) -> dict[str, Fact]:
    """Per person, category and calendar month, in date order (sheet order on the same day): the claim
    that takes the running total over the limit fails, and so does every later one. Claimed amounts
    count — the audit runs before anyone decides an exception (fixtures/README, expense/)."""
    groups: dict[tuple, list[Claim]] = {}
    for i, c in enumerate(claims):
        cat = cats[c.key]
        if cat is None or cat.per_person_month is None or c.amount is None or c.amount <= 0 or c.date is None or not c.employee:
            continue
        groups.setdefault((c.employee, cat.code, f"{c.date:%Y-%m}"), []).append(c)
    out = {}
    for (emp, code, month), cs in groups.items():
        cat = cats[cs[0].key]
        run, counted = Decimal(0), []
        for c in sorted(cs, key=lambda x: (x.date, order[x.key])):
            before = run
            run += c.amount
            counted.append(c.label)
            ev = {**_limit_ev(f"categories.{code}.per_person_month", cat.per_person_month), "month": month,
                  "amount": _s(c.amount), "before": _s(before), "after": _s(run), "counted": list(counted)}
            if run > cat.per_person_month:
                out[c.key] = Fact("within_period_limit", "fail", "over_period_limit",
                                  W("fact.over_period_limit", label=cat.label, month=month, run=_m(run),
                                    counted=", ".join(counted), per_person_month=_m(cat.per_person_month)),
                                  ev, "exception")
            else:
                out[c.key] = Fact("within_period_limit", "pass",
                                  detail=W("fact.within_period_limit", run=_m(run), per_person_month=_m(cat.per_person_month)),
                                  evidence=ev)
    return out


def _receipt(c: Claim, rec: ReceiptView | None, policy: Policy) -> Fact:
    if c.amount is None:
        return Fact("receipt", "skip", detail=W("fact.no_amount"))
    required = c.amount > policy.receipt_required_above
    ev = {**_limit_ev("receipt_required_above", policy.receipt_required_above), "amount": _s(c.amount),
          "required": required, "referenced": c.receipt_ref, "receipt": rec.name if rec else None, "uploaded": rec is not None}
    if rec is not None:
        return Fact("receipt", "pass", detail=W("fact.receipt.attached", name=rec.name), evidence=ev)
    if not required:
        return Fact("receipt", "pass",
                    detail=W("fact.receipt.not_required", amount=_m(c.amount), receipt_required_above=_m(policy.receipt_required_above)),
                    evidence=ev)
    why = (W("fact.receipt.not_sent", receipt_ref=c.receipt_ref) if c.receipt_ref else W("fact.receipt.none"))
    return Fact("receipt", "fail", "missing_receipt",
                W("fact.receipt.missing", amount=_m(c.amount), receipt_required_above=_m(policy.receipt_required_above), why=why), ev, "exception")


def _receipt_amount(c: Claim, rec: ReceiptView | None) -> Fact:
    if rec is None or c.amount is None:
        return Fact("receipt_amount", "skip", detail=W("fact.receipt_amount.none"))
    ev = {"receipt": rec.name, "amount": _s(c.amount), "receipt_total": _s(rec.total), **rec.total_source}
    if rec.status != "read":
        why = rec.unconfirmed_reason or W("fact.receipt_unconfirmed.open")
        return Fact("receipt_amount", "fail", "receipt_unconfirmed",
                    W("fact.receipt_unconfirmed", name=rec.name, why=why), ev, "exception")
    if rec.currency not in (None, "THB"):
        return Fact("receipt_amount", "fail", "receipt_amount_mismatch",
                    W("fact.receipt_currency", name=rec.name, currency=rec.currency, amount=_m(c.amount)), {**ev, "currency": rec.currency},
                    "exception")
    if rec.total is None:
        return Fact("receipt_amount", "fail", "receipt_unconfirmed", W("fact.receipt_total_unread", name=rec.name), ev, "exception")
    if rec.total != c.amount:
        ev["difference"] = _s(c.amount - rec.total)
        return Fact("receipt_amount", "fail", "receipt_amount_mismatch",
                    W("fact.receipt_mismatch", amount=_m(c.amount), name=rec.name, total=_m(rec.total), difference=_m(c.amount - rec.total)),
                    ev, "exception")
    return Fact("receipt_amount", "pass", detail=W("fact.receipt_match", total=_m(rec.total)), evidence=ev)


def _duplicates(claims: list[Claim], linked: dict, history: list[dict]) -> dict[str, Fact]:
    """The first claim is the original; a later one with the same employee, merchant, date and amount,
    or the same receipt (the same bytes, whatever the file is called, or the same file name), is a
    duplicate of it. A receipt already claimed in an earlier run of this install (the duplicate index)
    makes a duplicate too."""
    by_details: dict[tuple, str] = {}
    by_receipt: dict[str, str] = {}
    hist_sha = {h.get("sha256"): h for h in history if isinstance(h, dict) and h.get("sha256")}
    out = {}
    for c in claims:
        rec = linked[c.key]
        match, first = [], None
        sig = (c.employee, _norm(c.merchant), c.date, c.amount) if c.employee and c.merchant and c.date and c.amount else None
        if sig and sig in by_details:
            first = by_details[sig]
            match.append("same_employee_merchant_date_amount")
        rkey = ("sha:" + rec.sha256) if rec else (("name:" + c.receipt_ref.lower()) if c.receipt_ref else None)
        if rkey and rkey in by_receipt:
            first = first or by_receipt[rkey]
            match.append("same_receipt_file" if rec else "same_receipt_name")
        earlier = hist_sha.get(rec.sha256) if rec else None
        if earlier is None and rec is not None:
            earlier = _history_match(rec, history)
        if match or earlier:
            ev = {"duplicate_of": first, "match": match}
            if earlier:
                ev["earlier_run"] = {"name": earlier.get("name"), "sha256": earlier.get("sha256"),
                                     "document_date": earlier.get("document_date"), "total": earlier.get("total")}
                match.append("receipt_in_an_earlier_run")
            where = words.and_(x for x in (W("dup.claim", first=first) if first else "",
                                           W("dup.earlier", name=earlier.get("name")) if earlier else "") if x)
            out[c.key] = Fact("duplicate", "fail", "duplicate_claim", W("fact.duplicate", where=where), ev, "exception")
        if sig and sig not in by_details:
            by_details[sig] = c.label
        if rkey and rkey not in by_receipt:
            by_receipt[rkey] = c.label
    return out


def _history_match(rec: ReceiptView, history: list[dict]) -> dict | None:
    """Same seller tax ID and document number, or same seller tax ID, date and total (engine/e2's rule)."""
    if not rec.seller_tax_id:
        return None
    for h in history:
        if not isinstance(h, dict) or h.get("seller_tax_id") != rec.seller_tax_id:
            continue
        if rec.document_number and str(h.get("document_number") or "").replace(" ", "") == rec.document_number.replace(" ", ""):
            return h
        if rec.document_date and rec.total is not None and str(h.get("document_date")) == rec.document_date and \
                h.get("total") is not None and Decimal(str(h["total"])) == rec.total:
            return h
    return None


def _split_skip(c: Claim) -> Fact:
    return Fact("split_purchase", "pass") if c.amount is not None and c.date and c.merchant else \
        Fact("split_purchase", "skip", detail=W("fact.split.skip"))


def _splits(claims: list[Claim], dup: dict, policy: Policy) -> dict[str, Fact]:
    """Several claims by one person at one merchant on one day, each under the approval threshold,
    that together are over it. A duplicate claim is the same purchase, not another part of one."""
    groups: dict[tuple, list[Claim]] = {}
    for c in claims:
        if c.key in dup or c.amount is None or c.amount <= 0 or not c.date or not c.merchant or not c.employee:
            continue
        groups.setdefault((c.employee, _norm(c.merchant), c.date), []).append(c)
    t = policy.approval_threshold
    out = {}
    for cs in groups.values():
        under = [c for c in cs if c.amount < t]
        total = sum((c.amount for c in under), Decimal(0))
        if len(under) < 2 or total <= t:
            continue
        ids = [c.label for c in under]
        for c in under:
            out[c.key] = Fact("split_purchase", "fail", "split_purchase",
                              W("fact.split", n=len(under), ids=", ".join(ids), t=_m(t), total=_m(total)),
                              {**_limit_ev("approval_threshold", t), "group": ids, "sum": _s(total)}, "exception")
    return out


def _prohibited(c: Claim, rec: ReceiptView | None, policy: Policy) -> Fact:
    if not policy.prohibited:
        return Fact("prohibited_merchant", "skip", detail=W("fact.prohibited.none"))
    seller = rec.seller_name if rec is not None and rec.status == "read" else None
    for w in policy.prohibited:
        where = [n for n, text in (("claim_merchant", c.merchant), ("receipt_seller", seller)) if text and _norm(w) in _norm(text)]
        if where:
            return Fact("prohibited_merchant", "fail", "prohibited_merchant",
                        (W("fact.prohibited.seller", merchant=c.merchant, seller=seller, w=w) if seller and "receipt_seller" in where else
                         W("fact.prohibited", merchant=c.merchant, w=w)),
                        {"policy_ref": "prohibited_merchants", "word": w, "where": where, "merchant": c.merchant,
                         "receipt_seller": seller}, "exception")
    return Fact("prohibited_merchant", "pass", evidence={"policy_ref": "prohibited_merchants"})


DOC_TH = W.table({"full_tax_invoice": "doc.full_tax_invoice", "abbreviated_tax_invoice": "doc.abbreviated_tax_invoice",
                  "receipt": "doc.receipt", "invoice": "doc.invoice", "unknown": "doc.unknown", None: "doc.none"})


def _vat(c: Claim, rec: ReceiptView | None, policy: Policy) -> Fact:
    if not c.vat_claim:
        return Fact("vat_document", "skip", detail=W("fact.vat.not_claimed"))
    need = policy.vat_claim_requires
    ok_types = ("full_tax_invoice",) if need == "full_tax_invoice" else ("full_tax_invoice", "abbreviated_tax_invoice")
    dtype = rec.document_type if rec is not None and rec.status == "read" else None
    ev = {"policy_ref": "vat_claim_requires", "required": need, "document_type": dtype, "receipt": rec.name if rec else None}
    problems = []
    if rec is None:
        problems.append(W("fact.vat.no_document"))
    elif rec.status != "read":
        problems.append(W("fact.vat.unconfirmed", name=rec.name))
    elif dtype not in ok_types:
        problems.append(W("fact.vat.is", name=rec.name, dtype=DOC_TH.get(dtype, dtype)))
    else:
        if rec.buyer_match == "fail":
            problems.append(W("fact.vat.other_buyer", name=rec.name))
        if rec.missing_fields:
            problems.append(W("fact.vat.lacks", name=rec.name, missing_fields=", ".join(rec.missing_fields)))
            ev["missing_fields"] = list(rec.missing_fields)
    if problems:
        return Fact("vat_document", "fail", "vat_claim_without_full_tax_invoice",
                    W("fact.vat", need=DOC_TH[need], problems="; ".join(problems)), ev, "exception")
    return Fact("vat_document", "pass", detail=W("fact.vat.is", name=rec.name, dtype=DOC_TH.get(dtype, dtype)), evidence=ev)


def _wht(c: Claim, cat: Category | None) -> Fact:
    if cat is None or cat.wht_rate is None or c.amount is None:
        return Fact("wht", "skip", detail=W("fact.wht.none"))
    frm = cat.wht_from or Decimal(0)
    ev = {**(_limit_ev(f"categories.{cat.code}.wht_from", frm) if cat.wht_from is not None else {}),
          "rate": str(cat.wht_rate), "rate_ref": f"categories.{cat.code}.wht_rate", "amount": _s(c.amount)}
    if c.amount < frm:
        return Fact("wht", "skip", detail=W("fact.wht.below", amount=_m(c.amount), frm=_m(frm)), evidence=ev)
    due = _q2(c.amount * cat.wht_rate / Decimal(100))
    deducted = c.wht_deducted if c.wht_deducted is not None else Decimal("0.00")
    ev.update({"due": _s(due), "deducted": _s(deducted), "stated": c.wht_deducted is not None})
    if deducted == due:
        return Fact("wht", "pass", detail=W("fact.wht.ok", deducted=_m(deducted), wht_rate=cat.wht_rate), evidence=ev)
    if deducted == 0:
        # (a file that does not state what was deducted says so)
        return Fact("wht", "fail", "wht_missing",
                    W("fact.wht.missing" if c.wht_deducted is not None else "fact.wht.missing_unstated",
                      label=cat.label, amount=_m(c.amount), wht_rate=cat.wht_rate, due=_m(due)), ev, "exception")
    return Fact("wht", "fail", "wht_mismatch",
                W("fact.wht.mismatch", wht_rate=cat.wht_rate, due=_m(due), deducted=_m(deducted)), ev, "exception")


def _weekend(c: Claim, calendar: Calendar, policy: Policy) -> Fact:
    if c.date is None:
        return Fact("weekend_holiday", "skip", detail=W("fact.no_date"))
    wd = c.date.weekday()
    hol = calendar.holiday(c.date)
    # the holiday part runs only on the company's own confirmed calendar (AM3: fail closed); weekends always
    ev = {"policy_ref": "weekend_holiday", "rule": policy.weekend_holiday, "weekday": WEEKDAY_TH[wd], "holiday": hol,
          "calendar": calendar.source, "holiday_check": "on" if calendar.active else "off",
          "calendar_covers_year": calendar.covers(c.date.year) if calendar.active else None}
    if wd < 5 and not hol:
        return Fact("weekend_holiday", "pass", detail=WEEKDAY_TH[wd], evidence=ev)
    what = words.and_(x for x in ((WEEKDAY_TH[wd] if wd >= 5 else ""), (W("day.holiday", hol=hol) if hol else "")) if x)
    disp = {"flag": "question", "exception": "exception"}.get(policy.weekend_holiday)
    return Fact("weekend_holiday", "fail", "weekend_or_holiday", W("fact.weekend", date=date_th(c.date), what=what), ev, disp)


def _purpose(c: Claim, cat: Category | None) -> Fact:
    why = vague_purpose(c.description, cat.label if cat else c.category_raw)
    needs = bool(cat and cat.needs_purpose)
    ev = {"description": c.description, "needs_purpose": needs,
          "policy_ref": f"categories.{cat.code}.needs_purpose" if cat else "categories"}
    if why is None:
        return Fact("purpose", "pass", evidence=ev)
    ev["why"] = why
    if why == "empty" and needs:
        return Fact("purpose", "fail", "missing_purpose", W("fact.purpose.missing", label=cat.label), ev, "exception")
    text = {"empty": W("said.none"), "restates_category": W("fact.purpose.restates", description=c.description),
            "generic": W("fact.purpose.generic", description=c.description)}[why]
    return Fact("purpose", "fail", "vague_purpose", text + " " + W("fact.purpose.judge"), ev, "question")
