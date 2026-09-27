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

from ....engine.e2 import thai_text as T
from ....engine.thai import MONTHS_TH_ABBR
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
WEEKDAY_TH = ("วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์")
Q2 = Decimal("0.01")
# a description that says nothing about the business reason (compared without spaces, punctuation or ค่า)
GENERIC = ("ใช้จ่าย", "ใช้จ่ายทั่วไป", "ใช้จ่ายอื่นๆ", "ใช้จ่ายในงาน", "อื่นๆ", "อื่น", "ทั่วไป", "เบ็ดเตล็ด", "ตามใบเสร็จ",
           "ตามบิล", "ใช้ในงาน", "งาน", "ธุระ", "misc", "other", "others", "expense", "expenses", "general", "na", "n/a")
_PUNCT = re.compile(r"[\s\.,\-/\\()\[\]{}\"'“”‘’!?:;_*#]+")


def date_th(d: _dt.date | None) -> str:
    """2026-10-10 → '10 ต.ค. 2569'."""
    return f"{d.day} {MONTHS_TH_ABBR[d.month - 1]} {d.year + 543}" if d else "ไม่ระบุวันที่"


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Q2, rounding=ROUND_HALF_UP)


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
        return self.id or f"แถว {self.row}"

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
        problems.append(("amount", f"จำนวนเงิน “{c.amount_raw}” อ่านเป็นตัวเลขไม่ได้" if c.amount_raw else "ไม่มีจำนวนเงิน"))
    elif c.amount <= 0:
        problems.append(("amount", f"จำนวนเงิน {c.amount:,.2f} ต้องมากกว่า 0"))
    if c.date is None:
        problems.append(("date", f"วันที่ “{c.date_raw}” อ่านเป็นวันที่ไม่ได้" if c.date_raw else "ไม่มีวันที่"))
    if not c.employee:
        problems.append(("employee", "ไม่มีรหัสหรือชื่อพนักงาน"))
    if problems:
        return Fact("claim_data", "fail", "claim_row_unreadable", "; ".join(p[1] for p in problems),
                    {"problems": [p[0] for p in problems], "amount_raw": c.amount_raw, "date_raw": c.date_raw},
                    "exception")
    return Fact("claim_data", "pass")


def _category(c: Claim, cat: Category | None, policy: Policy) -> Fact:
    if cat is None:
        return Fact("category", "fail", "unknown_category",
                    f"หมวด “{c.category_raw}” ไม่มีในตารางนโยบาย จึงไม่มีวงเงินให้ตรวจ",
                    {"category_raw": c.category_raw, "policy_ref": "categories",
                     "known": [x.label for x in policy.categories.values()]}, "exception")
    return Fact("category", "pass", evidence={"code": cat.code, "label": cat.label, "policy_ref": f"categories.{cat.code}"})


def _skip(name: str, cat: Category | None, what: str, c: Claim) -> Fact:
    if cat is None or c.amount is None:
        return Fact(name, "skip", detail="ไม่มีหมวดในตารางหรือไม่มีจำนวนเงิน")
    per = "ต่อครั้ง" if what == "per_claim" else "ต่อคนต่อเดือน"
    if getattr(cat, what) is None:
        # the table sets no such limit: none is applied (a KB document never supplies one)
        return Fact(name, "skip", detail=f"ตารางนโยบายไม่ได้กำหนดวงเงิน{per}ของหมวด{cat.label}",
                    evidence={"policy_ref": f"categories.{cat.code}.{what}", "limit_source": "policy_table"})
    return Fact(name, "skip", detail="ไม่มีวันที่หรือพนักงาน")


