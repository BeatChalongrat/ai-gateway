"""AM8 evidence, made offline in the sandbox image (no network, read-only source):

    docker run --rm --network none -v <repo>/backend:/src:ro -v <repo>/services/chat-edge-api/scripts:/sandbox/scripts:ro \
        -v <out>:/out --entrypoint python3 ai-gateway-sandbox:latest /src/../scripts/probes/AM8/evidence.py

(run_evidence.sh does exactly that). Writes under /out:
  silent.json      the output audit's six silent template cases, filled again: passed, and what the person sees
  mutations.json   the audit's four broken files through the gate: each check that fails, with its words
  citation.json    one sentence of the report traced: the figure, its citation, the metric, the source cells
  invoice.json     the sample invoice's line items: name, confidence, the status the register prints
  bank/            the bank sample's working paper (for the print test: page setup read back)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path[0:0] = ["/src", "/sandbox/scripts"]
OUT = os.environ.get("AM8_OUT", "/out")
os.makedirs(OUT, exist_ok=True)


def dump(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


# ── 1. the six silent template cases ──────────────────────────────────────
def silent_cases():
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.shared import Inches
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches as PI

    from agent_market.deliverables import template_fill as tf
    from agent_market.deliverables import template_out as to
    from agent_market.tests.test_am8_templates import _deck, _doc

    def run(name, obj, ext, values, mapping=None):
        tmp = tempfile.mkdtemp()
        src, dst = os.path.join(tmp, "before" + ext), os.path.join(tmp, "after" + ext)
        obj.save(src)
        sl = to.slots(tf.inspect(src))
        if mapping is None:
            mapping = {s["key"]: s["key"] for s in sl if s["fillable"]}
        blocking = to.blocking(sl)
        rep = to.fill(src, dst, {"slots": sl, "mapping": mapping}, values)
        return {"case": name, "refused_before_work": [{"slot": b["label"], "reason": b["reason"]} for b in blocking],
                "passed": rep["passed"], "failed_checks": [{c["label"]: c["detail"]} for c in rep["checks"] if not c["ok"]],
                "card_lists": rep["unfilled"], "markers_left": tf.markers_left(dst)}

    out = []
    d = _doc()
    d.add_paragraph("{{company}}")
    d.add_paragraph("ยอดรวม {{missing_total}} บาท")
    out.append(run("docx_missing_mapped_value", d, ".docx", {"company": "บริษัท ทดสอบ จำกัด"}))
    d = _doc()
    t = d.add_table(rows=1, cols=1)
    t.rows[0].height = Inches(.3)
    t.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    t.cell(0, 0).text = "{{company}}"
    out.append(run("docx_fixed_height_overflow", d, ".docx", {"company": "บริษัท ทดสอบบัญชีและตรวจสอบเอกสาร จำกัด " * 18}))
    p, _ = _deck()
    p.slide_master.shapes[0].text = "{{company}}"
    out.append(run("pptx_master_marker", p, ".pptx", {"period": "กันยายน 2569", "company": "NEW COMPANY"}))
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[1])
    s.shapes.title.text = "{{period}}"
    s.placeholders[1].name = "Revenue"
    s.placeholders[1].text = "OLD REVENUE: 999"
    out.append(run("pptx_named_placeholder", p, ".pptx", {"period": "กันยายน 2569", "shape:Revenue": "1,234.50"},
                   {"period": "period", "shape:Revenue": "shape:Revenue"}))
    p, s = _deck()
    box = s.shapes.add_textbox(PI(1), PI(2), PI(7), PI(1))
    box.name = "Revenue"
    box.text = "OLD REVENUE: 999"
    out.append(run("pptx_shape_name", p, ".pptx", {"period": "กันยายน 2569", "shape:Revenue": "1,234.50"}))
    p, s = _deck()
    data = CategoryChartData()
    data.categories = ["Revenue", "Cost"]
    data.add_series("THB", [999, 888])
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, PI(1), PI(1.5), PI(7), PI(3.5), data)
    out.append(run("pptx_existing_chart", p, ".pptx", {"period": "กันยายน 2569"}))
    dump("silent.json", out)


# ── 2. the four mutated files ─────────────────────────────────────────────
def mutations():
    from agent_market.deliverables import content, quality
    from agent_market.deliverables.theme import resolve
    from agent_market.packages.invoice_processing.scripts.workbook import check as invoice_check
    from agent_market.tests.test_am8_gate import FIX, _load

    fin, inv, theme = _load("financial_computed.json"), _load("invoice_computed.json"), resolve(None)
    req = {"formats": ["docx", "pptx", "xlsx"], "assumptions": [{"source": "default", "text": "Audit local default: THB"}]}
    model = content.build(fin, {**req, "theme": theme})

    def failed(checks):
        return [{"check": c["name"], "says": c["detail"]} for c in checks if not c["ok"]]
    dump("mutations.json", [
        {"file": "financial_missing_revenue.xlsx", "passed": False,
         "failed": failed(quality.check_xlsx(os.path.join(FIX, "financial_missing_revenue.xlsx"), fin))},
        {"file": "invoice_wrong_vat.xlsx", "failed": failed(invoice_check(os.path.join(FIX, "invoice_wrong_vat.xlsx"), inv))},
        {"file": "bad_header_font.docx", "failed": failed(quality.check_docx(os.path.join(FIX, "bad_header_font.docx"), model, theme, req))},
        {"file": "wrong_chart.pptx", "failed": failed(quality.check_pptx(os.path.join(FIX, "wrong_chart.pptx"),
                                                                         {"chart_bindings": []}, fin, theme))},
    ])


# ── 3. a citation, traced ─────────────────────────────────────────────────
def citation():
    from docx import Document

    from agent_market.deliverables.render import render_all
    from agent_market.tests.test_deliverables import REQ, computed_for

    c = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])
    out = tempfile.mkdtemp()
    narrative = {"summary": "ค่าใช้จ่ายในการขายและบริหารสูงกว่างบประมาณ {operating_expenses_var_budget_pct} "
                            "ขณะที่ต้นทุนขายเพิ่มขึ้น {cost_of_sales_var_prior_pct:abs} จากเดือนก่อน",
                 "movements": [], "watch": [], "next_steps": []}
    res = render_all(c, {**REQ, "formats": ["docx"], "narrative": narrative}, in_dir=out, out_dir=out)
    doc = Document(os.path.join(out, "r_v1.docx"))
    p = next(p for p in doc.paragraphs if "สูงกว่างบประมาณ" in p.text)
    runs = [(r.text, bool(r.font.superscript)) for r in p.runs]
    cites = {x["id"]: x for x in c["citations"]}

    def chain(cid, depth=0):
        x = cites[cid]
        node = {"id": cid, "label": x["label"], "value": x["value"], "formula": x["formula"]}
        if x["sources"]:
            node["sources"] = [{k: s.get(k) for k in ("file", "sheet", "cells")} for s in x["sources"]]
        if x.get("derived_from") and depth < 3:
            node["from"] = [chain(d, depth + 1) for d in x["derived_from"][:12]]
        return node
    pairs = []
    for i, (t, sup) in enumerate(runs):
        if sup:
            pairs.append({"printed": runs[i - 1][0].strip()[-12:], "cite": t.strip(), "trace": chain(t.strip())})
    dump("citation.json", {"sentence": p.text, "figures": pairs,
                           "figure_keys": {k: c["figures"][k] for k in ("operating_expenses_var_budget_pct", "cost_of_sales_var_prior_pct")},
                           "gate": [x for x in res["artifacts"][0]["quality"]["checks"] if x["name"] in ("docx.narrative", "citations")]})


# ── 4. the sample invoice's items; 5. the bank sample's paper ─────────────
def samples():
    from openpyxl import load_workbook

    from agent_market.packages.invoice_processing.scripts.workbook import item_status
    from agent_market.tests.samplekit import render_sample

    os.makedirs(os.path.join(OUT, "invoice"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "bank"), exist_ok=True)
    inv = render_sample("invoice_processing", out_dir=os.path.join(OUT, "invoice"))
    dump("invoice.json", [{"file": l["name"], "row": l["row"], "name": l["description"], "confidence": l["confidence"],
                           "status": item_status(l)} for l in inv["computed"]["line_items"]])
    bank = render_sample("bank_reconciliation", out_dir=os.path.join(OUT, "bank"))
    path = bank["paths"]["xlsx"]
    wb = load_workbook(path)
    setup = []
    for ws in wb.worksheets:
        setup.append({"sheet": ws.title, "orientation": ws.page_setup.orientation, "fitToWidth": ws.page_setup.fitToWidth,
                      "fitToHeight": ws.page_setup.fitToHeight, "print_area": ws.print_area, "title_rows": ws.print_title_rows,
                      "title_cols": ws.print_title_cols, "footer": [ws.oddFooter.left.text, ws.oddFooter.right.text]})
    face = {r[0]: r[1] for r in wb.worksheets[0].iter_rows(min_row=1, max_col=2, values_only=True) if r and r[0]}
    dump("bank_setup.json", {"file": os.path.basename(path), "sheets": setup,
                             "header": {k: face.get(k) for k in ("นิติบุคคล", "ธนาคาร", "ชื่อบัญชี", "เลขที่บัญชี", "รหัสบัญชีแยกประเภท", "ผู้จัดทำ")},
                             "gate": [c for c in bank["render"]["artifacts"][0]["quality"]["checks"] if c["name"] in ("xlsx.print", "xlsx.header")]})


if __name__ == "__main__":
    for step in [f for f in (silent_cases, mutations, citation, samples) if len(sys.argv) < 2 or f.__name__ in sys.argv[1:]]:
        step()
        print("done", step.__name__, flush=True)
