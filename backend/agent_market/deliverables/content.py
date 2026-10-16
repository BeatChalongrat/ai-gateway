"""One content model, many formats.

The report (.docx), the deck (.pptx) and the workbook are all
rendered from the model built here, so a number, its citation and its
wording are identical in every file of a run — and in any file re-rendered
later from the same computed table.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal

from ..engine import words
from ..engine.thai import fmt_millions, fmt_money, fmt_pct
from ..engine.words import SHARED as S

REQUIRED_SECTIONS = ("summary", "pl", "exceptions", "insights", "assumptions", "sources", "citations")

# AM8: what the report is, and what the agent does not do — said the way the system works.
SCOPE_NOTE = ("docs.report.scope")
DOES_NOT = ("docs.report.does_not")


# AM11b — what a person asked of the documents (agent.json "instructions"; the runner sends the ids a
# model read out of the person's words as req["instructions"]["apply"]). A directive is applied here, at
# render time, and nowhere else: it leaves a section out or orders a table's rows. It never touches a
# figure — the computed table and its citations are the same with or without it — and the sections a
# report must always have (REQUIRED_SECTIONS: the checks' results, the assumptions, the sources) stay.
DIRECTIVES = ("no_charts", "brief", "sort_by_variance")
SECTION_TITLES = S.table({"charts": "docs.section.charts", "movements": "docs.section.movements", "accounts": "docs.section.accounts"})
BRIEF_LEAVES_OUT = ("charts", "movements", "accounts")
CHART_TABLE_HEADS = S.heads(["docs.chart.col.item", "docs.chart.col.month"])


def asked_of(req: dict) -> list[str]:
    """The directives asked for, in a fixed order; an id this renderer does not know is not one."""
    ids = (req.get("instructions") or {}).get("apply") or []
    return [i for i in DIRECTIVES if i in ids]


def variance_order(computed: dict) -> tuple[str | None, list | None]:
    """The accounts from the largest difference to the smallest — the difference from the budget when
    the round has one, else from the prior month. (None, None) when the round has neither: there is no
    difference to order by, and the request cannot be honoured."""
    key = "var_budget" if computed["has_budget"] else ("var_prior" if computed["has_prior"] else None)
    if key is None:
        return None, None

    def size(a):
        v = _d(a.get(key))
        return abs(v) if v is not None else Decimal(-1)

    return key, sorted(computed["accounts"], key=lambda a: -size(a))  # stable: equal sizes keep the ledger's order


def _d(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def money0(v) -> str:
    return "—" if v is None else fmt_money(Decimal(str(v)), 0)


def pct_or_dash(v, signed=True) -> str:
    return "—" if v is None else fmt_pct(Decimal(str(v)), 1, signed=signed)


def date_th(d: _dt.date) -> str:
    """A day as the report prints it, in the run's language (the name is from before a report could be English)."""
    return words.date(d.isoformat())


# ── figures in prose (AM8) ────────────────────────────────────────────────
#
# A sentence names a figure by its key — "{operating_expenses_var_budget_pct}" —
# and the figure's printed text and its own citation are put there together,
# from computed.json's ``figures`` table. Nothing is matched by its printed
# text afterwards: two figures that print the same ("10.5%") once took each
# other's citation. ":abs" prints the figure without its sign or brackets, for
# a sentence that says the direction in words ("ลดลง {x:abs}").

FIGURE = re.compile(r"\{([A-Za-z0-9_][A-Za-z0-9_.\-/]*)(?::(abs))?\}")


def figure_text(fig: dict, mod: str | None = None) -> str:
    text = str(fig.get("text") or "")
    if mod == "abs":
        text = text.lstrip("+-")
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
    return text


def cite_runs(text: str, computed: dict) -> list[tuple[str, str | None]]:
    """Split prose into (text, cite_id) runs: each {key} becomes its figure, followed by its cite.

    A key that is not in the table stays as written: the gate finds it in the finished file."""
    figures = computed.get("figures") or {}
    out: list[tuple[str, str | None]] = []
    pos = 0
    for m in FIGURE.finditer(text or ""):
        fig = figures.get(m.group(1))
        if fig is None:
            continue
        out.append((text[pos:m.start()] + figure_text(fig, m.group(2)), fig.get("cite")))
        pos = m.end()
    out.append(((text or "")[pos:], None))
    return [(t, c) for t, c in out if t]


