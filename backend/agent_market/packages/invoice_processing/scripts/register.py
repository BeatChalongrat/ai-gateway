"""compute — the documents of a run as a register, with every value's provenance and every exception.

Deterministic, Decimal only. The same review as preflight (checks/review.py) decides which
documents are recorded; the numbers are the E2 values the documents printed (or the person typed
or confirmed), never a model's. A foreign-currency document is converted at the rate the person
typed, rounded to the satang, and the rate is written next to it.

computed.json (schema ``invoice.register.v1``):
  documents[]   one row per upload: status included / excluded, the header values, THB amounts
  line_items[]  the lines of the recorded documents (photos have none: triage only)
  provenance[]  every recorded value: where it came from (page, box, method, printed text), its
                confidence, the checks it passed, and who decided it (document / person / computed)
  exceptions[]  reason-coded: why a document was left out, or what was recorded about one kept in
  checks[]      each document's deterministic checks
  totals        counts and THB sums of the recorded documents
  index_entries what the duplicate index keeps for the recorded documents (written by the runner
                only when the run succeeds)
  models[]      which model saw which documents, and whether it runs in the organisation
"""

from __future__ import annotations

import datetime as _dt
from decimal import ROUND_HALF_UP, Decimal

from ....engine.e2 import cards
from ....engine.e2.schema import FIELD_KINDS, MONEY_FIELDS, ORDINARY_THRESHOLD, STRICT_THRESHOLD
from ....engine.thai import fmt_money, period_label_th
from ..checks import review as R

SCHEMA = "invoice.register.v1"
Q2 = Decimal("0.01")

DOC_TYPE_TH = {
    "full_tax_invoice": "ใบกำกับภาษีเต็มรูป", "abbreviated_tax_invoice": "ใบกำกับภาษีอย่างย่อ", "receipt": "ใบเสร็จรับเงิน",
    "invoice": "ใบแจ้งหนี้", "credit_note": "ใบลดหนี้", "debit_note": "ใบเพิ่มหนี้", "wht_certificate": "หนังสือรับรองหัก ณ ที่จ่าย",
    "quotation": "ใบเสนอราคา", "unknown": "ไม่ทราบชนิด",
}
TIER_TH = {"digital": "PDF ดิจิทัล", "etax": "e-Tax (XML)", "scan": "สแกน", "photo": "รูปถ่าย (best-effort)"}

