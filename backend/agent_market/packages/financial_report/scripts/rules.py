"""Deterministic detectors shared by the compute script and the checks.

One function decides "which documents look duplicated" and "which entries
are outside materiality"; the postcompute check uses it to ask, and the
compute script uses the same function to act on the answer. They can never
disagree about which rows a question was about.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from decimal import Decimal

from ....engine.ledger import LedgerRow, account_digits

RULES_VERSION = "rules.py@1.0.0"

PL_GROUPS = (
    # key, Thai label, predicate on the account digits, natural side (+1 credit, -1 debit)
    ("revenue", "รายได้จากการขายและบริการ", lambda d: d.startswith("41"), 1),
    ("other_income", "รายได้อื่น", lambda d: d.startswith("4") and not d.startswith("41"), 1),
    ("cost_of_sales", "ต้นทุนขาย", lambda d: d.startswith("51"), -1),
    ("operating_expenses", "ค่าใช้จ่ายในการขายและบริหาร", lambda d: d[:2] in ("52", "53", "54", "55", "56", "57"), -1),
    ("finance_costs", "ต้นทุนทางการเงิน", lambda d: d.startswith("58"), -1),
    ("income_tax", "ภาษีเงินได้", lambda d: d.startswith("59"), -1),
)

STANDARD_NAMES = {
    "41": "รายได้จากการขายและบริการ", "42": "รายได้อื่น", "51": "ต้นทุนขาย",
    "52": "ค่าใช้จ่ายเกี่ยวกับพนักงาน", "53": "ค่าธรรมเนียมวิชาชีพ", "54": "ค่าใช้จ่ายสำนักงาน",
    "55": "ค่าใช้จ่ายในการขาย", "56": "ค่าเสื่อมราคา", "57": "ค่าใช้จ่ายเบ็ดเตล็ด",
    "58": "ต้นทุนทางการเงิน", "59": "ภาษีเงินได้",
}


def group_of(code: str) -> str | None:
    d = account_digits(code)
    for key, _, pred, _ in PL_GROUPS:
        if pred(d):
            return key
    return None


def is_pl(code: str) -> bool:
    return group_of(code) is not None


def _norm_desc(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def duplicate_docs(rows: list[LedgerRow]) -> list[dict]:
    """Documents posted twice: same date and the same set of lines.

    With document numbers, a whole document is the unit (so excluding the
    copy keeps the ledger balanced). Without them, identical P&L lines on the
    same day with the same description are the unit.
    """
    have_docs = sum(1 for r in rows if r.doc_no) >= len(rows) * 0.9
    groups: dict = defaultdict(list)
    if have_docs:
        by_doc: dict[str, list[LedgerRow]] = defaultdict(list)
        for r in rows:
            by_doc[r.doc_no].append(r)
        for doc, ls in by_doc.items():
            if not any(is_pl(r.account_code) for r in ls):
                continue  # balance-sheet-only documents (receipts, payments) repeat legitimately
            sig = (ls[0].date, tuple(sorted((account_digits(r.account_code), r.debit, r.credit) for r in ls)),
                   _norm_desc(ls[0].description))
            groups[sig].append(doc)
        out = []
        for sig, docs in groups.items():
            if len(docs) < 2:
                continue
            docs_sorted = sorted(docs, key=lambda d: min(r.row for r in by_doc[d]))
            original, copies = docs_sorted[0], docs_sorted[1:]
            pl_line = next(r for r in by_doc[original] if is_pl(r.account_code))
            out.append({
                "original": original, "copies": copies,
                "rows": sorted(r.row for d in copies for r in by_doc[d]),
                "original_rows": sorted(r.row for r in by_doc[original]),
                "amount": str(pl_line.debit + pl_line.credit),
                "account": pl_line.account_code, "description": pl_line.description,
                "date": sig[0].isoformat() if sig[0] else None,
            })
        return out
    for r in rows:
        if is_pl(r.account_code):
            groups[(r.date, account_digits(r.account_code), r.debit, r.credit, _norm_desc(r.description))].append(r)
    out = []
    for key, ls in groups.items():
        if len(ls) < 2:
            continue
        ls = sorted(ls, key=lambda r: r.row)
        out.append({"original": f"row {ls[0].row}", "copies": [f"row {r.row}" for r in ls[1:]],
                    "rows": [r.row for r in ls[1:]], "original_rows": [ls[0].row],
                    "amount": str(ls[0].debit + ls[0].credit), "account": ls[0].account_code,
                    "description": ls[0].description, "date": ls[0].date.isoformat() if ls[0].date else None})
    return out


EXPENSE_GROUPS = ("cost_of_sales", "operating_expenses", "finance_costs", "income_tax")


def expense_total(rows: list[LedgerRow]) -> Decimal:
    """Net debit of every expense account in `rows` — the base of the 5% floor."""
    return sum((r.debit - r.credit for r in rows if group_of(r.account_code) in EXPENSE_GROUPS), Decimal(0))


def large_entries(rows: list[LedgerRow], total_expenses: Decimal, prior_by_account: dict | None = None) -> list[dict]:
    """Single P&L entries far outside their account's own pattern.

    An entry is flagged when its account has ≥3 other entries this period,
    the entry is ≥10× their median, and it is ≥5% of the period's expenses —
    the shape of a typo'd extra zero, not of a month-end journal.
    """
    by_acct: dict[str, list[LedgerRow]] = defaultdict(list)
    for r in rows:
        if is_pl(r.account_code):
            by_acct[r.account_code].append(r)
    out = []
    floor = total_expenses * Decimal("0.05")
    for acct, ls in by_acct.items():
        if len(ls) < 4:
            continue
        for r in ls:
            amt = r.debit + r.credit
            others = [x.debit + x.credit for x in ls if x is not r]
            med = Decimal(str(statistics.median(others)))
            if med > 0 and amt >= med * 10 and amt >= floor:
                out.append({"row": r.row, "doc": r.doc_no, "account": acct, "amount": str(amt),
                            "median": str(med.quantize(Decimal("0.01"))), "description": r.description,
                            "date": r.date.isoformat() if r.date else None,
                            "share_of_expenses": str((amt / total_expenses * 100).quantize(Decimal("0.1"))) if total_expenses else None,
                            "prior_account_total": str((prior_by_account or {}).get(acct, "")) or None})
    return out


def unbalanced_docs(rows: list[LedgerRow], limit: int = 20) -> list[dict]:
    by_doc: dict[str, list[LedgerRow]] = defaultdict(list)
    for r in rows:
        if r.doc_no:
            by_doc[r.doc_no].append(r)
    out = []
    for doc, ls in by_doc.items():
        d = sum((r.debit for r in ls), Decimal(0))
        c = sum((r.credit for r in ls), Decimal(0))
        if d != c:
            out.append({"doc": doc, "debit": str(d), "credit": str(c), "diff": str(d - c),
                        "rows": [r.row for r in ls]})
    out.sort(key=lambda x: -abs(Decimal(x["diff"])))
    return out[:limit]
