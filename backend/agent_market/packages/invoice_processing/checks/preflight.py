"""preflight — every document judged in the run's context; its E2 questions become the run's cards.

Returns the AM1 preflight shape (roles, period, findings, assumptions, stats, files). The cards
are the E2 bridge's (engine/e2/cards.py): one per document and field or check, every financial
card's default "leave the document out".
"""

from __future__ import annotations

import datetime as _dt

from ..words import W
from . import review as R


def run(docs: list[R.Doc], *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
        today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None) -> dict:
    reviewed = R.review(docs, answers=answers, defaults=defaults, today=today, history=history, proposals=proposals)
    period, source = R.period_of(reviewed, today)
    findings = [f for r in reviewed for f in r.findings]
    assumptions = []
    if R.org_from(defaults or {}) is None:
        assumptions.append({"code": "no_company_tax_id", "source": "rule",
                            "text": W("assume.no_tax_id")})
    if any(r.status == "excluded" and (r.exclusion or {}).get("code") == "same_file_twice" for r in reviewed):
        assumptions.append({"code": "same_file_twice", "source": "rule",
                            "text": W("assume.same_file"), "changes_numbers": True})
    count = lambda s: sum(1 for r in reviewed if r.status == s)  # noqa: E731
    return {
        "roles": {r.doc.key: "document" for r in reviewed},
        "period": period, "period_source": source, "prior_period": None,
        "findings": [f.to_dict() for f in findings],
        "assumptions": assumptions,
        "stats": {"documents": len(reviewed), "included": count("included"), "pending": count("pending"),
                  "excluded": count("excluded"), "questions": len(findings)},
        "files": [{**r.doc.summary(), "status": r.status} for r in reviewed],
        "today": (today or _dt.date.today()).isoformat(),
    }