def plain_text(text: str, computed: dict) -> str:
    """The sentence as printed, with its figures and without the citation marks (the deck's body text)."""
    return "".join(t for t, _ in cite_runs(text, computed))


def cites_in(text: str, computed: dict) -> list[str]:
    out: list[str] = []
    for _, c in cite_runs(text, computed):
        if c and c not in out:
            out.append(c)
    return out


# ── the model ─────────────────────────────────────────────────────────────

def _line(computed, key):
    return next(l for l in computed["lines"] if l["key"] == key)


def _ratio(computed, key):
    return next(r for r in computed["ratios"] if r["key"] == key)


def kpis(computed: dict) -> list[dict]:
    has_prior = computed["has_prior"]
    out = []
    for key, label, good_when_up in (("revenue", S("docs.kpi.revenue"), True),
                                     ("total_expenses", S("docs.kpi.total_expenses"), False),
                                     ("net_profit", S("docs.kpi.net_profit"), True)):
        l = _line(computed, key)
        delta = None
        tone = "flat"
        if has_prior and l["var_prior_pct"] is not None:
            v = Decimal(l["var_prior_pct"])
            delta = S("docs.kpi.vs_prior", v=fmt_pct(v, 1, signed=True), prior_label=computed["prior_label"])
            tone = "up" if v > 0 else ("down" if v < 0 else "flat")
        elif computed["has_budget"] and l["var_budget_pct"] is not None:
            v = Decimal(l["var_budget_pct"])
            delta = S("docs.kpi.vs_budget", v=fmt_pct(v, 1, signed=True))
            tone = "up" if v > 0 else ("down" if v < 0 else "flat")
        good = None if tone == "flat" else ((tone == "up") == good_when_up)
        out.append({"key": key, "label": label, "value": money0(l["current"]),
                    "value_short": fmt_millions(Decimal(l["current"])), "delta": delta, "tone": tone,
                    "good": good, "cite": l["cite"]})
    nm = _ratio(computed, "net_margin")
    delta = S("docs.points", change_points=f"{Decimal(nm['change_points']):+.1f}") if nm["change_points"] is not None else None
    tone = "flat" if not nm["change_points"] else ("up" if Decimal(nm["change_points"]) > 0 else "down")
    out.append({"key": "net_margin", "label": S("docs.kpi.net_margin"), "value": pct_or_dash(nm["current"], signed=False),
                "value_short": pct_or_dash(nm["current"], signed=False), "delta": delta, "tone": tone,
                "good": None if tone == "flat" else tone == "up", "cite": nm["cite"]})
    return out


def pl_table(computed: dict) -> dict:
    has_prior, has_budget = computed["has_prior"], computed["has_budget"]
    cols = [S("docs.col.item"), S("docs.col.current")]
    if has_prior:
        cols += [computed["prior_label"] or S("docs.col.prior"), S("docs.col.change"), "%"]
    if has_budget:
        cols += [S("docs.col.budget"), S("docs.col.var_budget")]
    cols.append(S("docs.col.ref"))
    order = ["revenue", "other_income", "cost_of_sales", "gross_profit", "operating_expenses", "finance_costs",
             "income_tax", "total_expenses", "net_profit"]
    emphasis = {"gross_profit", "net_profit"}
    rows, strong = [], []
    for key in order:
        l = _line(computed, key)
        if key in ("income_tax", "other_income", "finance_costs") and Decimal(l["current"]) == 0 and \
                (l["prior"] is None or Decimal(l["prior"]) == 0):
            continue
        r = [l["label"], money0(l["current"])]
        if has_prior:
            r += [money0(l["prior"]), money0(l["var_prior"]), pct_or_dash(l["var_prior_pct"])]
        if has_budget:
            r += [money0(l["budget"]), money0(l["var_budget"])]
        r.append(l["cite"])
        if key in emphasis:
            strong.append(len(rows))
        rows.append(r)
    for rt in computed["ratios"]:
        r = [rt["label"], pct_or_dash(rt["current"], signed=False)]
        if has_prior:
            r += [pct_or_dash(rt["prior"], signed=False),
                  "—" if rt["change_points"] is None else S("docs.points", change_points=f"{Decimal(rt['change_points']):+.1f}"), ""]
        if has_budget:
            r += ["", ""]
        r.append(rt["cite"])
        rows.append(r)
    return {"type": "table", "columns": cols, "rows": rows, "strong_rows": strong,
            "numeric_from": 1, "cite_col": len(cols) - 1,
            "caption": S("docs.caption.pl") + " " + computed["script"]}


