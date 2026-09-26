"""Deliverables: every format renders from one computed table and passes the
gate — and the gate really fails on each thing it claims to catch.

Runs inside the sandbox image (python-docx, python-pptx, openpyxl,
matplotlib and /sandbox/scripts are there).
"""

import copy
import datetime as dt
import json
import os
import tempfile
import unittest
from decimal import Decimal

from agent_market.deliverables import content, quality
from agent_market.deliverables.render import render_all
from agent_market.deliverables.theme import resolve
from agent_market.packages import financial_report as pkg

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(os.path.dirname(HERE), "packages", "financial_report", "sample-data")


def computed_for(names):
    files = [pkg.parse(os.path.join(SAMPLE, n), n, {}) for n in names]
    pre = pkg.preflight(files, message="ปิดงบกันยายน", today=dt.date(2026, 9, 25))
    return pkg.compute(files, pre["roles"], pre["period"])


REQ = {"names": {"docx": "r_v1.docx", "pptx": "d_v1.pptx", "xlsx": "t_v1.xlsx", "pdf": "r_v1.pdf"},
       "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"}, "assumptions": [],
       "run": {"id": "t", "date": "2026-09-25", "agent_version": "1.0.0"},
       "model_info": {"model": "qwen3.8-27b-fp8", "self_hosted": True}}


class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])
        cls.out = tempfile.mkdtemp()
        cls.res = render_all(cls.computed, {**REQ, "formats": ["docx", "pptx", "xlsx"]},
                             in_dir=cls.out, out_dir=cls.out)

    def test_all_formats_pass_the_gate(self):
        self.assertTrue(self.res["passed"], json.dumps(
            [c for a in self.res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]], ensure_ascii=False))
        self.assertEqual({a["format"] for a in self.res["artifacts"]}, {"docx", "pptx", "xlsx"})

    def test_check_details_read_as_plain_thai(self):
        # People see these lines on the run page: no Python lists or internals.
        for a in self.res["artifacts"]:
            for c in a["quality"]["checks"]:
                self.assertNotRegex(c["detail"], r"\[\]|\bcs:|None", f"{a['format']} {c['name']}: {c['detail']}")

    def test_the_report_is_tagged_thai_so_word_breaks_lines_between_words(self):
        # AM1b: python-docx's template says the complex-script language is
        # ar-SA; Word then breaks Thai anywhere ("ค่าจ้า|ง") in narrow cells.
        import re
        import zipfile

        with zipfile.ZipFile(os.path.join(self.out, "r_v1.docx")) as z:
            styles = z.read("word/styles.xml").decode("utf-8")
        default = re.search(r"<w:rPrDefault>.*?</w:rPrDefault>", styles, re.S).group(0)
        self.assertIn('w:bidi="th-TH"', default)

    def test_word_never_has_to_wrap_thai_in_a_table_cell(self):
        # AM1b left over: Word without Thai proofing tools breaks Thai anywhere ("อ้างอิ|ง") when it wraps a
        # cell. Every Thai line of every sized table cell must fit its cell as written, measured with the font.
        from docx import Document

        from agent_market.deliverables.fit import SAFETY, text_width_pt

        CELL_PAD_PT, EMU_PER_PT = 9.0, 12700      # the report's cell margins (90 twips a side); EMU per point

        doc = Document(os.path.join(self.out, "r_v1.docx"))
        checked, too_wide = 0, []
        for t in doc.tables:
            for row in t.rows:
                for cell in row.cells:
                    if cell.width is None:
                        continue
                    avail = cell.width / EMU_PER_PT - CELL_PAD_PT
                    for para in cell.paragraphs:
                        runs = [r for r in para.runs if r.text.strip()]
                        if not runs:
                            continue
                        size = runs[0].font.size.pt if runs[0].font.size else 10.5
                        for line in para.text.split("\n"):
                            if not any(0x0E00 <= ord(ch) <= 0x0E7F for ch in line):
                                continue
                            checked += 1
                            w = text_width_pt(line, "Sarabun", size, bool(runs[0].bold)) * SAFETY
                            if w > avail + 0.01:
                                too_wide.append(f"{line} ({w:.1f} > {avail:.1f} pt)")
        self.assertGreater(checked, 20)
        self.assertEqual(too_wide, [])

    def test_deck_bars_under_budget_keep_their_colour(self):
        # AM1b walkthrough: PowerPoint inverts the fill of a negative bar
        # unless the series says not to — the under-budget bars came out white.
        import zipfile

        with zipfile.ZipFile(os.path.join(self.out, "d_v1.pptx")) as z:
            charts = [z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("ppt/charts/chart")]
        bars = [c for c in charts if "<c:barChart>" in c]
        self.assertTrue(bars)
        import re as _re

        for c in bars:
            self.assertNotIn('<c:invertIfNegative val="1"/>', c)
            self.assertIn('<c:invertIfNegative val="0"/>', c)
            # a bar coloured on its own (<c:dPt>) inverts by itself unless told not to
            for dpt in _re.findall(r"<c:dPt>.*?</c:dPt>", c, _re.S):
                self.assertIn('<c:invertIfNegative val="0"/>', dpt)

    def test_deck_has_8_to_12_slides(self):
        deck = next(a for a in self.res["artifacts"] if a["format"] == "pptx")
        self.assertTrue(8 <= deck["meta"]["slides"] <= 12)

    def test_report_embeds_the_thai_font(self):
        rep = next(a for a in self.res["artifacts"] if a["format"] == "docx")
        self.assertEqual(rep["meta"]["embedded_fonts"], ["Sarabun Regular", "Sarabun Bold"])

    def test_workbook_numbers_equal_the_report(self):
        from openpyxl import load_workbook

        wb = load_workbook(os.path.join(self.out, "t_v1.xlsx"))
        got = {r[0]: r[1] for r in wb["สรุป"].iter_rows(min_row=5, values_only=True) if r[0]}
        for line in self.computed["lines"]:
            self.assertEqual(Decimal(str(got[line["label"]])).quantize(Decimal("0.01")), Decimal(line["current"]))

    def test_rerender_is_byte_stable_for_the_numbers(self):
        # A later render (another format, a new version) quotes the same numbers.
        out2 = tempfile.mkdtemp()
        res2 = render_all(copy.deepcopy(self.computed), {**REQ, "formats": ["xlsx"], "names": {"xlsx": "t_v2.xlsx"}},
                          in_dir=out2, out_dir=out2)
        from openpyxl import load_workbook

        a = [r for r in load_workbook(os.path.join(self.out, "t_v1.xlsx"))["สรุป"].iter_rows(min_row=5, values_only=True)]
        b = [r for r in load_workbook(os.path.join(out2, "t_v2.xlsx"))["สรุป"].iter_rows(min_row=5, values_only=True)]
        self.assertEqual(a, b)
        self.assertTrue(res2["passed"])