def _claim_limit(c: Claim, cat: Category | None) -> Fact:
    if cat is None or c.amount is None or c.amount <= 0 or cat.per_claim is None:
        return _skip("within_claim_limit", cat, "per_claim", c)
    ev = {**_limit_ev(f"categories.{cat.code}.per_claim", cat.per_claim), "amount": _s(c.amount)}
    if c.amount > cat.per_claim:
        ev["excess"] = _s(c.amount - cat.per_claim)
        return Fact("within_claim_limit", "fail", "over_claim_limit",
                    f"เบิก {c.amount:,.2f} บาท เกินวงเงิน{cat.label}ต่อครั้ง {cat.per_claim:,.2f} บาท อยู่ {c.amount - cat.per_claim:,.2f} บาท",
                    ev, "exception")
    return Fact("within_claim_limit", "pass", detail=f"ไม่เกินวงเงินต่อครั้ง {cat.per_claim:,.2f} บาท", evidence=ev)


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
                                  f"{cat.label}ของพนักงานคนนี้ในเดือน {month} รวม {run:,.2f} บาท (รายการ {', '.join(counted)}) "
                                  f"เกินวงเงินต่อคนต่อเดือน {cat.per_person_month:,.2f} บาท — นับยอดที่เบิก ก่อนมีใครตัดสินข้อยกเว้น",
                                  ev, "exception")
            else:
                out[c.key] = Fact("within_period_limit", "pass", detail=f"รวมทั้งเดือน {run:,.2f} จาก {cat.per_person_month:,.2f} บาท",
                                  evidence=ev)
    return out


def _receipt(c: Claim, rec: ReceiptView | None, policy: Policy) -> Fact:
    if c.amount is None:
        return Fact("receipt", "skip", detail="ไม่มีจำนวนเงิน")
    required = c.amount > policy.receipt_required_above
    ev = {**_limit_ev("receipt_required_above", policy.receipt_required_above), "amount": _s(c.amount),
          "required": required, "referenced": c.receipt_ref, "receipt": rec.name if rec else None, "uploaded": rec is not None}
    if rec is not None:
        return Fact("receipt", "pass", detail=f"แนบใบเสร็จ {rec.name}", evidence=ev)
    if not required:
        return Fact("receipt", "pass", detail=f"ยอด {c.amount:,.2f} ไม่เกิน {policy.receipt_required_above:,.2f} บาท ไม่ต้องมีใบเสร็จ",
                    evidence=ev)
    why = (f"แถวนี้อ้างถึงไฟล์ {c.receipt_ref} แต่ไม่ได้ส่งไฟล์นั้นมา" if c.receipt_ref else "ไม่ได้ระบุใบเสร็จ")
    return Fact("receipt", "fail", "missing_receipt",
                f"ยอด {c.amount:,.2f} บาท เกิน {policy.receipt_required_above:,.2f} บาท ต้องมีใบเสร็จ: {why}", ev, "exception")


def _receipt_amount(c: Claim, rec: ReceiptView | None) -> Fact:
    if rec is None or c.amount is None:
        return Fact("receipt_amount", "skip", detail="ไม่มีใบเสร็จให้เทียบ")
    ev = {"receipt": rec.name, "amount": _s(c.amount), "receipt_total": _s(rec.total), **rec.total_source}
    if rec.status != "read":
        return Fact("receipt_amount", "fail", "receipt_unconfirmed",
                    f"ยืนยันยอดในใบเสร็จ {rec.name} ไม่ได้ ({rec.unconfirmed_reason or 'ยังมีคำถามที่ไม่ได้ตอบ'})", ev, "exception")
    if rec.currency not in (None, "THB"):
        return Fact("receipt_amount", "fail", "receipt_amount_mismatch",
                    f"ใบเสร็จ {rec.name} เป็นเงิน {rec.currency} แต่เบิกเป็นบาท {c.amount:,.2f}", {**ev, "currency": rec.currency},
                    "exception")
    if rec.total is None:
        return Fact("receipt_amount", "fail", "receipt_unconfirmed", f"อ่านยอดรวมในใบเสร็จ {rec.name} ไม่ได้", ev, "exception")
    if rec.total != c.amount:
        ev["difference"] = _s(c.amount - rec.total)
        return Fact("receipt_amount", "fail", "receipt_amount_mismatch",
                    f"เบิก {c.amount:,.2f} บาท แต่ใบเสร็จ {rec.name} ยอดรวม {rec.total:,.2f} บาท ต่างกัน {c.amount - rec.total:,.2f} บาท",
                    ev, "exception")
    return Fact("receipt_amount", "pass", detail=f"ยอดในใบเสร็จ {rec.total:,.2f} ตรงกับที่เบิก", evidence=ev)


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
            where = (f"รายการ {first}" if first else "") + (" และ" if first and earlier else "") + \
                    (f"ใบเสร็จ {earlier.get('name')} ที่เบิกในรอบก่อน" if earlier else "")
            out[c.key] = Fact("duplicate", "fail", "duplicate_claim", f"ซ้ำกับ{where}", ev, "exception")
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
        Fact("split_purchase", "skip", detail="ไม่มีจำนวนเงิน วันที่ หรือร้านค้า")


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
                              f"{len(under)} รายการที่ร้านเดียวกันวันเดียวกัน ({', '.join(ids)}) แต่ละรายการต่ำกว่าวงเงินอนุมัติ "
                              f"{t:,.2f} บาท แต่รวมกัน {total:,.2f} บาท",
                              {**_limit_ev("approval_threshold", t), "group": ids, "sum": _s(total)}, "exception")
    return out


