"""What an invoice-to-PO matching's report and deck hold (AM23 §1.2; deliverables/document.py writes them).

The face: the period, the value that passed and the value blocked, and how many invoices a person must decide,
each with its own citation, and the result in the run's own words; the invoices that did not pass, the largest
first (the workbook has every invoice and every line with its evidence); the commentary; the sources. Nothing here
reads the workbook, and nothing here approves or pays an invoice — the paper never says it does.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.thai import fmt_money
from ..words import W
from .match import never_written
from .workbook import S_INVOICES


def _abs(v) -> Decimal:
    try:
        return abs(Decimal(str(v)))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def spec(view: dict, applied: list | tuple = ()) -> dict:
    c = view
    held = [i for i in c.get("invoices") or [] if i.get("status") not in ("pass", None)]
    # the largest first — unless an order was asked of the documents, which the view already holds (AM11b)
    if not applied:
        held = sorted(held, key=lambda i: (-_abs(i.get("value_thb") or i.get("total")), str(i.get("number") or "")))

    def total(i) -> str:
        v = i.get("total")
        cur = "" if (i.get("currency") or "THB") == "THB" else f" {i.get('currency')}"
        return (fmt_money(Decimal(str(v))) + cur) if v not in (None, "") else "—"

    rows = [[str(i.get("number") or i.get("name") or "—"), str((i.get("supplier") or {}).get("name") or "—"), total(i),
             str(i.get("status_label") or "—"), " · ".join(str(x) for x in i.get("reason_labels") or []) or "—"] for i in held]
    return {
        "title": W("doc.title.summary", period_label=c["period_label"]),
        "subtitle": c.get("company") or c["period_label"],
        "cover_lines": [],
        "tiles": ["value_blocked", "person", "received_not_billed"], "headline": "value_blocked",
        # (AM23 §1.5: every invoice passed and no exception — a document would have nothing to say)
        "nothing_to_say": not rows and not (c.get("exceptions") or []),
        "table": {"title": W("doc.title.held"), "rows": rows, "numeric_from": 2, "deck_columns": [0, 2, 3],
                  "columns": [W("doc.col.number"), W("doc.col.seller"), W("doc.col.total"), W("doc.col.status"), W("doc.col.reason")],
                  "sheet": W(S_INVOICES), "order": "asked" if applied else "amount",
                  "amounts": [_abs(i.get("value_thb") or i.get("total")) for i in held], "unit": "invoices"},
        # the words the paper never says (its manifest's narrative_never, its workbook's gate): nor do its documents
        "never": never_written(),
    }
