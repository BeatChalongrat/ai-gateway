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

from ....engine.thai import fmt_money, period_label_th
from ..checks import cards as CARDS
from ..checks import review as RV
from ..checks import rules as R
from ..checks.receipts import SCAN_TIERS, index_entry

SCHEMA = "expense.audit.v1"

REASON_TH = {
    # rules → a person decides (severity review)
    "claim_row_unreadable": "อ่านวันที่ จำนวนเงิน หรือพนักงานในแถวนี้ไม่ได้",
    "unknown_category": "หมวดไม่อยู่ในตารางนโยบาย",
    "over_claim_limit": "เกินวงเงินต่อครั้ง",
    "over_period_limit": "เกินวงเงินต่อคนต่อเดือน",
    "missing_receipt": "ไม่มีใบเสร็จ ทั้งที่ยอดเกินเกณฑ์",
    "receipt_amount_mismatch": "ยอดในใบเสร็จไม่ตรงกับยอดที่เบิก",
    "receipt_unconfirmed": "ยืนยันยอดในใบเสร็จไม่ได้",
    "duplicate_claim": "เบิกซ้ำ",
    "split_purchase": "แยกบิลให้ต่ำกว่าวงเงินอนุมัติ",
    "prohibited_merchant": "ร้านค้าต้องห้ามตามนโยบาย",
    "vat_claim_without_full_tax_invoice": "ขอคืนภาษีซื้อโดยไม่มีใบกำกับภาษีเต็มรูป",
    "wht_missing": "ไม่ได้หักภาษี ณ ที่จ่าย",
    "wht_mismatch": "หักภาษี ณ ที่จ่ายไม่ตรงอัตรา",
    "missing_purpose": "ไม่ระบุวัตถุประสงค์ (หมวดนี้ต้องระบุ)",
    # judgement → a card; skipped / "ask the employee" → review, accepted / typed → info
    "weekend_or_holiday": "เบิกในวันหยุด",
    "vague_purpose": "วัตถุประสงค์ไม่ชัด",
    # about a file, not a claim (info)
    "receipt_not_linked": "ใบเสร็จที่ไม่มีรายการเบิกอ้างถึง",
}
assert set(R.EXCEPTION_CODES) | set(R.JUDGEMENT_CODES) <= set(REASON_TH)

