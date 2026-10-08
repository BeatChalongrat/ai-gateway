"""Judgement → clarification cards (engine/findings.py), and what a person's answer means.

A rule can see that a claim is dated on a Saturday or that its description is only its category's
name; whether the business reason is good enough is judgement, so it is a card — never a rule's
verdict. One card per claim and trigger (``expense_weekend_holiday:C07``,
``expense_vague_purpose:C14``; both codes are in engine/findings.MULTI_INSTANCE so the subject is in
the key and an answer can never land on another claim).

Every card offers the same three answers, and its default — what a skipped card records — is the
one that hands the claim to a person:

    accept        "fine as it is": recorded as an info exception naming who decided (the person)
    ask_employee  a review exception: ask the employee for the reason
    review        (default) a review exception for the approver to decide
    {"typed": …}  the purpose card also takes the purpose the person got from the employee: recorded
                  word for word as the claim's purpose (info), never interpreted by a model

Anything else — an answer that is not one of these, an empty typed value — is treated as ``review``:
a judgement is never passed by default.
"""

from __future__ import annotations

import re

from ....engine import words
from ....engine.findings import FORBIDDEN_WORDS, Finding, finding
from ..words import W
from .rules import ClaimAudit, date_th

CARD_CODES = {"weekend_or_holiday": "expense_weekend_holiday", "vague_purpose": "expense_vague_purpose"}
FLAG_OF = {v: k for k, v in CARD_CODES.items()}
# a card's heading: ≤ 12 display characters in either language (a table says its words when it is read)
HEADER = W.table({"weekend_or_holiday": "day.off", "vague_purpose": "word.purpose"}, then=words.cap)
DEFAULT = "review"
RESOLUTIONS = ("accepted", "typed", "ask_employee", "review")
_FORBIDDEN = re.compile("|".join(re.escape(w) for w in FORBIDDEN_WORDS), re.IGNORECASE)


def safe(text: str) -> str:
    """Free text from a sheet (a merchant, a description) quoted on a card: a word a card may never
    say (engine/findings.FORBIDDEN_WORDS — a card never asks for a secret) is masked, so a merchant
    called "…Shopping…" cannot break the run."""
    return _FORBIDDEN.sub("…", str(text or ""))


def key_for(flag: str, claim_key: str) -> str:
    return f"{CARD_CODES[flag]}:{claim_key}"


def resolution(value) -> tuple[str, str | None]:
    """A card's answer → (accepted | typed | ask_employee | review, the typed purpose)."""
    if value == "accept":
        return "accepted", None
    if value == "ask_employee":
        return "ask_employee", None
    if isinstance(value, dict) and isinstance(value.get("typed"), str):
        t = " ".join(value["typed"].split())
        if t:
            return "typed", t[:300]
    return "review", None


def _who(a: ClaimAudit) -> str:
    c = a.claim
    return safe(" ".join(x for x in (c.label, "·", c.employee_name or c.employee_id or "") if x).strip())


def _what(a: ClaimAudit) -> str:
    c = a.claim
    label = a.category.label if a.category else c.category_raw
    amount = f"{c.amount:,.2f}" if c.amount is not None else c.amount_raw
    merchant = c.merchant or W("card.no_merchant")
    return safe(W("card.what", label=label, amount=amount, merchant=merchant))