def account_table(computed: dict, limit: int = 40, by_variance: bool = False) -> dict:
    has_prior, has_budget = computed["has_prior"], computed["has_budget"]
    accounts, caption, order = computed["accounts"], S("docs.caption.unit_baht"), None
    if by_variance:
        key, ordered = variance_order(computed)
        if ordered is not None:
            accounts, order = ordered, key
            caption = S("docs.caption.by_variance", source=S("docs.variance.budget") if key == "var_budget" else S("docs.variance.prior"))
    cols = [S("docs.col.code"), S("docs.col.account_name"), S("docs.col.current")]
    if has_prior:
        cols += [S("docs.col.prior"), "%"]
    if has_budget:
        cols += [S("docs.col.budget"), S("docs.col.var_budget")]
    cols.append(S("docs.col.ref"))
    rows = []
    for a in accounts[:limit]:
        r = [a["code"], a["name"] or "—", money0(a["current"])]
        if has_prior:
            r += [money0(a["prior"]), pct_or_dash(a["var_prior_pct"])]
        if has_budget:
            r += [money0(a["budget"]), money0(a["var_budget"])]
        r.append(a["cite"])
        rows.append(r)
    return {"type": "table", "columns": cols, "rows": rows, "strong_rows": [], "numeric_from": 2,
            "cite_col": len(cols) - 1, "caption": caption, "small": True, "ordered_by": order}


def movement_table(computed: dict) -> dict | None:
    if not computed["top_movements"]:
        return None
    has_prior = computed["has_prior"]
    cols = [S("docs.col.account"), S("docs.col.current")] + ([S("docs.col.prior"), S("docs.col.change"), "%"] if has_prior else []) + [S("docs.col.ref")]
    rows = []
    for m in computed["top_movements"]:
        r = [f"{m['code']} {m['name']}".strip(), money0(m["current"])]
        if has_prior:
            r += [money0(m["prior"]), money0(m["change"]), pct_or_dash(m["change_pct"])]
        r.append(m["cite"])
        rows.append(r)
    return {"type": "table", "columns": cols, "rows": rows, "strong_rows": [], "numeric_from": 1,
            "cite_col": len(cols) - 1,
            "caption": S("docs.caption.movements") if has_prior else S("docs.caption.largest")}


EXC_LABEL = S.table({
    "budget_variance_high": "docs.exc.budget_variance_high", "expense_spike": "docs.exc.expense_spike", "new_account": "docs.exc.new_account",
    "negative_revenue": "docs.exc.negative_revenue", "large_single_entry": "docs.exc.large_single_entry", "unbalanced_ledger": "docs.exc.unbalanced_ledger",
})


def exceptions_table(computed: dict) -> dict:
    rows = []
    src = computed["source"]["primary"]["file"]
    for e in computed["exceptions"]:
        where = S("docs.where.rows", src=src, v=", ".join((str(r) for r in e["rows"][:6]))) + (" …" if len(e["rows"]) > 6 else "") \
            if e["rows"] else src
        rows.append([e["code"], EXC_LABEL.get(e["code"], e["code"]) + ": " + e["title"], e["detail"], where,
                     e.get("cite") or ""])
    return {"type": "table", "columns": [S("docs.col.reason_code"), S("docs.col.item"), S("docs.col.detail"), S("docs.col.source_row"), S("docs.col.ref")],
            "rows": rows, "strong_rows": [], "numeric_from": None, "cite_col": 4, "small": True,
            "empty_text": S("docs.exc.none_with", materiality=money0(computed["materiality"]))}