_APPROVE = ("อนุมัติเป็นกรณียกเว้น", "approve_exception")
_ASK = ("ถามพนักงาน", "ask_employee")
_NOT_PAID = ("ไม่เบิกจ่ายรายการนี้", "exclude_from_reimbursement")      # a person's choice, never a default
CHOICES = {
    "claim_row_unreadable": [("แก้ไฟล์แล้วส่งใหม่", "fix_sheet"), _ASK, ("ตรวจรายการนี้เอง", "check_by_hand")],
    "unknown_category": [_ASK, ("เพิ่มหมวดในตารางนโยบาย", "update_policy"), _APPROVE],
    "over_claim_limit": [_APPROVE, ("เบิกได้เท่าวงเงิน", "reimburse_up_to_limit"), _ASK, _NOT_PAID],
    "over_period_limit": [_APPROVE, ("เบิกได้เท่าวงเงินที่เหลือ", "reimburse_up_to_limit"), _ASK, _NOT_PAID],
    "missing_receipt": [("ขอใบเสร็จจากพนักงาน", "request_receipt"), ("อนุมัติโดยใช้ใบรับรองแทนใบเสร็จ", "approve_with_substitute"),
                        _NOT_PAID],
    "receipt_amount_mismatch": [("เบิกตามยอดในใบเสร็จ", "reimburse_receipt_amount"), _ASK, _APPROVE],
    "receipt_unconfirmed": [("ตรวจใบเสร็จเองแล้วอนุมัติ", "approve_after_checking"), ("ขอใบเสร็จที่ชัดกว่า", "request_receipt"), _ASK],
    "duplicate_claim": [("ไม่ใช่รายการซ้ำ", "not_duplicate"), _ASK, ("จ่ายครั้งเดียว ไม่เบิกรายการซ้ำ", "pay_once")],
    "split_purchase": [("อนุมัติรวมเป็นรายการเดียว", "approve_as_one"), _ASK, _NOT_PAID],
    "prohibited_merchant": [_APPROVE, _ASK, _NOT_PAID],
    "vat_claim_without_full_tax_invoice": [("เบิกได้ แต่ไม่ขอคืนภาษีซื้อ", "reimburse_without_vat"),
                                           ("ขอใบกำกับภาษีเต็มรูปจากร้าน", "request_full_tax_invoice"), _ASK],
    "wht_missing": [("หักภาษี ณ ที่จ่ายก่อนจ่าย", "withhold_tax"), _ASK, _APPROVE],
    "wht_mismatch": [("ปรับยอดหักภาษี ณ ที่จ่าย", "withhold_tax"), _ASK, _APPROVE],
    "missing_purpose": [_ASK, _APPROVE, _NOT_PAID],
    "weekend_or_holiday": [("อนุมัติ เป็นงานของบริษัท", "approve_exception"), _ASK, _NOT_PAID],
    "vague_purpose": [("อนุมัติ วัตถุประสงค์ชัดพอ", "approve_exception"), _ASK, _NOT_PAID],
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
TIER_TH = {"digital": "PDF ดิจิทัล", "etax": "e-Tax (XML)", "scan": "สแกน", "photo": "รูปถ่าย (อ่านได้เท่าที่ภาพชัด)"}
OUTCOME_TH = {"pass": "ผ่าน", "exception": "ข้อยกเว้น รอคนตัดสิน", "question": "ต้องใช้วิจารณญาณ"}
RESOLUTION_TH = {"accepted": "ผู้ใช้ยืนยันว่ารับได้", "typed": "ผู้ใช้ระบุวัตถุประสงค์", "ask_employee": "ให้ถามพนักงาน",
                 "review": "ส่งให้ผู้อนุมัติตัดสิน"}


def _money(v) -> str:
    return fmt_money(v) if v not in (None, "") else "-"


def _proposed(code: str, a: R.ClaimAudit, f: R.Fact | None, res: str | None = None, typed: str | None = None) -> str:
    c, ev = a.claim, (f.evidence if f else {})
    label = a.category.label if a.category else c.category_raw
    if code == "weekend_or_holiday":
        what = " ".join(x for x in (ev.get("weekday") if c.date and c.date.weekday() >= 5 else "", ev.get("holiday") or "") if x)
        return {"accepted": "ผู้ใช้ยืนยันระหว่างตรวจว่าเป็นงานของบริษัท ไม่ต้องทำอะไรเพิ่ม",
                "ask_employee": f"ถามพนักงานว่าค่าใช้จ่ายวันที่ {R.date_th(c.date)} ({what}) เป็นงานอะไร แล้วตัดสิน"}.get(
            res, f"พิจารณาว่าค่าใช้จ่ายวันที่ {R.date_th(c.date)} ({what}) เป็นงานของบริษัทหรือไม่ — กฎบอกได้แค่ว่าเป็นวันหยุด")
    if code == "vague_purpose":
        return {"accepted": "ผู้ใช้ยืนยันระหว่างตรวจว่าวัตถุประสงค์ชัดพอ ไม่ต้องทำอะไรเพิ่ม",
                "typed": f"ผู้ใช้ระบุวัตถุประสงค์ระหว่างตรวจ: “{typed}”",
                "ask_employee": "ขอวัตถุประสงค์ทางธุรกิจของรายการนี้จากพนักงาน แล้วตัดสิน"}.get(
            res, f"พิจารณาวัตถุประสงค์ทางธุรกิจของรายการนี้ (รายละเอียดเขียนเพียง “{c.description}”)")
    return {
        "claim_row_unreadable": f"แก้แถว {c.row} ของ {c.sheet} ({f.detail if f else ''}) แล้วส่งใหม่ หรือตรวจรายการนี้เอง",
        "unknown_category": f"หมวด “{c.category_raw}” ไม่อยู่ในตารางนโยบาย: ถามพนักงานว่าเป็นหมวดใด หรือเพิ่มหมวดในตารางนโยบายก่อน",
        "over_claim_limit": f"ตัดสินว่าจะอนุมัติเกินวงเงิน{label}ต่อครั้ง {_money(ev.get('limit'))} บาท เป็นกรณียกเว้น "
                            f"หรือเบิกได้เท่าวงเงิน (เกิน {_money(ev.get('excess'))} บาท) — วงเงินจากตารางนโยบาย",
        "over_period_limit": f"ยอด{label}ของพนักงานในเดือน {ev.get('month')} รวม {_money(ev.get('after'))} บาท เกินวงเงิน "
                             f"{_money(ev.get('limit'))} บาท ตัดสินว่าจะอนุมัติส่วนเกินหรือเบิกได้เท่าที่เหลือ "
                             f"{_money(max(Decimal(0), Decimal(ev.get('limit') or 0) - Decimal(ev.get('before') or 0)))} บาท",
        "missing_receipt": "ขอใบเสร็จจากพนักงาน หรืออนุมัติโดยใช้ใบรับรองแทนใบเสร็จ",
        "receipt_amount_mismatch": f"เปิดใบเสร็จ {ev.get('receipt')} ดู: ยอดในใบเสร็จ {_money(ev.get('receipt_total'))} บาท "
                                   f"แต่เบิก {_money(ev.get('amount'))} บาท เลือกเบิกตามใบเสร็จ หรือถามพนักงาน",
        "receipt_unconfirmed": f"เปิดใบเสร็จ {ev.get('receipt')} ดูยอดเอง ({f.detail if f else ''}) แล้วตัดสิน",
        "duplicate_claim": f"ตรวจว่าเป็นค่าใช้จ่ายเดียวกับ{(' รายการ ' + ev['duplicate_of']) if ev.get('duplicate_of') else ''}"
                           f"{(' ใบเสร็จที่เบิกในรอบก่อน ' + str(ev['earlier_run'].get('name'))) if ev.get('earlier_run') else ''} "
                           f"หรือไม่ ถ้าซ้ำจริงให้จ่ายครั้งเดียว",
        "split_purchase": f"รายการ {', '.join(ev.get('group') or [])} รวม {_money(ev.get('sum'))} บาท เกินวงเงินอนุมัติ "
                          f"{_money(ev.get('limit'))} บาท ให้ผู้มีอำนาจอนุมัติตามยอดรวม",
        "prohibited_merchant": f"ร้านนี้ตรงกับรายชื่อร้านต้องห้าม (“{ev.get('word')}”) ให้ผู้อนุมัติตัดสินว่าจะอนุมัติเป็นกรณียกเว้นหรือไม่",
        "vat_claim_without_full_tax_invoice": "ขอคืนภาษีซื้อด้วยเอกสารนี้ไม่ได้: เบิกได้แต่ไม่ขอคืนภาษีซื้อ หรือขอใบกำกับภาษีเต็มรูปจากร้าน",
        "wht_missing": f"หักภาษี ณ ที่จ่าย {_money(ev.get('due'))} บาท ({ev.get('rate')}%) ก่อนจ่าย และออกหนังสือรับรองการหักภาษี (50 ทวิ)",
        "wht_mismatch": f"ปรับยอดหักภาษี ณ ที่จ่ายเป็น {_money(ev.get('due'))} บาท ({ev.get('rate')}%) จากที่หัก {_money(ev.get('deducted'))} บาท",
        "missing_purpose": f"ขอวัตถุประสงค์ทางธุรกิจจากพนักงานก่อนอนุมัติ (หมวด{label}ต้องระบุ)",
    }[code]


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


def _row(code: str, severity: str, a: R.ClaimAudit, f: R.Fact | None, context: list[dict], *, res: str | None = None,
         typed: str | None = None) -> dict:
    ref = _source_ref(a, f)
    if a.also_flagged and severity == "review" and code not in R.JUDGEMENT_CODES:
        ref["also_flagged"] = [{"code": j, "reason": REASON_TH[j], "detail": a.facts[_fact_of(j)].detail} for j in a.also_flagged]
    kb = _kb_for(code, a, context)
    if kb:
        ref["kb_context"] = kb
    if res:
        ref["answered"] = res
    return {"reason_code": code, "reason": REASON_TH[code], "severity": severity, "subject": a.claim.key,
            "source_ref": ref, "detail": f.detail if f else "", "proposed_action": _proposed(code, a, f, res, typed),
            "choices": [] if severity == "info" else [{"label": l, "value": v} for l, v in CHOICES[code]],
            "status": "open"}


def _fact_of(code: str) -> str:
    return {"weekend_or_holiday": "weekend_holiday", "vague_purpose": "purpose"}[code]


def compute(uploads, roles, period, *, answers=None, defaults=None, today=None, history=None, proposals=None) -> dict:
    answers = answers or {}
    rv = RV.review(uploads, defaults=defaults, answers=answers, today=today, history=history, proposals=proposals, final=True)
    if rv.policy is None:
        raise ValueError("ยังไม่มีตารางนโยบายการเบิกที่ใช้ได้ ตรวจไม่ได้โดยไม่เดาวงเงิน: " + "; ".join(rv.problems))
    if not rv.sheets:
        raise ValueError("ยังไม่มีไฟล์รายการเบิกค่าใช้จ่าย")
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
                       "detail": "", "proposed_action": f"ใบเสร็จ {r.up.name} ไม่มีรายการเบิกใดอ้างถึง ตรวจว่าลืมใส่ในไฟล์รายการเบิกหรือไม่",
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
    label = period_label_th(period)
    reasons = sorted({e["reason"] for e in run_ex if e["severity"] == "review"})
    fallback = {"summary": (f"ตรวจรายการเบิก {len(claims)} รายการ ยอดเบิกรวม {fmt_money(claimed)} บาท: ผ่าน {count['pass']} รายการ "
                            f"เป็นข้อยกเว้นรอคนตัดสิน {count['exception']} รายการ และต้องใช้วิจารณญาณ {count['question']} รายการ "
                            f"ไม่มีรายการใดถูกปฏิเสธอัตโนมัติ ทุกวงเงินมาจากตารางนโยบายของบริษัท"),
                "movements": [], "watch": reasons,
                "next_steps": ([f"ผู้อนุมัติตัดสินข้อยกเว้น {review_n} รายการในชีต “ข้อยกเว้น”"] if review_n else [])
                + (["ตรวจตารางนโยบายในหน้าตั้งค่า agent: ตอนนี้ยังเป็นตารางตัวอย่าง"] if p.source == "package_sample" else [])}
    quotable = {"claims": str(len(claims)), "pass": str(count["pass"]), "exception": str(count["exception"]),
                "question": str(count["question"]), "claimed_thb": fmt_money(claimed), "review": str(review_n)}
    cal = rv.calendar
    return {
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
        "reason_codes": REASON_TH, "assumptions": rv.assumptions,
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


def _citations(claims: list[dict], totals: dict) -> list[dict]:
    """How each amount of the summary sheet was made (AM4) — the provenance an answer to "why is this
    figure what it is" is built from: which claims it adds up, each with its own amount and outcome."""
    by = totals["claimed_by_outcome"]

    def part(c: dict) -> dict:
        who = c.get("employee_name") or c.get("employee_id") or ""
        return {"file": c.get("sheet") or "", "sheet": c.get("sheet_name") or "", "row": c.get("row"),
                "value": c.get("amount"), "note": f"{who} · {c.get('outcome_label') or ''}".strip(" ·")}

    def c(cid: str, label: str, value, formula: str, rows: list[dict], aliases: list[str]) -> dict:
        return {"id": cid, "label": label, "value": value, "formula": formula, "sources": [part(x) for x in rows],
                "left_out": [], "aliases": aliases, "script": "packages/expense_audit/scripts/audit.py"}

    with_amount = [x for x in claims if x.get("amount")]
    return [
        c("e1", "ยอดเบิกรวม (บาท)", totals["claimed"], "ผลรวมยอดเบิกของทุกรายการในไฟล์รายการเบิก ตามที่พนักงานกรอก",
          with_amount, ["ยอดเบิกรวม", "ยอดเบิก", "เบิกรวม"]),
        c("e2", "ยอดที่ผ่าน (บาท)", by["pass"], "ผลรวมยอดเบิกของรายการที่ผ่านทุกกฎในตารางนโยบาย",
          [x for x in with_amount if x["outcome"] == "pass"], ["ยอดที่ผ่าน", "ผ่าน"]),
        c("e3", "ยอดที่เป็นข้อยกเว้น (บาท)", by["exception"],
          "ผลรวมยอดเบิกของรายการที่ไม่ผ่านกฎข้อใดข้อหนึ่ง รอคนตัดสินในชีตข้อยกเว้น",
          [x for x in with_amount if x["outcome"] == "exception"], ["ข้อยกเว้น", "ไม่ผ่าน"]),
        c("e4", "ยอดที่ต้องใช้วิจารณญาณ (บาท)", by["question"],
          "ผลรวมยอดเบิกของรายการที่กฎตัดสินเองไม่ได้ จึงถามผู้ตรวจในการ์ดคำถาม",
          [x for x in with_amount if x["outcome"] == "question"], ["วิจารณญาณ", "ต้องใช้วิจารณญาณ"]),
    ]


def summarize(computed: dict) -> dict:
    t = computed["totals"]
    return {"period": computed["period"], "period_label": computed["period_label"], "claims": t["claims"], "pass": t["pass"],
            "exception": t["exception"], "question": t["question"], "claimed_thb": t["claimed"],
            "run_exceptions": t["run_exceptions"], "review": t["review"], "policy": computed["policy"]["source"],
            "models": [{"model": m["model"], "self_hosted": m["self_hosted"], "documents": len(m["documents"])}
                       for m in computed["models"]],
            "excluded_list": []}