class GateCatches(unittest.TestCase):
    """Each gate rule is proven by breaking exactly that thing."""

    @classmethod
    def setUpClass(cls):
        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx"])
        cls.theme = resolve({"id": "navy"})

    def test_blank_citation_fails(self):
        req = {**REQ, "theme": self.theme}
        model = content.build(self.computed, req)
        model["sections"][1]["blocks"][0]["rows"][0][-1] = ""
        self.assertFalse(quality.check_citations(model, self.computed)["ok"])

    def test_empty_assumptions_with_defaulted_findings_fails(self):
        req = {**REQ, "theme": self.theme,
               "assumptions": [{"code": "year_convention_mixed", "source": "default", "text": "x"}]}
        model = content.build(self.computed, req)
        model["assumptions"] = [a for a in model["assumptions"] if a.get("source") != "default"]
        self.assertFalse(quality.check_assumptions(model, req)["ok"])

    def test_overflowing_slide_text_fails(self):
        from pptx import Presentation
        from pptx.util import Inches, Pt

        out = tempfile.mkdtemp()
        res = render_all(self.computed, {**REQ, "formats": ["pptx"]}, in_dir=out, out_dir=out)
        path = os.path.join(out, "d_v1.pptx")
        prs = Presentation(path)
        box = prs.slides[2].shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(0.4))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = "ข้อความยาวมากที่ไม่มีทางใส่ลงในกล่องเล็ก ๆ นี้ได้ " * 6
        run.font.size = Pt(18)
        run.font.name = "Sarabun"
        prs.save(path)
        deck = next(a for a in res["artifacts"] if a["format"] == "pptx")
        checks = quality.check_pptx(path, {"chart_bindings": deck["chart_bindings"]}, self.computed, self.theme)
        self.assertFalse(next(c for c in checks if c["name"] == "pptx.overflow")["ok"])

    def test_chart_values_that_differ_from_the_table_fail(self):
        out = tempfile.mkdtemp()
        res = render_all(self.computed, {**REQ, "formats": ["pptx"]}, in_dir=out, out_dir=out)
        deck = next(a for a in res["artifacts"] if a["format"] == "pptx")
        tampered = copy.deepcopy(deck["chart_bindings"])
        tampered[0]["series"][0]["source_values"][0] = "1.00"
        checks = quality.check_pptx(os.path.join(out, "d_v1.pptx"), {"chart_bindings": tampered},
                                    self.computed, self.theme)
        self.assertFalse(next(c for c in checks if c["name"] == "pptx.charts")["ok"])

    def test_chart_without_binding_fails(self):
        out = tempfile.mkdtemp()
        render_all(self.computed, {**REQ, "formats": ["pptx"]}, in_dir=out, out_dir=out)
        checks = quality.check_pptx(os.path.join(out, "d_v1.pptx"), {"chart_bindings": []}, self.computed, self.theme)
        self.assertFalse(next(c for c in checks if c["name"] == "pptx.charts")["ok"])

    def test_font_outside_the_theme_fails(self):
        from pptx import Presentation

        out = tempfile.mkdtemp()
        res = render_all(self.computed, {**REQ, "formats": ["pptx"]}, in_dir=out, out_dir=out)
        path = os.path.join(out, "d_v1.pptx")
        prs = Presentation(path)
        for sh in prs.slides[1].shapes:
            if sh.has_text_frame:
                for p in sh.text_frame.paragraphs:
                    for r in p.runs:
                        r.font.name = "Comic Sans MS"
        prs.save(path)
        deck = next(a for a in res["artifacts"] if a["format"] == "pptx")
        checks = quality.check_pptx(path, {"chart_bindings": deck["chart_bindings"]}, self.computed, self.theme)
        self.assertFalse(next(c for c in checks if c["name"] == "pptx.fonts")["ok"])

    def test_missing_required_section_fails(self):
        req = {**REQ, "theme": self.theme}
        model = content.build(self.computed, req)
        model["sections"] = [s for s in model["sections"] if s["key"] != "exceptions"]
        from agent_market.deliverables.report_docx import ReportWriter

        out = tempfile.mkdtemp()
        path = os.path.join(out, "r.docx")
        ReportWriter(model, self.theme, {}).write(path)
        checks = quality.check_docx(path, content.build(self.computed, req), self.theme, req)
        self.assertFalse(next(c for c in checks if c["name"] == "docx.sections")["ok"])