def charts(computed: dict) -> list[dict]:
    """Chart specs whose values are taken verbatim from the computed table.

    Every point a chart draws carries its own citation (``series[].cites``, one per category) and
    the chart cites all of them (``cites``) — not its first row (AM8)."""
    out = []
    t = computed["trend"]
    tc = t.get("cites") or {}
    if len(t["months"]) >= 2:
        out.append({
            "id": "trend", "kind": "line", "title": S("docs.chart.trend", n=len(t["months"])),
            "subtitle": S("docs.caption.unit_m"), "categories": t["labels"], "scale": 1_000_000,
            "series": [{"name": S("docs.series.revenue"), "values": t["revenue"], "path": "trend.revenue",
                        "cites": tc.get("revenue") or [t["cite"]] * len(t["labels"])},
                       {"name": S("docs.kpi.total_expenses"), "values": t["total_expenses"], "path": "trend.total_expenses",
                        "cites": tc.get("total_expenses") or [t["cite"]] * len(t["labels"])},
                       {"name": S("docs.kpi.net_profit"), "values": t["net_profit"], "path": "trend.net_profit",
                        "cites": tc.get("net_profit") or [t["cite"]] * len(t["labels"])}],
        })
    if computed["has_budget"]:
        rows = [a for a in computed["accounts"] if a["var_budget"] is not None and a["group"] not in ("revenue", "other_income")
                and Decimal(a["var_budget"]) != 0]
        rows.sort(key=lambda a: -abs(Decimal(a["var_budget"])))
        rows = rows[:7]
        if rows:
            out.append({
                "id": "budget_variance", "kind": "barh", "title": S("docs.chart.budget"),
                "subtitle": S("docs.chart.budget_sub"),
                "categories": [a["name"] or a["code"] for a in rows], "scale": 1,
                "series": [{"name": S("docs.col.var_budget"), "values": [a["var_budget"] for a in rows],
                            "path": "accounts[].var_budget", "codes": [a["code"] for a in rows],
                            "cites": [a.get("cite_var_budget") or a["cite"] for a in rows]}],
                "signed": True,
            })
    comp = computed["composition"]
    if comp:
        out.append({
            "id": "composition", "kind": "barh", "title": S("docs.chart.composition"),
            "subtitle": S("docs.caption.unit_m"), "categories": [c["name"] for c in comp], "scale": 1_000_000,
            "series": [{"name": S("docs.col.current"), "values": [c["amount"] for c in comp], "path": "composition[].amount",
                        "codes": [c.get("code") or "" for c in comp], "cites": [c.get("cite") for c in comp]}],
        })
    for spec in out:
        seen: list[str] = []
        for ser in spec["series"]:
            for c in ser.get("cites") or []:
                if c and c not in seen:
                    seen.append(c)
        spec["cites"] = seen
        spec["cite"] = ", ".join(seen)  # what a caption prints
    return out


def chart_table(spec: dict) -> dict:
    """The numbers a chart draws, as a table under it: a reader can read each point and its citation,
    and the gate reads them back out of the finished file (AM8)."""
    scale = Decimal(str(spec.get("scale", 1)))
    unit = S("docs.unit.millions") if scale == 1_000_000 else S("docs.unit.baht")
    # a heading of its own ("…ในกราฟ"): with one month and no budget, the P&L table's columns are the same
    # words, and a reader (and the gate) must tell the two tables apart
    cols = [S("docs.chart.col.item") if spec["kind"] == "barh" else S("docs.chart.col.month")] + [s["name"] for s in spec["series"]] + [S("docs.col.ref")]
    rows = []
    for i, cat in enumerate(spec["categories"]):
        vals = []
        for s in spec["series"]:
            v = Decimal(str(s["values"][i]))
            vals.append(f"{(v / scale).quantize(Decimal('0.01')):,.2f}" if scale != 1 else money0(v))
        cites = [c for c in ((s.get("cites") or [None] * len(spec["categories"]))[i] for s in spec["series"]) if c]
        rows.append([str(cat)] + vals + [", ".join(dict.fromkeys(cites))])
    return {"type": "table", "columns": cols, "rows": rows, "strong_rows": [], "numeric_from": 1,
            "cite_col": len(cols) - 1, "small": True, "chart_data": spec["id"],
            "caption": S("docs.caption.chart_table", unit=unit)}


