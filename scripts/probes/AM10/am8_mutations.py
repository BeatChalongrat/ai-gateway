"""AM10 gate line — AM8's four files, broken on purpose, opened by this checkout's gate.

The output audit's four mutated copies (backend/agent_market/tests/fixtures/gate_mutations) are
handed to the quality gate as it is at this commit. For each: how many checks the gate ran on the
file, which of them did not pass and the gate's own sentence for it. A file that passes, or that
fails for a reason other than the way it was broken, is a red line and exit 1. The unbroken set
rendered now is the control: it must pass.

Runs in the sandbox image, like the Python lane:  bash scripts/probes/AM10/am8_mutations.sh
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path[0:0] = ["/src", "/sandbox/scripts"]

from agent_market.deliverables import content, quality  # noqa: E402
from agent_market.deliverables.render import render_all  # noqa: E402
from agent_market.deliverables.theme import resolve  # noqa: E402
from agent_market.packages.invoice_processing.scripts.workbook import check as invoice_check  # noqa: E402
from agent_market.tests.test_deliverables import REQ, computed_for  # noqa: E402

FIX = "/src/agent_market/tests/fixtures/gate_mutations"


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    financial, invoice, theme = load("financial_computed.json"), load("invoice_computed.json"), resolve(None)
    req = {"formats": ["docx", "pptx", "xlsx"], "assumptions": [{"source": "default", "text": "Audit local default: THB"}]}
    model = content.build(financial, {**req, "theme": theme})
    lying = {"chart_bindings": [{"slide": 4, "series": [{"name": "รายได้", "source_values": ["99990000.0"]}]}]}
    cases = [
        ("financial_missing_revenue.xlsx", "the revenue cell deleted", "xlsx.figures", "ว่าง",
         lambda p: quality.check_xlsx(p, financial)),
        ("invoice_wrong_vat.xlsx", "VAT changed to 999,999", None, "999999",
         lambda p: invoice_check(p, invoice)),
        ("bad_header_font.docx", "a header run in a font outside the theme", "docx.fonts", "Comic Sans MS",
         lambda p: quality.check_docx(p, model, theme, req)),
        ("wrong_chart.pptx", "a chart redrawn at 99.99 million, its metadata changed to match", "pptx.figures",
         "กราฟในไฟล์ไม่ตรงกับตัวเลขที่คำนวณ", lambda p: quality.check_pptx(p, lying, financial, theme)),
    ]
    red = 0
    print("file\thow it was broken\tchecks run\tnot passed\tfails for its own breakage\tthe gate's sentence")
    for name, how, want_check, want_words, run in cases:
        checks = run(os.path.join(FIX, name))
        bad = [c for c in checks if not c["ok"]]
        own = [c for c in bad if (want_check is None or c["name"] == want_check)
               and want_words in c["detail"].replace(",", "")]
        verdict = "FAILS THE GATE" if own else ("fails, for another reason" if bad else "PASSES — RED")
        red += 0 if own else 1
        said = (own or bad or [{"name": "-", "detail": "-"}])[0]
        others = ", ".join(sorted({c["name"] for c in bad} - {said["name"]})) or "-"
        print(f"{name}\t{how}\t{len(checks)}\t{len(bad)}\t{verdict}\t{said['name']}: {said['detail'][:230]}")
        print(f"\t\t\t\tother checks not passed: {others}")
    # the control: the same writer's unbroken files, rendered now
    with tempfile.TemporaryDirectory() as out:
        res = render_all(computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"]),
                         {**REQ, "formats": ["docx", "pptx", "xlsx"]}, in_dir=out, out_dir=out)
        bad = [c for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]
        n = sum(len(a["quality"]["checks"]) for a in res["artifacts"])
        print(f"control\tthe unbroken set rendered now ({len(res['artifacts'])} files)\t{n}\t{len(bad)}\t"
              f"{'PASSES' if not bad else 'DOES NOT PASS — RED'}\t{'; '.join(c['name'] for c in bad) or '-'}")
        red += 1 if bad else 0
    print(f"mutated files that still fail for their own breakage: {len(cases) - min(red, len(cases))}/{len(cases)} · red lines {red}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
