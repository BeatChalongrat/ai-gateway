"""preflight — the round judged in its context; what needs a person now becomes the round's cards.

Returns the AM1 preflight shape (roles, period, findings, assumptions, stats, files). The cards, in
the order they are asked (checks/cards.py): what a table is, a missing file, whether the company
issues goods receipts (once), a document's unclear values, the proposed links, a supplier the
names cannot settle, units nothing converts.
"""

from __future__ import annotations

import datetime as _dt

from . import review as RV


def run(uploads: list, *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
        today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None) -> dict:
    rv = RV.review(uploads, message=message, defaults=defaults, answers=answers, today=today, history=history,
                   proposals=proposals)
    roles, status = {}, {}
    used = {u.key: sorted({k for x, _t, k in rv.tables if x.key == u.key}) for u in uploads}
    docs = {d.upload.key: d for d in rv.docs}
    for u in uploads:
        if u.kind == "tables":
            kinds = used.get(u.key) or []
            roles[u.key] = "+".join(kinds) if kinds else ("undecided" if any(x.key == u.key for x, _ in rv.waiting_kind) else "other")
            status[u.key] = "read" if kinds else roles[u.key]
        elif u.kind == "document":
            d = docs.get(u.key)
            roles[u.key] = "invoice" if d is not None and d.status != "excluded" else "other"
            status[u.key] = d.status if d is not None else "excluded"
        else:
            roles[u.key] = "other"
            status[u.key] = "other"
    stats = {"po_lines": len(rv.po), "gr_lines": len(rv.gr), "invoices": len(rv.invoices),
             "invoice_lines": sum(len(i.lines) for i in rv.invoices), "questions": len(rv.findings),
             "linked": len(rv.po_of), "waiting": len(rv.waiting_of), "rows": len(rv.po) + len(rv.gr)}
    return {
        "roles": roles,
        "period": rv.period, "period_source": rv.period_source or "today", "prior_period": None,
        "findings": [f.to_dict() for f in rv.findings],
        "assumptions": rv.assumptions,
        "stats": stats,
        "files": [{**u.summary(), "status": status[u.key]} for u in uploads],
        "today": rv.today.isoformat(),
    }
