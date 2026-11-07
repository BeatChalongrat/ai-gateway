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
from math import gcd

from ....engine.ledger import LedgerRow, account_digits, apply_date_order
from ....engine.thai import DATE_ORDERS
from ..words import W

RULES_VERSION = "rules.py@1.1.0"

PL_GROUPS = (
    # key, the key of its name (words.py: said when it is read), predicate on the account digits,
    # natural side (+1 credit, -1 debit)
    ("revenue", "line.revenue", lambda d: d.startswith("41"), 1),
    ("other_income", "line.other_income", lambda d: d.startswith("4") and not d.startswith("41"), 1),
    ("cost_of_sales", "line.cost_of_sales", lambda d: d.startswith("51"), -1),
    ("operating_expenses", "line.operating_expenses", lambda d: d[:2] in ("52", "53", "54", "55", "56", "57"), -1),
    ("finance_costs", "line.finance_costs", lambda d: d.startswith("58"), -1),
    ("income_tax", "line.income_tax", lambda d: d.startswith("59"), -1),
)

STANDARD_NAMES = W.table({
    "41": "doc.std.41", "42": "doc.std.42", "51": "doc.std.51",
    "52": "doc.std.52", "53": "doc.std.53", "54": "doc.std.54",
    "55": "doc.std.55", "56": "doc.std.56", "57": "doc.std.57",
    "58": "doc.std.58", "59": "doc.std.59",
})


GROUP_LABEL = W.table({key: name for key, name, _, _ in PL_GROUPS})
GROUP_FAMILY = {"revenue": "income", "other_income": "income", "cost_of_sales": "expense",
                "operating_expenses": "expense", "finance_costs": "expense", "income_tax": "expense"}
NOT_PL = "not_pl"


def _digits(code) -> str:
    """Account digits for classification; SAP's zero-padded '0000400000' is 400000."""
    return account_digits(code).lstrip("0")


def _thai_group(d: str) -> str | None:
    for key, _, pred, _ in PL_GROUPS:
        if pred(d):
            return key
    return None


def _pl_range(d: str) -> bool:
    return d[:1] in ("4", "5", "6", "7", "8", "9")


def _thai_fit(d: str) -> bool:
    """A P&L code the Thai standard numbering knows: 41-49 income, 51-59 expenses."""
    return len(d) >= 2 and d[0] in "45" and d[1] in "123456789"


# ── names: what an account's own name says ─────────────────────────────────
# Checked in this order; the first that matches wins. English by whole words,
# Thai by substring. A name only ever narrows what the number allows.

_EN_FINANCE = ("interest expense", "interest expenses", "finance cost", "finance costs", "financial cost",
               "financial costs", "interest paid", "loan interest", "bank interest", "borrowing cost",
               "borrowing costs", "cost of borrowing")
_EN_TAX = ("income tax", "income taxes", "corporate tax", "corporate income tax", "tax expense", "taxes on income")
_EN_COGS = ("cost of", "cogs")
_EN_EXPENSE = frozenset((
    "expense", "expenses", "cost", "costs", "salary", "salaries", "wage", "wages", "rent", "depreciation",
    "amortisation", "amortization", "fee", "fees", "charge", "charges", "commission", "commissions",
    "promotion", "advertising", "welfare", "travel", "travelling", "entertainment", "utilities", "electricity",
    "water", "telephone", "internet", "supplies", "freight", "insurance", "repair", "repairs", "maintenance",
    "loss", "losses", "purchase", "purchases", "consumption", "overtime", "bonus", "allowance", "contribution",
    "postage", "stationery", "fuel", "transport", "transportation", "impairment", "write", "penalty"))
_EN_INCOME = frozenset(("revenue", "revenues", "income", "sales", "sale", "gain", "gains", "turnover",
                        "dividend", "dividends"))
_EN_SALES = frozenset(("revenue", "revenues", "sales", "sale", "turnover"))
_EN_OTHER_INCOME = frozenset(("interest", "dividend", "dividends", "gain", "gains", "other", "miscellaneous",
                              "sundry", "exchange"))
