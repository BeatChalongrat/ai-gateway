"""AM8: the gate opens the finished file and reads the figures back.

The output audit broke four copies on purpose and the earlier gate passed every one:
the revenue cell deleted (2/2), VAT changed to 999,999 (7/7), a header run in a font
outside the theme (3/3), and a chart redrawn at 99.99 million with its metadata changed
to match (5/5). Those four files are kept here (fixtures/gate_mutations/, with the
computed tables they were made from) and must fail, every run, for the reason they are
broken. The same four breakages are also made on a file rendered now, so the gate
keeps up with the renderer.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import unittest
import zipfile

from agent_market.deliverables import content, quality
from agent_market.deliverables.render import render_all
from agent_market.deliverables.theme import resolve
from agent_market.tests.test_deliverables import REQ, computed_for

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "gate_mutations")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def _failed(checks, name):
    c = next(c for c in checks if c["name"] == name)
    return (not c["ok"]), c["detail"]


class TheAuditsFourFilesFail(unittest.TestCase):
    """The audit's own files, as it left them."""

    @classmethod
    def setUpClass(cls):
        cls.financial = _load("financial_computed.json")
        cls.invoice = _load("invoice_computed.json")
        cls.theme = resolve(None)

    def test_the_revenue_cell_deleted_fails(self):
        checks = quality.check_xlsx(os.path.join(FIX, "financial_missing_revenue.xlsx"), self.financial)
        failed, detail = _failed(checks, "xlsx.figures")
        self.assertTrue(failed, detail)
        self.assertIn("รายได้จากการขายและบริการ", detail)
        self.assertIn("ว่าง", detail)

    def test_a_vat_of_999999_fails(self):
        from agent_market.packages.invoice_processing.scripts.workbook import check

        checks = check(os.path.join(FIX, "invoice_wrong_vat.xlsx"), self.invoice)
        bad = [c for c in checks if not c["ok"]]
        self.assertTrue(bad, json.dumps(checks, ensure_ascii=False))
        self.assertTrue(any("999999" in c["detail"].replace(",", "") and "VAT" in c["detail"] for c in bad),
                        json.dumps(bad, ensure_ascii=False))

    def test_a_header_font_outside_the_theme_fails(self):
        req = {"formats": ["docx", "pptx", "xlsx"], "assumptions": [{"source": "default", "text": "Audit local default: THB"}]}
        model = content.build(self.financial, {**req, "theme": self.theme})
        checks = quality.check_docx(os.path.join(FIX, "bad_header_font.docx"), model, self.theme, req)
        failed, detail = _failed(checks, "docx.fonts")
        self.assertTrue(failed, detail)
        self.assertIn("Comic Sans MS", detail)

    def test_a_chart_redrawn_with_matching_metadata_fails(self):
        # The writer's record says 99.99 million as well: the gate does not read it.
        lying = {"chart_bindings": [{"slide": 4, "series": [{"name": "รายได้", "source_values": ["99990000.0"]}]}]}
        checks = quality.check_pptx(os.path.join(FIX, "wrong_chart.pptx"), lying, self.financial, self.theme)
        failed, detail = _failed(checks, "pptx.figures")
        self.assertTrue(failed, detail)
        self.assertIn("กราฟในไฟล์ไม่ตรงกับตัวเลขที่คำนวณ", detail)


class TheSameBreakagesOnAFreshRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])
        cls.theme = resolve({"id": "navy", "company": "บริษัท ทดสอบ จำกัด"})
        cls.out = tempfile.mkdtemp()
        cls.req = {**REQ, "formats": ["docx", "pptx", "xlsx"]}
        cls.res = render_all(cls.computed, cls.req, in_dir=cls.out, out_dir=cls.out)
        cls.model = content.build(cls.computed, {**cls.req, "theme": cls.theme})

    def test_the_unbroken_files_pass(self):
        bad = [(a["format"], c) for a in self.res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]
        self.assertTrue(self.res["passed"], json.dumps(bad, ensure_ascii=False))

    def test_every_figure_check_says_how_many_values_it_compared(self):
        for a in self.res["artifacts"]:
            fig = next(c for c in a["quality"]["checks"] if c["name"].endswith(".figures"))
            self.assertGreater(fig["values"], 10, (a["format"], fig))

    def test_the_run_level_checks_are_counted_once(self):
        names = [c["name"] for a in self.res["artifacts"] for c in a["quality"]["checks"]]
        self.assertEqual(names.count("citations"), 1)
        self.assertEqual(names.count("assumptions"), 1)

    def test_deleting_a_figure_from_the_workbook_fails(self):
        from openpyxl import load_workbook

        path = os.path.join(self.out, "t_v1.xlsx")
        wb = load_workbook(path)
        ws = wb["สรุป"]
        row = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == "ค่าใช้จ่ายรวม")
        ws.cell(row, 2).value = None
        broken = os.path.join(self.out, "t_broken.xlsx")
        wb.save(broken)
        failed, detail = _failed(quality.check_xlsx(broken, self.computed), "xlsx.figures")
        self.assertTrue(failed)
        self.assertIn("ค่าใช้จ่ายรวม", detail)

    def test_a_header_run_in_another_font_fails(self):
        from docx import Document

        doc = Document(os.path.join(self.out, "r_v1.docx"))
        doc.sections[0].header.paragraphs[0].add_run("AUDIT HEADER").font.name = "Comic Sans MS"
        broken = os.path.join(self.out, "r_broken.docx")
        doc.save(broken)
        failed, detail = _failed(quality.check_docx(broken, self.model, self.theme, self.req), "docx.fonts")
        self.assertTrue(failed)
        self.assertIn("Comic Sans MS", detail)

    def test_a_chart_redrawn_in_the_deck_fails(self):
        from pptx import Presentation
        from pptx.chart.data import CategoryChartData

        prs = Presentation(os.path.join(self.out, "d_v1.pptx"))
        chart = next(sh.chart for s in prs.slides for sh in s.shapes if getattr(sh, "has_chart", False) and sh.has_chart)
        data = CategoryChartData()
        data.categories = [c.label for c in chart.plots[0].categories]
        for i, ser in enumerate(chart.series):
            values = list(ser.values)
            if i == 0:
                values[-1] = 99.99
            data.add_series(ser.name, values)
        chart.replace_data(data)
        broken = os.path.join(self.out, "d_broken.pptx")
        prs.save(broken)
        deck = next(a for a in self.res["artifacts"] if a["format"] == "pptx")
        lying = copy.deepcopy(deck["chart_bindings"])
        failed, detail = _failed(quality.check_pptx(broken, {"chart_bindings": lying}, self.computed, self.theme),
                                 "pptx.figures")
        self.assertTrue(failed, detail)

    def test_a_chart_picture_swapped_in_the_report_fails(self):
        src = os.path.join(self.out, "r_v1.docx")
        broken = os.path.join(self.out, "r_picture.docx")
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as out:
            media = sorted(n for n in z.namelist() if n.startswith("word/media/"))
            for item in z.infolist():
                data = z.read(item.filename)
                if item.filename == media[-1]:
                    data = z.read(media[0])  # one chart's picture where another's was
                out.writestr(item, data)
        failed, detail = _failed(quality.check_docx(broken, self.model, self.theme, self.req), "docx.charts")
        self.assertTrue(failed, detail)

    def test_a_figure_typed_into_a_sentence_fails(self):
        from docx import Document

        doc = Document(os.path.join(self.out, "r_v1.docx"))
        title = next(s["title"] for s in self.model["sections"] if s["key"] == "summary")
        heading = next(i for i, p in enumerate(doc.paragraphs) if p.text.strip() == title)
        doc.paragraphs[heading + 1].add_run(" ยอดขายเพิ่ม 47,000 บาท")
        broken = os.path.join(self.out, "r_typed.docx")
        doc.save(broken)
        failed, detail = _failed(quality.check_docx(broken, self.model, self.theme, self.req), "docx.narrative")
        self.assertTrue(failed)
        self.assertIn("47,000", detail)


class EveryShapeOfInputPasses(unittest.TestCase):
    """The integration lane found it: with one month and no budget the P&L table and the table under a
    chart had the same headings, and the gate read the wrong one. Every combination of inputs renders
    files that pass their own gate."""

    def test_one_two_and_three_files(self):
        for names in (["GL_2569-09.xlsx"], ["GL_2569-09.xlsx", "GL_2569-08.xlsx"],
                      ["GL_2569-09.xlsx", "งบประมาณ_2569.xlsx"], ["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"]):
            c = computed_for(names)
            out = tempfile.mkdtemp()
            res = render_all(c, {**REQ, "formats": ["docx", "pptx", "xlsx"]}, in_dir=out, out_dir=out)
            bad = [(a["format"], x["name"], x["detail"]) for a in res["artifacts"] for x in a["quality"]["checks"] if not x["ok"]]
            self.assertTrue(res["passed"], (names, bad))