def apply_presentation(computed: dict, pres: dict) -> dict:
    """Cosmetic answers applied at render time (never in compute)."""
    from ..engine.thai import period_label_th

    if not pres:
        return computed
    c = dict(computed)
    # Keys are the cosmetic findings' codes (the runner passes cosmetic answers as-is).
    pres = {"year_style": pres.get("year_style") or pres.get("year_convention_mixed"),
            "account_names": pres.get("account_names") or pres.get("account_name_missing")}
    if pres.get("year_style") == "ce":
        # (an English label is in the common era already)
        ce = (lambda p: words.period(p)) if words.lang() == "en" else (lambda p: period_label_th(p, "ce"))
        c["period_label"] = ce(c["period"])
        if c.get("prior_period"):
            c["prior_label"] = ce(c["prior_period"])
    if pres.get("account_names") == "standard_names":
        from ..engine.ledger import account_digits
        from ..packages.financial_report.scripts.rules import STANDARD_NAMES

        def fill(items, key="name"):
            out = []
            for it in items:
                it = dict(it)
                if not it.get(key):
                    it[key] = STANDARD_NAMES.get(account_digits(it.get("code", ""))[:2], "")
                out.append(it)
            return out

        c["accounts"] = fill(c["accounts"])
        c["top_movements"] = fill(c["top_movements"])
        c["composition"] = [dict(x, name=x["name"] if x["name"] != x.get("code") else
                                 (STANDARD_NAMES.get(account_digits(x.get("code", ""))[:2]) or x["name"]))
                            for x in c["composition"]]
    return c


def sentences(narrative: dict, key: str) -> list:
    """One of a narrative's lists, as a list of sentences. A model may leave a
    list null, send one sentence instead of a list, or put something that is
    not a sentence among them (AM5: a null list reached the loop below and the
    round failed on it); none of that is the report's problem."""
    value = narrative.get(key)
    if not isinstance(value, (list, tuple)):
        return []
    return [t for t in value if isinstance(t, str) and t.strip()]