_TH_FINANCE = ("ดอกเบี้ยจ่าย", "doc.std.58")
_TH_TAX = ("doc.std.59",)
_TH_COGS = ("doc.std.51", "ต้นทุนบริการ", "ต้นทุนสินค้า")
_TH_EXPENSE = ("ค่าใช้จ่าย", "ต้นทุน", "เงินเดือน", "ขาดทุน")
_TH_INCOME = ("รายได้", "กำไรจาก", "ดอกเบี้ยรับ", "เงินปันผลรับ")
_TH_SALES = ("รายได้จากการขาย", "รายได้จากการให้บริการ", "รายได้ค่าบริการ", "ขายสินค้า")
_TH_OTHER_INCOME = ("ดอกเบี้ยรับ", "เงินปันผลรับ", "doc.std.42", "กำไรจาก")


def name_group(name: str) -> tuple[str | None, str | None]:
    """(family, group) an account name says: family 'income' | 'expense' | None;
    group a P&L line when the name is specific ('Interest Expense'), None when
    it only says the family ('Office Rent' is an expense of some kind)."""
    raw = (name or "").strip()
    if not raw:
        return None, None
    low = raw.lower()
    words = re.findall(r"[a-z]+", low)
    text = " " + " ".join(words) + " "

    def has(phrases) -> bool:
        return any(f" {p} " in text for p in phrases)

    def th(parts) -> bool:
        return any(p in raw for p in parts)

    if has(_EN_FINANCE) or th(_TH_FINANCE):
        return "expense", "finance_costs"
    if has(_EN_TAX) or th(_TH_TAX):
        return "expense", "income_tax"
    if has(_EN_COGS) or th(_TH_COGS):
        return "expense", "cost_of_sales"
    ws = set(words)
    if ws & _EN_EXPENSE or th(_TH_EXPENSE) or raw.startswith("ค่า"):
        return "expense", None
    if ws & _EN_INCOME or th(_TH_INCOME):
        if ws & _EN_OTHER_INCOME or th(_TH_OTHER_INCOME):
            return "income", "other_income"
        if ws & _EN_SALES or th(_TH_SALES):
            return "income", "revenue"
        return "income", None          # 'Rental Income': income, but which line depends on the business
    return None, None


# A chart numbered by its first digit (SAP and most ERPs outside the Thai
# standard): 4 revenue, 5 cost of sales, 6 operating expenses. 7-9 have no
# meaning of their own — other income, finance costs, tax or something else
# depending on the company — so only the account's name can place them.
_RANGE = {"4": ("income", "revenue"), "5": ("expense", "cost_of_sales"), "6": ("expense", "operating_expenses")}


def _resolve(name: str, range_family: str | None, range_default: str | None,
             generic_income: str | None) -> tuple[str | None, str | None]:
    """(group, why) — group None with why 'undecided' when nothing decides."""
    fam, specific = name_group(name)
    if fam is None:
        return (range_default, "number") if range_default else (None, "undecided")
    if range_family and fam != range_family:
        return None, "undecided"      # the name and the number disagree
    if specific:
        return specific, "name"
    if range_default and GROUP_FAMILY[range_default] == fam:
        return range_default, "number"
    if fam == "income" and generic_income:
        return generic_income, "name"
    return None, "undecided"