# reason codes (the exceptions sheet and the report list them with these words)
REASON_TH = {
    "same_file_twice": "ไฟล์เดียวกันส่งมาซ้ำ",
    "duplicate": "เอกสารซ้ำกับที่เคยบันทึก",
    "missing_required_field": "ขาดข้อมูลที่เอกสารชนิดนี้ต้องมี",
    "unconfirmed_value": "ค่าที่อ่านได้ไม่มั่นใจ และไม่ได้ยืนยัน",
    "sources_disagree": "สองแหล่งอ่านได้ไม่ตรงกัน",
    "check_failed": "ตรวจตัวเลขไม่ผ่าน",
    "copy_not_original": "เอกสารเป็นสำเนา",
    "unknown_document_type": "ไม่ทราบชนิดเอกสาร",
    "unreadable_page": "ภาพไม่ชัด",
    "typed_value_invalid": "ค่าที่พิมพ์มาใช้ไม่ได้",
    "unresolved_question": "ยังมีคำถามที่ไม่ได้ตอบ",
    "excluded_by_person": "ผู้ใช้ตัดออก",
    "accepted_as_printed": "บันทึกตามที่พิมพ์ แม้ตรวจไม่ผ่าน",
    "field_absent": "เอกสารไม่ได้พิมพ์ค่าที่ควรมี",
    "foreign_currency": "เงินตราต่างประเทศ แปลงด้วยอัตราที่ผู้ใช้ระบุ",
    "not_duplicate_by_person": "คล้ายเอกสารเดิม แต่ผู้ใช้ยืนยันว่าคนละฉบับ",
    "photo_triage_only": "รูปถ่าย: บันทึกเฉพาะผู้ขาย วันที่ ยอดรวม VAT และเลขผู้เสียภาษี",
    "line_items_unverified": "รายการสินค้าบางบรรทัดอ่านได้ไม่มั่นใจ ยอดหัวเอกสารยังถูกต้อง",
}
KEEP_NOTES = ("accepted_as_printed", "field_absent", "foreign_currency", "not_duplicate_by_person", "copy_not_original")
REGISTER_FIELDS = ("seller_name", "seller_tax_id", "seller_branch", "buyer_name", "buyer_tax_id", "buyer_branch",
                   "document_number", "document_date", "due_date", "currency", "sum_lines", "discount", "subtotal",
                   "vat_amount", "total", "wht_amount", "net_payable")


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _q2(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(Q2, rounding=ROUND_HALF_UP)


def _s(v) -> str | None:
    return None if v is None else str(v)


def _display(name: str, f: dict):
    n = f.get("normalized")
    if n is None:
        return None
    if FIELD_KINDS.get(name) == "party":
        return f.get("value") or (n.get("core") if isinstance(n, dict) else str(n))
    return n


def decided_by(f: dict) -> str:
    src = f.get("source") or {}
    checks = f.get("checks") or []
    m = src.get("method") or ""
    if m in ("typed_by_person", "confirmed_by_person"):
        return m
    if "confirmed_by_person" in checks:
        return "confirmed_by_person"
    if not src and f.get("candidates"):
        return "computed" if any(str(c.get("method", "")).startswith(("computed", "default", "document_scan"))
                                 for c in f["candidates"]) else "model_proposal"
    if any(c in checks for c in ("derived_from_sum_lines", "no_vat_base_is_total", "computed_from_lines")):
        return "computed"
    return "document"


def _method(f: dict) -> str:
    src = f.get("source") or {}
    if src.get("method"):
        return src["method"]
    return next((c.get("method") for c in f.get("candidates") or []), "") or ""


def compute(docs: list[R.Doc], roles: dict, period: str, *, answers: dict | None = None, defaults: dict | None = None,
            today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None) -> dict:
    reviewed = R.review(docs, answers=answers, defaults=defaults, today=today, history=history, proposals=proposals)
    documents, lines, prov, exceptions, checks, index = [], [], [], [], [], []
    totals = {"subtotal": Decimal(0), "vat_amount": Decimal(0), "total": Decimal(0), "wht_amount": Decimal(0),
              "net_payable": Decimal(0)}
    by_currency: dict[str, dict] = {}
    for r in reviewed:
        doc, out = r.doc, r.out
        f = out["fields"]
        tier = out["document"]["tier"]
        status = r.status if r.status != "pending" else "excluded"
        exclusion = r.exclusion
        if r.status == "pending":
            exclusion = {"code": "unresolved_question", "decided_by": "rule",
                         "detail": "; ".join(x.evidence.get("question") or x.header for x in r.findings)[:400]}
        dtype = out["document_type"]["normalized"] or "unknown"
        row = {"doc_key": doc.key, "name": doc.name, "status": status, "tier": tier, "tier_label": TIER_TH.get(tier, tier),
               "method": out["method"], "document_type": dtype, "document_type_label": DOC_TYPE_TH.get(dtype, dtype),
               "copy_mark": out["document"]["copy_mark"], "vat_mode": out["document"]["vat_mode"],
               "triage_only": out["document"]["triage_only"], "sha256": doc.sha256, "answered": r.dec.answered,
               "fx_rate": _s(r.dec.fx_rate), "exclusion": exclusion}
        for n in REGISTER_FIELDS:
            v = _display(n, f.get(n) or {})
            row[n] = v if not isinstance(v, (dict, list)) else str(v)
        currency = (f.get("currency") or {}).get("normalized") or "THB"
        rate = Decimal(1) if currency == "THB" else r.dec.fx_rate
        row["currency"] = currency
        for n in totals:
            v = _dec((f.get(n) or {}).get("normalized"))
            row[f"thb_{n}"] = _s(_q2(v * rate)) if v is not None and rate is not None else None
        documents.append(row)
        checks += [{"doc_key": doc.key, "name": doc.name, **c} for c in out["deterministic_checks"]]
        if status == "excluded":
            others = [REASON_TH.get(c, c) for c in exclusion.get("codes", []) if c != exclusion["code"]]
            detail = "; ".join(x for x in (exclusion.get("detail", ""), ("และ: " + ", ".join(others)) if others else "") if x)
            exceptions.append({"doc_key": doc.key, "name": doc.name, "code": exclusion["code"],
                               "reason": REASON_TH.get(exclusion["code"], exclusion["code"]), "detail": detail,
                               "codes": exclusion.get("codes", [exclusion["code"]]),
                               "severity": "excluded", "decided_by": exclusion.get("decided_by", "rule"),
                               "subject": exclusion.get("subject")})
            continue
        # recorded
        index.append(R.index_entry(doc, out))
        for n in totals:
            v = row[f"thb_{n}"]
            if v is not None:
                totals[n] += Decimal(v)
        cur = by_currency.setdefault(currency, {"documents": 0, "total": Decimal(0), "vat_amount": Decimal(0)})
        cur["documents"] += 1
        for n in ("total", "vat_amount"):
            v = _dec((f.get(n) or {}).get("normalized"))
            if v is not None:
                cur[n] += v
        for note in r.dec.notes:
            if note["code"] in KEEP_NOTES:
                exceptions.append({"doc_key": doc.key, "name": doc.name, "code": note["code"],
                                   "reason": REASON_TH.get(note["code"], note["code"]), "detail": note.get("detail", ""),
                                   "severity": "recorded", "decided_by": "person", "subject": note.get("subject")})
        if out["document"]["triage_only"]:
            exceptions.append({"doc_key": doc.key, "name": doc.name, "code": "photo_triage_only",
                               "reason": REASON_TH["photo_triage_only"], "detail": out["document"].get("notice") or "",
                               "severity": "recorded", "decided_by": "rule", "subject": None})
        weak = [it["row"] for it in out["line_items"]
                if (it.get("amount") or {}).get("normalized") is not None and it["amount"]["confidence"] < STRICT_THRESHOLD]
        # on a page image, lines that do not add up are recorded unverified rather than asked (engine/e2/extract)
        line_fail = [c["code"] for c in out["deterministic_checks"]
                     if tier in R.SCAN_TIERS and c["code"] in ("line_arithmetic", "subtotal_sum") and c["status"] == "fail"]
        if (weak or line_fail) and out["line_items"]:
            exceptions.append({"doc_key": doc.key, "name": doc.name, "code": "line_items_unverified",
                               "reason": REASON_TH["line_items_unverified"],
                               "detail": "; ".join(x for x in ("บรรทัด " + ", ".join(str(x) for x in weak) if weak else "",
                                                             "ตรวจไม่ผ่าน: " + ", ".join(line_fail) if line_fail else "") if x),
                               "severity": "recorded", "decided_by": "rule", "subject": None})
            weak_rows = set(weak) if weak else {it["row"] for it in out["line_items"]}
        else:
            weak_rows = set()
        for it in out["line_items"]:
            conf = min([(it.get(k) or {}).get("confidence", 0) for k in ("quantity", "unit_price", "amount")
                        if (it.get(k) or {}).get("normalized") is not None] or [0])
            lines.append({"doc_key": doc.key, "name": doc.name, "row": it["row"], "page": it.get("page"),
                          "description": (it.get("description") or {}).get("normalized") or (it.get("description") or {}).get("value"),
                          "quantity": _s((it.get("quantity") or {}).get("normalized")),
                          "unit_price": _s((it.get("unit_price") or {}).get("normalized")),
                          "amount": _s((it.get("amount") or {}).get("normalized")), "confidence": round(conf, 3),
                          "verified": conf >= STRICT_THRESHOLD and it["row"] not in weak_rows})
        for n, fd in f.items():
            if fd.get("normalized") is None:
                continue
            src = fd.get("source") or {}
            bbox = src.get("bbox")
            prov.append({"doc_key": doc.key, "name": doc.name, "field": n, "label": cards.FIELD_TH.get(n, n),
                         "value": _display(n, fd) if not isinstance(_display(n, fd), dict) else str(_display(n, fd)),
                         "confidence": fd.get("confidence"), "method": _method(fd), "page": src.get("page"),
                         "bbox": ",".join(f"{x:.1f}" for x in bbox) if bbox else None, "raw_text": src.get("raw_text"),
                         "checks": [c for c in fd.get("checks") or []], "decided_by": decided_by(fd),
                         "candidates": [{"method": c.get("method"), "value": _s(c.get("normalized"))} for c in fd.get("candidates") or []
                                        if str(c.get("method", "")).startswith("vision")]})
    included = [d for d in documents if d["status"] == "included"]
    models: dict[str, dict] = {}
    for doc_key, p in (proposals or {}).items():
        if not isinstance(p, dict) or not p.get("model"):
            continue
        m = models.setdefault(p["model"], {"model": p["model"], "self_hosted": bool(p.get("self_hosted")), "documents": []})
        d = next((x for x in documents if x["doc_key"] == doc_key), None)
        if d is not None and d["tier"] in R.SCAN_TIERS:
            m["documents"].append(d["name"])
    models_list = [m for m in models.values() if m["documents"]]
    tot = {k: _s(_q2(v)) for k, v in totals.items()}
    counts = {"documents": len(documents), "included": len(included), "excluded": len(documents) - len(included),
              "exceptions": len(exceptions), "questions_answered": sum(d["answered"] for d in documents)}
    label = period_label_th(period)
    quotable = {"documents": str(counts["documents"]), "included": str(counts["included"]),
                "excluded": str(counts["excluded"]), "total_thb": fmt_money(totals["total"]),
                "vat_thb": fmt_money(totals["vat_amount"])}
    fallback = {"summary": (f"รอบนี้ได้รับเอกสาร {counts['documents']} ฉบับ บันทึกเข้าทะเบียน {counts['included']} ฉบับ "
                            f"ยอดรวม {fmt_money(totals['total'])} บาท ภาษีมูลค่าเพิ่ม {fmt_money(totals['vat_amount'])} บาท"
                            + (f" และมี {counts['excluded']} ฉบับที่ไม่ได้บันทึก ดูเหตุผลในชีตข้อยกเว้น" if counts["excluded"] else "")),
                "movements": [], "watch": sorted({e["reason"] for e in exceptions}), "next_steps": []}
    return {"schema": SCHEMA, "period": period, "period_label": label,
            "prepared_on": (today or _dt.date.today()).isoformat(), "company": (defaults or {}).get("company") or "",
            "company_tax_id": (R.org_from(defaults or {}) or {}).get("tax_id"),
            "documents": documents, "line_items": lines, "provenance": prov, "exceptions": exceptions, "checks": checks,
            "totals": {**counts, "thb": tot, "by_currency": {k: {"documents": v["documents"], "total": _s(_q2(v["total"])),
                                                                  "vat_amount": _s(_q2(v["vat_amount"]))}
                                                              for k, v in sorted(by_currency.items())}},
            "index_entries": index, "models": models_list,
            "quotable": quotable, "quotable_ids": [], "fallback_narrative": fallback,
            # the runner's generic view of computed.json
            "lines": [], "ratios": [], "top_movements": [], "citations": [],
            "excluded": [{"code": e["code"], "reason": e["reason"], "name": e["name"]} for e in exceptions if e["severity"] == "excluded"],
            "source": {"documents": [{"name": d["name"], "sha256": d["sha256"], "tier": d["tier"]} for d in documents]},
            "script": "packages/invoice_processing/scripts/register.py"}


def confidence_buckets(provenance: list[dict]) -> dict:
    """How sure the recorded values were, for the agent's dashboard: values read from a document by
    confidence band (the E2 thresholds: at or above STRICT is accepted as read, between ORDINARY and
    STRICT only for ordinary fields, below ORDINARY is always asked), and values a person typed or
    confirmed, or that were computed."""
    out = {"high": 0, "medium": 0, "low": 0, "person": 0, "computed": 0}
    for p in provenance:
        by = p.get("decided_by") or ""
        if by in ("typed_by_person", "confirmed_by_person"):
            out["person"] += 1
        elif by in ("computed", "model_proposal"):
            out["computed"] += 1
        else:
            c = p.get("confidence") or 0
            out["high" if c >= STRICT_THRESHOLD else "medium" if c >= ORDINARY_THRESHOLD else "low"] += 1
    return out


def summarize(computed: dict) -> dict:
    t = computed["totals"]
    tiers: dict[str, int] = {}
    for d in computed["documents"]:
        tiers[d["tier"]] = tiers.get(d["tier"], 0) + 1
    return {"period": computed["period"], "period_label": computed["period_label"], "documents": t["documents"],
            "confidence": confidence_buckets(computed["provenance"]), "tiers": tiers,
            "included": t["included"], "excluded": t["excluded"], "exceptions": t["exceptions"],
            "total_thb": t["thb"]["total"], "vat_thb": t["thb"]["vat_amount"],
            "models": [{"model": m["model"], "self_hosted": m["self_hosted"], "documents": len(m["documents"])}
                       for m in computed["models"]],
            "excluded_list": [{"code": e["code"], "reason": e["reason"], "name": e["name"]} for e in computed["excluded"]]}