def build(computed: dict, req: dict) -> dict:
    computed = apply_presentation(computed, req.get("presentation") or {})
    narrative = dict(req.get("narrative") or computed["fallback_narrative"])
    for key in ("movements", "watch", "next_steps"):
        narrative[key] = sentences(narrative, key)  # the report and the deck both read these
    assumptions = list(req.get("assumptions") or [])
    for e in computed["excluded"]:
        assumptions.append({"code": e["code"], "source": "answer", "text": S("docs.assume.excluded", reason=e["reason"]),
                            "changes_numbers": True})
    for n in computed["notes"]:
        assumptions.append({"code": n["code"], "source": "answer", "text": n["text"], "changes_numbers": True})
    if not computed["has_prior"]:
        assumptions.append({"code": "no_prior", "source": "data", "text": S("docs.assume.no_prior")})
    if not computed["has_budget"]:
        assumptions.append({"code": "no_budget", "source": "data", "text": S("docs.assume.no_budget")})
    if not any(a["group"] == "income_tax" for a in computed["accounts"]):
        # AM8 (the audit): a zero tax line is the absence of a tax account, not a computed tax
        assumptions.append({"code": "no_income_tax", "source": "data",
                            "text": S("docs.assume.no_income_tax")})
    assumptions.append({"code": "materiality", "source": "rule",
                        "text": S("docs.assume.materiality", materiality=money0(computed["materiality"]))})
    # Deduplicate by text, keep order.
    seen, uniq = set(), []
    for a in assumptions:
        if a["text"] not in seen:
            seen.add(a["text"])
            uniq.append(a)
    assumptions = uniq

    run = req.get("run") or {}
    model = req.get("model_info") or {}
    today = _dt.date.fromisoformat(run.get("date")) if run.get("date") else _dt.date.today()
    theme_company = (req.get("theme") or {}).get("company") or ""
    src = computed["source"]
    compare_bits = []
    if computed["has_prior"]:
        compare_bits.append(computed["prior_label"])
    if computed["has_budget"]:
        compare_bits.append(S("docs.col.budget"))
    doc_type_th = {"gl": S("docs.doctype.gl"), "tb": S("docs.doctype.tb"), "pl": S("docs.section.pl")}.get(computed["doc_type"], S("docs.doctype.file"))
    cover_lines = [S("docs.cover.made_from", doc_type_th=doc_type_th, rows_used=f"{src['primary']['rows_used']:,}")]
    if compare_bits:
        cover_lines.append(S("docs.cover.compared", what=words.and_(compare_bits)))

    sources_rows = []
    for f in src["files"]:
        role = {"primary": S("docs.role.primary"), "prior": S("docs.col.prior"), "budget": S("docs.col.budget"), "ignored": S("docs.role.unused"),
                "unknown": S("docs.role.unused")}.get(f["role"], f["role"] or "")
        sources_rows.append([f["file"], role, f"{f['rows']:,}", f["sha256"][:16] + "…"])
    citation_rows = []
    for c in computed["citations"]:
        if c["sources"]:
            where = "; ".join(source_where(s) for s in c["sources"])
        elif c.get("derived_from"):
            where = S("docs.cite.derived") + " " + ", ".join(c["derived_from"])  # every one: a reader follows each (AM8)
        else:
            where = "—"
        numeric = c["value"] not in (None, "") and re.match(r"^-?\d+(\.\d+)?$", str(c["value"]))
        if numeric and c.get("unit") == "pct":
            val = fmt_pct(Decimal(str(c["value"])), 1)  # a ratio, not baht ("อัตรากำไรสุทธิ 2.4%", not "2")
        elif numeric and c.get("unit") == "points":
            val = S("docs.points2", value=f"{Decimal(str(c['value'])):+.2f}")
        else:
            val = money0(c["value"]) if numeric else (c["value"] or "")
        citation_rows.append([c["id"], c["label"], val, where, f"{c['script']} · {c['formula']}"])

    about = [
        S("docs.about.model", model=model.get("model") or S("docs.about.no_model"),
          place=" " + S("docs.model.inside") if model.get("self_hosted") else (" " + S("docs.model.outside") if model.get("model") else "")),
        S("docs.about.sees"),
        S("docs.about.computation", script=computed["script"], rules=computed["rules"]),
        S("docs.about.run", v=run.get("id", "—"), v2=run.get("agent_version", "—"), v3=req.get("theme", {}).get("name", ""), v4=req.get("theme", {}).get("version", 1)),
        S(SCOPE_NOTE),
        S(DOES_NOT),
    ]
    if narrative.get("written_by") == "rules":
        about.insert(1, S("docs.about.rules_wrote"))

    # AM11b: what the person asked of the documents (asked_of). Ordering by the difference needs one.
    ask = asked_of(req)
    refused = [i for i in ask if i == "sort_by_variance" and variance_order(computed)[0] is None]
    by_variance = "sort_by_variance" in ask and not refused
    chart_specs = [] if "no_charts" in ask else charts(computed)
    chart_blocks = []
    for c in chart_specs:
        chart_blocks += [{"type": "chart", "chart": c["id"]}, chart_table(c)]
    sections = [
        {"key": "summary", "title": S("docs.section.summary"), "blocks": [b for b in (
            {"type": "paragraph", "runs": cite_runs(narrative["summary"], computed), "narrative": True},
            # AM22 §1.1: a commentary the rules wrote says so on the page, under the words themselves; one a
            # model wrote never carries the line (the appendix names the model)
            {"type": "paragraph", "runs": [(S("doc.commentary.rules_wrote"), None)], "muted": True}
            if narrative.get("written_by") == "rules" else None,
        ) if b]},
        {"key": "pl", "title": S("docs.section.pl"), "blocks": [pl_table(computed)]},
        {"key": "charts", "title": SECTION_TITLES["charts"], "blocks": chart_blocks},
        {"key": "movements", "title": SECTION_TITLES["movements"], "blocks": [b for b in (
            movement_table(computed),
            {"type": "bullets", "items": [cite_runs(t, computed) for t in sentences(narrative, "movements")],
             "narrative": True}
            if sentences(narrative, "movements") else None,
        ) if b]},
        {"key": "accounts", "title": SECTION_TITLES["accounts"], "blocks": [account_table(computed, by_variance=by_variance)]},
        {"key": "exceptions", "title": S("docs.section.exceptions"), "blocks": [
            {"type": "paragraph", "runs": [(S("docs.exceptions.note"), None)], "muted": True},
            exceptions_table(computed)]},
        {"key": "insights", "title": S("docs.section.insights"), "blocks": [
            {"type": "subheading", "text": S("docs.sub.watch")},
            {"type": "bullets", "items": [cite_runs(t, computed) for t in sentences(narrative, "watch")], "narrative": True},
            {"type": "subheading", "text": S("docs.sub.next")},
            {"type": "bullets", "items": [cite_runs(t, computed) for t in sentences(narrative, "next_steps")],
             "narrative": True},
        ]},
        {"key": "assumptions", "title": S("docs.section.assumptions"), "blocks": [
            {"type": "bullets", "items": [[(a["text"] + _assumption_tag(a), None)] for a in assumptions]},
        ]},
        {"key": "sources", "title": S("docs.section.sources"), "appendix": True, "blocks": [
            {"type": "table", "columns": [S("docs.col.file"), S("docs.col.role"), S("docs.col.rows"), "SHA-256"], "rows": sources_rows,
             "strong_rows": [], "numeric_from": None, "cite_col": None, "small": True},
        ]},
        {"key": "citations", "title": S("docs.section.citations"), "appendix": True, "blocks": [
            {"type": "table", "columns": [S("docs.col.ref"), S("docs.col.figure"), S("docs.col.value"), S("docs.col.file_sheet_cell"), S("docs.col.computed_by")],
             "rows": citation_rows, "strong_rows": [], "numeric_from": None, "cite_col": 0, "small": True},
        ]},
        {"key": "about", "title": S("docs.section.about"), "appendix": True, "blocks": [
            {"type": "bullets", "items": [[(t, None)] for t in about]},
        ]},
    ]
    sections = [s for s in sections if s["blocks"]]
    if "brief" in ask:
        # the short report: the summary, the P&L, what must be checked, the assumptions and where every
        # figure came from. The charts stay in the deck (model["charts"]), which is already the short form.
        sections = [s for s in sections if s["key"] not in BRIEF_LEAVES_OUT]
    # What the deck prints: the same sentences with their figures in place, and which figures each cites.
    printed = {"summary": plain_text(narrative["summary"], computed)}
    narrative_cites = {"summary": cites_in(narrative["summary"], computed)}
    for key in ("movements", "watch", "next_steps"):
        printed[key] = [plain_text(t, computed) for t in narrative[key]]
        narrative_cites[key] = [c for t in narrative[key] for c in cites_in(t, computed)]
    return {
        "title": S("docs.report.title"),
        "subtitle": S("docs.report.subtitle", period_label=computed["period_label"]),
        "company": theme_company,
        "prepared": S("docs.report.prepared", today=date_th(today)),
        "cover_lines": cover_lines,
        "kpis": kpis(computed),
        "charts": chart_specs,
        "sections": sections,
        "assumptions": assumptions,
        "narrative": {**narrative, **printed},
        "narrative_source": narrative,
        "narrative_cites": narrative_cites,
        "source_note": S("docs.report.source_note", file=src["primary"]["file"], v=src["primary"]["sha256"][:8], script=computed["script"]),
        "model": model,
        "computed": computed,
        "asked": {"applied": [i for i in ask if i not in refused], "refused": refused},
    }


