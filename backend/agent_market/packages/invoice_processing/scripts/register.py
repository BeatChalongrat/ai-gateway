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
  not_included[] AM5 "เอกสารที่ยังไม่ได้รวม": every document left out, why, and what bringing it back
                takes — {"set": answers to give, "drop": answers to take back}; the round keeps the list
  checks[]      each document's deterministic checks
  totals        counts and THB sums of the recorded documents
  index_entries what the duplicate index keeps for the recorded documents (written by the runner
                only when the run succeeds)
  models[]      which model saw which documents, and whether it runs in the organisation
"""

from __future__ import annotations

import datetime as _dt
from decimal import ROUND_HALF_UP, Decimal

from ....engine import words
from ....engine.e2 import cards
from ....engine.e2.schema import FIELD_KINDS, MONEY_FIELDS, ORDINARY_THRESHOLD, STRICT_THRESHOLD
from ....engine.thai import fmt_money
from ..checks import review as R
from ..words import W

SCHEMA = "invoice.register.v1"
Q2 = Decimal("0.01")

# every table keeps the keys of its words (agent.json) and says each in the language of the run when it is read
DOC_TYPE_TH = W.table({k: f"doc.{k}" for k in ("full_tax_invoice", "abbreviated_tax_invoice", "receipt", "invoice", "credit_note", "debit_note",
                                               "wht_certificate", "quotation", "unknown")})
TIER_TH = W.table({k: f"tier.{k}" for k in ("digital", "etax", "scan", "photo")})

# reason codes (the exceptions sheet and the report list them with these words)
REASON_TH = W.table({k: f"reason.{k}" for k in (
    "same_file_twice", "duplicate", "missing_required_field", "unconfirmed_value", "sources_disagree",
    "check_failed", "copy_not_original", "unknown_document_type", "unreadable_page",
    "typed_value_invalid", "unresolved_question", "excluded_by_person", "accepted_as_printed",
    "field_absent", "foreign_currency", "not_duplicate_by_person", "photo_triage_only",
    "line_items_unverified")})
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
    not_included = []
    totals = {"subtotal": Decimal(0), "vat_amount": Decimal(0), "total": Decimal(0), "wht_amount": Decimal(0),
              "net_payable": Decimal(0)}
    by_currency: dict[str, dict] = {}
    payable = Decimal(0)  # AM3: the amount to pay (printed net payable, else total − withholding)
    for r in reviewed:
        doc, out = r.doc, r.out
        f = out["fields"]
        tier = out["document"]["tier"]
        status = r.status if r.status != "pending" else "excluded"
        exclusion = r.exclusion
        if r.status == "pending":
            exclusion = {"code": "unresolved_question", "decided_by": "rule",
                         "detail": "; ".join(x.question or x.header for x in r.findings)[:400],
                         "restore": {"set": {}, "drop": []}}
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
        # AM3: what is actually to be paid — the net payable the document printed, else its total less
        # any withholding (a document with none: its total). net_payable itself stays as printed.
        row["payable"] = row.get("net_payable")
        row["payable_computed"] = False
        if row["payable"] is None and row.get("total") is not None:
            row["payable"] = _s(_q2(Decimal(str(row["total"])) - Decimal(str(row.get("wht_amount") or 0))))
            row["payable_computed"] = True
        row["thb_payable"] = row["thb_net_payable"]
        if row["thb_payable"] is None and row["thb_total"] is not None:
            row["thb_payable"] = _s(_q2(Decimal(row["thb_total"]) - Decimal(row["thb_wht_amount"] or 0)))
        documents.append(row)
        checks += [{"doc_key": doc.key, "name": doc.name, **c} for c in out["deterministic_checks"]]
        if status == "excluded":
            others = [REASON_TH.get(c, c) for c in exclusion.get("codes", []) if c != exclusion["code"]]
            detail = "; ".join(x for x in (exclusion.get("detail", ""), W("detail.and", others=", ".join(others)) if others else "") if x)
            exceptions.append({"doc_key": doc.key, "name": doc.name, "code": exclusion["code"],
                               "reason": REASON_TH.get(exclusion["code"], exclusion["code"]), "detail": detail,
                               "codes": exclusion.get("codes", [exclusion["code"]]),
                               "severity": "excluded", "decided_by": exclusion.get("decided_by", "rule"),
                               "subject": exclusion.get("subject")})
            restore = exclusion.get("restore")
            # AM5: what bringing a document back does, as the list says it
            how = W("how.none") if restore is None else W("how.ask") if (restore["drop"] or not restore["set"]) else W("how.record")
            not_included.append({"doc_key": doc.key, "name": doc.name, "code": exclusion["code"],
                                 "reason": REASON_TH.get(exclusion["code"], exclusion["code"]), "detail": detail,
                                 "decided_by": exclusion.get("decided_by", "rule"), "how": how,
                                 "can_restore": restore is not None, "restore": restore or {"set": {}, "drop": []}})
            continue
        # recorded
        index.append(R.index_entry(doc, out))
        for n in totals:
            v = row[f"thb_{n}"]
            if v is not None:
                totals[n] += Decimal(v)
        if row["thb_payable"] is not None:
            payable += Decimal(row["thb_payable"])
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
                               "reason": REASON_TH["photo_triage_only"], "detail": cards.said(out["document"].get("notice")),
                               "severity": "recorded", "decided_by": "rule", "subject": None})
        weak = [it["row"] for it in out["line_items"]
                if (it.get("amount") or {}).get("normalized") is not None and it["amount"]["confidence"] < STRICT_THRESHOLD]
        # on a page image, lines that do not add up are recorded unverified rather than asked (engine/e2/extract)
        line_fail = [c["code"] for c in out["deterministic_checks"]
                     if tier in R.SCAN_TIERS and c["code"] in ("line_arithmetic", "subtotal_sum") and c["status"] == "fail"]
        if (weak or line_fail) and out["line_items"]:
            exceptions.append({"doc_key": doc.key, "name": doc.name, "code": "line_items_unverified",
                               "reason": REASON_TH["line_items_unverified"],
                               "detail": "; ".join(x for x in (W("detail.lines", rows=", ".join(str(x) for x in weak)) if weak else "",
                                                             W("detail.failed", checks=", ".join(line_fail)) if line_fail else "") if x),
                               "severity": "recorded", "decided_by": "rule", "subject": None})
            weak_rows = set(weak) if weak else {it["row"] for it in out["line_items"]}
        else:
            weak_rows = set()
        for it in out["line_items"]:
            # AM8: the item's name counts like its figures — a name with words of its row left out is not
            # read with confidence, whatever its numbers are
            d = it.get("description") or {}
            conf = min([(it.get(k) or {}).get("confidence", 0) for k in ("quantity", "unit_price", "amount", "description")
                        if (it.get(k) or {}).get("normalized") is not None] or [0])
            by_person = "confirmed_by_person" in (d.get("checks") or [])
            lines.append({"doc_key": doc.key, "name": doc.name, "row": it["row"], "page": it.get("page"),
                          "description": d.get("normalized") or d.get("value"),
                          "quantity": _s((it.get("quantity") or {}).get("normalized")),
                          "unit_price": _s((it.get("unit_price") or {}).get("normalized")),
                          "amount": _s((it.get("amount") or {}).get("normalized")), "confidence": round(conf, 3),
                          "verified": conf >= STRICT_THRESHOLD and it["row"] not in weak_rows,
                          "left_out": list(it.get("left_out") or []),
                          # who decided it: a person only when a person answered about this item (never a threshold)
                          "decided_by": "person" if by_person else "rule"})
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
    tot["payable"] = _s(_q2(payable))
    counts = {"documents": len(documents), "included": len(included), "excluded": len(documents) - len(included),
              "exceptions": len(exceptions), "questions_answered": sum(d["answered"] for d in documents)}
    label = words.period(period)
    quotable = {"documents": str(counts["documents"]), "included": str(counts["included"]),
                "excluded": str(counts["excluded"]), "total_thb": fmt_money(totals["total"]),
                "vat_thb": fmt_money(totals["vat_amount"])}
    citations = _citations(documents, tot, exceptions)
    fallback = {"summary": (W("say.summary", documents=counts["documents"], included=counts["included"],
                              total=fmt_money(totals["total"]), vat_amount=fmt_money(totals["vat_amount"]))
                            + (" " + W("say.summary.excluded", excluded=counts["excluded"]) if counts["excluded"] else "")),
                "movements": [], "watch": sorted({e["reason"] for e in exceptions}), "next_steps": []}
    return {"schema": SCHEMA, "period": period, "period_label": label,
            "prepared_on": (today or _dt.date.today()).isoformat(), "company": (defaults or {}).get("company") or "",
            "company_tax_id": (R.org_from(defaults or {}) or {}).get("tax_id"),
            "documents": documents, "line_items": lines, "provenance": prov, "exceptions": exceptions, "checks": checks,
            "not_included": not_included,
            "totals": {**counts, "thb": tot, "by_currency": {k: {"documents": v["documents"], "total": _s(_q2(v["total"])),
                                                                  "vat_amount": _s(_q2(v["vat_amount"]))}
                                                              for k, v in sorted(by_currency.items())}},
            "index_entries": index, "models": models_list,
            "quotable": quotable, "quotable_ids": [], "fallback_narrative": fallback,
            # the runner's generic view of computed.json
            "lines": [], "ratios": [], "top_movements": [], "citations": citations,
            "excluded": [{"code": e["code"], "reason": e["reason"], "name": e["name"]} for e in exceptions if e["severity"] == "excluded"],
            "source": {"documents": [{"name": d["name"], "sha256": d["sha256"], "tier": d["tier"]} for d in documents]},
            "script": "packages/invoice_processing/scripts/register.py"}


def _citations(documents: list[dict], tot: dict, exceptions: list[dict]) -> list[dict]:
    """How each figure of the summary sheet was made (AM4): the provenance an answer to "why is this
    figure what it is" is built from, and nothing else — the same shape as the financial report's
    citations. Every figure lists the documents it adds up (their own values) and the ones left out
    (with the reason), so "why 0.00" is answered from the register, not guessed."""
    included = [d for d in documents if d["status"] == "included"]
    left_out = [{"file": e["name"], "reason": e["reason"]} for e in exceptions if e["severity"] == "excluded"]

    def parts(field: str, note=None) -> list[dict]:
        return [{"file": d["name"], "value": d.get(f"thb_{field}"), "note": note(d) if note else ""} for d in included]

    def printed(field: str):
        return lambda d: W("cite.not_printed") if d.get(f"thb_{field}") is None else W("cite.as_printed")

    def payable_note(d: dict) -> str:
        if d.get("thb_payable") is None:
            return W("cite.no_total")
        return (W("cite.payable_computed") if d.get("payable_computed")
                else W("cite.payable_printed"))

    def named(key: str, aliases: list[str]) -> tuple:
        # a name of a figure is a sentence of the manifest, or a word that is the same in both languages ("vat")
        return words.names(lambda: (W(f"cite.{key}.label"), [W(x) if x in W.strings else x for x in aliases]))

    def c(cid: str, key: str, aliases: list[str], note=None, value=None) -> dict:
        label, names = named(key, aliases)
        return {"id": cid, "label": label, "value": value if value is not None else tot.get(key),
                "formula": W(f"cite.{key}"), "sources": parts(key, note), "left_out": left_out, "aliases": names,
                "script": "packages/invoice_processing/scripts/register.py"}

    label, names = named("count", ["cite.count.a1", "cite.count.a2", "cite.count.a3", "cite.count.a4", "cite.count.a5"])
    return [
        c("i1", "subtotal", ["cite.subtotal.a1", "cite.subtotal.a2", "cite.subtotal.a3"], printed("subtotal")),
        c("i2", "vat_amount", ["cite.vat_amount.a1", "vat", "cite.vat_amount.a2"], printed("vat_amount")),
        c("i3", "total", ["cite.total.a1", "cite.total.a2"], printed("total")),
        c("i4", "wht_amount", ["cite.wht_amount.a1", "cite.wht_amount.a2", "wht"], printed("wht_amount")),
        c("i5", "net_payable", ["cite.net_payable.a1", "cite.net_payable.a2", "net payable"], printed("net_payable")),
        c("i6", "payable", ["cite.payable.a1", "cite.payable.a2", "cite.payable.a3", "cite.payable.a4"], payable_note),
        {"id": "i7", "label": label, "value": str(len(included)), "unit": "count", "formula": W("cite.count"),
         "sources": [{"file": d["name"], "value": None, "note": W("cite.count.a1")} for d in included], "left_out": left_out,
         "aliases": names, "script": "packages/invoice_processing/scripts/register.py"},
    ]


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
            "excluded_list": [{"code": e["code"], "reason": e["reason"], "name": e["name"]} for e in computed["excluded"]],
            # AM5: what the round's files card lists under "เอกสารที่ยังไม่ได้รวม" (what restoring takes stays in computed.json)
            "not_included": [{k: e[k] for k in ("doc_key", "name", "reason", "detail", "how", "can_restore")}
                             for e in computed.get("not_included") or []]}