class Chart:
    """How this run's accounts map to the P&L lines.

    style 'th': every P&L code follows the Thai standard numbering (41
    revenue, 42-49 other income, 51 cost of sales, 52-57 operating expenses,
    58 finance costs, 59 income tax) — read by number only, as always.
    style 'extended': some codes fall outside that numbering (40, 50, 6-9)
    and none uses the Thai 52-59 detail (a 6-digit SAP chart: 400000
    revenue, 500000 cost of sales, 6xxxxx expenses, 7xxxxx …): the first
    digit gives the family and its usual line, the name narrows it (480000
    'Interest Income' is other income, 700000 'Interest Expense' a finance
    cost), and a name that contradicts the number decides nothing. style
    'mixed': Thai-standard codes by number, the others by name only.
    An account nothing decides is `pending` until the person answers
    `account_group:<code>`; it is never put on a P&L line by guess.
    """

    def __init__(self, style: str = "th", names: dict | None = None, answers: dict | None = None):
        self.style = style
        self.names = names or {}
        self.answers = answers or {}
        self.groups: dict[str, str | None] = {}
        self.why: dict[str, str] = {}
        self.codes: dict[str, str] = {}       # digits → the code as the file writes it
        self.pending: list[dict] = []

    def resolve(self, code: str, name: str | None = None) -> tuple[str | None, str]:
        d = _digits(code)
        ans = self.answers.get(f"account_group:{code}")
        if ans == NOT_PL:
            return None, "answer"
        if ans in GROUP_FAMILY:
            return ans, "answer"
        if not _pl_range(d):
            return None, "number"
        if self.style == "th" or (self.style == "mixed" and _thai_fit(d)):
            return _thai_group(d), "number"
        name = self.names.get(code, "") if name is None else name
        if self.style == "extended":
            fam, default = _RANGE.get(d[0], (None, None))
            return _resolve(name, fam, default, "other_income")
        return _resolve(name, None, None, None)

    def group(self, code: str) -> str | None:
        d = _digits(code)
        if d not in self.groups:
            self.groups[d], self.why[d] = self.resolve(code)
            self.codes.setdefault(d, code)
        return self.groups[d]

    def is_pl(self, code: str) -> bool:
        return self.group(code) is not None


THAI_CHART = Chart("th")


def chart_style(codes) -> str:
    pl = [d for d in (_digits(c) for c in codes) if _pl_range(d)]
    if all(_thai_fit(d) for d in pl):
        return "th"
    if not any(d[0] == "5" and d[1:2] in tuple("23456789") for d in pl):
        return "extended"
    return "mixed"


def chart_for(ledgers: list, answers: dict | None = None, named: list | None = None) -> Chart:
    """The chart of the run's ledger files (primary and prior), with names
    from every file. Accounts left undecided are listed in `pending` with
    their rows and amounts, for the check to ask about."""
    names: dict[str, str] = {}
    for f in (named or ledgers):
        names.update({k: v for k, v in f.account_names.items() if v})
    stats: dict[str, dict] = {}
    for f in ledgers:
        for r in f.rows:
            st = stats.setdefault(r.account_code, {"rows": 0, "amount": Decimal(0), "file": f.name, "sample": []})
            st["rows"] += 1
            st["amount"] += abs(r.debit) + abs(r.credit)
            if len(st["sample"]) < 5:
                st["sample"].append([r.row, r.date.isoformat() if r.date else None, r.description[:40],
                                     str(r.debit + r.credit)])
    chart = Chart(chart_style(stats), names, answers)
    for code, st in sorted(stats.items(), key=lambda t: _digits(t[0])):
        d = _digits(code)
        if d in chart.groups:
            continue
        chart.groups[d], chart.why[d] = chart.resolve(code)
        chart.codes[d] = code
        if chart.why[d] == "undecided":
            fam, _ = name_group(names.get(code, ""))
            chart.pending.append({"code": code, "name": names.get(code, ""), "family": fam, **st})
    return chart


def range_default(code: str, chart: Chart) -> str | None:
    """The line an account's first digit stands for in an extended chart."""
    d = _digits(code)
    return _RANGE.get(d[:1], (None, None))[1] if chart.style == "extended" else None


# ── date order: the answer and the remembered value, applied the same way
#    by the checks and by the compute ──────────────────────────────────────

def date_order_answer(pf, answers: dict | None, defaults: dict | None) -> tuple[str | None, str | None]:
    """(order, source) for a file whose date column nothing decided: the
    answer to date_order:<file>, else the install's remembered choice."""
    if (pf.date_order or {}).get("source") != "undecided":
        return None, None
    a = (answers or {}).get(f"date_order:{pf.name}")
    if a in DATE_ORDERS:
        return a, "answer"
    r = ((defaults or {}).get("answers") or {}).get("date_order")
    if r in DATE_ORDERS:
        return r, "remembered"
    return None, None


