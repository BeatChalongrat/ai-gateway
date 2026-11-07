"""preflight — the run judged in its context; what needs a person now becomes the run's cards.

Returns the AM1 preflight shape (roles, period, findings, assumptions, stats, files). The cards, in
the order they are asked (checks/cards.py): which side a file is, a side with no file, which
account of a ledger that holds several, a balance the file does not show, the items carried in,
then the match itself — several sure pairs confirmed by one answer, every other proposal on its
own, and the large rows without a partner.
"""

from __future__ import annotations

import datetime as _dt

from . import review as RV


def run(uploads: list, *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
        today: _dt.date | None = None) -> dict:
    rv = RV.review(uploads, message=message, defaults=defaults, answers=answers, today=today)
    roles = {}
    for u in uploads:
        if u.kind == "carried":
            roles[u.key] = "carried"
        elif u.kind == "table":
            roles[u.key] = rv.sides.get(u.key) or "undecided"
        else:
            roles[u.key] = "other"
    stats = {"rows": len(rv.bank) + len(rv.book), "bank_rows": len(rv.bank), "book_rows": len(rv.book),
             "carried": len(rv.carried), "outside_period": len(rv.outside), "questions": len(rv.findings)}
    if rv.match is not None:
        c = rv.match["counts"]
        stats.update({"matched": c["matched"], "missing": c["missing"], "extra": c["extra"], "suspect": c["suspect"]})
    period = rv.period
    if period is None:
        # nothing to reconcile yet (a side is missing, or a file's side is being asked): the month
        # the run was started in, so that the round has a name while it waits
        t = today or rv.today
        period = f"{t.year:04d}-{t.month:02d}"
    return {
        "roles": roles,
        "period": period, "period_source": rv.period_source or "today", "prior_period": None,
        "findings": [f.to_dict() for f in rv.findings],
        "assumptions": rv.assumptions,
        "stats": stats,
        "files": [{**u.summary(), "status": "read" if roles[u.key] in ("bank", "book", "carried") else roles[u.key]} for u in uploads],
        "today": (today or rv.today).isoformat(),
    }