def _prohibited(c: Claim, rec: ReceiptView | None, policy: Policy) -> Fact:
    if not policy.prohibited:
        return Fact("prohibited_merchant", "skip", detail="ตารางนโยบายไม่มีรายชื่อร้านต้องห้าม")
    seller = rec.seller_name if rec is not None and rec.status == "read" else None
    for w in policy.prohibited:
        where = [n for n, text in (("claim_merchant", c.merchant), ("receipt_seller", seller)) if text and _norm(w) in _norm(text)]
        if where:
            return Fact("prohibited_merchant", "fail", "prohibited_merchant",
                        f"ร้าน “{c.merchant}”{' / ใบเสร็จ “' + seller + '”' if seller and 'receipt_seller' in where else ''} "
                        f"มีคำว่า “{w}” ซึ่งอยู่ในรายชื่อร้านต้องห้าม",
                        {"policy_ref": "prohibited_merchants", "word": w, "where": where, "merchant": c.merchant,
                         "receipt_seller": seller}, "exception")
    return Fact("prohibited_merchant", "pass", evidence={"policy_ref": "prohibited_merchants"})


DOC_TH = {"full_tax_invoice": "ใบกำกับภาษีเต็มรูป", "abbreviated_tax_invoice": "ใบกำกับภาษีอย่างย่อ", "receipt": "ใบเสร็จรับเงิน",
          "invoice": "ใบแจ้งหนี้", "unknown": "ไม่ทราบชนิด", None: "ไม่มีเอกสาร"}


def _vat(c: Claim, rec: ReceiptView | None, policy: Policy) -> Fact:
    if not c.vat_claim:
        return Fact("vat_document", "skip", detail="ไม่ได้ขอคืนภาษีซื้อ")
    need = policy.vat_claim_requires
    ok_types = ("full_tax_invoice",) if need == "full_tax_invoice" else ("full_tax_invoice", "abbreviated_tax_invoice")
    dtype = rec.document_type if rec is not None and rec.status == "read" else None
    ev = {"policy_ref": "vat_claim_requires", "required": need, "document_type": dtype, "receipt": rec.name if rec else None}
    problems = []
    if rec is None:
        problems.append("ไม่มีเอกสารแนบ")
    elif rec.status != "read":
        problems.append(f"ยืนยันชนิดเอกสาร {rec.name} ไม่ได้")
    elif dtype not in ok_types:
        problems.append(f"{rec.name} เป็น{DOC_TH.get(dtype, dtype)}")
    else:
        if rec.buyer_match == "fail":
            problems.append(f"{rec.name} ออกในนามผู้ซื้ออื่น ไม่ใช่บริษัท")
        if rec.missing_fields:
            problems.append(f"{rec.name} ขาด {', '.join(rec.missing_fields)}")
            ev["missing_fields"] = list(rec.missing_fields)
    if problems:
        return Fact("vat_document", "fail", "vat_claim_without_full_tax_invoice",
                    f"ขอคืนภาษีซื้อได้เฉพาะเมื่อมี{DOC_TH[need]}ที่ออกในนามบริษัท: {'; '.join(problems)}", ev, "exception")
    return Fact("vat_document", "pass", detail=f"{rec.name} เป็น{DOC_TH.get(dtype, dtype)}", evidence=ev)


