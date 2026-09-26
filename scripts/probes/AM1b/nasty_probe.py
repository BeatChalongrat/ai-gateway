"""AM1b — run the nasty fixtures through the Financial Report package.

    (in the sandbox image, backend mounted at /src)
    python3 /probe/nasty_probe.py > results/nasty_probe.json

For each scenario: rows read vs the journal, rows skipped, the questions the
person would get, then (every question answered with its default) the P&L the
package computes next to the numbers the generator's journal says are right.
"""
import datetime as dt
import json
import os
import sys

sys.path[0:0] = ["/src", "/sandbox/scripts"]
from agent_market.packages import financial_report as pkg  # noqa: E402

NASTY = "/src/agent_market/packages/financial_report/evals/nasty"
EXP = json.load(open(os.path.join(NASTY, "expected.json"), encoding="utf-8"))
TODAY = dt.date(2026, 10, 3)

SCENARIOS = [
    ("nasty_csv_alone", ["GL_Express_2569-09.csv"], "ปิดงบเดือนกันยายน 2569", "pl_correct"),
    ("nasty_csv_prior_budget", ["GL_Express_2569-09.csv", "GL_Express_2569-08.xlsx", "งบประมาณ_2569.xlsx"],
     "ปิดงบเดือนกันยายน 2569 เทียบเดือนก่อนและงบประมาณ", "pl_correct"),
    ("clean_alone", ["GL_clean_2569-09.xlsx"], "ปิดงบเดือนกันยายน 2569", "pl"),
    ("prior_merged_alone", ["GL_Express_2569-08.xlsx"], "ปิดงบเดือนสิงหาคม 2569", "pl"),
    ("sap_en_alone", ["SAP_FBL3N_09-2026_EN.xlsx"], "close September 2026", "pl"),
]


def lines_of(computed):
    return {l["key"]: l["current"] for l in computed["lines"]}


def run(name, files, message, expect_key):
    parsed = [pkg.parse(os.path.join(NASTY, f), f, {}) for f in files]
    out = {"scenario": name, "files": []}
    for pf in parsed:
        s = pf.summary() if hasattr(pf, "summary") else {}
        out["files"].append({"name": pf.name, "kind": getattr(pf, "kind", None), "encoding": getattr(pf, "encoding", None),
                             "data_rows": pf.data_rows, "skipped_rows": pf.skipped_rows,
                             "expected_rows": EXP.get(pf.name, {}).get("data_rows"),
                             "header_row": s.get("header_row"), "fields": s.get("fields"),
                             "periods": s.get("periods"), "dc_mode": getattr(pf, "dc_mode", None)})
    answers = {}
    asked = []
    period = None
    for rnd in range(4):  # the engine re-checks after answers; take defaults like a hurried user
        pre = pkg.preflight(parsed, message=message, defaults={}, answers=answers, today=TODAY)
        period = pre["period"]
        fresh = [f for f in pre["findings"] if f["key"] not in answers and f.get("kind") != "request_files"]
        for f in pre["findings"]:
            if f["key"] not in answers:
                asked.append({"round": rnd + 1, "code": f["code"], "severity": f["severity"], "header": f["header"],
                              "question": f["question"][:160], "default": f.get("default")})
                answers[f["key"]] = f["default"]
        if not fresh:
            break
    out["period"] = period
    out["questions"] = asked
    if not period:
        out["computed"] = None
        return out
    try:
        computed = pkg.compute(parsed, pre["roles"], period, answers=answers, defaults={})
        post = pkg.postcompute(parsed, pre["roles"], period, computed, answers=answers, defaults={})
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:400]
        return out
    for f in post["findings"]:
        asked.append({"round": "post", "code": f["code"], "severity": f["severity"], "header": f["header"],
                      "question": f["question"][:160], "default": f.get("default")})
        answers[f["key"]] = f["default"]
    if post["findings"]:  # the runner recomputes with the post-compute answers
        computed = pkg.compute(parsed, pre["roles"], period, answers=answers, defaults={})
    got = lines_of(computed)
    exp_file = files[0]
    exp = EXP[exp_file][expect_key]
    out["pl_got"] = {k: got.get(k) for k in exp}
    out["pl_expected"] = exp
    out["pl_match"] = {k: (got.get(k) == v) for k, v in exp.items() if got.get(k) is not None}
    out["rows_used"] = computed.get("summary", {}).get("rows_used") if isinstance(computed.get("summary"), dict) else None
    return out


def main():
    results = [run(*s) for s in SCENARIOS]
    json.dump(results, sys.stdout, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
