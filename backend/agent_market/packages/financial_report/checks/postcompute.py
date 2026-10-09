"""Postcompute checks — only what the numbers reveal.

unbalanced · duplicate_suspect · materiality_exceeded · prior_period_mismatch.
They read the same prepared rows the compute used (scripts/rules.py), so the
rows a question is about are exactly the rows an answer acts on.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine import words as _words
from ....engine.findings import Finding, finding
from ....engine.ledger import ParsedFile, account_digits
from ....engine.thai import fmt_int, fmt_money
from ..scripts.metrics import prepare
from ..scripts.rules import duplicate_docs, expense_total, large_entries, unbalanced_docs
from ..words import W
from ..words import columns as _columns


def run(files: list[ParsedFile], roles: dict, period: str, computed: dict, *, answers: dict | None = None,
        defaults: dict | None = None) -> dict:
    answers = answers or {}
    defaults = defaults or {}
    prep = prepare(files, roles, period, answers, defaults)
    current = prep["current"]
    primary = prep["primary"]
    chart = prep["chart"]
    out: list[Finding] = []

    # unbalanced — GL and TB only; a P&L export has no double entry to check.
    bal = computed["balance"]
    if bal["applicable"] and Decimal(bal["diff"]) != 0 and "unbalanced" not in answers:
        docs = unbalanced_docs(current)
        diff = Decimal(bal["diff"])
        side = W("card.unbalanced.debit_over") if diff > 0 else W("card.unbalanced.debit_under")
        out.append(finding(
            "unbalanced", "blocking", "postcompute", W("card.unbalanced.header"),
            (W("card.unbalanced.question.docs", debit=fmt_money(Decimal(bal["debit"])), credit=fmt_money(Decimal(bal["credit"])), side=side,
               diff=fmt_money(abs(diff)), docs=", ".join(d["doc"] for d in docs[:3])) if docs else
             W("card.unbalanced.question", debit=fmt_money(Decimal(bal["debit"])), credit=fmt_money(Decimal(bal["credit"])), side=side,
               diff=fmt_money(abs(diff)))),
            [(W("card.unbalanced.continue"), "continue", W("card.unbalanced.continue.d")),
             (W("card.unbalanced.replace"), "replace", W("card.unbalanced.replace.d"))],
            "continue",
            evidence={"file": primary.name, "debit": bal["debit"], "credit": bal["credit"], "diff": bal["diff"],
                      "docs": docs[:10],
                      "rows": [[r.row, r.date.isoformat() if r.date else None, r.doc_no, r.account_code,
                                r.description[:40], str(r.debit), str(r.credit)]
                               for r in current if r.doc_no in {d["doc"] for d in docs[:3]}][:30],
                      "columns": _columns("col.row", "col.date", "col.doc_no", "col.account", "col.description", "word.dr", "word.cr")},
            facts={"diff": bal["diff"]},
        ))

    # duplicate_suspect
    if "duplicate_suspect" not in answers:
        groups = duplicate_docs(current, chart)
        if groups:
            n_rows = sum(len(g["rows"]) for g in groups)
            g0 = groups[0]
            amount = sum((Decimal(g["amount"]) for g in groups), Decimal(0))
            if len(groups) > 1:
                desc = W("card.duplicate.many", n=len(groups), amount=fmt_money(amount))
            elif g0.get("same_number"):
                desc = (W("card.duplicate.same_number", original=g0["original"], amount=fmt_money(Decimal(g0["amount"]))))
            else:
                desc = W("card.duplicate.copy", copy=g0["copies"][0], original=g0["original"], amount=fmt_money(Decimal(g0["amount"])))
            out.append(finding(
                "duplicate_suspect", "blocking", "postcompute", W("card.duplicate.header"),
                W("card.duplicate.question", desc=desc),
                [(W("card.duplicate.exclude"), "exclude", W("card.duplicate.exclude.d", n=fmt_int(n_rows))),
                 (W("card.duplicate.keep"), "keep", W("card.duplicate.keep.d"))],
                "exclude",
                evidence={"file": primary.name, "groups": groups[:10],
                          "rows": [[r.row, r.date.isoformat() if r.date else None, r.doc_no, r.account_code,
                                    r.description[:40], str(r.debit), str(r.credit)]
                                   for r in current if r.row in set(g0["rows"]) | set(g0["original_rows"])][:30],
                          "columns": _columns("col.row", "col.date", "col.doc_no", "col.account", "col.description", "word.dr", "word.cr")},
                facts={"groups": len(groups), "amount": str(amount)},
            ))

    # materiality_exceeded
    if "materiality_exceeded" not in answers:
        prior_by = {}
        for a in computed["accounts"]:
            if a["prior"] is not None:
                prior_by[a["code"]] = a["prior"]
        flagged = large_entries(current, expense_total(current, chart), prior_by, chart)
        if flagged:
            f0 = flagged[0]
            amt = Decimal(f0["amount"])
            share = f0.get("share_of_expenses")
            times = (amt / Decimal(f0["median"])).quantize(Decimal("1"))
            out.append(finding(
                "materiality_exceeded", "blocking", "postcompute", W("card.large.header"),
                (W("card.large.question.share", account=f0["account"], amount=fmt_money(amt), times=times, share=share) if share else
                 W("card.large.question", account=f0["account"], amount=fmt_money(amt), times=times)),
                [(W("card.large.keep"), "keep", W("card.large.keep.d")),
                 (W("card.large.exclude"), "exclude", W("card.large.exclude.d"))],
                "keep",
                evidence={"file": primary.name, "entries": flagged[:10],
                          "rows": [[f["row"], f["date"], f["doc"], f["account"], (f["description"] or "")[:40], f["amount"]]
                                   for f in flagged[:10]],
                          "columns": _columns("col.row", "col.date", "col.doc_no", "col.account", "col.description", "col.amount")},
                facts={"account": f0["account"], "amount": f0["amount"]},
            ))

    # prior_period_mismatch
    prior_file = prep["prior_file"]
    if prior_file is not None and "prior_period_mismatch" not in answers:
        from collections import Counter

        months = Counter(r.period for r in prior_file.rows if r.period)
        actual = months.most_common(1)[0][0] if months else prior_file.period_hint
        expected = computed.get("prior_period") or None
        from ....engine.thai import prev_period

        want = prev_period(period)
        cur_codes = {account_digits(r.account_code) for r in current}
        pri_codes = {account_digits(r.account_code) for r in prior_file.rows}
        overlap = len(cur_codes & pri_codes) / max(1, len(cur_codes | pri_codes))
        reason = None
        if actual and actual != want:
            reason = (W("card.prior.other_month", name=prior_file.name, actual=_words.period(actual), want=_words.period(want)))
        elif overlap < 0.5:
            reason = (W("card.prior.other_chart", name=prior_file.name, pct=int(overlap * 100)))
        if reason:
            out.append(finding(
                "prior_period_mismatch", "blocking", "postcompute", W("card.prior.header"),
                W("card.prior.question", reason=reason),
                [(W("card.prior.ignore"), "ignore_prior", W("card.prior.ignore.d")),
                 (W("card.prior.use"), "use_anyway", W("card.prior.use.d", what=_words.period(actual) if actual else W("card.prior.this_file")))],
                "ignore_prior",
                evidence={"file": prior_file.name, "months": dict(months), "expected": want, "actual": actual,
                          "account_overlap": round(overlap, 2)},
                facts={"actual": actual, "expected": want},
            ))
        _ = expected

    return {"findings": [f.to_dict() for f in out]}