class ACitationBelongsToItsFigure(unittest.TestCase):
    """The audit: "SG&A over budget 10.5%" cited c33, cost of sales, because both printed 10.5%."""

    @classmethod
    def setUpClass(cls):
        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])

    def test_two_figures_that_print_the_same_keep_their_own_citations(self):
        f = self.computed["figures"]
        sga, cogs = f["operating_expenses_var_budget_pct"], f["cost_of_sales_var_prior_pct"]
        self.assertEqual(sga["text"], cogs["text"])  # the trap: both print the same
        lines = {l["key"]: l for l in self.computed["lines"]}
        self.assertEqual(sga["cite"], lines["operating_expenses"]["cite_var_budget"])
        self.assertEqual(cogs["cite"], lines["cost_of_sales"]["cite_var_prior"])
        self.assertNotEqual(sga["cite"], cogs["cite"])

    def test_the_report_puts_the_sga_figure_with_the_sga_budget_citation(self):
        from docx import Document

        out = tempfile.mkdtemp()
        narrative = {"summary": "ค่าใช้จ่ายในการขายและบริหารสูงกว่างบประมาณ {operating_expenses_var_budget_pct}",
                     "movements": [], "watch": [], "next_steps": []}
        render_all(self.computed, {**REQ, "formats": ["docx"], "narrative": narrative}, in_dir=out, out_dir=out)
        doc = Document(os.path.join(out, "r_v1.docx"))
        p = next(p for p in doc.paragraphs if "สูงกว่างบประมาณ" in p.text)
        runs = [(r.text, bool(r.font.superscript)) for r in p.runs]
        i = next(i for i, (t, sup) in enumerate(runs) if not sup and t.rstrip().endswith("%"))
        cite = runs[i + 1][0].strip()
        lines = {l["key"]: l for l in self.computed["lines"]}
        self.assertEqual(cite, lines["operating_expenses"]["cite_var_budget"])
        sga = next(c for c in self.computed["citations"] if c["id"] == cite)
        # …and that citation is the SG&A budget comparison, which leads to the budget's own cells
        self.assertIn("ค่าใช้จ่ายในการขายและบริหาร", sga["label"])
        budget = next(c for c in self.computed["citations"] if c["id"] == sga["derived_from"][1])
        accounts = [next(c for c in self.computed["citations"] if c["id"] == x) for x in budget["derived_from"]]
        self.assertTrue(accounts and all(a["sources"] and a["sources"][0]["file"] == "งบประมาณ_2569.xlsx"
                                         and re.match(r"^[A-Z]+\d+$", a["sources"][0]["cells"]) for a in accounts))

    def test_figures_in_parentheses_and_said_in_words_pass_the_read_back(self):
        from docx import Document

        out = tempfile.mkdtemp()
        code = self.computed["top_movements"][0]["code"]
        narrative = {"summary": "รายได้ {revenue} บาท ({revenue_m}) ต้นทุนขายลดลง {cost_of_sales_var_prior_pct:abs} ใน 3 เดือน ปี 2569",
                     "movements": ["บัญชี " + code + " {acct_" + code + "} บาท"], "watch": [], "next_steps": []}
        res = render_all(self.computed, {**REQ, "formats": ["docx"], "narrative": narrative}, in_dir=out, out_dir=out)
        nar = next(c for c in res["artifacts"][0]["quality"]["checks"] if c["name"] == "docx.narrative")
        self.assertTrue(nar["ok"], nar)
        text = " ".join(p.text for p in Document(os.path.join(out, "r_v1.docx")).paragraphs)
        self.assertNotIn("{", text)

    def test_a_chart_cites_every_point_it_draws(self):
        for spec in content.charts(self.computed):
            for s in spec["series"]:
                self.assertEqual(len(s["cites"]), len(spec["categories"]), (spec["id"], s["name"]))
                self.assertTrue(all(s["cites"]), (spec["id"], s["name"]))

    def test_the_appendix_never_hides_ranges(self):
        model = content.build(self.computed, REQ)
        rows = [r for sec in model["sections"] if sec["key"] == "citations"
                for b in sec["blocks"] if b["type"] == "table" for r in b["rows"]]
        self.assertFalse([r for r in rows if "และอีก" in r[3] or "…" in r[3]])


if __name__ == "__main__":
    unittest.main()
