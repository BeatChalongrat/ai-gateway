"""Run the eval set the same way agent-runner runs a real case.

    python -m agent_market.tests.evalkit        # table of every case

A case passes when (1) exactly the expected finding codes fire — no more,
no fewer; silence on clean input is a pass condition — and (2) the computed
numbers equal the numbers derived from the generator's own journal. Prose is
never asserted.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

from agent_market.engine import words
from agent_market.packages import financial_report as pkg

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.join(os.path.dirname(HERE), "packages", "financial_report")
TODAY = dt.date(2026, 9, 25)
PL_KEYS = ("revenue", "other_income", "cost_of_sales", "gross_profit", "operating_expenses",
           "finance_costs", "total_expenses", "net_profit")


def load_cases() -> list[dict]:
    with open(os.path.join(PKG_DIR, "evals", "cases.json"), encoding="utf-8") as f:
        return json.load(f)["cases"]


def run_case(case: dict, lang: str | None = None, formats: tuple = ()) -> dict:
    """``lang`` (AM14): the run's language, as a step is told it (engine/words.use) — Thai when not given.
    ``formats``: the round's files are written too and put through the gate (``render``), and the words
    each file prints are read back out of it (``texts``: {file name: [every text in the file]})."""
    words.use(lang)
    try:
        return _run_case(case, formats)
    finally:
        words.use("th")


def _run_case(case: dict, formats: tuple = ()) -> dict:
    t0 = time.monotonic()
    defaults = case.get("defaults") or {}
    answers = dict(case.get("answers") or {})
    mapping = defaults.get("mapping") or {}
    files = [pkg.parse(os.path.join(PKG_DIR, "evals", "fixtures", n), n, mapping) for n in case["files"]]
    pre = pkg.preflight(files, message=case.get("message", ""), defaults=defaults, answers=answers, today=TODAY)
    fired = [f["code"] for f in pre["findings"]]
    result = {"id": case["id"], "preflight": fired, "postcompute": [], "period": pre["period"],
              "computed": None, "assumptions": pre["assumptions"], "findings": pre["findings"]}
    blocking = [f for f in pre["findings"] if f["severity"] == "blocking"]
    if not blocking and pre["period"]:
        # Cosmetic findings take their default (the engine records an assumption).
        for f in pre["findings"]:
            answers.setdefault(f["code"], f["default"])
        computed = pkg.compute(files, pre["roles"], pre["period"], answers=answers, defaults=defaults)
        post = pkg.postcompute(files, pre["roles"], pre["period"], computed, answers=answers, defaults=defaults)
        result["postcompute"] = [f["code"] for f in post["findings"]]
        result["findings"] += post["findings"]
        result["computed"] = computed
        result["summary"] = pkg.summarize(computed)
        if formats:
            result.update(_render(computed, pre, defaults, formats))
    result["answers"] = answers
    result["ms"] = int((time.monotonic() - t0) * 1000)
    return result


def _render(computed: dict, pre: dict, defaults: dict, formats: tuple) -> dict:
    """The files of a round as the runner has them made: the assumptions of the run, and — no model here —
    the commentary the rules wrote, taken from the computed table as the runner takes it."""
    import shutil
    import tempfile

    out = tempfile.mkdtemp(prefix="am_eval_")
    try:
        req = {"formats": list(formats), "names": {f: f"report.{f}" for f in formats},
               "theme": {"id": "navy", "company": defaults.get("company") or ""},
               "narrative": dict(computed["fallback_narrative"]), "assumptions": list(pre["assumptions"]),
               "run": {"id": "eval", "date": TODAY.isoformat(), "agent_version": pkg.VERSION},
               "model_info": {"model": None, "self_hosted": None}}
        rend = pkg.render(computed, req, in_dir=out, out_dir=out)
        return {"render": rend, "texts": {a["name"]: texts(os.path.join(out, a["name"])) for a in rend["artifacts"]}}
    finally:
        shutil.rmtree(out, ignore_errors=True)


def texts(path: str) -> list[str]:
    """Every text a finished file prints, in the file's own order: a report's paragraphs and table cells, a
    deck's shapes and tables, a workbook's sheet names and cells."""
    out: list[str] = []
    if path.endswith(".docx"):
        from docx import Document

        doc = Document(path)
        out += [p.text for p in doc.paragraphs]
        out += [c.text for t in doc.tables for row in t.rows for c in row.cells]
        for sec in doc.sections:
            out += [p.text for part in (sec.header, sec.footer) for p in part.paragraphs]
    elif path.endswith(".pptx"):
        from pptx import Presentation

        for slide in Presentation(path).slides:
            for sh in slide.shapes:
                if sh.has_text_frame:
                    out.append(sh.text_frame.text)
                if getattr(sh, "has_table", False) and sh.has_table:
                    out += [c.text for row in sh.table.rows for c in row.cells]
                if getattr(sh, "has_chart", False) and sh.has_chart:
                    out += [str(c) for c in sh.chart.plots[0].categories] + [s.name for s in sh.chart.plots[0].series]
    else:
        from openpyxl import load_workbook

        wb = load_workbook(path)
        for ws in wb.worksheets:
            out.append(ws.title)
            out += [str(v) for row in ws.iter_rows(values_only=True) for v in row if v is not None]
    return [t for t in out if t]


def check(case: dict, res: dict) -> list[str]:
    exp = case["expect"]
    errors = []
    fired = sorted(set(res["preflight"] + res["postcompute"]))
    want = sorted(set(exp.get("findings", [])))
    if fired != want:
        errors.append(f"findings {fired} != expected {want}")
    if exp.get("min_findings") and len(res["preflight"] + res["postcompute"]) < exp["min_findings"]:
        errors.append(f"only {len(res['preflight'] + res['postcompute'])} findings")
    if "period" in exp and res["period"] != exp["period"]:
        errors.append(f"period {res['period']} != {exp['period']}")
    c = res["computed"]
    if "pl" in exp:
        if c is None:
            errors.append("no computed table")
        else:
            lines = {l["key"]: l["current"] for l in c["lines"]}
            for k in PL_KEYS:
                if k in exp["pl"] and lines.get(k) != exp["pl"][k]:
                    errors.append(f"{k} {lines.get(k)} != {exp['pl'][k]}")
            if c["balance"]["applicable"] and c["balance"]["debit"] != exp["pl"]["debit_total"] and c["doc_type"] == "gl":
                errors.append(f"debit total {c['balance']['debit']} != {exp['pl']['debit_total']}")
    if "prior_pl" in exp and c is not None:
        lines = {l["key"]: l["prior"] for l in c["lines"]}
        for k in PL_KEYS:
            if lines.get(k) != exp["prior_pl"][k]:
                errors.append(f"prior {k} {lines.get(k)} != {exp['prior_pl'][k]}")
    if "budget_revenue" in exp and c is not None:
        b = next(l for l in c["lines"] if l["key"] == "revenue")["budget"]
        if b != exp["budget_revenue"]:
            errors.append(f"budget revenue {b} != {exp['budget_revenue']}")
    if "trend_months" in exp and c is not None and len(c["trend"]["months"]) != exp["trend_months"]:
        errors.append(f"trend months {len(c['trend']['months'])} != {exp['trend_months']}")
    if "excluded_rows" in exp:
        f = next((f for f in res["findings"] if f["code"] == "period_mixed"), None)
        if not f or f["evidence"]["count"] != exp["excluded_rows"]:
            errors.append(f"period_mixed count {f and f['evidence']['count']} != {exp['excluded_rows']}")
    if "imbalance" in exp and c is not None:
        if abs(float(c["balance"]["diff"])) != float(exp["imbalance"]):
            errors.append(f"imbalance {c['balance']['diff']} != {exp['imbalance']}")
    if "assumption_codes" in exp:
        got = sorted(a["code"] for a in res["assumptions"])
        if got != sorted(exp["assumption_codes"]):
            errors.append(f"assumptions {got} != {exp['assumption_codes']}")
    return errors


def main() -> int:
    cases = load_cases()
    failed = 0
    questions = 0
    for case in cases:
        res = run_case(case)
        errs = check(case, res)
        n_q = len(res["preflight"] + res["postcompute"])
        questions += n_q
        failed += bool(errs)
        print(f"{'PASS' if not errs else 'FAIL'} {case['id']:<32} q={n_q} {res['ms']:>5}ms "
              f"{'; '.join(errs) if errs else ''}")
    print(f"{len(cases) - failed}/{len(cases)} cases pass · {questions} findings in total")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
