"""AM14 — what a package hands to a thread when the run is in English, as its eval set produces it.

    bash scripts/probes/AM14/dump_english_texts.sh        (in the sandbox image, where the packages run)

The same reading as scripts/probes/AM6/dump_package_texts.py (every finding, compute summary, gate, quality
check and assumption of every case of evals/cases.json), with the run's language said to every step the way
the runner says it. A case is named "en/<its id>": the Go table's reader takes the language from that.

Written as services/chat-edge-api/internal/agentmarket/am14_packages_table_test.go — the test beside it puts
every one of these sentences through the thread's own door and expects it to come out as it went in.

PACKAGES is the packages whose sentences are in their manifests; it grows with the card.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _am6():
    spec = importlib.util.spec_from_file_location("am6_dump", os.path.join(os.environ.get("AM6_PROBE", os.path.join(HERE, "..", "AM6")), "dump_package_texts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    sys.path[0:0] = [os.environ.get("AM_SRC", "/src"), "/sandbox/scripts"]
    d = _am6()
    from agent_market.tests import bankkit, expensekit, invoicekit, pokit

    packages = (("bank_reconciliation", bankkit), ("invoice_processing", invoicekit), ("expense_audit", expensekit),
                ("po_matching", pokit))
    for pkg, kit in packages:
        cases = kit.load_cases()
        for c in cases["cases"] if isinstance(cases, dict) else cases:
            res = kit.run_case(c, lang="en")
            cid = "en/" + c["id"]
            d.walk(pkg, cid, res)
            comp = res.get("computed") if isinstance(res, dict) else None
            if isinstance(comp, dict):
                if isinstance(comp.get("gate"), dict) and d.once("gate", pkg, comp["gate"]):
                    d.summaries.append({"package": pkg, "case": cid, "gate": comp["gate"]})
                for a in comp.get("assumptions") or []:
                    if isinstance(a, dict) and d.once("assumption", pkg, a):
                        d.assumptions.append({"package": pkg, "case": cid, "assumption": a})
    doc = {"findings": d.findings, "summaries": d.summaries, "checks": d.checks, "assumptions": d.assumptions}
    print(f"{len(d.findings)} findings, {len(d.summaries)} summaries and gates, {len(d.checks)} checks, {len(d.assumptions)} assumptions")
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    lines = ["package agentmarket", "",
             "// What a package hands to a thread when the run is in English, as its eval set produces it: every",
             "// finding, compute summary, gate, quality check and assumption of every case of evals/cases.json,",
             "// each case run with the run's language said to every step (\"en/<case>\"). Written by",
             "// scripts/probes/AM14/dump_english_texts.sh (in the sandbox image); not edited by hand.",
             "const englishPackageOutputs = " + d.go_string(body), ""]
    out = sys.argv[1]
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("wrote", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    sys.exit(main())
