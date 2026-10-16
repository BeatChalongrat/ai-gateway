"""compute — every claim's facts and outcome, and the run exceptions a person decides.

Deterministic, Decimal only. The same review as preflight (checks/review.py) runs the rules
(checks/rules.py) over the claims, the policy table and the receipts; the answers to the cards
settle the judgement triggers. No model writes a number: every amount is the sheet's, the table's,
or a receipt's as E2 read it (or as the person typed or confirmed it).

computed.json (schema ``expense.audit.v1``):
  claims[]          one per sheet row: the row as written, every fact (pass / fail / skip, with the
                    evidence: policy_ref + limit from the table, the numbers compared, the receipt),
                    the outcome (pass / exception / question — there is no reject), the reason codes,
                    and how each judgement card was answered
  run_exceptions[]  what agent-runner stores in agent_market.agent_run_exceptions (status "open"):
                    reason_code, severity (review = a person must decide; info = recorded, a person
                    already decided or nothing to decide), subject (the claim, or the receipt file),
                    source_ref (claim, sheet, row, cell, receipt, page, box, printed text, the policy
                    entry and limit, the KB paragraph quoted as context), proposed_action (a Thai
                    sentence for the person), choices [{label, value}] — none pre-selected
  receipts[]        every receipt as read, with the provenance of its total
  policy            the table used (as stored), its fingerprint and source, the calendar, the KB
                    paragraphs as context (used_as_limit: false) and where they differ from the table
  totals            counts and baht by outcome; no "rejected" line — nothing is rejected here
  index_entries     the claimed receipts for the install's duplicate index (runner, on success)
  models[]          which model saw which receipt, and whether it runs in the organisation

Reason codes — stable; REASON_TH below has their Thai labels, CHOICES what a person can decide:
  rules (severity review): claim_row_unreadable · unknown_category · over_claim_limit ·
    over_period_limit · missing_receipt · receipt_amount_mismatch · receipt_unconfirmed ·
    duplicate_claim · split_purchase · prohibited_merchant · vat_claim_without_full_tax_invoice ·
    wht_missing · wht_mismatch · missing_purpose
  judgement (a card first; review when skipped or "ask the employee", info when the person accepted
    or typed the purpose): weekend_or_holiday · vague_purpose  (weekend_or_holiday is a rule when
    the policy's weekend_holiday is "exception")
  about a file (info): receipt_not_linked
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from ....engine import words
from ....engine.thai import fmt_money
from ..checks import cards as CARDS
from ..checks import review as RV
from ..checks import rules as R
from ..checks.receipts import SCAN_TIERS, index_entry
from ..words import W

SCHEMA = "expense.audit.v1"

# every reason's words are a sentence of the manifest ("reason.<code>"), said in the language of the run when read
REASON_TH = W.table({code: f"reason.{code}" for code in (
    # rules → a person decides (severity review)
    "claim_row_unreadable", "unknown_category", "over_claim_limit", "over_period_limit", "missing_receipt",
    "receipt_amount_mismatch", "receipt_unconfirmed", "duplicate_claim", "split_purchase", "prohibited_merchant",
    "vat_claim_without_full_tax_invoice", "wht_missing", "wht_mismatch", "missing_purpose",
    # judgement → a card; skipped / "ask the employee" → review, accepted / typed → info
    "weekend_or_holiday", "vague_purpose",
    # about a file, not a claim (info)
    "receipt_not_linked")})
assert set(R.EXCEPTION_CODES) | set(R.JUDGEMENT_CODES) <= set(REASON_TH)

# what a person can decide: (the key of the answer's words, its value)
_APPROVE = ("choice.approve", "approve_exception")
_ASK = ("choice.ask", "ask_employee")
_NOT_PAID = ("choice.not_paid", "exclude_from_reimbursement")      # a person's choice, never a default
CHOICES = {
    "claim_row_unreadable": [("choice.fix_sheet", "fix_sheet"), _ASK, ("choice.by_hand", "check_by_hand")],
    "unknown_category": [_ASK, ("choice.update_policy", "update_policy"), _APPROVE],
    "over_claim_limit": [_APPROVE, ("choice.up_to_limit", "reimburse_up_to_limit"), _ASK, _NOT_PAID],
    "over_period_limit": [_APPROVE, ("choice.up_to_rest", "reimburse_up_to_limit"), _ASK, _NOT_PAID],
    "missing_receipt": [("choice.request_receipt", "request_receipt"), ("choice.substitute", "approve_with_substitute"),
                        _NOT_PAID],
    "receipt_amount_mismatch": [("choice.receipt_amount", "reimburse_receipt_amount"), _ASK, _APPROVE],
    "receipt_unconfirmed": [("choice.after_checking", "approve_after_checking"), ("choice.clearer_receipt", "request_receipt"), _ASK],
    "duplicate_claim": [("choice.not_duplicate", "not_duplicate"), _ASK, ("choice.pay_once", "pay_once")],
    "split_purchase": [("choice.as_one", "approve_as_one"), _ASK, _NOT_PAID],
    "prohibited_merchant": [_APPROVE, _ASK, _NOT_PAID],
    "vat_claim_without_full_tax_invoice": [("choice.without_vat", "reimburse_without_vat"),
                                           ("choice.full_tax_invoice", "request_full_tax_invoice"), _ASK],
    "wht_missing": [("choice.withhold", "withhold_tax"), _ASK, _APPROVE],
    "wht_mismatch": [("choice.fix_wht", "withhold_tax"), _ASK, _APPROVE],
    "missing_purpose": [_ASK, _APPROVE, _NOT_PAID],
    "weekend_or_holiday": [("choice.approve.work", "approve_exception"), _ASK, _NOT_PAID],
    "vague_purpose": [("choice.approve.purpose", "approve_exception"), _ASK, _NOT_PAID],
}
# KB paragraphs quoted next to an exception: the claim's category, or the rule's topic
TOPICS = {"weekend_or_holiday": ("วันหยุด",), "missing_receipt": ("ใบเสร็จ",), "receipt_amount_mismatch": ("ใบเสร็จ",),
          "vat_claim_without_full_tax_invoice": ("ภาษีซื้อ", "ใบกำกับภาษี"), "wht_missing": ("หักณที่จ่าย",),
          "wht_mismatch": ("หักณที่จ่าย",), "split_purchase": ("แยกบิล", "แบ่งจ่าย"), "prohibited_merchant": ("ต้องห้าม", "สถานบันเทิง"),
          "vague_purpose": ("วัตถุประสงค์",), "missing_purpose": ("วัตถุประสงค์",), "duplicate_claim": ("ซ้ำ",)}
# the rules about a category's own terms: a KB paragraph about that category is their context
CATEGORY_CODES = ("over_claim_limit", "over_period_limit", "missing_purpose", "vague_purpose", "prohibited_merchant",
                  "unknown_category", "wht_missing", "wht_mismatch")
# the sheet cell each fact is about
FACT_CELL = {"claim_data": "amount", "category": "category", "within_claim_limit": "amount", "within_period_limit": "amount",
             "receipt": "receipt", "receipt_amount": "amount", "duplicate": "id", "split_purchase": "amount",
             "prohibited_merchant": "merchant", "vat_document": "vat_claim", "wht": "wht_deducted",
             "weekend_holiday": "date", "purpose": "description"}
TIER_TH = W.table({k: f"tier.{k}" for k in ("digital", "etax", "scan", "photo")})
OUTCOME_TH = W.table({k: f"outcome.{k}" for k in R.OUTCOMES})
RESOLUTION_TH = W.table({"accepted": "doc.resolution.accepted", "typed": "doc.resolution.typed", "ask_employee": "doc.resolution.ask_employee",
                         "review": "doc.resolution.review"})


def _money(v) -> str:
    return fmt_money(v) if v not in (None, "") else "-"


def _proposed(code: str, a: R.ClaimAudit, f: R.Fact | None, res: str | None = None, typed: str | None = None) -> str:
    c, ev = a.claim, (f.evidence if f else {})
    label = a.category.label if a.category else c.category_raw
    if code == "weekend_or_holiday":
        what = " ".join(x for x in (ev.get("weekday") if c.date and c.date.weekday() >= 5 else "", ev.get("holiday") or "") if x)
        return {"accepted": W("do.weekend.accepted"),
                "ask_employee": W("do.weekend.ask", date=R.date_th(c.date), what=what)}.get(
            res, W("do.weekend.review", date=R.date_th(c.date), what=what))
    if code == "vague_purpose":
        return {"accepted": W("do.purpose.accepted"),
                "typed": W("do.purpose.typed", typed=typed),
                "ask_employee": W("do.purpose.ask")}.get(
            res, W("do.purpose.review", description=c.description))
    return {
        "claim_row_unreadable": W("do.claim_row_unreadable", row=c.row, sheet=c.sheet, detail=f.detail if f else ""),
        "unknown_category": W("do.unknown_category", category_raw=c.category_raw),
        "over_claim_limit": W("do.over_claim_limit", label=label, limit=_money(ev.get("limit")), excess=_money(ev.get("excess"))),
        "over_period_limit": W("do.over_period_limit", label=label, month=ev.get("month"), after=_money(ev.get("after")),
                               limit=_money(ev.get("limit")),
                               left=_money(max(Decimal(0), Decimal(ev.get("limit") or 0) - Decimal(ev.get("before") or 0)))),
        "missing_receipt": W("do.missing_receipt"),
        "receipt_amount_mismatch": W("do.receipt_amount_mismatch", receipt=ev.get("receipt"),
                                     receipt_total=_money(ev.get("receipt_total")), amount=_money(ev.get("amount"))),
        "receipt_unconfirmed": W("do.receipt_unconfirmed", receipt=ev.get("receipt"), detail=f.detail if f else ""),
        "duplicate_claim": _same_expense(ev),
        "split_purchase": W("do.split_purchase", group=", ".join(ev.get("group") or []), sum=_money(ev.get("sum")), limit=_money(ev.get("limit"))),
        "prohibited_merchant": W("do.prohibited_merchant", word=ev.get("word")),
        "vat_claim_without_full_tax_invoice": W("do.vat"),
        "wht_missing": W("do.wht_missing", due=_money(ev.get("due")), rate=ev.get("rate")),
        "wht_mismatch": W("do.wht_mismatch", due=_money(ev.get("due")), rate=ev.get("rate"), deducted=_money(ev.get("deducted"))),
        "missing_purpose": W("do.missing_purpose", label=label),
    }[code]


def _same_expense(ev: dict) -> str:
    """What to do about a duplicate: which claim, which receipt of an earlier run, or both, it repeats."""
    first, earlier = ev.get("duplicate_of"), ev.get("earlier_run")
    if first and earlier:
        return W("do.duplicate.both", first=first, name=str(earlier.get("name")))
    if first:
        return W("do.duplicate.claim", first=first)
    if earlier:
        return W("do.duplicate.earlier", name=str(earlier.get("name")))
    return W("do.duplicate")


def _kb_for(code: str, a: R.ClaimAudit, context: list[dict]) -> list[dict]:
    cat = a.category.code if a.category else None
    words = TOPICS.get(code, ())
    hits = [p for p in context if cat and code in CATEGORY_CODES and cat in p["categories"]]
    hits += [p for p in context if p not in hits and any(w in "".join(p["text"].split()) for w in words)]
    return [{"file": p["file"], "paragraph": p["paragraph"], "text": p["text"], "numbers": p["numbers"],
             "used_as_limit": False} for p in hits]


def _source_ref(a: R.ClaimAudit, f: R.Fact | None) -> dict:
    c = a.claim
    ref = {"claim": c.label, "claim_key": c.key, "sheet": c.sheet, "sheet_name": c.sheet_name, "row": c.row,
           "cells": c.cells, "receipt": a.receipt.name if a.receipt else None,
           "receipt_sha256": a.receipt.sha256 if a.receipt else None}
    if a.receipt is not None:
        ts = a.receipt.total_source
        ref.update({"page": ts.get("page"), "bbox": ts.get("bbox"), "raw_text": ts.get("raw_text")})
    if f is not None:
        ref.update({k: v for k, v in f.evidence.items() if k not in ref or v is not None})
        ref["fact"] = f.name
        ref["cell"] = c.cells.get(FACT_CELL.get(f.name, "")) or c.cells.get("amount")
    return ref


def _head(text: str) -> str:
    """A fact on a line of its own. A fact is also said inside a sentence, so its words are kept without a capital;
    Thai has none, and a Thai line is left as it is."""
    return text if words.lang() == "th" else words.cap(text)


def _row(code: str, severity: str, a: R.ClaimAudit, f: R.Fact | None, context: list[dict], *, res: str | None = None,
         typed: str | None = None) -> dict:
    ref = _source_ref(a, f)
    if a.also_flagged and severity == "review" and code not in R.JUDGEMENT_CODES:
        ref["also_flagged"] = [{"code": j, "reason": REASON_TH[j], "detail": _head(a.facts[_fact_of(j)].detail)}
                               for j in a.also_flagged]
    kb = _kb_for(code, a, context)
    if kb:
        ref["kb_context"] = kb
    if res:
        ref["answered"] = res
    return {"reason_code": code, "reason": REASON_TH[code], "severity": severity, "subject": a.claim.key,
            "source_ref": ref, "detail": _head(f.detail) if f else "", "proposed_action": _proposed(code, a, f, res, typed),
            "choices": [] if severity == "info" else [{"label": W(l), "value": v} for l, v in CHOICES[code]],
            "status": "open"}


def _fact_of(code: str) -> str:
    return {"weekend_or_holiday": "weekend_holiday", "vague_purpose": "purpose"}[code]


def compute(uploads, roles, period, *, answers=None, defaults=None, today=None, history=None, proposals=None) -> dict:
    answers = answers or {}
    rv = RV.review(uploads, defaults=defaults, answers=answers, today=today, history=history, proposals=proposals, final=True)
    if rv.policy is None:
        raise ValueError(W("say.no_policy") + " " + "; ".join(rv.problems))
    if not rv.sheets:
        raise ValueError(W("say.no_claims"))
    claims, run_ex = [], []
    for a in rv.audits:
        c = a.claim
        resolution, purpose = {}, None
        for flag in a.flags:
            res, typed = CARDS.resolution(answers.get(CARDS.key_for(flag, c.key)))
            if flag != "vague_purpose" and res == "typed":
                res = "review"
            resolution[flag] = res
            if res == "typed":
                purpose = typed
            f = a.facts[_fact_of(flag)]
            run_ex.append(_row(flag, "info" if res in ("accepted", "typed") else "review", a, f, rv.context, res=res, typed=typed))
        for code in a.codes:
            f = next(x for x in a.facts.values() if x.code == code and x.status == "fail")
            run_ex.append(_row(code, "review", a, f, rv.context))
        claims.append({
            "key": c.key, "id": c.id, "row": c.row, "sheet": c.sheet, "sheet_name": c.sheet_name, "cells": c.cells,
            "employee_id": c.employee_id, "employee_name": c.employee_name,
            "date": c.date.isoformat() if c.date else None, "date_raw": c.date_raw,
            "weekday": R.WEEKDAY_TH[c.date.weekday()] if c.date else None,
            "category": a.category.code if a.category else None, "category_label": a.category.label if a.category else None,
            "category_raw": c.category_raw, "merchant": c.merchant,
            "amount": f"{c.amount:.2f}" if c.amount is not None else None, "amount_raw": c.amount_raw,
            "receipt_ref": c.receipt_ref, "receipt": a.receipt.name if a.receipt else None,
            "receipt_total": f"{a.receipt.total:.2f}" if a.receipt and a.receipt.total is not None and a.receipt.status == "read" else None,
            "vat_claim": c.vat_claim, "wht_deducted": f"{c.wht_deducted:.2f}" if c.wht_deducted is not None else None,
            "description": c.description, "purpose": purpose,
            "facts": {n: f.to_dict() for n, f in a.facts.items()},
            "outcome": a.outcome, "outcome_label": OUTCOME_TH[a.outcome], "codes": list(a.codes), "flags": list(a.flags),
            "also_flagged": list(a.also_flagged), "resolution": resolution,
        })
    for r in rv.unlinked:
        run_ex.append({"reason_code": "receipt_not_linked", "reason": REASON_TH["receipt_not_linked"], "severity": "info",
                       "subject": r.up.name, "source_ref": {"receipt": r.up.name, "receipt_sha256": r.up.sha256},
                       "detail": "", "proposed_action": W("do.receipt_not_linked", name=r.up.name),
                       "choices": [], "status": "open"})
    receipts, index = [], []
    linked = {k for a in rv.audits if a.receipt for k in [a.receipt.key]}
    # content order — the receipt's own date and number, then its bytes' hash — never its file name (AM4)
    for k, r in sorted(rv.receipts.items(), key=lambda kv: (rv.views[kv[0]].document_date or "",
                                                           rv.views[kv[0]].document_number or "", kv[0])):
        v = rv.views[k]
        f = r.out.get("fields") or {}
        receipts.append({"doc_key": k, "name": r.up.name, "names": v.names, "sha256": r.up.sha256, "tier": v.tier,
                         "tier_label": TIER_TH.get(v.tier, v.tier), "method": r.out["method"], "document_type": v.document_type,
                         "document_type_label": R.DOC_TH.get(v.document_type, v.document_type), "status": v.status,
                         "note": v.unconfirmed_reason, "total": f"{v.total:.2f}" if v.total is not None else None,
                         "total_source": v.total_source, "document_date": v.document_date, "document_number": v.document_number,
                         "seller_name": v.seller_name, "seller_tax_id": v.seller_tax_id,
                         "vat_amount": (f.get("vat_amount") or {}).get("normalized"),
                         "claims": [a.claim.label for a in rv.audits if a.receipt and a.receipt.key == k],
                         "answered": r.dec.answered if r.dec else 0})
        if k in linked:
            index.append(index_entry(r, v))
    models: dict[str, dict] = {}
    for doc_key, p in (proposals or {}).items():
        r = rv.receipts.get(doc_key)
        if not isinstance(p, dict) or not p.get("model") or r is None or r.up.tier not in SCAN_TIERS:
            continue
        m = models.setdefault(p["model"], {"model": p["model"], "self_hosted": bool(p.get("self_hosted")), "documents": []})
        m["documents"].append(r.up.name)
    count = {o: sum(1 for c in claims if c["outcome"] == o) for o in R.OUTCOMES}
    amount = {o: sum((Decimal(c["amount"]) for c in claims if c["outcome"] == o and c["amount"]), Decimal(0)) for o in R.OUTCOMES}
    claimed = sum(amount.values(), Decimal(0))
    review_n = sum(1 for e in run_ex if e["severity"] == "review")
    by_emp: dict[str, dict] = {}
    for c in claims:
        k = c["employee_id"] or c["employee_name"] or "-"
        e = by_emp.setdefault(k, {"employee_id": c["employee_id"], "employee_name": c["employee_name"], "claims": 0,
                                  "claimed": Decimal(0), "exception": 0, "question": 0})
        e["claims"] += 1
        e["claimed"] += Decimal(c["amount"]) if c["amount"] else Decimal(0)
        e["exception"] += c["outcome"] == "exception"
        e["question"] += c["outcome"] == "question"
    totals = {"claims": len(claims), **count, "claimed": f"{claimed:.2f}",
              "claimed_by_outcome": {o: f"{v:.2f}" for o, v in amount.items()},
              "run_exceptions": len(run_ex), "review": review_n, "info": len(run_ex) - review_n,
              "receipts": len(receipts), "judgement_cards": sum(len(a.flags) for a in rv.audits)}
    p = rv.policy
    label = words.period(period)
    reasons = sorted({e["reason"] for e in run_ex if e["severity"] == "review"})
    fallback = {"summary": W("say.summary", n=len(claims), claimed=fmt_money(claimed), pass_=count["pass"],
                             exception=count["exception"], question=count["question"]),
                "movements": [], "watch": _capped(reasons),
                "next_steps": ([W("say.next.review", review_n=review_n)] if review_n else [])
                + ([W("say.next.sample_table")] if p.source == "package_sample" else [])}
    quotable = {"claims": str(len(claims)), "pass": str(count["pass"]), "exception": str(count["exception"]),
                "question": str(count["question"]), "claimed_thb": fmt_money(claimed), "review": str(review_n),
                "pass_thb": fmt_money(amount["pass"]), "exception_thb": fmt_money(amount["exception"]), "question_thb": fmt_money(amount["question"])}
    cal = rv.calendar
    # AM22 §1.1: the figures a sentence of the commentary may name, each with its own citation; the facts a
    # model is shown, with the figures by key; the layouts' limits for the words, in the run's language
    from ....deliverables.look.catalogue import writing_limits
    from ....engine import figures as F
    figures = F.table([
        ("claimed_thb", quotable["claimed_thb"], "e1", W("cite.claimed.label")), ("pass_thb", quotable["pass_thb"], "e2", W("cite.pass.label")),
        ("exception_thb", quotable["exception_thb"], "e3", W("cite.exception.label")), ("question_thb", quotable["question_thb"], "e4", W("cite.question.label")),
        ("claims", quotable["claims"], "n1", W("cite.claims_n.label")), ("pass", quotable["pass"], "n2", W("cite.pass_n.label")),
        ("exception", quotable["exception"], "n3", W("cite.exception_n.label")), ("question", quotable["question"], "n4", W("cite.question_n.label")),
        ("review", quotable["review"], "n5", W("cite.review_n.label"))])
    facts = F.facts(
        (W("fact.h.result"), [W("fact.exp.counts", claims="{claims}", pass_="{pass}", exception="{exception}", question="{question}"),
                              W("fact.exp.amounts", claimed="{claimed_thb}", pass_="{pass_thb}", exception="{exception_thb}", question="{question_thb}"),
                              W("fact.exp.review", review="{review}")]),
        (W("fact.h.reasons"), [W("fact.exp.reason", reason=r) for r in reasons]),
        (W("fact.h.policy"), [W("fact.exp.policy.shipped") if p.source == "package_sample" else W("fact.exp.policy.set"), _calendar_fact(cal)]))
    return {
        "figures": figures, "narrative_facts": facts, "writing_limits": writing_limits("xlsx"),
        "schema": SCHEMA, "period": period, "period_label": label,
        "prepared_on": (today or _dt.date.today()).isoformat(), "company": (defaults or {}).get("company") or "",
        "claims": claims, "run_exceptions": run_ex, "receipts": receipts,
        "policy": {"source": p.source, "sha256": p.sha256, "table": p.table,
                   "calendar": {"source": cal.source, "note": cal.note, "active": cal.active, "reason": cal.reason,
                                "confirmed_by": cal.confirmed_by, "confirmed_at": cal.confirmed_at,
                                "dates": {d.isoformat(): n for d, n in sorted(cal.dates.items())
                                          if any(c.date and (c.date.year, c.date.month) == (d.year, d.month) for c in rv.claims)}},
                   "context": rv.context, "conflicts": rv.conflicts},
        "totals": totals,
        "by_employee": [{**e, "claimed": f"{e['claimed']:.2f}"} for e in by_emp.values()],
        "reason_codes": dict(REASON_TH), "assumptions": rv.assumptions,
        "index_entries": index, "models": [m for m in models.values() if m["documents"]],
        "quotable": quotable, "quotable_ids": [], "fallback_narrative": fallback,
        # the runner's generic view of computed.json
        "exceptions": run_ex, "excluded": [], "lines": [], "ratios": [], "top_movements": [],
        "citations": _citations(claims, totals),
        "source": {"claims": [{"name": s.name, "sha256": s.sha256, "sheet": s.data["sheet"]} for s in rv.sheets],
                   "receipts": [{"name": r["name"], "sha256": r["sha256"], "tier": r["tier"]} for r in receipts],
                   "policy_documents": [{"name": u.name, "sha256": u.sha256, "used_as": "context"} for u in rv.kb]},
        "script": "packages/expense_audit/scripts/audit.py",
    }


def _calendar_fact(cal) -> str:
    """What the model is told of the holiday calendar: whether public holidays were checked at all."""
    return W("fact.exp.calendar.on") if getattr(cal, "active", False) else W("fact.exp.calendar.off")


def _citations(claims: list[dict], totals: dict) -> list[dict]:
    """How each amount of the summary sheet was made (AM4) — the provenance an answer to "why is this
    figure what it is" is built from: which claims it adds up, each with its own amount and outcome."""
    by = totals["claimed_by_outcome"]

    def part(c: dict) -> dict:
        who = c.get("employee_name") or c.get("employee_id") or ""
        return {"file": c.get("sheet") or "", "sheet": c.get("sheet_name") or "", "row": c.get("row"),
                "value": c.get("amount"), "note": f"{who} · {c.get('outcome_label') or ''}".strip(" ·")}

    def c(cid: str, key: str, value, rows: list[dict], aliases: list[str]) -> dict:
        # a figure is asked about by its name in the run's language — and, where the workbook is not in it, by the
        # names the workbook gives it (engine/words.names)
        label, names = words.names(lambda: (W(f"cite.{key}.label"), [W(x) for x in aliases]))
        return {"id": cid, "label": label, "value": value, "formula": W(f"cite.{key}"), "sources": [part(x) for x in rows],
                "left_out": [], "aliases": names, "script": "packages/expense_audit/scripts/audit.py"}

    with_amount = [x for x in claims if x.get("amount")]
    # AM22: the counts a commentary names, so that a sentence can point to them too
    by_outcome = {o: [x for x in claims if x["outcome"] == o] for o in ("pass", "exception", "question")}
    return [
        c("n1", "claims_n", str(totals["claims"]), claims, []),
        c("n2", "pass_n", str(totals["pass"]), by_outcome["pass"], []),
        c("n3", "exception_n", str(totals["exception"]), by_outcome["exception"], []),
        c("n4", "question_n", str(totals["question"]), by_outcome["question"], []),
        c("n5", "review_n", str(totals["review"]), [], []),
        c("e1", "claimed", totals["claimed"], with_amount, ["cite.claimed.a1", "cite.claimed.a2", "cite.claimed.a3"]),
        c("e2", "pass", by["pass"], [x for x in with_amount if x["outcome"] == "pass"], ["cite.pass.a1", "outcome.pass"]),
        c("e3", "exception", by["exception"], [x for x in with_amount if x["outcome"] == "exception"],
          ["cite.exception.a1", "cite.exception.a2"]),
        c("e4", "question", by["question"], [x for x in with_amount if x["outcome"] == "question"],
          ["cite.question.a1", "outcome.question"]),
    ]


def summarize(computed: dict) -> dict:
    t = computed["totals"]
    return {"period": computed["period"], "period_label": computed["period_label"], "claims": t["claims"], "pass": t["pass"],
            "exception": t["exception"], "question": t["question"], "claimed_thb": t["claimed"],
            "run_exceptions": t["run_exceptions"], "review": t["review"], "policy": computed["policy"]["source"],
            "models": [{"model": m["model"], "self_hosted": m["self_hosted"], "documents": len(m["documents"])}
                       for m in computed["models"]],
            "excluded_list": []}


def _capped(lines: list) -> list:
    """AM22: the rules' list under one heading, at most the commentary sheet's ceiling; more is said, never cut."""
    from ....engine import figures as F

    return F.capped_rules(lines)
