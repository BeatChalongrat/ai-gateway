"""metrics.py — the deterministic compute for the Financial Report agent.

All arithmetic is Decimal. The model never adds, subtracts or divides: it is
handed the table this script produces and quotes from it. Every figure in
the output carries a citation id; the citation names the source file, sheet,
cells and this script's version.

Inputs: parsed files + roles + period + the answers given so far.
Output: computed.json (format am.computed.v1).
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from ....engine import words as _words
from ....engine.ledger import (
    LedgerRow, ParsedFile, account_digits, account_level_rows, apply_convention, indicator_guess,
)
from ....engine.tabular import col_letter
from ....engine.thai import (
    fmt_millions, fmt_money, fmt_pct, period_bounds, prev_period,
)
from .rules import (
    PL_GROUPS, RULES_VERSION, STANDARD_NAMES, Chart, apply_date_orders, chart_for, duplicate_docs, expense_total,
    group_of, is_pl, large_entries,
)
from ..words import W
from ..words import printed as _printed

SCRIPT_VERSION = "metrics.py@1.2.0"
# What a rule-flagged exception is, in words (the figure table names an exception's amount by it). These, the
# names of the figures a sentence may quote, the exceptions and the commentary the rules write are read by the
# files and by the model that writes their commentary, never by a screen: the documents' own words, in Thai.
EXC_TH = W.table({"budget_variance_high": "doc.exc.budget_variance_high", "expense_spike": "doc.exc.expense_spike", "new_account": "doc.exc.new_account",
                  "negative_revenue": "doc.exc.negative_revenue", "large_single_entry": "doc.exc.large_single_entry", "unbalanced_ledger": "doc.exc.unbalanced_ledger"})
FORMAT = "am.computed.v1"
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def pct(num: Decimal, den: Decimal) -> Decimal | None:
    if den == 0:
        return None
    return (num / abs(den) * 100).quantize(CENT, rounding=ROUND_HALF_UP)


def s(x) -> str | None:
    return None if x is None else str(x)


def _month(m: str) -> str:
    """A month in the name of a figure: the short form the report's charts print (“ก.ย. 69”); a run in
    another language says the month in full."""
    return _words.period_short(m) if _words.lang() == "th" else _words.period(m)


def _name(label) -> tuple:
    """(name, the other names a question may ask by) of a figure. ``label`` is its name, or what says it: a
    figure's record names it in the run's language, and a person looking at the report may still ask by the
    words it prints (engine/words.names)."""
    return _words.names(lambda: (label(), [])) if callable(label) else (label, [])


def _named(item: dict, also: list) -> dict:
    if also:
        item["aliases"] = list(also)
    return item


# ── citations ─────────────────────────────────────────────────────────────

def cell_ranges(rows: list[int], cols: str) -> str:
    """[12,13,14,40] with 'G:H' → 'G12:H14, G40:H40' — every range, never "and N more" (AM8: a
    reader holding only the report must be able to open each cell a number came from)."""
    if not rows:
        return ""
    a, _, b = cols.partition(":")
    b = b or a
    rows = sorted(set(rows))
    parts = []
    start = prev = rows[0]
    for r in rows[1:] + [None]:
        if r is not None and r == prev + 1:
            prev = r
            continue
        parts.append(f"{a}{start}:{b}{prev}" if (start != prev or a != b) else f"{a}{start}")
        if r is not None:
            start = prev = r
    return ", ".join(parts)


class Citations:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, label, value, refs: list[LedgerRow], formula: str, script: str = SCRIPT_VERSION) -> str:
        cid = f"c{len(self.items) + 1}"
        by_file: dict[tuple, list[int]] = defaultdict(list)
        cols: dict[tuple, str] = {}
        for r in refs:
            by_file[(r.file, r.sheet)].append(r.row)
            cols[(r.file, r.sheet)] = r.amount_cols or cols.get((r.file, r.sheet), "")
        sources = [{"file": f, "sheet": sh, "cells": cell_ranges(rows, cols[(f, sh)] or "A"),
                    "rows": len(rows), "row_numbers": sorted(rows)[:2000]}
                   for (f, sh), rows in by_file.items()]
        label, also = _name(label)
        self.items.append(_named({"id": cid, "label": label, "value": s(value), "sources": sources,
                                  "script": script, "formula": formula}, also))
        return cid

    def cells(self, label, value, file: str, sheet: str, cells: str, formula: str) -> str:
        """A value read from named cells (a budget figure): file · sheet · cells, like any other."""
        cid = f"c{len(self.items) + 1}"
        label, also = _name(label)
        self.items.append(_named({"id": cid, "label": label, "value": s(value),
                                  "sources": [{"file": file, "sheet": sheet, "cells": cells, "rows": 1}],
                                  "script": SCRIPT_VERSION, "formula": formula}, also))
        return cid

    def derived(self, label, value, from_ids: list[str], formula: str, unit: str | None = None) -> str:
        cid = f"c{len(self.items) + 1}"
        label, also = _name(label)
        item = {"id": cid, "label": label, "value": s(value), "sources": [],
                "derived_from": from_ids, "script": SCRIPT_VERSION, "formula": formula}
        if unit:
            item["unit"] = unit  # "pct": the value is a percentage, not baht
        self.items.append(_named(item, also))
        return cid


# ── preparation: answers → the rows that count ────────────────────────────

def _convention_for(pf: ParsedFile, answers: dict, defaults: dict):
    if pf.dc_mode not in ("indicator", "signed"):
        return None
    hm = pf.header
    header = (hm.header_of("dc_indicator") if pf.dc_mode == "indicator" else hm.header_of("amount")) if hm else None
    ans = answers.get("ambiguous_debit_credit")
    if ans is not None and pf.name == answers.get("_primary"):
        return ans
    remembered = (defaults.get("dc_convention") or {}).get(header or "")
    if remembered is not None:
        return remembered
    if ans is not None:
        return ans
    if pf.dc_mode == "indicator":
        return indicator_guess(pf.indicator_values)
    return "positive_debit"


def prepare(files: list[ParsedFile], roles: dict, period: str, answers: dict, defaults: dict) -> dict:
    """Apply every answer; return the rows per role plus what was excluded and why."""
    by_name = {f.name: f for f in files}
    primary = next(by_name[n] for n, r in roles.items() if r == "primary")
    answers = dict(answers)
    answers["_primary"] = primary.name
    start, end = period_bounds(period)
    excluded: list[dict] = []
    notes: list[dict] = []

    apply_date_orders(files, answers, defaults)
    ledgers = [f for f in files if roles.get(f.name) in ("primary", "prior")]
    chart = chart_for(ledgers, answers, files)

    def income(code: str) -> bool:
        return chart.group(code) in ("revenue", "other_income")

    for pf in ledgers:
        apply_convention(pf, _convention_for(pf, answers, defaults))
        if pf.doc_type in ("tb", "pl"):
            target = period if roles[pf.name] == "primary" else (pf.period_hint or prev_period(period))
            account_level_rows(pf, period_bounds(target)[1], income)

    rows = list(primary.rows)
    if chart.pending:
        # Only reached when compute runs before the account_group questions
        # were answered: those accounts stay out of the P&L, and the report says so.
        codes = {p["code"] for p in chart.pending}
        excluded.append({"code": "account_group",
                         "reason": W("left.account_group", codes=", ".join(sorted(codes))),
                         "rows": [r.row for r in rows if r.account_code in codes]})
    base = defaults.get("currency", "THB")
    # AM4: a remembered answer carries only the export's convention ("the amounts are already baht");
    # leaving rows out is this round's decision, asked again every round.
    remembered_cur = (defaults.get("answers") or {}).get("currency_mixed")
    cur_ans = answers.get("currency_mixed") or (remembered_cur if remembered_cur == "amounts_in_base" else None)
    if cur_ans == "exclude_foreign":
        foreign = [r for r in rows if r.currency != base]
        if foreign:
            excluded.append({"code": "currency_mixed", "reason": W("left.currency", n=len(foreign)),
                             "rows": [r.row for r in foreign]})
        rows = [r for r in rows if r.currency == base]

    months = defaultdict(int)
    for r in rows:
        if r.period:
            months[r.period] += 1
    in_p = months.get(period, 0)
    current, history, strays = [], [], []
    for r in rows:
        if r.period == period:
            current.append(r)
        elif r.date and r.date < start and months.get(r.period, 0) >= max(20, in_p * 0.3):
            history.append(r)
        else:
            strays.append(r)
    # AM4: which stray rows to count is this round's decision — never an earlier round's answer.
    pm = answers.get("period_mixed")
    if strays:
        if pm == "include":
            current += strays
            notes.append({"code": "period_mixed", "text": W("note.period_mixed", n=len(strays))})
        else:
            excluded.append({"code": "period_mixed", "reason": W("left.period_mixed", n=len(strays)),
                             "rows": [r.row for r in strays]})

    dup_ans = answers.get("duplicate_suspect")
    if dup_ans == "exclude":
        drop = set()
        for g in duplicate_docs(current, chart):
            drop.update(g["rows"])
        if drop:
            excluded.append({"code": "duplicate_suspect", "reason": W("left.duplicate", n=len(drop)), "rows": sorted(drop)})
            current = [r for r in current if r.row not in drop]

    mat_ans = answers.get("materiality_exceeded")
    if mat_ans == "exclude":
        flagged = large_entries(current, expense_total(current, chart), chart=chart)
        docs = {f["doc"] for f in flagged if f["doc"]}
        rows_drop = {f["row"] for f in flagged}
        drop = {r.row for r in current if (r.doc_no and r.doc_no in docs) or r.row in rows_drop}
        if drop:
            excluded.append({"code": "materiality_exceeded", "reason": W("left.large", n=len(drop)),
                             "rows": sorted(drop)})
            current = [r for r in current if r.row not in drop]

    prior_rows: list[LedgerRow] = []
    prior_file = next((by_name[n] for n, r in roles.items() if r == "prior"), None)
    prior_period = prev_period(period)
    pa = answers.get("prior_period_mismatch")
    if prior_file is not None and pa != "ignore_prior":
        pp = prior_period
        if pa == "use_anyway":
            months_p = defaultdict(int)
            for r in prior_file.rows:
                if r.period:
                    months_p[r.period] += 1
            pp = max(months_p, key=months_p.get) if months_p else prior_period
            notes.append({"code": "prior_period_mismatch", "text": W("note.prior", month=_words.period(pp))})
        prior_rows = [r for r in prior_file.rows if r.period == pp]
        prior_period = pp
    elif history:
        prior_rows = [r for r in history if r.period == prior_period]

    budget_file = next((by_name[n] for n, r in roles.items() if r == "budget"), None)
    return {"primary": primary, "current": current, "history": history, "prior_rows": prior_rows,
            "prior_period": prior_period if prior_rows else None, "prior_file": prior_file,
            "budget_file": budget_file, "excluded": excluded, "notes": notes, "chart": chart}


# ── compute ───────────────────────────────────────────────────────────────

def _acct_sum(rows: list[LedgerRow]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in rows:
        out[r.account_code] += r.debit - r.credit
    return out


def _natural(code: str, net_debit: Decimal, chart: Chart | None = None) -> Decimal:
    """Net debit → the amount as a reader expects it (income positive, expense positive)."""
    g = group_of(code, chart)
    sign = next((side for key, _, _, side in PL_GROUPS if key == g), -1)
    return q2(-net_debit) if sign == 1 else q2(net_debit)


def _budget_cells(budget_file: ParsedFile | None, period: str) -> dict[str, str]:
    """The cell each account's budget for the period was read from (the month's column, else the single one)."""
    if budget_file is None:
        return {}
    month = str(int(period.split("-")[1]))
    return {code: cells.get(month) or cells.get("budget") or "" for code, cells in budget_file.budget_cells.items()}


def _budget_for(budget_file: ParsedFile | None, period: str) -> dict[str, Decimal]:
    if budget_file is None:
        return {}
    month = int(period.split("-")[1])
    out = {}
    for code, by_m in budget_file.budget_by_month.items():
        if month in by_m:
            out[code] = by_m[month]
    for code, v in budget_file.budget.items():
        out.setdefault(code, v)
    return out


def compute(files: list[ParsedFile], roles: dict, period: str, answers: dict | None = None,
            defaults: dict | None = None) -> dict:
    answers = answers or {}
    defaults = defaults or {}
    prep = prepare(files, roles, period, answers, defaults)
    cur_rows = prep["current"]
    chart: Chart = prep["chart"]
    prior_rows = prep["prior_rows"]
    cites = Citations()

    names: dict[str, str] = {}
    for f in files:
        names.update({k: v for k, v in f.account_names.items() if v})
    # Names come from the files only. How a missing name is shown (code only
    # or the standard chart-of-accounts name) is a presentation choice made
    # at render time, so answering that cosmetic question later re-renders
    # without recomputing.

    def acct_name(code: str) -> str:
        return names.get(code, "")

    def account(kind: str, code: str, squeeze: bool = False):
        """What says the name of an account's figure (cite.account.*): “5300-01 ค่าเช่า งวดนี้”. An account its
        file gives no name is named by its code (cite.code.*)."""
        name = acct_name(code)
        if not name:
            return lambda: W(f"cite.code.{kind}", code=code)
        if squeeze:  # (as these names always were: two spaces in a row are one)
            return lambda: W(f"cite.account.{kind}", code=code, name=name).replace("  ", " ")
        return lambda: W(f"cite.account.{kind}", code=code, name=name)

    cur_acc = _acct_sum(cur_rows)
    pri_acc = _acct_sum(prior_rows)
    budget = _budget_for(prep["budget_file"], period)
    has_prior = bool(prior_rows)
    has_budget = bool(budget)

    rows_by_acct: dict[str, list[LedgerRow]] = defaultdict(list)
    for r in cur_rows:
        rows_by_acct[r.account_code].append(r)
    prior_by_acct: dict[str, list[LedgerRow]] = defaultdict(list)
    for r in prior_rows:
        prior_by_acct[r.account_code].append(r)

    # Match budget codes written without the dash ('530001' vs '5300-01').
    budget_by_digits = {account_digits(k): v for k, v in budget.items()}
    budget_cell_by_digits = {account_digits(k): c for k, c in _budget_cells(prep["budget_file"], period).items()}
    bfile = prep["budget_file"]

    accounts = []
    codes = sorted({c for c in list(cur_acc) + list(pri_acc) + list(budget) if is_pl(c, chart)},
                   key=lambda c: account_digits(c))
    seen_digits = set()
    for code in codes:
        dg = account_digits(code)
        if dg in seen_digits:
            continue
        seen_digits.add(dg)
        cur = _natural(code, cur_acc.get(code, ZERO), chart)
        pri = _natural(code, pri_acc.get(code, ZERO), chart) if has_prior else None
        bud = budget_by_digits.get(dg) if has_budget else None
        cid = cites.add(account("now", code), cur, rows_by_acct.get(code, []),
                        W("formula.account.now"))
        pid = cites.add(lambda: W("cite.code.prior", code=code), pri, prior_by_acct.get(code, []), W("formula.account.prior")) if has_prior else None
        bid = vid = None
        if bud is not None and bfile is not None:
            bid = cites.cells(account("budget", code, squeeze=True), q2(bud), bfile.name,
                              bfile.sheet or "", budget_cell_by_digits.get(dg, ""), W("formula.account.budget"))
            vid = cites.derived(account("var_budget", code, squeeze=True), q2(cur - bud), [cid, bid],
                                W("formula.var_budget"))
        accounts.append({
            "code": code, "name": acct_name(code), "group": group_of(code, chart),
            "current": s(cur), "prior": s(pri), "budget": s(q2(bud)) if bud is not None else None,
            "var_prior": s(q2(cur - pri)) if pri is not None else None,
            "var_prior_pct": s(pct(cur - pri, pri)) if pri is not None else None,
            "var_budget": s(q2(cur - bud)) if bud is not None else None,
            "var_budget_pct": s(pct(cur - bud, bud)) if bud is not None else None,
            "rows": len(rows_by_acct.get(code, [])), "cite": cid, "cite_prior": pid,
            "cite_budget": bid, "cite_var_budget": vid,
        })

    def total(group: str, field: str) -> Decimal | None:
        vals = [Decimal(a[field]) for a in accounts if a["group"] == group and a[field] is not None]
        if field != "current" and not vals and not (has_prior if field == "prior" else has_budget):
            return None
        return q2(sum(vals, ZERO))

    lines = []
    line_cite: dict[str, str] = {}

    line_cite_of: dict[str, dict[str, str]] = {}

    def add_line(key, name, cur, pri, bud, formula, from_ids, prior_ids=(), budget_ids=()):
        """A P&L line. Each figure it gives a sentence (this period, the prior one, the budget and the
        differences) has its own citation (AM8): "over budget by 10.5%" cites the budget comparison of
        this line, never another figure that happens to print the same. ``name``: the key of the line's
        name — the table's line carries it as the report prints it, each citation in the run's language."""
        label = _printed(name)
        cid = cites.derived(lambda: W(name), cur, from_ids, formula)
        line_cite[key] = cid
        own = {"cite": cid}
        if pri is not None:
            own["cite_prior"] = cites.derived(lambda: W("cite.prior", label=W(name)), pri, list(prior_ids), formula)
            own["cite_var_prior"] = cites.derived(lambda: W("cite.var_prior", label=W(name)), q2(cur - pri),
                                                  [cid, own["cite_prior"]], W("formula.var_prior.pct"))
        if bud is not None:
            own["cite_budget"] = cites.derived(lambda: W("cite.budget", label=W(name)), bud, list(budget_ids), formula)
            own["cite_var_budget"] = cites.derived(lambda: W("cite.var_budget", label=W(name)), q2(cur - bud), [cid, own["cite_budget"]],
                                                   W("formula.var_budget.pct"))
        line_cite_of[key] = own
        lines.append({
            "key": key, "label": label, "current": s(cur), "prior": s(pri), "budget": s(bud),
            "var_prior": s(q2(cur - pri)) if pri is not None else None,
            "var_prior_pct": s(pct(cur - pri, pri)) if pri is not None else None,
            "var_budget": s(q2(cur - bud)) if bud is not None else None,
            "var_budget_pct": s(pct(cur - bud, bud)) if bud is not None else None,
            **own,
        })

    for key, name, _, _ in PL_GROUPS:
        ids = [a["cite"] for a in accounts if a["group"] == key]
        add_line(key, name, total(key, "current"), total(key, "prior") if has_prior else None,
                 total(key, "budget") if has_budget else None, W("formula.group", label=W(name)), ids,
                 [a["cite_prior"] for a in accounts if a["group"] == key and a.get("cite_prior")],
                 [a["cite_budget"] for a in accounts if a["group"] == key and a.get("cite_budget")])

    def L(key, field="current"):
        v = next(l for l in lines if l["key"] == key)[field]
        return Decimal(v) if v is not None else None

    def combo(field):
        vals = {k: L(k, field) for k in ("revenue", "other_income", "cost_of_sales", "operating_expenses", "finance_costs", "income_tax")}
        if any(v is None for v in vals.values()):
            return None, None, None
        gp = q2(vals["revenue"] - vals["cost_of_sales"])
        te = q2(vals["cost_of_sales"] + vals["operating_expenses"] + vals["finance_costs"] + vals["income_tax"])
        np_ = q2(vals["revenue"] + vals["other_income"] - te)
        return gp, te, np_

    gp_c, te_c, np_c = combo("current")
    gp_p, te_p, np_p = combo("prior") if has_prior else (None, None, None)
    gp_b, te_b, np_b = combo("budget") if has_budget else (None, None, None)
    def of(keys, which):
        return [line_cite_of[k][which] for k in keys if line_cite_of.get(k, {}).get(which)]

    for key, name, cur, pri, bud, formula, parts in (
            ("gross_profit", "line.gross_profit", gp_c, gp_p, gp_b, W("formula.gross_profit"),
             ("revenue", "cost_of_sales")),
            ("total_expenses", "line.total_expenses", te_c, te_p, te_b,
             W("formula.total_expenses"),
             ("cost_of_sales", "operating_expenses", "finance_costs", "income_tax")),
            ("net_profit", "line.net_profit", np_c, np_p, np_b, W("formula.net_profit"),
             ("revenue", "other_income", "total_expenses"))):
        add_line(key, name, cur, pri, bud, formula, of(parts, "cite"), of(parts, "cite_prior"), of(parts, "cite_budget"))

    rev_c, rev_p = L("revenue"), (L("revenue", "prior") if has_prior else None)

    def ratio(key, name, num_key, formula):
        label = _printed(name)
        cur = pct(L(num_key), rev_c) if rev_c else None
        pri = pct(L(num_key, "prior"), rev_p) if has_prior and rev_p else None
        cid = cites.derived(lambda: W(name), cur, [line_cite[num_key], line_cite["revenue"]], formula, unit="pct")
        out = {"key": key, "label": label, "current": s(cur), "prior": s(pri),
               "change_points": s(q2(cur - pri)) if cur is not None and pri is not None else None, "cite": cid}
        if pri is not None:
            out["cite_prior"] = cites.derived(lambda: W("cite.prior", label=W(name)), pri, of((num_key, "revenue"), "cite_prior"), formula,
                                              unit="pct")
            if cur is not None:
                out["cite_change_points"] = cites.derived(lambda: W("cite.change_points", label=W(name)), q2(cur - pri),
                                                          [cid, out["cite_prior"]], W("formula.change_points"),
                                                          unit="points")
        return out

    ratios = [
        ratio("gross_margin", "line.gross_margin", "gross_profit", W("formula.gross_margin")),
        ratio("net_margin", "line.net_margin", "net_profit", W("formula.net_margin")),
        ratio("opex_ratio", "line.opex_ratio", "operating_expenses",
              W("formula.opex_ratio")),
    ]

    # Materiality for exceptions: install value, else 1% of revenue (at least 10,000).
    mat = defaults.get("materiality")
    materiality = Decimal(str(mat)) if mat else max(q2((rev_c or ZERO) * Decimal("0.01")), Decimal("10000.00"))

    # Top movements.
    movers = []
    for a in accounts:
        if a["group"] in ("revenue", "other_income") and not has_prior:
            continue
        if has_prior and a["var_prior"] is not None:
            movers.append((abs(Decimal(a["var_prior"])), a))
        elif not has_prior and a["group"] not in ("revenue", "other_income"):
            movers.append((Decimal(a["current"]), a))
    movers.sort(key=lambda t: -t[0])
    top_movements = [{"code": a["code"], "name": a["name"], "current": a["current"], "prior": a["prior"],
                      "change": a["var_prior"], "change_pct": a["var_prior_pct"], "cite": a["cite"]}
                     for _, a in movers[:6]]

    # Expense composition (for the chart when there is no trend).
    exp_accounts = sorted((a for a in accounts if a["group"] not in ("revenue", "other_income") and Decimal(a["current"]) > 0),
                          key=lambda a: -Decimal(a["current"]))
    composition = [{"code": a["code"], "name": a["name"] or a["code"], "amount": a["current"], "cite": a["cite"]}
                   for a in exp_accounts[:8]]
    rest = sum((Decimal(a["current"]) for a in exp_accounts[8:]), ZERO)
    if rest:
        composition.append({"code": "", "name": W("doc.word.other"), "amount": s(q2(rest)),
                            "cite": cites.derived(lambda: W("cite.other_expenses"), q2(rest),
                                                  [a["cite"] for a in exp_accounts[8:]], W("formula.other_expenses"))})

    # Trend: history months from the export, the prior file, and this period.
    series_rows: dict[str, list[LedgerRow]] = defaultdict(list)
    for r in prep["history"]:
        series_rows[r.period].append(r)
    for r in prior_rows:
        series_rows.setdefault(r.period, [])
        if r not in series_rows[r.period]:
            series_rows[r.period].append(r)
    series_rows[period] = cur_rows
    months_sorted = sorted(series_rows)[-6:]
    trend = {"months": months_sorted, "labels": [_words.period_short(m) for m in months_sorted],
             "revenue": [], "total_expenses": [], "net_profit": [],
             # AM8: every point the chart draws has its own citation (one list per series, one entry per month)
             "cites": {"revenue": [], "total_expenses": [], "net_profit": []}}
    for m, label in zip(months_sorted, trend["labels"]):
        rows_m = series_rows[m]
        acc = _acct_sum(rows_m)
        rev = sum((_natural(c, v, chart) for c, v in acc.items() if group_of(c, chart) == "revenue"), ZERO)
        oth = sum((_natural(c, v, chart) for c, v in acc.items() if group_of(c, chart) == "other_income"), ZERO)
        exp = sum((_natural(c, v, chart) for c, v in acc.items()
                   if group_of(c, chart) not in (None, "revenue", "other_income")), ZERO)
        trend["revenue"].append(s(q2(rev)))
        trend["total_expenses"].append(s(q2(exp)))
        trend["net_profit"].append(s(q2(rev + oth - exp)))
        c_rev = cites.add(lambda: W("cite.trend.revenue", month=_month(m)), q2(rev), [r for r in rows_m if group_of(r.account_code, chart) == "revenue"],
                          W("formula.trend.revenue"))
        c_oth = cites.add(lambda: W("cite.trend.other_income", month=_month(m)), q2(oth),
                          [r for r in rows_m if group_of(r.account_code, chart) == "other_income"], W("formula.trend.other_income"))
        c_exp = cites.add(lambda: W("cite.trend.expenses", month=_month(m)), q2(exp),
                          [r for r in rows_m if group_of(r.account_code, chart) not in (None, "revenue", "other_income")],
                          W("formula.trend.expenses"))
        trend["cites"]["revenue"].append(c_rev)
        trend["cites"]["total_expenses"].append(c_exp)
        trend["cites"]["net_profit"].append(cites.derived(lambda: W("cite.trend.net_profit", month=_month(m)), q2(rev + oth - exp), [c_rev, c_oth, c_exp],
                                                          W("formula.trend.net_profit")))
    trend["cite"] = cites.derived(lambda: W("cite.trend"), None, [c for v in trend["cites"].values() for c in v],
                                  W("formula.trend"))

    # Balance of the period (GL / TB).
    primary = prep["primary"]
    dsum = q2(sum((r.debit for r in cur_rows), ZERO))
    csum = q2(sum((r.credit for r in cur_rows), ZERO))
    balance = {"applicable": primary.doc_type in ("gl", "tb"), "debit": s(dsum), "credit": s(csum),
               "diff": s(q2(dsum - csum)),
               "cite_debit": cites.add(lambda: W("cite.side_total", side=W("word.dr")), dsum, cur_rows, W("formula.side_total", side=W("word.dr"))),
               "cite_credit": cites.add(lambda: W("cite.side_total", side=W("word.cr")), csum, cur_rows, W("formula.side_total", side=W("word.cr")))}

    # Rule-flagged exceptions: reason codes + source rows, never buried in prose.
    exceptions = []
    for a in accounts:
        cur = Decimal(a["current"])
        if a["group"] in ("revenue", "other_income"):
            if cur < 0:
                exceptions.append({"code": "negative_revenue", "title": W("doc.exc.negative_revenue.title", code=a["code"], name=a["name"]),
                                   "detail": W("doc.exc.amount", cur=fmt_money(cur)), "account": a["code"], "amount": a["current"],
                                   "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30], "cite": a["cite"]})
            continue
        if a["budget"] is not None and Decimal(a["budget"]) > 0:
            b = Decimal(a["budget"])
            if cur > b * Decimal("1.5") and cur - b >= materiality:
                times = (cur / b).quantize(Decimal("0.1"))
                exceptions.append({"code": "budget_variance_high",
                                   "title": W("doc.exc.over_budget.title", v=a["name"] or a["code"], cur=fmt_money(cur, 0), times=times),
                                   "detail": W("doc.exc.over_budget.detail", b=fmt_money(b, 0), v=fmt_money(cur - b, 0)),
                                   "account": a["code"], "amount": a["current"],
                                   "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30], "cite": a["cite"]})
                continue
        if a["prior"] is not None:
            p = Decimal(a["prior"])
            if p > 0 and cur > p * 2 and cur - p >= materiality:
                exceptions.append({"code": "expense_spike",
                                   "title": W("doc.exc.spike.title", v=a["name"] or a["code"], v2=fmt_pct(pct(cur - p, p), 0)),
                                   "detail": f"{fmt_money(p, 0)} → {fmt_money(cur, 0)}", "account": a["code"],
                                   "amount": a["current"], "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30],
                                   "cite": a["cite"]})
            elif p == 0 and cur >= materiality and has_prior:
                exceptions.append({"code": "new_account", "title": W("doc.exc.new_account.title", code=a["code"], name=a["name"]),
                                   "detail": W("doc.exc.amount", cur=fmt_money(cur, 0)), "account": a["code"], "amount": a["current"],
                                   "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30], "cite": a["cite"]})
    if answers.get("materiality_exceeded") == "keep":
        for f in large_entries(cur_rows, te_c or ZERO, chart=chart):
            exceptions.append({"code": "large_single_entry",
                               "title": W("doc.exc.single.title", amount=fmt_money(Decimal(f["amount"]), 0), account=f["account"]),
                               "detail": W("doc.exc.single.detail", v=(Decimal(f["amount"]) / Decimal(f["median"])).quantize(Decimal("1"))),
                               "account": f["account"], "amount": f["amount"], "rows": [f["row"]], "cite": None})
    if balance["applicable"] and Decimal(balance["diff"]) != 0:
        exceptions.append({"code": "unbalanced_ledger", "title": W("doc.exc.unbalanced.title", diff=fmt_money(Decimal(balance["diff"]))),
                           "detail": W("doc.exc.unbalanced.detail"), "account": "",
                           "amount": balance["diff"], "rows": [], "cite": balance["cite_debit"]})

    # Pre-formatted strings the narrative may quote. The guard rejects any
    # number in the model's text that is not one of these.
    # AM8: the narrative names a figure by its key ({operating_expenses_var_budget_pct}) and the
    # report puts the figure and its citation there together — a number is never matched to a
    # citation by its printed text (two figures printing "10.5%" once took each other's citation).
    # ``figures`` is that table: key → printed text, the figure's own citation, what it is.
    quotable: dict[str, str] = {}
    quotable_cites: dict[str, str] = {}
    figures: dict[str, dict] = {}

    def addq(key, value, kind="money", cite=None, label=""):
        if value is None:
            return
        d = Decimal(value)
        if kind == "money":
            quotable[key] = fmt_money(d, 0)
            quotable[key + "_m"] = fmt_millions(d)
            figures[key + "_m"] = {"text": quotable[key + "_m"], "cite": cite, "label": W("doc.cite.millions", label=label)}
            if cite:
                quotable_cites[key + "_m"] = cite
            label = W("doc.cite.baht", label=label)
        elif kind == "points":
            quotable[key] = W("doc.points", d=f"{d:+.2f}")
            label = W("doc.cite.points", label=label)
        else:
            quotable[key] = fmt_pct(d)
            label = f"{label} (%)"
        figures[key] = {"text": quotable[key], "cite": cite, "label": label}
        if cite:
            quotable_cites[key] = cite

    for l in lines:
        n = l["label"]
        addq(l["key"], l["current"], cite=l["cite"], label=W("doc.cite.current", n=n))
        addq(l["key"] + "_prior", l["prior"], cite=l.get("cite_prior"), label=W("doc.cite.prior", n=n))
        addq(l["key"] + "_budget", l["budget"], cite=l.get("cite_budget"), label=W("doc.cite.budget", n=n))
        addq(l["key"] + "_var_prior", l["var_prior"], cite=l.get("cite_var_prior"), label=W("doc.cite.change", n=n))
        addq(l["key"] + "_var_budget", l.get("var_budget"), cite=l.get("cite_var_budget"),  # the report's "ต่างจากงบ"
             label=W("doc.cite.var_budget", n=n))
        if l["var_prior_pct"] is not None:
            addq(l["key"] + "_var_prior_pct", l["var_prior_pct"], "pct", cite=l.get("cite_var_prior"),
                 label=W("doc.cite.change", n=n))
        if l["var_budget_pct"] is not None:
            addq(l["key"] + "_var_budget_pct", l["var_budget_pct"], "pct", cite=l.get("cite_var_budget"),
                 label=W("doc.cite.var_budget", n=n))
    for r in ratios:
        addq(r["key"], r["current"], "pct", cite=r["cite"], label=W("doc.cite.ratio_current", label=r["label"]))
        addq(r["key"] + "_prior", r["prior"], "pct", cite=r.get("cite_prior"), label=W("doc.cite.ratio_prior", label=r["label"]))
        if r["change_points"] is not None:
            addq(r["key"] + "_change_points", r["change_points"], "points", cite=r.get("cite_change_points"),
                 label=W("doc.cite.ratio_change", label=r["label"]))
    by_code = {a["code"]: a for a in accounts}
    for m in top_movements:
        n = W("doc.cite.account", code=m["code"], name=m["name"]).strip()
        addq(f"acct_{m['code']}", m["current"], cite=m["cite"], label=W("doc.cite.current", n=n))
        addq(f"acct_{m['code']}_change", m["change"], cite=m["cite"], label=W("doc.cite.change", n=n))
        if m["change_pct"] is not None:
            addq(f"acct_{m['code']}_change_pct", m["change_pct"], "pct", cite=m["cite"], label=W("doc.cite.change", n=n))
    for a in accounts:
        n = W("doc.cite.account", code=a["code"], name=a["name"]).strip()
        if f"acct_{a['code']}" not in figures:
            addq(f"acct_{a['code']}", a["current"], cite=a["cite"], label=W("doc.cite.current", n=n))
        if a["budget"] is not None:
            addq(f"acct_{a['code']}_budget", a["budget"], cite=a.get("cite_budget") or a["cite"], label=W("doc.cite.budget", n=n))
            addq(f"acct_{a['code']}_var_budget", a["var_budget"], cite=a.get("cite_var_budget") or a["cite"],
                 label=W("doc.cite.var_budget", n=n))
            if a["var_budget_pct"] is not None:
                addq(f"acct_{a['code']}_var_budget_pct", a["var_budget_pct"], "pct", cite=a.get("cite_var_budget") or a["cite"],
                     label=W("doc.cite.var_budget", n=n))
    for e in exceptions:
        if e.get("amount"):
            addq(f"exc_{e['code']}_{e.get('account') or 'x'}", e["amount"], cite=e.get("cite"),
                 label=f"{EXC_TH.get(e['code'], e['code'])}: {by_code.get(e.get('account'), {}).get('name') or e.get('account') or ''}".strip())
    quotable["rows_used"] = f"{len(cur_rows):,}"
    figures["rows_used"] = {"text": quotable["rows_used"], "label": _printed("cite.rows_used"),
                            "cite": cites.add(lambda: W("cite.rows_used"), len(cur_rows), cur_rows, W("formula.rows_used"))}
    # Account codes in the tables the model sees are identifiers it may cite
    # ("บัญชี 5300-01"), not figures; the guard skips exactly these.
    quotable_ids = sorted({m["code"] for m in top_movements} | {a["code"] for a in accounts}
                          | {e["account"] for e in exceptions if e.get("account")})

    # The rows that produced the numbers, for the checkable workbook and the
    # run's evidence view (file index into source.files).
    file_index = {f.name: i for i, f in enumerate(files)}
    ledger = [[file_index.get(r.file, 0), r.row, r.date.isoformat() if r.date else None, r.account_code,
               r.description[:120], str(r.debit), str(r.credit), r.doc_no, r.cost_center]
              for r in cur_rows]

    computed = {
        "format": FORMAT,
        "script": SCRIPT_VERSION,
        "rules": RULES_VERSION,
        "period": period,
        "period_label": _words.period(period),
        "prior_period": prep["prior_period"],
        "prior_label": _words.period(prep["prior_period"]) if prep["prior_period"] else None,
        "currency": defaults.get("currency", "THB"),
        "doc_type": primary.doc_type,
        "has_prior": has_prior, "has_budget": has_budget,
        "materiality": s(materiality),
        "source": {
            "primary": {"file": primary.name, "sha256": primary.sha256, "sheet": primary.sheet,
                        "rows_in_file": primary.data_rows, "rows_used": len(cur_rows)},
            "prior": ({"file": prep["prior_file"].name, "sha256": prep["prior_file"].sha256, "rows_used": len(prior_rows)}
                      if prep["prior_file"] is not None and prior_rows else None),
            "budget": ({"file": prep["budget_file"].name, "sha256": prep["budget_file"].sha256}
                       if prep["budget_file"] is not None else None),
            "files": [{"file": f.name, "sha256": f.sha256, "size": f.size, "role": roles.get(f.name),
                       "doc_type": f.doc_type, "rows": f.data_rows} for f in files],
        },
        "excluded": prep["excluded"],
        "notes": prep["notes"],
        "lines": lines,
        "ratios": ratios,
        "accounts": accounts,
        "top_movements": top_movements,
        "composition": composition,
        "trend": trend,
        "balance": balance,
        "exceptions": exceptions,
        "quotable": quotable,
        "quotable_cites": quotable_cites,
        "quotable_ids": quotable_ids,
        "figures": figures,
        "citations": cites.items,
        "ledger_columns": ["file", "row", "date", "account", "description", "debit", "credit", "doc_no", "cost_center"],
        "ledger": ledger,
    }
    computed["fallback_narrative"] = fallback_narrative(computed)
    return computed


def fallback_narrative(c: dict) -> dict:
    """A narrative written by rules, used when no model answered.

    The user never gets an empty report; the report says this text was not
    written by a model. Like a model's text (AM8), it names every figure by
    its key — {revenue} — and the report puts the figure and its citation
    there; no figure is typed into the sentence.
    """
    f = c.get("figures") or {}
    lines = {l["key"]: l for l in c["lines"]}
    rev = lines["revenue"]
    parts = [W("doc.narr.summary", period_label=c["period_label"], revenue="{revenue}", net_profit="{net_profit}")]
    if c["has_prior"] and rev["var_prior_pct"] is not None:
        way = W("doc.narr.up") if Decimal(rev["var_prior_pct"]) > 0 else (W("doc.narr.down") if Decimal(rev["var_prior_pct"]) < 0 else W("doc.narr.flat"))
        parts.append(W("doc.narr.revenue_vs_prior", way=way, pct="{revenue_var_prior_pct:abs}", prior_label=c["prior_label"]))
    nm = next(r for r in c["ratios"] if r["key"] == "net_margin")
    if nm["current"] is not None:
        parts.append(W("doc.narr.margin", margin="{net_margin}"))
    summary = " ".join(parts) + "."
    movers = []
    for m in c["top_movements"][:4]:
        k = f"acct_{m['code']}"
        name = m["name"] or W("doc.narr.account")
        text = W("doc.narr.mover_amount", name=name, figure=f"{{{k}}}") if k in f else name
        if m.get("change_pct") and f"{k}_change_pct" in f:
            way = W("doc.narr.up") if Decimal(m["change_pct"]) > 0 else W("doc.narr.down")
            text += " " + W("doc.narr.mover_change", way=way, pct=f"{{{k}_change_pct:abs}}")
        movers.append(text)
    watch, steps = [], []
    for e in c["exceptions"][:4]:
        name = next((a["name"] for a in c["accounts"] if a["code"] == e.get("account")), "") or W("doc.narr.account")
        k = f"acct_{e.get('account')}"
        if e["code"] == "budget_variance_high" and f"{k}_budget" in f:
            watch.append(W("doc.narr.over_budget", name=name, figure=f"{{{k}}}", budget=f"{{{k}_budget}}", over=f"{{{k}_var_budget}}"))
        elif e["code"] == "unbalanced_ledger":
            watch.append(W("doc.narr.unbalanced"))
        elif k in f:
            watch.append(W("doc.narr.exc_amount", label=EXC_TH.get(e["code"], W("doc.narr.to_check")), name=name, figure=f"{{{k}}}"))
        else:
            watch.append(W("doc.narr.exc", label=EXC_TH.get(e["code"], W("doc.narr.to_check")), name=name))
    watch = watch or [W("doc.exc.none")]
    for e in c["exceptions"][:3]:
        name = next((a["name"] for a in c["accounts"] if a["code"] == e.get("account")), "") or W("doc.narr.this_account")
        if e["code"] == "budget_variance_high":
            steps.append(W("doc.narr.review_budget", name=name))
        elif e["code"] == "unbalanced_ledger":
            steps.append(W("doc.narr.find_unbalanced"))
        else:
            steps.append(W("doc.narr.check", label=EXC_TH.get(e["code"], W("doc.narr.item")), name=name))
    if not steps:
        steps = [W("doc.narr.review_figures")]
    return {"summary": summary, "movements": movers, "watch": watch, "next_steps": steps,
            "written_by": "rules", "model": None}