def source_where(s: dict) -> str:
    """Where a figure was read, every row of it, as briefly as a reader can follow: "GL.xlsx · Sheet1 ·
    คอลัมน์ G:H แถว 11, 59, 62–64" (AM8: never "and N more"). When the row list was capped, the cell
    ranges are printed instead — complete either way."""
    rows = s.get("row_numbers") or []
    m = re.match(r"^([A-Z]+)\d+(?::([A-Z]+)\d+)?", str(s.get("cells") or ""))
    if rows and m and len(rows) >= int(s.get("rows") or len(rows)):
        cols = m.group(1) + (f":{m.group(2)}" if m.group(2) and m.group(2) != m.group(1) else "")
        parts, start, prev = [], rows[0], rows[0]
        for r in rows[1:] + [None]:
            if r is not None and r == prev + 1:
                prev = r
                continue
            parts.append(f"{start}–{prev}" if prev != start else f"{start}")
            if r is not None:
                start = prev = r
        return S("docs.where.cells", file=s["file"], sheet=s["sheet"], cols=cols, parts=", ".join(parts))
    return f"{s['file']} · {s['sheet']} · {s.get('cells') or ''}"


def _assumption_tag(a: dict) -> str:
    tag = {"default": "docs.tag.default", "remembered": "docs.tag.remembered", "answer": "docs.tag.answer",
           "reprompt_default": "docs.tag.reprompt"}.get(a.get("source", ""))
    return " " + S(tag) if tag else ""
