"""AM6 — what the five packages hand to a thread, as their eval sets produce it.

    cd backend && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python ../scripts/agent-market/words/dump_package_texts.py

Each package's eval kit runs every case of its evals/cases.json the way the runner does. What
reaches a thread is kept as the runner receives it: every finding (a card's question with its
options, evidence, request and input), every compute summary (the files card's summary, the
documents left out, the gate), every quality check of a rendered file, every assumption.

Written to scripts/agent-market/words/results/package_texts.json (gitignored), and — with --go — as the Go test table
services/chat-edge-api/internal/agentmarket/am6_packages_test.go (evidence tables cut to their
first rows; a finding or a summary that repeats word for word is kept once).
"""

from __future__ import annotations

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(HERE, "results", "package_texts.json")
GO = os.path.join(ROOT, "services", "chat-edge-api", "internal", "agentmarket", "am6_packages_test.go")

findings: list[dict] = []
summaries: list[dict] = []
checks: list[dict] = []
assumptions: list[dict] = []
failed: list[str] = []
seen: set = set()


def once(kind, pkg, obj) -> bool:
    k = (kind, pkg, json.dumps(obj, ensure_ascii=False, sort_keys=True))
    if k in seen:
        return False
    seen.add(k)
    return True


def cut(v, rows=3):
    """An evidence value with its tables cut to their first rows."""
    if isinstance(v, list):
        return [cut(x, rows) for x in v[:rows]] if v and isinstance(v[0], (list, dict)) else v[:20]
    if isinstance(v, dict):
        return {k: cut(x, rows) for k, x in v.items()}
    return v


def walk(pkg, case, node, path=""):
    if isinstance(node, dict):
        if "question" in node and "header" in node and "options" in node:
            f = {k: node.get(k) for k in ("key", "code", "severity", "moment", "header", "question", "options", "default",
                                          "rememberable", "kind", "subject", "request", "input", "recommended", "leaves_out")}
            f["evidence"] = cut(node.get("evidence") or {})
            if once("finding", pkg, {k: f[k] for k in ("header", "question", "options", "request", "input")}):
                findings.append({"package": pkg, "case": case, "finding": f})
            return
        if "checks" in node and isinstance(node["checks"], list) and node["checks"] and isinstance(node["checks"][0], dict) \
                and "ok" in node["checks"][0]:
            for c in node["checks"]:
                if once("check", pkg, c):
                    checks.append({"package": pkg, "case": case, "file": node.get("name") or "", "check": c})
        if path.endswith("/summary"):
            s = cut(node, rows=12)
            if once("summary", pkg, s):
                summaries.append({"package": pkg, "case": case, "summary": s})
        for k, v in node.items():
            if k in ("sheets", "computed_bytes", "computed"):
                continue
            if k == "assumptions" and isinstance(v, list):
                for a in v:
                    if isinstance(a, dict) and once("assumption", pkg, a):
                        assumptions.append({"package": pkg, "case": case, "assumption": a})
                continue
            walk(pkg, case, v, path + "/" + k)
    elif isinstance(node, list):
        for v in node:
            walk(pkg, case, v, path)


def run(pkg, kit, cases):
    for c in cases:
        cid = c.get("id", "?")
        try:
            res = kit.run_case(c)
        except Exception:  # a case this host cannot run (no OCR, no converter) is reported, not hidden
            failed.append(f"{pkg}/{cid}: {traceback.format_exc().strip().splitlines()[-1]}")
            continue
        walk(pkg, cid, res)
        comp = res.get("computed") if isinstance(res, dict) else None
        if isinstance(comp, dict):
            if isinstance(comp.get("gate"), dict) and once("gate", pkg, comp["gate"]):
                summaries.append({"package": pkg, "case": cid, "gate": comp["gate"]})
            for a in comp.get("assumptions") or []:
                if isinstance(a, dict) and once("assumption", pkg, a):
                    assumptions.append({"package": pkg, "case": cid, "assumption": a})


def go_string(s: str) -> str:
    out = json.dumps(s, ensure_ascii=False)
    return out.replace("\u2028", "\u2028").replace("\u2029", "\u2029")


def main():
    from agent_market.packages import financial_report
    from agent_market.tests import bankkit, evalkit, expensekit, invoicekit, pokit

    for c in evalkit.load_cases():
        try:
            res = evalkit.run_case(c)
        except Exception:
            failed.append(f"financial_report/{c.get('id')}: {traceback.format_exc().strip().splitlines()[-1]}")
            continue
        walk("financial_report", c["id"], {"findings": res["findings"], "assumptions": res["assumptions"]})
        if res.get("computed"):
            s = financial_report.summarize(res["computed"])
            if once("summary", "financial_report", s):
                summaries.append({"package": "financial_report", "case": c["id"], "summary": cut(s, rows=12)})
    for pkg, kit in (("bank_reconciliation", bankkit), ("expense_audit", expensekit), ("invoice_processing", invoicekit),
                     ("po_matching", pokit)):
        cases = kit.load_cases()
        run(pkg, kit, cases["cases"] if isinstance(cases, dict) else cases)
    doc = {"findings": findings, "summaries": summaries, "checks": checks, "assumptions": assumptions, "failed": failed}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"{len(findings)} findings, {len(summaries)} summaries and gates, {len(checks)} checks, {len(assumptions)} assumptions; "
          f"{len(failed)} cases not run")
    for x in failed:
        print("  not run:", x)
    if "--go" in sys.argv:
        body = json.dumps({k: doc[k] for k in ("findings", "summaries", "checks", "assumptions")}, ensure_ascii=False,
                          separators=(",", ":"))
        lines = ["package agentmarket", "",
                 "// What the five packages hand to a thread, as their eval sets produce it: every",
                 "// finding, compute summary, gate, quality check and assumption of every case",
                 "// of evals/cases.json (evidence tables cut to their first rows). Written by",
                 "// scripts/agent-market/words/dump_package_texts.py --go; not edited by hand.",
                 "const packageOutputs = " + go_string(body), ""]
        with open(GO, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        print("wrote", os.path.relpath(GO, ROOT), os.path.getsize(GO), "bytes")


if __name__ == "__main__":
    sys.exit(main())
