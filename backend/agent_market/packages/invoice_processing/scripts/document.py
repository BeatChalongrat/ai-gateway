"""What a purchase register's report and deck hold (AM23 §1.2; deliverables/document.py writes them).

The face: the period, the total and the VAT recorded and how many documents were entered, each with its own
citation, and the result in the run's own words; the documents that carry a note (left out, or recorded with a
reason), the largest first (the workbook has every document with the source of every value); the commentary; the
sources. Nothing here reads the workbook.
"""

from __future__ import annotations

from decimal import Decimal

from ....engine.thai import fmt_money
from ..words import W
from .workbook import S_REGISTER


def _abs(v) -> Decimal:
    try:
        return abs(Decimal(str(v)))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def spec(view: dict, applied: list | tuple = ()) -> dict:
    c = view
    docs = {d.get("doc_key"): d for d in c.get("documents") or []}
    noted: dict[str, list[str]] = {}
    for e in c.get("exceptions") or []:
        key = e.get("doc_key")
        if key in docs and e.get("reason") and e["reason"] not in noted.setdefault(key, []):
            noted[key].append(e["reason"])
    for key, d in docs.items():
        if d.get("status") != "included" and key not in noted:
            noted[key] = [str((d.get("exclusion") or {}).get("reason") or "") or W("doc.sheet.not_included")]

    def total(d) -> str:
        v = d.get("thb_total") if d.get("thb_total") not in (None, "") else d.get("total")
        cur = "" if d.get("thb_total") not in (None, "") or (d.get("currency") or "THB") == "THB" else f" {d.get('currency')}"
        return (fmt_money(Decimal(str(v))) + cur) if v not in (None, "") else "—"

    # the largest first — unless an order was asked of the documents, which the view's documents already hold (AM11b)
    keys = ([k for k in docs if k in noted] if applied
            else sorted(noted, key=lambda k: (-_abs(docs[k].get("thb_total") or docs[k].get("total")), str(docs[k].get("name") or ""))))
    rows = [[str(docs[k].get("document_number") or docs[k].get("name") or "—"), str(docs[k].get("seller_name") or "—"), total(docs[k]),
             " · ".join(noted[k])] for k in keys]
    return {
        "title": W("doc.title.summary", period_label=c["period_label"]),
        "subtitle": c.get("company") or c["period_label"],
        "cover_lines": [],
        "tiles": ["total_thb", "vat_thb", "documents"], "headline": "total_thb",
        # (AM23 §1.5: every document entered with no note and no exception — a document would have nothing to say)
        "nothing_to_say": not rows and not (c.get("exceptions") or []),
        "table": {"title": W("doc.title.noted"), "rows": rows, "numeric_from": 2, "deck_columns": [0, 1, 2],
                  "columns": [W("doc.col.number"), W("doc.col.seller"), W("doc.col.total"), W("doc.col.reason")],
                  "sheet": W(S_REGISTER), "order": "asked" if applied else "amount",
                  "amounts": [_abs(docs[k].get("thb_total") or docs[k].get("total")) for k in keys], "unit": "documents"},
    }