def card(a: ClaimAudit, flag: str) -> Finding:
    c = a.claim
    fact = a.facts["weekend_holiday" if flag == "weekend_or_holiday" else "purpose"]
    ev = {"claim": c.label, "sheet": c.sheet, "sheet_name": c.sheet_name, "row": c.row, "fact": fact.to_dict()}
    if flag == "weekend_or_holiday":
        day = fact.evidence.get("weekday") if (c.date and c.date.weekday() >= 5) else ""
        hol = fact.evidence.get("holiday")
        what = words.and_(x for x in (day, W("day.holiday", hol=hol) if hol else "") if x)
        q = (W("card.weekend.question", who=_who(a), claim=_what(a), date=date_th(c.date), days=what))
        opts = [(W("card.weekend.accept"), "accept", W("card.accept.d")),
                (W("card.ask"), "ask_employee", W("card.weekend.ask.d")),
                (W("card.review"), DEFAULT, W("card.review.d"))]
        return finding(CARD_CODES[flag], "blocking", "preflight", HEADER[flag], q, opts, DEFAULT, subject=c.key,
                       evidence=ev, facts={"claim": c.label, "check": "weekend_or_holiday", "date": c.date.isoformat(),
                                           "holiday": hol})
    desc = safe(c.description)
    said = W("said.description", desc=desc) if desc.strip() else W("said.none")
    q = (W("card.purpose.question", who=_who(a), claim=_what(a), date=date_th(c.date), said=said))
    opts = [(W("card.purpose.accept"), "accept", W("card.accept.d")),
            (W("card.ask"), "ask_employee", W("card.purpose.ask.d")),
            (W("card.review"), DEFAULT, W("card.review.d"))]
    return finding(CARD_CODES[flag], "blocking", "preflight", HEADER[flag], q, opts, DEFAULT, subject=c.key, evidence=ev,
                   input={"kind": "text", "hint": W("card.purpose.hint")},
                   facts={"claim": c.label, "check": "vague_purpose", "description": desc})


def cards_for(audits: list[ClaimAudit], answers: dict | None) -> list[Finding]:
    """One card per judgement trigger of a claim whose outcome is ``question`` and that has no answer yet."""
    answers = answers or {}
    return [card(a, flag) for a in audits for flag in a.flags if key_for(flag, a.claim.key) not in answers]


# the policy table's keys as the settings page names them (a card once said "categories.meals.per_claim")
POLICY_KEY_TH = W.table({k: f"setting.{k}" for k in (
    "per_claim", "per_person_month", "wht_rate", "wht_from", "needs_purpose", "label", "receipt_required_above",
    "approval_threshold", "prohibited_merchants", "weekend_holiday", "vat_claim_requires", "currency", "categories")})


def plain_problem(p: str) -> str:
    """'categories.meals.per_claim: ... ไม่ใช่จำนวนเงิน' → 'หมวด meals · วงเงินต่อครั้ง: ... ไม่ใช่จำนวนเงิน'."""
    where, sep, rest = p.partition(": ")
    if not sep or " " in where:
        return p
    parts = where.split(".")
    if parts[0] == "categories" and len(parts) >= 2:
        named = W("setting.category", code=parts[1]) + (f" · {POLICY_KEY_TH.get(parts[2], parts[2])}" if len(parts) > 2 else "")
    else:
        named = POLICY_KEY_TH.get(parts[0], parts[0])
    return f"{named}: {rest}"


def no_policy(problems: list[str]) -> Finding:
    plain = [plain_problem(p) for p in problems]
    listed = safe("; ".join(plain[:4]) + (" " + W("card.policy.more", n=len(plain) - 4) if len(plain) > 4 else ""))
    return finding("expense_no_policy", "blocking", "preflight", W("card.policy.header"),
                   W("card.policy.question", listed=listed),
                   [(W("card.policy.continue"), "wait", W("card.policy.continue.d")),
                    (W("opt.cancel"), "cancel", W("opt.cancel.d"))], "wait", facts={"problems": problems[:10]})


def missing_claims(others: list[str]) -> Finding:
    return finding("missing_file", "blocking", "preflight", W("card.missing.header"),
                   W("card.missing.question_others", extra=W("card.missing.others", files=safe(", ".join(others[:3]))))
                   if others else W("card.missing.question"),
                   [(W("opt.wait"), "wait", W("opt.wait.d")),
                    (W("opt.cancel"), "cancel", W("opt.cancel.d"))], "wait", kind="request_files", subject="claims",
                   request=[{"role": "claims", "label": W("need.claims"),
                             "why": W("need.claims.why"),
                             "required": True, "accept": [".xlsx", ".xlsm", ".csv"]}],
                   facts={"missing": "claims"})
