"""What an expense audit's report and deck hold (AM23 §1.2; deliverables/document.py writes them).

The face: the period, what was claimed and how much of it is an exception or needs judgement, each with its own
citation, and the result in the run's own words; the claims that did not pass, the largest first (the workbook has
every claim with its evidence); the commentary; the sources. Nothing here reads the workbook.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.thai import fmt_money
from ..words import W
from .workbook import S_CLAIMS


def _money(v) -> str:
    return fmt_money(Decimal(str(v))) if v not in (None, "") else "—"


def _abs(v) -> Decimal:
    try:
        return abs(Decimal(str(v)))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def spec(view: dict, applied: list | tuple = ()) -> dict:
    c = view
    reasons: dict[str, list[str]] = {}
    for e in c.get("exceptions") or []:
        subject = str(e.get("subject") or "")
        if subject and e.get("reason") and e["reason"] not in reasons.setdefault(subject, []):
            reasons[subject].append(e["reason"])
    held = [x for x in c.get("claims") or [] if x.get("outcome") != "pass"]
    # the largest first — unless an order was asked of the documents, which the view already holds (AM11b)
    claims = held if applied else sorted(held, key=lambda x: (-_abs(x.get("amount")), str(x.get("id") or "")))
    rows = [[str(x.get("id") or "—"), str(x.get("employee_name") or "—"), str(x.get("category_label") or "—"), _money(x.get("amount")),
             " · ".join(reasons.get(str(x.get("id") or ""), [])) or "—", str(x.get("outcome_label") or "—")] for x in claims]
    return {
        "title": W("doc.title.summary", period_label=c["period_label"]),
        "subtitle": c.get("company") or c["period_label"],
        "cover_lines": [],
        "tiles": ["claimed_thb", "exception_thb", "question_thb"], "headline": "exception_thb",
        "table": {"title": W("doc.title.held"), "rows": rows, "numeric_from": 3, "deck_columns": [0, 3, 5],
                  "columns": [W("doc.col.claim_no"), W("doc.col.employee"), W("doc.col.category"), W("doc.col.amount"), W("doc.col.reason"),
                              W("doc.col.outcome")],
                  "sheet": W(S_CLAIMS), "order": "asked" if applied else "amount", "amounts": [_abs(x.get("amount")) for x in claims],
                  "unit": "claims"},
    }