class PdfIsOnlyAConversion(unittest.TestCase):
    """AM1b took PDF out (ReportLab's Thai shaping was wrong). AM2: a PDF is the finished .docx converted by
    LibreOffice in the doc-converter service and gated by the runner (runner/pdf.go) — offered as an optional
    output, never required, and never drawn by a second renderer here."""

    def test_the_manifest_offers_pdf_as_an_optional_conversion(self):
        import agent_market.packages.financial_report as fr

        with open(os.path.join(os.path.dirname(fr.__file__), "agent.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        pdf = next(o for o in manifest["outputs"] if o["format"] == "pdf")
        self.assertFalse(pdf["default"])
        self.assertIn("deliverable.pdf", manifest["capabilities"]["optional"])
        self.assertNotIn("deliverable.pdf", manifest["capabilities"]["required"])

    def test_the_renderer_makes_no_pdf(self):
        c = computed_for(["GL_2569-09.xlsx"])
        out = tempfile.mkdtemp()
        res = render_all(c, {**REQ, "formats": ["pdf"]}, in_dir=out, out_dir=out)
        self.assertEqual(res["artifacts"], [])
        self.assertFalse([n for n in os.listdir(out) if n.endswith(".pdf")])


class CitationMarkers(unittest.TestCase):
    def test_numbers_in_prose_get_their_cite_even_inside_parentheses(self):
        c = computed_for(["GL_2569-09.xlsx"])
        rev = next(l for l in c["lines"] if l["key"] == "revenue")
        text = f"รายได้ {c['quotable']['revenue']} บาท ({c['quotable']['revenue_m']})"
        runs = content.mark_citations(text, c)
        cites = [cid for _, cid in runs if cid]
        self.assertEqual(cites, [rev["cite"], rev["cite"]])


class QuotableIdentifiers(unittest.TestCase):
    def test_account_codes_shown_to_the_model_are_listed_as_identifiers(self):
        c = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx"])
        ids = set(c["quotable_ids"])
        shown = {m["code"] for m in c["top_movements"]} | {e["account"] for e in c["exceptions"] if e.get("account")}
        self.assertTrue(shown)
        self.assertLessEqual(shown, ids)
        # Identifiers only: never an amount.
        self.assertFalse(ids & set(c["quotable"].values()))


class QuotableBudgetVariance(unittest.TestCase):
    def test_the_budget_difference_the_report_shows_is_quotable(self):
        # AM1b walkthrough: the table has "ต่างจากงบ" in baht, the narrative may
        # quote it — the guard refused it because it was not in the list.
        c = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])
        rev = next(l for l in c["lines"] if l["key"] == "revenue")
        self.assertIn("revenue_var_budget", c["quotable"])
        self.assertIn("revenue_var_budget_m", c["quotable"])
        self.assertTrue(rev["var_budget"])


class CitationAppendix(unittest.TestCase):
    def test_ratio_citations_read_as_percent(self):
        # AM1b walkthrough: Appendix ข printed "อัตรากำไรสุทธิ 2" (money, rounded)
        # for a 2.4 % ratio.
        c = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx"])
        model = content.build(c, REQ)
        rows = [r for sec in model["sections"] if sec["key"] == "citations"
                for b in sec["blocks"] if b["type"] == "table" for r in b["rows"]]
        nm = next(r for r in rows if r[1] == "อัตรากำไรสุทธิ")
        self.assertRegex(nm[2], r"^-?\d+\.\d%$")


class NoComputeInRender(unittest.TestCase):
    def test_render_never_changes_a_number(self):
        c = computed_for(["GL_2569-09.xlsx"])
        before = json.dumps(c["lines"], sort_keys=True)
        out = tempfile.mkdtemp()
        render_all(c, {**REQ, "formats": ["docx", "xlsx"]}, in_dir=out, out_dir=out)
        self.assertEqual(before, json.dumps(c["lines"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
