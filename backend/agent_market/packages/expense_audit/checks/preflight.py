"""preflight — the run judged in its context; what needs a person now becomes the run's cards.

Returns the AM1 preflight shape (roles, period, findings, assumptions, stats, files). The cards,
in the order they are asked: a policy table the run cannot go without, a missing claims sheet, the
E2 questions about a receipt's total (engine/e2/cards.py, default "leave the reading out"), and the
judgement cards (checks/cards.py, default "hand it to a person"). Rule failures are not asked:
they are facts, and compute turns them into run exceptions for a person.
"""

from __future__ import annotations

import datetime as _dt

from . import review as RV


def run(uploads: list, *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
        today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None) -> dict:
    rv = RV.review(uploads, defaults=defaults, answers=answers, today=today, history=history, proposals=proposals)
    period, source = RV.period_of(rv.claims, today)
    count = lambda o: sum(1 for a in rv.audits if a.outcome == o)  # noqa: E731
    roles = {u.key: ("receipt" if u.kind == "receipt" else u.kind) for u in rv.sheets + rv.kb + rv.others}
    roles.update({k: "receipt" for k in rv.receipts})
    return {
        "roles": roles,
        "period": period, "period_source": source, "prior_period": None,
        "findings": [f.to_dict() for f in rv.findings],
        "assumptions": rv.assumptions,
        "stats": {"claims": len(rv.claims), "receipts": len(rv.receipts), "pass": count("pass"),
                  "exception": count("exception"), "question": count("question"), "questions": len(rv.findings),
                  "policy": rv.policy.source if rv.policy else None},
        "files": [{**u.summary(), "status": (rv.receipts[u.key].status if u.kind == "receipt" else "read")}
                  for u in rv.sheets + [r.up for r in rv.receipts.values()] + rv.kb + rv.others],
        "today": (today or _dt.date.today()).isoformat(),
    }
