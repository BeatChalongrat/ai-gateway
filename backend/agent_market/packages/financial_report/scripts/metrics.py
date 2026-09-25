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

from ....engine.ledger import (
    LedgerRow, ParsedFile, account_digits, account_level_rows, apply_convention, indicator_guess,
)
from ....engine.tabular import col_letter
from ....engine.thai import (
    fmt_millions, fmt_money, fmt_pct, period_bounds, period_label_th, period_short_th, prev_period,
)
from .rules import (
    PL_GROUPS, RULES_VERSION, STANDARD_NAMES, duplicate_docs, expense_total, group_of, is_pl, large_entries,
)

SCRIPT_VERSION = "metrics.py@1.0.0"
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


# ── citations ─────────────────────────────────────────────────────────────

def cell_ranges(rows: list[int], cols: str) -> str:
    """[12,13,14,40] with 'G:H' → 'G12:H14, G40:H40'."""
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
    if len(parts) > 6:
        return ", ".join(parts[:6]) + f" และอีก {len(parts) - 6} ช่วง"
    return ", ".join(parts)


class Citations:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, label: str, value, refs: list[LedgerRow], formula: str, script: str = SCRIPT_VERSION) -> str:
        cid = f"c{len(self.items) + 1}"
        by_file: dict[tuple, list[int]] = defaultdict(list)
        cols: dict[tuple, str] = {}
        for r in refs:
            by_file[(r.file, r.sheet)].append(r.row)
            cols[(r.file, r.sheet)] = r.amount_cols or cols.get((r.file, r.sheet), "")
        sources = [{"file": f, "sheet": sh, "cells": cell_ranges(rows, cols[(f, sh)] or "A"),
                    "rows": len(rows), "row_numbers": sorted(rows)[:2000]}
                   for (f, sh), rows in by_file.items()]
        self.items.append({"id": cid, "label": label, "value": s(value), "sources": sources,
                           "script": script, "formula": formula})
        return cid

    def derived(self, label: str, value, from_ids: list[str], formula: str) -> str:
        cid = f"c{len(self.items) + 1}"
        self.items.append({"id": cid, "label": label, "value": s(value), "sources": [],
                           "derived_from": from_ids, "script": SCRIPT_VERSION, "formula": formula})
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

    for pf in files:
        if roles.get(pf.name) in ("primary", "prior"):
            apply_convention(pf, _convention_for(pf, answers, defaults))
            if pf.doc_type in ("tb", "pl"):
                target = period if roles[pf.name] == "primary" else (pf.period_hint or prev_period(period))
                account_level_rows(pf, period_bounds(target)[1])

    rows = list(primary.rows)
    base = defaults.get("currency", "THB")
    cur_ans = answers.get("currency_mixed") or (defaults.get("answers") or {}).get("currency_mixed")
    if cur_ans == "exclude_foreign":
        foreign = [r for r in rows if r.currency != base]
        if foreign:
            excluded.append({"code": "currency_mixed", "reason": f"รายการสกุลเงินอื่น ({len(foreign)} รายการ)",
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
    pm = answers.get("period_mixed") or (defaults.get("answers") or {}).get("period_mixed")
    if strays:
        if pm == "include":
            current += strays
            notes.append({"code": "period_mixed", "text": f"นับรวม {len(strays)} รายการที่ลงวันที่นอกงวดตามที่เลือก"})
        else:
            excluded.append({"code": "period_mixed", "reason": f"ลงวันที่นอกงวด ({len(strays)} รายการ)",
                             "rows": [r.row for r in strays]})

    dup_ans = answers.get("duplicate_suspect")
    if dup_ans == "exclude":
        drop = set()
        for g in duplicate_docs(current):
            drop.update(g["rows"])
        if drop:
            excluded.append({"code": "duplicate_suspect", "reason": f"รายการซ้ำ ({len(drop)} แถว)", "rows": sorted(drop)})
            current = [r for r in current if r.row not in drop]

    mat_ans = answers.get("materiality_exceeded")
    if mat_ans == "exclude":
        flagged = large_entries(current, expense_total(current))
        docs = {f["doc"] for f in flagged if f["doc"]}
        rows_drop = {f["row"] for f in flagged}
        drop = {r.row for r in current if (r.doc_no and r.doc_no in docs) or r.row in rows_drop}
        if drop:
            excluded.append({"code": "materiality_exceeded", "reason": f"รายการผิดปกติที่เลือกไม่นับ ({len(drop)} แถว)",
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
            notes.append({"code": "prior_period_mismatch", "text": f"เทียบกับ{period_label_th(pp)} ตามที่เลือก"})
        prior_rows = [r for r in prior_file.rows if r.period == pp]
        prior_period = pp
    elif history:
        prior_rows = [r for r in history if r.period == prior_period]

    budget_file = next((by_name[n] for n, r in roles.items() if r == "budget"), None)
    return {"primary": primary, "current": current, "history": history, "prior_rows": prior_rows,
            "prior_period": prior_period if prior_rows else None, "prior_file": prior_file,
            "budget_file": budget_file, "excluded": excluded, "notes": notes}


# ── compute ───────────────────────────────────────────────────────────────

def _acct_sum(rows: list[LedgerRow]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in rows:
        out[r.account_code] += r.debit - r.credit
    return out


def _natural(code: str, net_debit: Decimal) -> Decimal:
    """Net debit → the amount as a reader expects it (income positive, expense positive)."""
    g = group_of(code)
    sign = next((side for key, _, _, side in PL_GROUPS if key == g), -1)
    return q2(-net_debit) if sign == 1 else q2(net_debit)


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

    accounts = []
    codes = sorted({c for c in list(cur_acc) + list(pri_acc) + list(budget) if is_pl(c)},
                   key=lambda c: account_digits(c))
    seen_digits = set()
    for code in codes:
        dg = account_digits(code)
        if dg in seen_digits:
            continue
        seen_digits.add(dg)
        cur = _natural(code, cur_acc.get(code, ZERO))
        pri = _natural(code, pri_acc.get(code, ZERO)) if has_prior else None
        bud = budget_by_digits.get(dg) if has_budget else None
        cid = cites.add(f"{code} {acct_name(code)} งวดนี้", cur, rows_by_acct.get(code, []),
                        "ผลรวม เดบิต − เครดิต ของบัญชีนี้ในงวด (รายได้แสดงเป็นบวก)")
        pid = cites.add(f"{code} งวดก่อน", pri, prior_by_acct.get(code, []), "ผลรวมของบัญชีนี้ในงวดก่อน") if has_prior else None
        accounts.append({
            "code": code, "name": acct_name(code), "group": group_of(code),
            "current": s(cur), "prior": s(pri), "budget": s(q2(bud)) if bud is not None else None,
            "var_prior": s(q2(cur - pri)) if pri is not None else None,
            "var_prior_pct": s(pct(cur - pri, pri)) if pri is not None else None,
            "var_budget": s(q2(cur - bud)) if bud is not None else None,
            "var_budget_pct": s(pct(cur - bud, bud)) if bud is not None else None,
            "rows": len(rows_by_acct.get(code, [])), "cite": cid, "cite_prior": pid,
        })

    def total(group: str, field: str) -> Decimal | None:
        vals = [Decimal(a[field]) for a in accounts if a["group"] == group and a[field] is not None]
        if field != "current" and not vals and not (has_prior if field == "prior" else has_budget):
            return None
        return q2(sum(vals, ZERO))

    lines = []
    line_cite: dict[str, str] = {}

    def add_line(key, label, cur, pri, bud, formula, from_ids):
        cid = cites.derived(label, cur, from_ids, formula)
        line_cite[key] = cid
        lines.append({
            "key": key, "label": label, "current": s(cur), "prior": s(pri), "budget": s(bud),
            "var_prior": s(q2(cur - pri)) if pri is not None else None,
            "var_prior_pct": s(pct(cur - pri, pri)) if pri is not None else None,
            "var_budget": s(q2(cur - bud)) if bud is not None else None,
            "var_budget_pct": s(pct(cur - bud, bud)) if bud is not None else None,
            "cite": cid,
        })

    for key, label, _, _ in PL_GROUPS:
        ids = [a["cite"] for a in accounts if a["group"] == key]
        add_line(key, label, total(key, "current"), total(key, "prior") if has_prior else None,
                 total(key, "budget") if has_budget else None, f"ผลรวมบัญชีในหมวด{label}", ids)

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
    add_line("gross_profit", "กำไรขั้นต้น", gp_c, gp_p, gp_b, "รายได้จากการขายและบริการ − ต้นทุนขาย",
             [line_cite["revenue"], line_cite["cost_of_sales"]])
    add_line("total_expenses", "ค่าใช้จ่ายรวม", te_c, te_p, te_b,
             "ต้นทุนขาย + ค่าใช้จ่ายในการขายและบริหาร + ต้นทุนทางการเงิน + ภาษีเงินได้",
             [line_cite[k] for k in ("cost_of_sales", "operating_expenses", "finance_costs", "income_tax")])
    add_line("net_profit", "กำไรสุทธิ", np_c, np_p, np_b, "รายได้ทั้งหมด − ค่าใช้จ่ายรวม",
             [line_cite["revenue"], line_cite["other_income"], line_cite["total_expenses"]])

    rev_c, rev_p = L("revenue"), (L("revenue", "prior") if has_prior else None)

    def ratio(key, label, num_key, formula):
        cur = pct(L(num_key), rev_c) if rev_c else None
        pri = pct(L(num_key, "prior"), rev_p) if has_prior and rev_p else None
        cid = cites.derived(label, cur, [line_cite[num_key], line_cite["revenue"]], formula)
        return {"key": key, "label": label, "current": s(cur), "prior": s(pri),
                "change_points": s(q2(cur - pri)) if cur is not None and pri is not None else None, "cite": cid}

    ratios = [
        ratio("gross_margin", "อัตรากำไรขั้นต้น", "gross_profit", "กำไรขั้นต้น ÷ รายได้จากการขายและบริการ × 100"),
        ratio("net_margin", "อัตรากำไรสุทธิ", "net_profit", "กำไรสุทธิ ÷ รายได้จากการขายและบริการ × 100"),
        ratio("opex_ratio", "ค่าใช้จ่ายขายและบริหารต่อรายได้", "operating_expenses",
              "ค่าใช้จ่ายในการขายและบริหาร ÷ รายได้จากการขายและบริการ × 100"),
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
        composition.append({"code": "", "name": "อื่น ๆ", "amount": s(q2(rest)), "cite": None})

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
    trend = {"months": months_sorted, "labels": [period_short_th(m) for m in months_sorted],
             "revenue": [], "total_expenses": [], "net_profit": []}
    for m in months_sorted:
        acc = _acct_sum(series_rows[m])
        rev = sum((_natural(c, v) for c, v in acc.items() if group_of(c) == "revenue"), ZERO)
        oth = sum((_natural(c, v) for c, v in acc.items() if group_of(c) == "other_income"), ZERO)
        exp = sum((_natural(c, v) for c, v in acc.items() if group_of(c) not in (None, "revenue", "other_income")), ZERO)
        trend["revenue"].append(s(q2(rev)))
        trend["total_expenses"].append(s(q2(exp)))
        trend["net_profit"].append(s(q2(rev + oth - exp)))
    trend["cite"] = cites.derived("แนวโน้มรายเดือน", None, [line_cite["revenue"], line_cite["total_expenses"]],
                                  "ผลรวมรายเดือนจากไฟล์เดียวกันและไฟล์งวดก่อน")

    # Balance of the period (GL / TB).
    primary = prep["primary"]
    dsum = q2(sum((r.debit for r in cur_rows), ZERO))
    csum = q2(sum((r.credit for r in cur_rows), ZERO))
    balance = {"applicable": primary.doc_type in ("gl", "tb"), "debit": s(dsum), "credit": s(csum),
               "diff": s(q2(dsum - csum)),
               "cite_debit": cites.add("ยอดรวมเดบิตของงวด", dsum, cur_rows, "ผลรวมคอลัมน์เดบิตของรายการในงวด"),
               "cite_credit": cites.add("ยอดรวมเครดิตของงวด", csum, cur_rows, "ผลรวมคอลัมน์เครดิตของรายการในงวด")}

    # Rule-flagged exceptions: reason codes + source rows, never buried in prose.
    exceptions = []
    for a in accounts:
        cur = Decimal(a["current"])
        if a["group"] in ("revenue", "other_income"):
            if cur < 0:
                exceptions.append({"code": "negative_revenue", "title": f"รายได้ติดลบ: {a['code']} {a['name']}",
                                   "detail": f"ยอด {fmt_money(cur)}", "account": a["code"], "amount": a["current"],
                                   "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30], "cite": a["cite"]})
            continue
        if a["budget"] is not None and Decimal(a["budget"]) > 0:
            b = Decimal(a["budget"])
            if cur > b * Decimal("1.5") and cur - b >= materiality:
                times = (cur / b).quantize(Decimal("0.1"))
                exceptions.append({"code": "budget_variance_high",
                                   "title": f"{a['name'] or a['code']} {fmt_money(cur, 0)} สูงกว่างบ {times} เท่า",
                                   "detail": f"งบ {fmt_money(b, 0)} · ส่วนต่าง {fmt_money(cur - b, 0)}",
                                   "account": a["code"], "amount": a["current"],
                                   "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30], "cite": a["cite"]})
                continue
        if a["prior"] is not None:
            p = Decimal(a["prior"])
            if p > 0 and cur > p * 2 and cur - p >= materiality:
                exceptions.append({"code": "expense_spike",
                                   "title": f"{a['name'] or a['code']} เพิ่มขึ้น {fmt_pct(pct(cur - p, p), 0)} จากงวดก่อน",
                                   "detail": f"{fmt_money(p, 0)} → {fmt_money(cur, 0)}", "account": a["code"],
                                   "amount": a["current"], "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30],
                                   "cite": a["cite"]})
            elif p == 0 and cur >= materiality and has_prior:
                exceptions.append({"code": "new_account", "title": f"บัญชีใหม่ที่งวดก่อนไม่มี: {a['code']} {a['name']}",
                                   "detail": f"ยอด {fmt_money(cur, 0)}", "account": a["code"], "amount": a["current"],
                                   "rows": [r.row for r in rows_by_acct.get(a["code"], [])][:30], "cite": a["cite"]})
    if answers.get("materiality_exceeded") == "keep":
        for f in large_entries(cur_rows, te_c or ZERO):
            exceptions.append({"code": "large_single_entry",
                               "title": f"รายการเดียว {fmt_money(Decimal(f['amount']), 0)} ในบัญชี {f['account']}",
                               "detail": f"คุณยืนยันว่าถูกต้อง · สูงกว่ารายการปกติของบัญชีนี้ {(Decimal(f['amount']) / Decimal(f['median'])).quantize(Decimal('1'))} เท่า",
                               "account": f["account"], "amount": f["amount"], "rows": [f["row"]], "cite": None})
    if balance["applicable"] and Decimal(balance["diff"]) != 0:
        exceptions.append({"code": "unbalanced_ledger", "title": f"เดบิตไม่เท่ากับเครดิต ต่างกัน {fmt_money(Decimal(balance['diff']))}",
                           "detail": "ทำต่อตามที่เลือก ตัวเลขในรายงานคำนวณจากรายการที่มี", "account": "",
                           "amount": balance["diff"], "rows": [], "cite": balance["cite_debit"]})

    # Pre-formatted strings the narrative may quote. The guard rejects any
    # number in the model's text that is not one of these.
    quotable: dict[str, str] = {}
    quotable_cites: dict[str, str] = {}

    def addq(key, value, kind="money", cite=None):
        if value is None:
            return
        d = Decimal(value)
        if kind == "money":
            quotable[key] = fmt_money(d, 0)
            quotable[key + "_m"] = fmt_millions(d)
            if cite:
                quotable_cites[key + "_m"] = cite
        else:
            quotable[key] = fmt_pct(d)
        if cite:
            quotable_cites[key] = cite

    for l in lines:
        addq(l["key"], l["current"], cite=l["cite"])
        addq(l["key"] + "_prior", l["prior"], cite=l["cite"])
        addq(l["key"] + "_budget", l["budget"], cite=l["cite"])
        addq(l["key"] + "_var_prior", l["var_prior"], cite=l["cite"])
        if l["var_prior_pct"] is not None:
            addq(l["key"] + "_var_prior_pct", l["var_prior_pct"], "pct", cite=l["cite"])
        if l["var_budget_pct"] is not None:
            addq(l["key"] + "_var_budget_pct", l["var_budget_pct"], "pct", cite=l["cite"])
    for r in ratios:
        addq(r["key"], r["current"], "pct", cite=r["cite"])
        addq(r["key"] + "_prior", r["prior"], "pct", cite=r["cite"])
        if r["change_points"] is not None:
            quotable[r["key"] + "_change_points"] = f"{Decimal(r['change_points']):+.2f} จุด"
            quotable_cites[r["key"] + "_change_points"] = r["cite"]
    for m in top_movements:
        addq(f"acct_{m['code']}", m["current"], cite=m["cite"])
        addq(f"acct_{m['code']}_change", m["change"], cite=m["cite"])
        if m["change_pct"] is not None:
            addq(f"acct_{m['code']}_change_pct", m["change_pct"], "pct", cite=m["cite"])
    for a in accounts:
        if a["budget"] is not None:
            addq(f"acct_{a['code']}_budget", a["budget"], cite=a["cite"])
    for e in exceptions:
        if e.get("amount"):
            addq(f"exc_{e['code']}_{e.get('account') or 'x'}", e["amount"], cite=e.get("cite"))
    quotable["rows_used"] = f"{len(cur_rows):,}"
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
        "period_label": period_label_th(period),
        "prior_period": prep["prior_period"],
        "prior_label": period_label_th(prep["prior_period"]) if prep["prior_period"] else None,
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
        "citations": cites.items,
        "ledger_columns": ["file", "row", "date", "account", "description", "debit", "credit", "doc_no", "cost_center"],
        "ledger": ledger,
    }
    computed["fallback_narrative"] = fallback_narrative(computed)
    return computed


def fallback_narrative(c: dict) -> dict:
    """A narrative written by rules, used when no model answered.

    The user never gets an empty report; the report says this text was not
    written by a model.
    """
    q = c["quotable"]
    lines = {l["key"]: l for l in c["lines"]}
    rev, net = lines["revenue"], lines["net_profit"]
    parts = [f"งวด{c['period_label']} รายได้จากการขายและบริการ {q.get('revenue')} บาท กำไรสุทธิ {q.get('net_profit')} บาท"]
    if c["has_prior"] and rev["var_prior_pct"] is not None:
        parts.append(f"รายได้เปลี่ยนแปลง {fmt_pct(Decimal(rev['var_prior_pct']), signed=True)} จาก{c['prior_label']}")
    nm = next(r for r in c["ratios"] if r["key"] == "net_margin")
    if nm["current"] is not None:
        parts.append(f"อัตรากำไรสุทธิ {fmt_pct(Decimal(nm['current']))}")
    summary = " ".join(parts) + "."
    movers = [f"{m['name'] or m['code']}: {fmt_money(Decimal(m['current']), 0)} บาท"
              + (f" ({fmt_pct(Decimal(m['change_pct']), signed=True)})" if m.get("change_pct") else "")
              for m in c["top_movements"][:4]]
    watch = [e["title"] for e in c["exceptions"][:4]] or ["ไม่พบรายการผิดปกติตามกฎที่ตั้งไว้"]
    steps = []
    for e in c["exceptions"][:3]:
        if e["code"] == "budget_variance_high":
            steps.append(f"ทบทวนเหตุที่ {e['title'].split(' ')[0]} สูงกว่างบประมาณ")
        elif e["code"] == "unbalanced_ledger":
            steps.append("หาสาเหตุที่เดบิตไม่เท่ากับเครดิตก่อนปิดงบ")
        else:
            steps.append(f"ตรวจสอบ: {e['title']}")
    if not steps:
        steps = ["ตรวจทานตัวเลขกับผู้รับผิดชอบแต่ละหมวดก่อนส่งผู้บริหาร"]
    _ = net
    return {"summary": summary, "movements": movers, "watch": watch, "next_steps": steps,
            "written_by": "rules", "model": None}