def _wht(c: Claim, cat: Category | None) -> Fact:
    if cat is None or cat.wht_rate is None or c.amount is None:
        return Fact("wht", "skip", detail="หมวดนี้ไม่มีภาษีหัก ณ ที่จ่ายในตารางนโยบาย")
    frm = cat.wht_from or Decimal(0)
    ev = {**(_limit_ev(f"categories.{cat.code}.wht_from", frm) if cat.wht_from is not None else {}),
          "rate": str(cat.wht_rate), "rate_ref": f"categories.{cat.code}.wht_rate", "amount": _s(c.amount)}
    if c.amount < frm:
        return Fact("wht", "skip", detail=f"ยอด {c.amount:,.2f} ต่ำกว่า {frm:,.2f} บาท ไม่ต้องหัก", evidence=ev)
    due = _q2(c.amount * cat.wht_rate / Decimal(100))
    deducted = c.wht_deducted if c.wht_deducted is not None else Decimal("0.00")
    ev.update({"due": _s(due), "deducted": _s(deducted), "stated": c.wht_deducted is not None})
    if deducted == due:
        return Fact("wht", "pass", detail=f"หัก {deducted:,.2f} บาท ตรงกับ {cat.wht_rate}%", evidence=ev)
    said = "" if c.wht_deducted is not None else " (ไม่ได้ระบุในไฟล์)"
    if deducted == 0:
        return Fact("wht", "fail", "wht_missing",
                    f"{cat.label} {c.amount:,.2f} บาท ต้องหักภาษี ณ ที่จ่าย {cat.wht_rate}% = {due:,.2f} บาท แต่หัก 0.00{said}",
                    ev, "exception")
    return Fact("wht", "fail", "wht_mismatch",
                f"ต้องหักภาษี ณ ที่จ่าย {cat.wht_rate}% = {due:,.2f} บาท แต่หัก {deducted:,.2f} บาท", ev, "exception")


def _weekend(c: Claim, calendar: Calendar, policy: Policy) -> Fact:
    if c.date is None:
        return Fact("weekend_holiday", "skip", detail="ไม่มีวันที่")
    wd = c.date.weekday()
    hol = calendar.holiday(c.date)
    # the holiday part runs only on the company's own confirmed calendar (AM3: fail closed); weekends always
    ev = {"policy_ref": "weekend_holiday", "rule": policy.weekend_holiday, "weekday": WEEKDAY_TH[wd], "holiday": hol,
          "calendar": calendar.source, "holiday_check": "on" if calendar.active else "off",
          "calendar_covers_year": calendar.covers(c.date.year) if calendar.active else None}
    if wd < 5 and not hol:
        return Fact("weekend_holiday", "pass", detail=WEEKDAY_TH[wd], evidence=ev)
    what = " และ".join(x for x in ((WEEKDAY_TH[wd] if wd >= 5 else ""), (f"วันหยุด{hol}" if hol else "")) if x)
    disp = {"flag": "question", "exception": "exception"}.get(policy.weekend_holiday)
    return Fact("weekend_holiday", "fail", "weekend_or_holiday", f"วันที่ {date_th(c.date)} เป็น{what}", ev, disp)


def _purpose(c: Claim, cat: Category | None) -> Fact:
    why = vague_purpose(c.description, cat.label if cat else c.category_raw)
    needs = bool(cat and cat.needs_purpose)
    ev = {"description": c.description, "needs_purpose": needs,
          "policy_ref": f"categories.{cat.code}.needs_purpose" if cat else "categories"}
    if why is None:
        return Fact("purpose", "pass", evidence=ev)
    ev["why"] = why
    if why == "empty" and needs:
        return Fact("purpose", "fail", "missing_purpose", f"หมวด{cat.label}ต้องระบุวัตถุประสงค์ แต่ไม่ได้เขียนรายละเอียด", ev, "exception")
    text = {"empty": "ไม่ได้เขียนรายละเอียด", "restates_category": f"รายละเอียด “{c.description}” เป็นแค่ชื่อหมวด",
            "generic": f"รายละเอียด “{c.description}” ไม่บอกวัตถุประสงค์"}[why]
    return Fact("purpose", "fail", "vague_purpose", text + " — วัตถุประสงค์ทางธุรกิจต้องให้คนพิจารณา", ev, "question")
