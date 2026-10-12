"""AM16 — the cards of a scanned invoice whose page a model OUTSIDE the organisation read, as the engine writes them.

    bash scripts/probes/AM16/dump_reader_cards.sh        (in the sandbox image, where the packages run)

The tax-invoice agent's own one-scan case (evals/cases.json: scan_vision_disagrees), run in Thai and in
English with the page's reading handed in the way the runner hands it when an outside model read the page:
``{"model": "google/gemini-2.5-flash", "self_hosted": false}``. Written as
services/chat-edge-api/internal/agentmarket/am16_reader_table_test.go — the test beside it puts every one
of these questions through the thread's own door (the control plane does not link the Python engine) and
expects each to come out as it went in, naming that model and never an in-house one.
"""

from __future__ import annotations

import json
import os
import sys

READER = {"model": "google/gemini-2.5-flash", "self_hosted": False}


def main():
    sys.path[0:0] = [os.environ.get("AM_SRC", "/src"), "/sandbox/scripts"]
    from agent_market.tests import invoicekit

    case = next(c for c in invoicekit.load_cases()["cases"] if c["id"] == "scan_vision_disagrees")
    findings = []
    for lang, name in ((None, "scan_vision_disagrees@outside"), ("en", "en/scan_vision_disagrees@outside")):
        run = invoicekit.run_case({**case, "reader": READER}, lang)
        for f in run["asked"].values():
            findings.append({"package": "invoice_processing", "case": name, "finding": f})
    doc = json.dumps({"findings": findings}, ensure_ascii=False, sort_keys=True)
    assert "`" not in doc, "a backquote in the cards: the Go raw string cannot hold it"
    out = sys.argv[1]
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("package agentmarket\n\n")
        fh.write("// Written by scripts/probes/AM16/dump_reader_cards.sh — not edited by hand.\n")
        fh.write("// The cards of the tax-invoice agent's one-scan case, the page read by a model outside the organisation.\n")
        fh.write("const readerOutsideCards = `" + doc + "`\n")
    print(f"{len(findings)} findings")
    print(f"wrote {out} {os.path.getsize(out)} bytes")


if __name__ == "__main__":
    main()
