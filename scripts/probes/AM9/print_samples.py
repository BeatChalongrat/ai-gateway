"""AM9 print evidence, made offline in the sandbox image: each agent's sample working paper, rendered
by its own writer (the self-test), saved for the converter to print.

    docker run --rm --network none -v <repo>/backend:/src:ro -v <repo>/services/chat-edge-api/scripts:/sandbox/scripts:ro \
        -v <repo>/scripts/probes/AM9:/am9:ro -v <out>:/out --entrypoint python3 ai-gateway-sandbox:latest /am9/print_samples.py

Writes /out/<package>.xlsx and /out/<package>.rows.json (workpaper.print_rows: what the print proof looks for).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path[0:0] = ["/src", "/sandbox/scripts"]
OUT = os.environ.get("AM9_OUT", "/out")

from agent_market.deliverables import workpaper  # noqa: E402
from agent_market.tests import samplekit  # noqa: E402

for pkg in ("bank_reconciliation", "po_matching", "expense_audit", "invoice_processing", "financial_report"):
    res = samplekit.render_sample(pkg, out_dir=tempfile.mkdtemp(), formats=["xlsx"])
    path = res["paths"]["xlsx"]
    shutil.copy(path, os.path.join(OUT, f"{pkg}.xlsx"))
    with open(os.path.join(OUT, f"{pkg}.rows.json"), "w", encoding="utf-8") as f:
        json.dump(workpaper.print_rows(path), f, ensure_ascii=False)
    print("done", pkg, workpaper.check(path)["ok"])
