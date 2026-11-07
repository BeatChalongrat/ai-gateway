"""Preflight checks — run the moment files arrive, before any compute.

Each check reads the parsed files and returns findings. Nothing here
computes a reported number; it only decides what is unclear. A check that
has nothing to say returns nothing — on clean input the agent asks zero
questions, and the eval set fails the gate if it does not.

Checks: missing_file · wrong_document_type · unmapped_column ·
unreadable_dates · date_order · account_group · ambiguous_debit_credit ·
period_undetermined · period_mixed · currency_mixed · account_name_missing
(cosmetic) · year_convention_mixed (cosmetic).
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from decimal import Decimal

from ....engine import words as _words
from ....engine.findings import FORBIDDEN_WORDS, Finding, finding
from ....engine.headers import FIELD_LABEL
from ....engine.ledger import ParsedFile, apply_convention, indicator_guess
from ....engine.thai import (
    find_period_in_text, fmt_int, fmt_money, parse_date, period_bounds, prev_period, year_style,
)
from ..scripts.rules import (
    GROUP_LABEL, NOT_PL, Chart, apply_date_orders, chart_for, date_order_answer, group_of, range_default,
)
from ..words import W
from ..words import columns as _columns

LEDGER_TYPES = ("gl", "tb", "pl")
# what a file was read as, said when it is read (a table keeps keys: engine/words.py)
DOC_TYPE = W.table({"gl": "doc.gl", "tb": "doc.tb", "pl": "doc.pl", "budget": "doc.budget",
               "unknown": "doc.unknown", "unreadable": "doc.unreadable"})


# ── roles ──────────────────────────────────────────────────────────────────

def dominant_period(pf: ParsedFile) -> tuple[str | None, float, Counter]:
    months = Counter(r.period for r in pf.rows if r.period)
    if not months:
        return pf.period_hint, 0.0, months
    top, n = months.most_common(1)[0]
    return top, n / max(1, sum(months.values())), months


def assign_roles(files: list[ParsedFile], answers: dict) -> dict[str, str]:
    """name → primary | prior | budget | ignored | unknown."""
    roles: dict[str, str] = {}
    ignored = {k.split(":", 1)[1] for k, v in answers.items()
               if k.startswith("wrong_document_type:") and v == "ignore"}
    ledgers = []
    for pf in files:
        if pf.name in ignored:
            roles[pf.name] = "ignored"
        elif pf.doc_type == "budget":
            roles[pf.name] = "budget"
        elif pf.doc_type in LEDGER_TYPES and pf.rows:
            ledgers.append(pf)
        else:
            roles[pf.name] = "unknown"
    # Latest data wins primary; the rest are prior-period candidates. A tie is
    # broken by the file's content (more rows in that month, then its bytes'
    # hash) — never by upload order or name (AM4).
    def latest(pf):
        ps = [r.period for r in pf.rows if r.period] or [pf.period_hint or ""]
        top = max(ps)
        return (top, sum(1 for r in pf.rows if r.period == top), pf.sha256)
    ledgers.sort(key=latest, reverse=True)
    for i, pf in enumerate(ledgers):
        roles[pf.name] = "primary" if i == 0 else "prior"
    return roles


# ── the period ────────────────────────────────────────────────────────────

def resolve_period(primary: ParsedFile, message: str, answers: dict, today: _dt.date) -> tuple[str | None, str]:
    """(period, source). source says where the period came from."""
    if answers.get("period_undetermined"):
        return answers["period_undetermined"], "answer"
    p = find_period_in_text(message or "", today)
    if p:
        return p, "message"
    if primary.period_hint:
        return primary.period_hint, "file_title"
    top, share, months = dominant_period(primary)
    if top and (share >= 0.8 or len(months) == 1):
        return top, "data"
    if primary.doc_type in ("tb", "pl"):
        return None, "none"
    return None, "none"


# ── individual checks ─────────────────────────────────────────────────────

def check_files(files, roles) -> list[Finding]:
    out = []
    for pf in files:
        if roles.get(pf.name) != "unknown":
            continue
        what = DOC_TYPE.get(pf.doc_type, DOC_TYPE["unknown"])
        cols = [h for h in (pf.header.headers if pf.header else []) if h][:6]
        out.append(finding(
            "wrong_document_type", "blocking", "preflight", W("card.wrong_file.header"),
            (W("card.wrong_file.question.unreadable", name=pf.name) if pf.doc_type == "unreadable"
             else W("card.wrong_file.question", name=pf.name)),
            [(W("card.wrong_file.ignore"), "ignore", W("card.wrong_file.ignore.d")),
             (W("card.wrong_file.replace"), "replace", W("card.wrong_file.replace.d"))],
            "ignore",
            evidence={"file": pf.name, "columns": cols, "rows": pf.data_rows, "detected": what,
                      "error": pf.error},
            subject=pf.name, facts={"file": pf.name, "detected": what},
        ))
    return out


def check_missing(files, roles, message: str, answers: dict | None = None) -> list[Finding]:
    answers = answers or {}
    if any(r == "primary" for r in roles.values()):
        out = []
        low = (message or "").lower()
        wants_budget = any(w in low for w in ("งบประมาณ", "budget")) and answers.get("missing_file") != "proceed_without"
        if wants_budget and not any(r == "budget" for r in roles.values()):
            out.append(finding(
                "missing_file", "blocking", "preflight", W("card.missing.header"),
                W("card.missing.budget.question"),
                [(W("card.missing.budget.without"), "proceed_without", W("card.missing.budget.without.d")),
                 (W("card.missing.wait"), "wait", W("card.missing.wait.d"))],
                "wait", kind="request_files", subject="budget",
                request=[{"role": "budget", "label": W("ask.budget.label"),
                          "why": W("ask.budget.why"), "required": False, "accept": [".xlsx", ".csv"]}],
                facts={"missing": "budget"},
            ))
        return out
    return [finding(
        "missing_file", "blocking", "preflight", W("card.missing.header"),
        W("card.missing.primary.question"),
        [(W("card.missing.wait"), "wait", W("card.missing.wait.d")),
         (W("card.missing.cancel"), "cancel", W("card.missing.cancel.d"))],
        "wait", kind="request_files", subject="primary",
        request=[{"role": "primary", "label": W("ask.primary.label"),
                  "why": W("ask.primary.why"), "required": True, "accept": [".xlsx", ".csv"]}],
        facts={"missing": "primary"},
    )]


def check_mapping(primary: ParsedFile, answers: dict) -> list[Finding]:
    hm = primary.header
    if hm is None:
        return []
    key = f"unmapped_column:{primary.name}"
    if key in answers:
        return []
    required = ["account_code"]
    if primary.doc_type == "gl":
        required.append("date")
    if "debit" in hm.fields or "credit" in hm.fields:
        required += ["debit", "credit"]
    else:
        required.append("amount")
    inferred = [f for f in required if hm.source.get(f) == "inferred"]
    missing = [f for f in required if f not in hm.fields]
    if not inferred and not missing:
        return []
    proposal = {f: hm.headers[hm.fields[f]] for f in hm.fields if f in required or hm.source.get(f) == "inferred"}
    guessed = ", ".join(f"{FIELD_LABEL.get(f, f)} = “{hm.headers[hm.fields[f]]}”"
                        for f in required if hm.source.get(f) == "inferred")
    q = (W("card.columns.guessed", name=primary.name, guessed=guessed))
    if missing:
        q = (W("card.columns.missing", name=primary.name,
              columns=", ".join(f"“{FIELD_LABEL.get(f, f)}”" for f in missing)))
    options = [(W("card.columns.accept"), "accept", W("card.columns.accept.d"))] if not missing else []
    options += [(W("card.columns.manual"), "manual", W("card.columns.manual.d")),
                (W("card.columns.replace"), "replace", W("card.columns.replace.d"))]
    return [finding(
        "unmapped_column", "blocking", "preflight", W("card.columns.header"),
        q, options, options[0][1],
        evidence={"file": primary.name, "sheet": primary.sheet, "header_row": hm.header_row + 1,
                  "proposal": proposal, "headers": [h for h in hm.headers if h],
                  "sample_rows": [[r.row, r.date.isoformat() if r.date else None, r.account_code,
                                   str(r.debit), str(r.credit)] for r in primary.rows[:5]]},
        rememberable=True, subject=primary.name, facts={"file": primary.name, "fields": list(proposal)},
    )]


def check_unreadable_dates(files, roles, answers: dict) -> tuple[list[Finding], list[dict]]:
    """Rows with an account and money whose date the parser cannot read are
    left out of the ledger. That must never happen silently: the person
    either sends an export with day/month/year dates or agrees to go on
    without them, and the report says how much was left out."""
    out: list[Finding] = []
    assumptions: list[dict] = []
    for pf in files:
        if not pf.unreadable_date_rows or roles.get(pf.name) not in ("primary", "prior"):
            continue
        key = f"unreadable_dates:{pf.name}"
        n, amount = pf.unreadable_date_rows, pf.unreadable_date_amount
        if key in answers:
            if answers[key] == "exclude":
                assumptions.append({"code": "unreadable_dates", "source": "answer", "value": "exclude",
                                    "text": W("assume.unreadable_dates", n=fmt_int(n), name=pf.name, amount=fmt_money(amount))})
            continue
        examples = []
        for s in pf.unreadable_date_samples:
            if s[1] not in examples:
                examples.append(s[1])
        out.append(finding(
            "unreadable_dates", "blocking", "preflight", W("card.unreadable_dates.header"),
            W("card.unreadable_dates.question", name=pf.name, n=fmt_int(n), examples=", ".join(("“" + e + "”" for e in examples[:3])), amount=fmt_money(amount)),
            [(W("card.unreadable_dates.replace"), "replace", W("card.unreadable_dates.replace.d")),
             (W("card.unreadable_dates.exclude"), "exclude", W("card.unreadable_dates.exclude.d", amount=fmt_money(amount)))],
            "replace",
            evidence={"file": pf.name, "rows": pf.unreadable_date_samples[:20],
                      "columns": _columns("col.row", "col.date_in_file", "col.account", "col.amount")},
            subject=pf.name, facts={"file": pf.name, "rows": n, "amount": str(amount)},
        ))
    return out, assumptions


_ORDER = W.table({"dmy": "order.dmy", "mdy": "order.mdy"})


def _quotable(text: str) -> bool:
    """A file's own words (a column header, an account name) go into a question
    only when the card validator will accept them ('Shipping' holds 'pin');
    otherwise they stay in the evidence."""
    return bool(text) and not any(w in text.lower() for w in FORBIDDEN_WORDS)


def check_date_order(files, roles, answers: dict, defaults: dict, message: str,
                     today: _dt.date) -> tuple[list[Finding], list[dict]]:
    """A date column whose every date has day and month both 1-12, from an
    export that does not say its order (not Thai, not SAP), is read one way
    only after the person says which: read the wrong way, September's rows
    land in eleven other months. The column's own values decide whenever they
    can (engine/headers.decide_date_order); this asks only when they cannot."""
    out: list[Finding] = []
    assumptions: list[dict] = []
    for pf in files:
        info = pf.date_order
        if not info or roles.get(pf.name) not in ("primary", "prior"):
            continue
        column = info.get("column") or W("col.date")
        if info.get("source") == "sap":
            assumptions.append({
                "code": "date_order", "source": "rule", "value": info["order"],
                "text": W("assume.date_order.sap", column=column, name=pf.name, order=_ORDER[info["order"]])})
            continue
        if info.get("source") != "undecided":
            continue
        order, src = date_order_answer(pf, answers, defaults)
        if order:
            if src == "remembered":
                assumptions.append({"code": "date_order", "source": "remembered", "value": order,
                                    "text": W("assume.date_order.remembered", column=column, order=_ORDER[order])})
            continue
        applied = info.get("applied") or "dmy"
        readings: dict[str, Counter] = {"dmy": Counter(), "mdy": Counter()}
        for r in pf.rows:
            if r.date is None:
                continue
            d = r.date
            other = _dt.date(d.year, d.day, d.month) if r.date_swappable else d
            for o, x in ((applied, d), ("mdy" if applied == "dmy" else "dmy", other)):
                readings[o][f"{x.year:04d}-{x.month:02d}"] += 1
        wanted = find_period_in_text(message or "", today)

        def score(o: str) -> int:
            return readings[o].get(wanted, 0) if wanted else (readings[o].most_common(1)[0][1] if readings[o] else 0)

        best = "mdy" if score("mdy") > score("dmy") else "dmy"
        samples = (info.get("evidence") or {}).get("samples", {}).get("ambiguous", [])[:3]
        example = next((s for s in samples if parse_date(s) != parse_date(s, "mdy")), None)
        options = []
        for o in (best, "mdy" if best == "dmy" else "dmy"):
            top = readings[o].most_common(1)
            where = (W("card.date_order.where", month=_words.period(top[0][0]), n=fmt_int(top[0][1]), total=fmt_int(sum(readings[o].values())))) if top else ""
            ex = f"“{example}” = {_words.date(parse_date(example, o))} · " if example else ""
            options.append((_words.cap(_ORDER[o]), o, ex + where))
        which = (W("card.date_order.which.column", column=column, name=pf.name) if _quotable(column)
                 else W("card.date_order.which", name=pf.name))
        out.append(finding(
            "date_order", "blocking", "preflight", W("card.date_order.header"),
            (W("card.date_order.question.samples", which=which, samples=", ".join("“" + s + "”" for s in samples)) if samples
             else W("card.date_order.question", which=which)),
            options, best,
            evidence={"file": pf.name, "column": column, "samples": samples,
                      "months": {o: dict(sorted(c.items())) for o, c in readings.items()},
                      "counts": {k: (info.get("evidence") or {}).get(k) for k in ("ambiguous", "separators")}},
            rememberable=True, subject=pf.name, facts={"file": pf.name, "column": column},
        ))
    return out, assumptions


# what each P&L line holds, for a person choosing one (AM3: the options had no description)
GROUP_HINT = W.table({"revenue": "hint.revenue", "other_income": "hint.other_income",
              "cost_of_sales": "hint.cost_of_sales", "operating_expenses": "hint.operating_expenses",
              "finance_costs": "hint.finance_costs", "income_tax": "hint.income_tax"})

_ACCOUNT_OPTIONS = {
    "income": ("other_income", "revenue"),
    "expense": ("operating_expenses", "cost_of_sales", "finance_costs"),
    None: ("operating_expenses", "other_income", "finance_costs"),
}


def check_accounts(chart: Chart) -> list[Finding]:
    """An account the chart cannot place — outside the Thai standard
    numbering, with a number that does not say its line (7xxxxx-9xxxxx in an
    SAP chart) and a name that does not either, or a name that contradicts
    the number — is asked about, one card per account. It is never put on a
    P&L line by guess, and never dropped from the P&L without a word."""
    out: list[Finding] = []
    for p in chart.pending:
        code, name = p["code"], p["name"]
        cands = list(_ACCOUNT_OPTIONS[p["family"]])
        by_number = range_default(code, chart)
        if by_number and by_number not in cands:
            cands = cands[:2] + [by_number]
        options = [(GROUP_LABEL[g], g, GROUP_HINT.get(g, "")) for g in cands[:3]]
        options.append((W("card.account_group.not_pl"), NOT_PL, W("card.account_group.not_pl.d")))
        account = (W("card.account_group.account.named", code=code, name=name) if _quotable(name)
                   else W("card.account_group.account", code=code))
        out.append(finding(
            "account_group", "blocking", "preflight", W("card.account_group.header"),
            (W("card.account_group.question.conflict", account=account, rows=fmt_int(p["rows"]), amount=fmt_money(p["amount"]))
             if by_number and p["family"] else
             W("card.account_group.question.unknown", account=account, rows=fmt_int(p["rows"]), amount=fmt_money(p["amount"]))),
            options, options[0][1],
            evidence={"file": p["file"], "account": code, "name": name, "count": p["rows"],
                      "amount": str(p["amount"]), "rows": p["sample"],
                      "columns": _columns("col.row", "col.date", "col.description", "col.amount")},
            subject=code, facts={"account": code, "name": name},
        ))
    return out


def chart_assumptions(chart: Chart) -> list[dict]:
    """Say how a chart outside the Thai standard was read, so the report shows it."""
    if chart.style == "th":
        return []
    by_name = [(code, chart.groups[d]) for d, code in sorted(chart.codes.items())
               if chart.why.get(d) == "name" and chart.groups.get(d)]
    named = ", ".join(f"{c} {chart.names.get(c, '')}".strip() + f" ({GROUP_LABEL[g]})" for c, g in by_name[:6])
    text = (W("assume.chart.extended") if chart.style == "extended"
            else W("assume.chart.by_name"))
    if named:
        text = W("assume.chart.named", text=text, named=named)
    return [{"code": "chart_of_accounts", "source": "rule", "value": chart.style, "text": text}]


def _dc_header(pf: ParsedFile) -> str:
    hm = pf.header
    f = "dc_indicator" if pf.dc_mode == "indicator" else "amount"
    return hm.header_of(f) if hm else f


def remembered_convention(pf: ParsedFile, defaults: dict):
    conv = (defaults.get("dc_convention") or {})
    return conv.get(_dc_header(pf))


def _dc_example(pf: ParsedFile, chart: Chart | None) -> str:
    """A row from the person's own file that shows the convention: a revenue account's line (revenue sits on
    the credit side), so the question can be answered by looking at the file rather than by knowing the export."""
    for r in pf.rows:
        if r.raw_indicator and group_of(r.account_code, chart) == "revenue":
            shown = {"+": W("card.dc.shown.plus"), "-": W("card.dc.shown.minus")}.get(r.raw_indicator, W("card.dc.shown.value", value=r.raw_indicator))
            return (" " + W("card.dc.example", row=r.row, account=r.account_code, side=W("word.cr"), shown=shown))
    return ""


def check_dc(pf: ParsedFile, defaults: dict, answers: dict, chart: Chart | None = None) -> list[Finding]:
    if pf.dc_mode not in ("indicator", "signed") or pf.doc_type == "pl":
        return []
    if answers.get("ambiguous_debit_credit") is not None or remembered_convention(pf, defaults) is not None:
        return []
    header = _dc_header(pf)
    import copy

    def revenue_under(conv) -> Decimal:
        trial = copy.deepcopy(pf)
        apply_convention(trial, conv)
        return sum((r.credit - r.debit for r in trial.rows if group_of(r.account_code, chart) == "revenue"),
                   Decimal(0))

    if pf.dc_mode == "indicator":
        guess = indicator_guess(pf.indicator_values) or {v: ("debit" if i == 0 else "credit")
                                                         for i, v in enumerate(pf.indicator_values[:2])}
        flipped = {k: ("credit" if v == "debit" else "debit") for k, v in guess.items()}
        if revenue_under(guess) < 0:
            guess, flipped = flipped, guess
        deb = next((k for k, v in guess.items() if v == "debit"), "?")
        cre = next((k for k, v in guess.items() if v == "credit"), "?")
        q = (W("card.dc.indicator.question", header=header, deb=deb, cre=cre)
             + _dc_example(pf, chart))
        options = [(W("card.dc.indicator.yes", deb=deb), guess, W("card.dc.revenue", amount=fmt_money(revenue_under(guess)))),
                   (W("card.dc.indicator.no", deb=deb, side=W("word.cr")), flipped, W("card.dc.revenue", amount=fmt_money(revenue_under(flipped))))]
        default = guess
    else:
        pos_d = revenue_under("positive_debit")
        q = (W("card.dc.signed.question", header=header) + _dc_example(pf, chart))
        a, b = ("positive_debit", "positive_credit") if pos_d >= 0 else ("positive_credit", "positive_debit")
        label = {"positive_debit": W("card.dc.signed.positive", side=W("word.dr")), "positive_credit": W("card.dc.signed.positive", side=W("word.cr"))}
        options = [(W("card.dc.signed.yes", label=label[a]), a, W("card.dc.revenue", amount=fmt_money(revenue_under(a)))),
                   (_words.cap(label[b]), b, W("card.dc.revenue", amount=fmt_money(revenue_under(b))))]
        default = a
    ev_rows = [[r.row, r.account_code, r.description[:40], str(r.debit + r.credit), r.raw_indicator]
               for r in pf.rows[:5]]
    return [finding(
        "ambiguous_debit_credit", "blocking", "preflight", W("card.dc.header"),
        q, options, default,
        evidence={"file": pf.name, "column": header, "values": pf.indicator_values[:4], "sample_rows": ev_rows,
                  "sample_columns": [*_columns("col.row", "col.account", "col.description", "col.amount"), header]},
        rememberable=True, subject=header, facts={"column": header},
    )]


def check_period(primary: ParsedFile, period: str | None, source: str, answers: dict,
                 defaults: dict) -> tuple[list[Finding], list[dict]]:
    """Returns (findings, assumptions)."""
    out: list[Finding] = []
    assumptions: list[dict] = []
    top, share, months = dominant_period(primary)
    if period is None:
        if primary.doc_type in ("tb", "pl"):
            opts_periods = [primary.period_hint] if primary.period_hint else []
        else:
            opts_periods = sorted(months, reverse=True)[:3]
        if not opts_periods:
            opts_periods = []
        options = [(_words.period(p), p, W("card.period.entries", n=fmt_int(months.get(p, 0)))) for p in opts_periods]
        if len(options) < 2:
            # Offer the neighbouring month so the card is always a real choice.
            base = opts_periods[0] if opts_periods else None
            if base:
                options.append((_words.period(prev_period(base)), prev_period(base), W("card.period.previous")))
        if len(options) >= 2:
            out.append(finding(
                "period_undetermined", "blocking", "preflight", W("card.period.header"),
                W("card.period.question", name=primary.name),
                options[:4], options[0][1],
                evidence={"file": primary.name, "months": {p: n for p, n in sorted(months.items())}},
                subject=primary.name, facts={"months": list(months)},
            ))
        return out, assumptions
    if primary.doc_type not in ("gl",):
        return out, assumptions
    start, end = period_bounds(period)
    in_p = months.get(period, 0)
    stray = []
    for r in primary.rows:
        if r.period == period or r.date is None:
            continue
        # Earlier months with real volume are history for the trend, not strays.
        if r.date < start and months.get(r.period, 0) >= max(20, in_p * 0.3):
            continue
        stray.append(r)
    if not stray:
        return out, assumptions
    total = sum((r.debit for r in stray), Decimal(0))
    stray_months = sorted({r.period for r in stray})
    date_span = f"{min(r.date for r in stray).strftime('%d/%m')}–{max(r.date for r in stray).strftime('%d/%m')}"
    if answers.get("period_mixed") is not None:
        return out, assumptions
    # AM4: never answered from an earlier round — September's "leave the October rows out" must not
    # quietly shape October. The question is about these rows, so it is not rememberable.
    label = _words.period(period).split(" ")[0]
    others = ", ".join(_words.period(p).split(" ")[0] for p in stray_months)
    out.append(finding(
        "period_mixed", "blocking", "preflight", W("card.period_mixed.header"),
        W("card.period_mixed.question", n=fmt_int(len(stray)), date_span=date_span, others=others, total=fmt_money(total), month=label),
        [(W("card.period_mixed.exclude", month=label), "exclude", W("card.period_mixed.exclude.d")),
         (W("card.period_mixed.include"), "include", W("card.period_mixed.include.d"))],
        "exclude",
        evidence={"file": primary.name, "count": len(stray), "debit_total": str(total), "months": stray_months,
                  "rows": [[r.row, r.date.isoformat(), r.doc_no, r.account_code, r.description[:40],
                            str(r.debit), str(r.credit)] for r in stray[:50]],
                  "columns": _columns("col.row", "col.date", "col.doc_no", "col.account", "col.description", "word.dr", "word.cr")},
        rememberable=False, subject="", facts={"count": len(stray), "total": str(total), "period": period},
    ))
    return out, assumptions


def check_currency(primary: ParsedFile, base: str, answers: dict, defaults: dict) -> tuple[list[Finding], list[dict]]:
    foreign = {c: n for c, n in primary.currencies.items() if c and c != base}
    if not foreign or answers.get("currency_mixed") is not None:
        return [], []
    remembered = (defaults.get("answers") or {}).get("currency_mixed")
    # AM4: only the export's convention carries over ("the amounts are already baht"); leaving
    # rows out is asked again every round.
    if remembered == "amounts_in_base":
        return [], [{"code": "currency_mixed", "source": "remembered", "value": remembered,
                     "text": W("assume.currency.remembered")}]
    rows = [r for r in primary.rows if r.currency != base]
    names = ", ".join(W("card.currency.count", currency=c, n=fmt_int(n)) for c, n in foreign.items())
    return [finding(
        "currency_mixed", "blocking", "preflight", W("card.currency.header"),
        W("card.currency.question", names=names),
        [(W("card.currency.in_base"), "amounts_in_base", W("card.currency.in_base.d")),
         (W("card.currency.exclude"), "exclude_foreign", W("card.currency.exclude.d", n=fmt_int(len(rows))))],
        "amounts_in_base",
        evidence={"file": primary.name, "currencies": primary.currencies,
                  "rows": [[r.row, r.date.isoformat() if r.date else None, r.account_code, r.description[:40],
                            str(r.debit), str(r.credit), r.currency] for r in rows[:50]],
                  "columns": _columns("col.row", "col.date", "col.account", "col.description", "word.dr", "word.cr", "col.currency")},
        rememberable=True, facts={"foreign": foreign},
    )], []


def check_cosmetic(primary: ParsedFile, files, answers) -> list[Finding]:
    out = []
    names_known = bool(primary.account_names) or any(f.account_names for f in files if f is not primary)
    if not names_known and "account_name_missing" not in answers:
        out.append(finding(
            "account_name_missing", "cosmetic", "preflight", W("card.account_name.header"),
            W("card.account_name.question"),
            [(W("card.account_name.code_only"), "code_only", W("card.account_name.code_only.d")),
             (W("card.account_name.standard"), "standard_names", W("card.account_name.standard.d"))],
            "code_only", evidence={"file": primary.name, "accounts": sorted({r.account_code for r in primary.rows})[:20]},
            rememberable=True,
        ))
    style = year_style(primary.raw_date_texts)
    if style == "mixed" and "year_convention_mixed" not in answers:
        be_n = sum(1 for t in primary.raw_date_texts if isinstance(t, str) and t.strip()[-4:].startswith("25"))
        out.append(finding(
            "year_convention_mixed", "cosmetic", "preflight", W("card.year.header"),
            W("card.year.question"),
            [(W("card.year.be"), "be", ""), (W("card.year.ce"), "ce", "")],
            "be", evidence={"file": primary.name, "be_texts": be_n, "sample": primary.raw_date_texts[:6]},
            rememberable=True,
        ))
    return out


# ── entry point ───────────────────────────────────────────────────────────

def run(files: list[ParsedFile], *, message: str = "", defaults: dict | None = None,
        answers: dict | None = None, today: _dt.date | None = None) -> dict:
    defaults = defaults or {}
    answers = answers or {}
    today = today or _dt.date.today()
    # Dates first: which month a row is in decides the roles and the period.
    apply_date_orders(files, answers, defaults)
    roles = assign_roles(files, answers)
    findings: list[Finding] = []
    assumptions: list[dict] = []
    findings += check_files(files, roles)
    findings += check_missing(files, roles, message, answers)
    primary = next((f for f in files if roles.get(f.name) == "primary"), None)
    period = source = None
    if primary is not None:
        findings += check_mapping(primary, answers)
        blocking_mapping = any(f.code == "unmapped_column" for f in findings)
        if not blocking_mapping:
            f0, a0 = check_unreadable_dates(files, roles, answers)
            findings += f0
            assumptions += a0
            fd, ad = check_date_order(files, roles, answers, defaults, message, today)
            findings += fd
            assumptions += ad
            chart = chart_for([f for f in files if roles.get(f.name) in ("primary", "prior")], answers, files)
            findings += check_accounts(chart)
            assumptions += chart_assumptions(chart)
            findings += check_dc(primary, defaults, answers, chart)
            period, source = resolve_period(primary, message, answers, today)
            if not any(f.code == "date_order" and f.subject == primary.name for f in fd):
                # the months are only known once the date order is
                f2, a2 = check_period(primary, period, source, answers, defaults)
                findings += f2
                assumptions += a2
            f3, a3 = check_currency(primary, defaults.get("currency", "THB"), answers, defaults)
            findings += f3
            assumptions += a3
            findings += check_cosmetic(primary, files, answers)
    stats = {}
    if primary is not None:
        stats = {"rows": primary.data_rows, "skipped_rows": primary.skipped_rows, "doc_type": primary.doc_type,
                 "debit_total": str(sum((r.debit for r in primary.rows), Decimal(0))) if primary.dc_mode == "columns" else None,
                 "credit_total": str(sum((r.credit for r in primary.rows), Decimal(0))) if primary.dc_mode == "columns" else None}
    return {
        "roles": roles,
        "period": period,
        "period_source": source,
        "prior_period": prev_period(period) if period else None,
        "findings": [f.to_dict() for f in findings],
        "assumptions": assumptions,
        "stats": stats,
        "files": [f.summary() for f in files],
    }