def apply_date_orders(files: list, answers: dict | None, defaults: dict | None) -> None:
    """Every undecided date column read the way it was answered — or back to
    day-first while it has not been (the rows then wait for the question)."""
    for pf in files:
        order, _ = date_order_answer(pf, answers, defaults)
        apply_date_order(pf, order or "dmy")


def group_of(code: str, chart: Chart | None = None) -> str | None:
    return (chart or THAI_CHART).group(code)


def is_pl(code: str, chart: Chart | None = None) -> bool:
    return group_of(code, chart) is not None


def _norm_desc(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _repeated_under_one_number(ls: list[LedgerRow]) -> tuple[list[int], list[int]] | None:
    """A document whose every line appears k ≥ 2 times was posted k times
    under the same number (an export or import that ran twice). Returns the
    rows of the first copy and the rows of the others; None otherwise. One
    line repeated inside a real document leaves the gcd at 1."""
    by_sig: dict = defaultdict(list)
    for r in sorted(ls, key=lambda r: r.row):
        by_sig[(r.date, account_digits(r.account_code), r.debit, r.credit, _norm_desc(r.description))].append(r.row)
    k = 0
    for rows_ in by_sig.values():
        k = gcd(k, len(rows_))
    if k < 2:
        return None
    original, copies = [], []
    for rows_ in by_sig.values():
        keep = len(rows_) // k
        original += rows_[:keep]
        copies += rows_[keep:]
    return sorted(original), sorted(copies)


def duplicate_docs(rows: list[LedgerRow], chart: Chart | None = None) -> list[dict]:
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
        out = []
        for doc, ls in by_doc.items():
            if not any(is_pl(r.account_code, chart) for r in ls):
                continue  # balance-sheet-only documents (receipts, payments) repeat legitimately
            repeat = _repeated_under_one_number(ls)
            if repeat:
                original_rows, copy_rows = repeat
                pl_line = next(r for r in ls if is_pl(r.account_code, chart))
                out.append({
                    "original": doc, "copies": [doc], "same_number": True,
                    "rows": copy_rows, "original_rows": original_rows,
                    "amount": str(pl_line.debit + pl_line.credit),
                    "account": pl_line.account_code, "description": pl_line.description,
                    "date": pl_line.date.isoformat() if pl_line.date else None,
                })
                continue
            sig = (ls[0].date, tuple(sorted((account_digits(r.account_code), r.debit, r.credit) for r in ls)),
                   _norm_desc(ls[0].description))
            groups[sig].append(doc)
        for sig, docs in groups.items():
            if len(docs) < 2:
                continue
            docs_sorted = sorted(docs, key=lambda d: min(r.row for r in by_doc[d]))
            original, copies = docs_sorted[0], docs_sorted[1:]
            pl_line = next(r for r in by_doc[original] if is_pl(r.account_code, chart))
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
        if is_pl(r.account_code, chart):
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


def expense_total(rows: list[LedgerRow], chart: Chart | None = None) -> Decimal:
    """Net debit of every expense account in `rows` — the base of the 5% floor."""
    return sum((r.debit - r.credit for r in rows if group_of(r.account_code, chart) in EXPENSE_GROUPS), Decimal(0))


def large_entries(rows: list[LedgerRow], total_expenses: Decimal, prior_by_account: dict | None = None,
                  chart: Chart | None = None) -> list[dict]:
    """Single P&L entries far outside their account's own pattern.

    An entry is flagged when its account has ≥3 other entries this period,
    the entry is ≥10× their median, and it is ≥5% of the period's expenses —
    the shape of a typo'd extra zero, not of a month-end journal.
    """
    by_acct: dict[str, list[LedgerRow]] = defaultdict(list)
    for r in rows:
        if is_pl(r.account_code, chart):
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
