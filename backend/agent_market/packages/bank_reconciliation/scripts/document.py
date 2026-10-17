"""What a bank reconciliation's report and deck hold (AM23 §1.2; deliverables/document.py writes them).

The face: whose account and which period, the two adjusted balances and their difference, each with its own
citation, and the result in the run's own words; the reconciliation statement; the reconciling items, the largest
first (the workbook has every one with its evidence); the commentary; the sources. Nothing here reads the workbook.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine import words
from ....engine.thai import fmt_money
from ..words import W
from .workbook import L_BANK_ADJ, L_BANK_BAL, L_BOOK_ADJ, L_BOOK_BAL, L_DIFF, S_ITEMS, S_STATEMENT


def _money(v) -> str:
    return fmt_money(Decimal(str(v))) if v not in (None, "") else "—"


def _abs(v) -> Decimal:
    try:
        return abs(Decimal(str(v)))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def spec(view: dict, applied: list | tuple = ()) -> dict:
    c, acc, st = view, view.get("account") or {}, view["statement"]
    # the largest first — unless an order was asked of the documents, which the view already holds (AM11b)
    items = list(c.get("items") or []) if applied else sorted(c.get("items") or [], key=lambda i: (-_abs(i.get("amount")), str(i.get("date") or ""), str(i.get("id") or "")))
    rows = [[i.get("type_label") or "", words.date(i["date"]) if i.get("date") else "—", str(i.get("ref") or "—"),
             str(i.get("description") or "—"), _money(i.get("amount"))] for i in items]
    statement, strong = [], []
    for side, bal, adj in (("bank", L_BANK_BAL, L_BANK_ADJ), ("book", L_BOOK_BAL, L_BOOK_ADJ)):
        s = st[side]
        strong.append(len(statement))
        statement.append([W(bal), _money(s.get("balance"))])
        for ln in s.get("lines") or []:
            statement.append([f"{ln['sign']}: {ln['label']}", _money(ln["amount"])])
        strong.append(len(statement))
        statement.append([W(adj), _money(s.get("adjusted"))])
    strong.append(len(statement))
    statement.append([W(L_DIFF), _money(st.get("difference"))])
    return {
        "title": W("doc.title.statement", period_label=c["period_label"]),
        "subtitle": " · ".join(x for x in (acc.get("bank"), acc.get("number"), acc.get("name")) if x) or c["period_label"],
        "cover_lines": [f"{W('doc.face.as_of')} {words.date(c['as_of'])}" if c.get("as_of") else c["period_label"]],
        "tiles": ["adjusted_bank", "adjusted_book", "difference"], "headline": "difference",
        "statement": {"title": W(S_STATEMENT), "columns": [W("doc.col.description"), W("doc.col.amount")], "rows": statement,
                      "strong_rows": strong, "numeric_from": 1},
        "table": {"title": W(S_ITEMS), "rows": rows, "numeric_from": 4, "deck_columns": [0, 1, 2, 4],
                  "columns": [W("doc.col.type"), W("doc.col.date"), W("doc.col.ref"), W("doc.col.description"), W("doc.col.amount")],
                  "sheet": W(S_ITEMS), "order": "asked" if applied else "amount", "amounts": [_abs(i.get("amount")) for i in items],
                  "unit": "items"},
    }
