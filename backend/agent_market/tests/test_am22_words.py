"""AM22 — the words every agent writes: counted as a reader sees them, budgeted the same width in both
languages, printed on the page with who wrote them, and never dropped on the way.

* One count (§1.4): a display string is measured in the characters a reader sees (agent_market.columns),
  never with bare len(); the catalogue's limits are compared in Latin columns, a Thai column weighed by how
  much wider it is in the family, so a Thai run and an English run are allowed the same width of text.
* The commentary sheet (§1.1): the five workbook agents print their commentary on a sheet after the face;
  a run the rules wrote says so on its face, and a run a model wrote never carries that line (the second
  fixture is the one that catches the line left in by accident). The gate opens the file and reads both back.
* Nothing drops (§1.2): nine points reach the sheet as nine; a list too long for its layout is refused
  naming the section, and no file is written — the refusal case is the test (CARD-RULES §6c).
* The report (§1.1): the same line under its summary when the rules wrote, nowhere when a model did; and a
  summary the validator's character count cannot fault but the frame cannot hold is refused by the renderer.

Runs inside the sandbox image.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import unittest

from agent_market import columns as C
from agent_market.deliverables.look import catalogue, measure
from agent_market.engine import words

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)   # backend/agent_market
RULES_LINE = "doc.commentary.rules_wrote"


def texts_of_xlsx(path: str) -> list[str]:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    return [ws.title for ws in wb.worksheets] + [str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None]


class OneCount(unittest.TestCase):
    def test_a_thai_mark_takes_no_column_and_the_engine_counts_the_same_way(self):
        from agent_market.engine.findings import display_len

        self.assertEqual(C.columns("กำไร"), 4)          # five code points
        self.assertEqual(C.columns("ที่ปรึกษา"), 6)      # nine code points
        self.assertEqual(C.columns("profit"), 6)
        self.assertEqual(C.columns(""), 0)
        self.assertEqual(display_len.__module__, "agent_market.engine.findings")
        self.assertEqual(display_len("ที่ปรึกษา"), C.columns("ที่ปรึกษา"))

    def test_nothing_a_reader_sees_is_measured_with_bare_len(self):
        # the contract (§1.4): the catalogue's measure has no len() on a display string left in it
        with open(os.path.join(ROOT, "deliverables", "look", "catalogue.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def measure("):src.index("def _fits(")]
        self.assertNotRegex(body, r"len\((?:v\.strip|str\()")
        self.assertIn("latin_columns(", body)
        got = catalogue.measure({"title": "ที่ปรึกษา", "items": ["กำไร", "profit"]})
        self.assertAlmostEqual(got["title"], 6 * measure.width_ratio("th"), places=6)
        self.assertEqual(got["items"], 2)
        self.assertAlmostEqual(got["item"], max(4 * measure.width_ratio("th"), 6.0), places=6)

    def test_the_width_of_a_thai_column_is_measured_from_the_font_not_typed(self):
        ratio = measure.width_ratio("th")
        self.assertEqual(measure.width_ratio("en"), 1.0)
        # Sarabun: a Thai column is about a quarter wider than a Latin one; a font change moves this and is noticed
        self.assertGreater(ratio, 1.15)
        self.assertLess(ratio, 1.35)


class TheSameWidthInBothLanguages(unittest.TestCase):
    def test_a_thai_run_and_an_english_run_are_told_the_same_width_of_text(self):
        for fmt in ("pptx", "xlsx", "docx"):
            th, en = catalogue.writing_limits(fmt, "th"), catalogue.writing_limits(fmt, "en")
            self.assertTrue(all(isinstance(v, int) and not isinstance(v, bool) for v in {**th, **en}.values()),
                            "the runner reads writing_limits as numbers only (map[string]int)")
            for key in ("summary_chars", "bullet_chars"):
                self.assertLess(th[key], en[key], (fmt, key))
                # the Thai budget in columns, widened by the Thai column's width, is the English budget (within a column)
                self.assertAlmostEqual(th[key] * measure.width_ratio("th"), en[key], delta=1.3, msg=(fmt, key))
            self.assertEqual(th["bullets_max"], en["bullets_max"])
            self.assertEqual(th["items_max"], en["items_max"])

    def test_the_budget_is_the_same_rendered_width_measured_in_the_font(self):
        # a Thai text of the Thai budget and an English text of the English budget are the same width in Sarabun
        th_lim, en_lim = catalogue.writing_limits("pptx", "th")["summary_chars"], catalogue.writing_limits("pptx", "en")["summary_chars"]
        th_text = en_text = ""
        for t in measure._corpus("th"):
            if C.columns(th_text + t) > th_lim:
                break
            th_text += t
        for t in measure._corpus("en"):
            if C.columns(en_text + " " + t) > en_lim:
                break
            en_text += " " + t
        self.assertGreater(C.columns(th_text), th_lim * 0.9)
        self.assertGreater(C.columns(en_text), en_lim * 0.9)
        w_th, w_en = measure.width_pt(th_text, 18), measure.width_pt(en_text.strip(), 18)
        self.assertLess(abs(w_th - w_en) / w_en, 0.12, (w_th, w_en))

    def test_the_run_language_is_the_default(self):
        with words.speaking("en"):
            self.assertEqual(catalogue.writing_limits(), catalogue.writing_limits(lang="en"))
        with words.speaking("th"):
            self.assertEqual(catalogue.writing_limits(), catalogue.writing_limits(lang="th"))

    def test_what_every_package_puts_in_its_table_is_what_the_runner_can_read(self):
        # every package's writing_limits is a map of numbers: the runner reads it as map[string]int, and a word in it
        # fails the reading of the whole computed table — the narrative step of every run (found by AM22's probe)
        import json

        for fmt in ("pptx", "docx", "xlsx"):
            for lang in ("th", "en"):
                lim = json.loads(json.dumps(catalogue.writing_limits(fmt, lang)))
                for k, v in lim.items():
                    self.assertTrue(isinstance(v, int) and not isinstance(v, bool), (fmt, lang, k, v))


class TheCommentarySheet(unittest.TestCase):
    """The bank reconciliation's sample, rendered with each kind of commentary."""

    @classmethod
    def setUpClass(cls):
        from agent_market.tests import bankkit, samplekit

        st = samplekit.self_test("bank_reconciliation")
        base = samplekit.package_dir("bank_reconciliation")
        files = [(os.path.join(base, rel), os.path.basename(rel)) for rel in st["files"]]
        res = bankkit.run_files(files, defaults=samplekit.self_test_defaults("bank_reconciliation"), message=st.get("message", ""))
        cls.computed = res["computed"]
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, narrative: dict | None, model_info: dict | None = None, name: str = "b.xlsx"):
        from agent_market.packages import bank_reconciliation as pkg

        out = tempfile.mkdtemp(dir=self.tmp)
        req = {"formats": ["xlsx"], "names": {"xlsx": name}, "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"},
               "run": {"id": "t", "date": "2026-10-17", "agent_version": "t", "preparer": "ผู้ทดสอบ", "organisation": "บริษัท ทดสอบ จำกัด"},
               "model_info": model_info or {"model": None, "self_hosted": None}}
        if narrative is not None:
            req["narrative"] = narrative
        return pkg.render(copy.deepcopy(self.computed), req, in_dir=out, out_dir=out), os.path.join(out, name)

    def checks(self, res):
        return {c["name"]: c for a in res["artifacts"] for c in a["quality"]["checks"]}

    def test_a_run_the_rules_wrote_says_so_on_its_face_and_the_gate_reads_it_back(self):
        fb = dict(self.computed["fallback_narrative"], written_by="rules")
        res, path = self.render(fb)
        self.assertTrue(res["passed"], [(c["name"], c["detail"]) for c in self.checks(res).values() if not c["ok"]])
        texts = texts_of_xlsx(path)
        self.assertIn(words.SHARED("doc.sheet.commentary"), texts[:3], "the commentary is the second sheet, after the face")
        self.assertEqual(sum(1 for t in texts if t == words.SHARED(RULES_LINE)), 1)
        self.assertTrue(self.checks(res)["xlsx.commentary"]["ok"])
        # the runner sends no narrative to an engine that is run by hand: the rules' words, saying so
        res2, path2 = self.render(None)
        self.assertIn(words.SHARED(RULES_LINE), texts_of_xlsx(path2))
        self.assertTrue(res2["passed"])

    def test_when_a_model_answered_and_was_refused_the_file_says_that_and_not_that_none_was_available(self):
        fb = dict(self.computed["fallback_narrative"], written_by="rules", why="unusable")
        res, path = self.render(fb)
        self.assertTrue(res["passed"], [(c["name"], c["detail"]) for c in self.checks(res).values() if not c["ok"]])
        texts = texts_of_xlsx(path)
        self.assertIn(words.SHARED("doc.commentary.rules_unusable"), texts)
        self.assertNotIn(words.SHARED(RULES_LINE), texts)

    def test_a_run_a_model_wrote_never_carries_the_line_anywhere(self):
        nar = {"summary": "กระทบยอดงวดนี้ลงตัว ยอดหลังปรับปรุงฝั่งธนาคาร {adjusted_bank} เท่ากับฝั่งบัญชี {adjusted_book}",
               "movements": ["จับคู่ได้ {matched} คู่", "รายการกระทบยอด {items} รายการ"], "watch": [], "next_steps": ["ควรตรวจสอบร่างรายการปรับปรุงก่อนบันทึก"],
               "written_by": "model", "model": "qwen3.8-27b-fp8"}
        res, path = self.render(nar, {"model": "qwen3.8-27b-fp8", "self_hosted": True})
        self.assertTrue(res["passed"], [(c["name"], c["detail"]) for c in self.checks(res).values() if not c["ok"]])
        texts = texts_of_xlsx(path)
        self.assertNotIn(words.SHARED(RULES_LINE), texts)
        self.assertNotIn(words.SHARED("doc.commentary.rules_unusable"), texts)
        self.assertTrue(any("qwen3.8-27b-fp8" in t for t in texts), "the sheet names the model that wrote")
        # the figures are printed with their citations beside them, and no key is left as written
        self.assertTrue(any(self.computed["figures"]["adjusted_bank"]["text"] in t for t in texts))
        self.assertFalse(any("{adjusted_bank}" in t for t in texts))
        self.assertTrue(any(t == "a1, a2" for t in texts), texts[:40])
        # the gate catches the line left in by accident
        from agent_market.deliverables.look import commentary

        left_in = dict(nar, written_by="rules")
        bad = commentary.check(path, left_in, self.computed, {"model": "qwen3.8-27b-fp8", "self_hosted": True})
        self.assertFalse(bad["ok"])
        res_r, path_r = self.render(dict(nar, written_by="rules"))
        extra = commentary.check(path_r, nar, self.computed, {"model": "qwen3.8-27b-fp8", "self_hosted": True})
        self.assertFalse(extra["ok"], "a file that says the rules wrote, read against a model's narrative, does not pass")

    def test_nine_points_reach_the_sheet_as_nine(self):
        nine = [f"ข้อสังเกตที่ {i}: ควรตรวจสอบรายการที่ยังไม่มีคู่" for i in range(1, 10)]
        nar = {"summary": "สรุปของรอบนี้", "movements": [], "watch": nine, "next_steps": [], "written_by": "model", "model": "m"}
        res, path = self.render(nar, {"model": "m", "self_hosted": True})
        self.assertFalse(res.get("refused"), res.get("refused"))
        texts = texts_of_xlsx(path)
        for line in nine:
            self.assertIn(line, texts)
        self.assertTrue(self.checks(res)["xlsx.commentary"]["ok"])

    def test_a_commentary_too_long_for_its_layout_is_refused_naming_the_section_and_no_file_is_written(self):
        nar = {"summary": "สรุป", "movements": ["ก" * 2000], "watch": [], "next_steps": [], "written_by": "model", "model": "m"}
        res, path = self.render(nar, {"model": "m", "self_hosted": True})
        self.assertFalse(res["passed"])
        self.assertEqual(res["artifacts"], [])
        self.assertFalse(os.path.exists(path), "no file is written for a format that was refused")
        self.assertEqual(len(res["refused"]), 1)
        r = res["refused"][0]
        self.assertEqual(r["format"], "xlsx")
        self.assertIn(words.SHARED("doc.sheet.commentary"), r["check"]["detail"])
        self.assertFalse(r["check"]["ok"])

    def test_a_point_a_little_too_long_steps_down_the_scale_and_one_far_too_long_is_refused(self):
        # AM22 §1.2: what reaches the renderer after two attempts too long — the ladder, unchanged: the small size first
        a_little = "ข้อสังเกต " + "ก" * 165                                      # over the body limit, within the small one
        nar = {"summary": "สรุป", "movements": [a_little], "watch": [], "next_steps": [], "written_by": "model", "model": "m"}
        res, path = self.render(nar, {"model": "m", "self_hosted": True})
        self.assertFalse(res.get("refused"), res.get("refused"))
        roles = [e["role"] for a in res["artifacts"] for e in a["meta"]["layouts"]]
        self.assertIn("small", roles)
        self.assertIn(a_little, texts_of_xlsx(path), "the point is printed whole")
        far = dict(nar, movements=["ก" * 400])
        res, path = self.render(far, {"model": "m", "self_hosted": True})
        self.assertEqual([r["format"] for r in res["refused"]], ["xlsx"])
        self.assertFalse(os.path.exists(path))

    def test_a_key_the_table_does_not_have_stays_as_written_and_the_gate_finds_it(self):
        nar = {"summary": "ยอด {no_such_figure} บาท", "movements": [], "watch": [], "next_steps": [], "written_by": "model", "model": "m"}
        res, path = self.render(nar, {"model": "m", "self_hosted": True})
        self.assertFalse(res["passed"])
        self.assertFalse(self.checks(res)["xlsx.commentary"]["ok"])
        self.assertIn("{no_such_figure}", self.checks(res)["xlsx.commentary"]["detail"])

    def test_the_facts_the_model_is_shown_name_the_figures_by_key_and_the_limits_are_the_sheets(self):
        c = self.computed
        self.assertTrue(c["narrative_facts"] and all(f["heading"] and f["lines"] for f in c["narrative_facts"]))
        joined = "\n".join(l for f in c["narrative_facts"] for l in f["lines"])
        for key in ("adjusted_bank", "adjusted_book", "difference", "matched"):
            self.assertIn("{%s}" % key, joined)
            self.assertIn(key, c["figures"])
            self.assertTrue(c["figures"][key]["cite"], key)
        self.assertEqual(c["writing_limits"]["items_max"], catalogue.writing_limits("xlsx")["items_max"])
        cites = {x["id"] for x in c["citations"]}
        for f in c["figures"].values():
            self.assertIn(f["cite"], cites, "a figure's citation is one of the table's")


class TheReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from .test_deliverables import computed_for

        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx"])
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, narrative: dict, formats=("docx", "pptx")):
        from agent_market.deliverables.render import render_all
        from agent_market.tests.evalkit import texts

        out = tempfile.mkdtemp(dir=self.tmp)
        req = {"formats": list(formats), "names": {f: f"r.{f}" for f in formats}, "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"},
               "assumptions": [], "run": {"id": "t", "date": "2026-09-25", "agent_version": "t"}, "narrative": narrative,
               "model_info": {"model": narrative.get("model"), "self_hosted": True}}
        res = render_all(copy.deepcopy(self.computed), req, in_dir=out, out_dir=out)
        return res, {a["format"]: texts(os.path.join(out, a["name"])) for a in res["artifacts"]}, out

    def test_the_rules_line_is_under_the_summary_when_the_rules_wrote_and_nowhere_when_a_model_did(self):
        line = words.SHARED(RULES_LINE)
        fb = dict(self.computed["fallback_narrative"], written_by="rules")
        res, texts, _ = self.render(fb)
        self.assertTrue(res["passed"], [(a["name"], c["name"]) for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]])
        docx = texts["docx"]
        at = docx.index(line)
        summary_at = docx.index(next(t for t in docx if t.startswith(fb["summary"].split("{")[0][:20])))
        self.assertLess(summary_at, at, "the line is under the summary paragraph")
        self.assertLess(at - summary_at, 3)
        deck_line = words.SHARED("docs.about.rules_wrote")
        self.assertTrue(any(deck_line in t for t in texts["pptx"]), "the deck says so on its closing slide")
        model = dict(fb, written_by="model", model="qwen3.8-27b-fp8")
        res, texts, _ = self.render(model)
        self.assertTrue(res["passed"])
        for fmt, ts in texts.items():
            self.assertNotIn(line, ts, fmt)
            self.assertFalse(any(deck_line in t for t in ts), fmt)

    def test_nine_points_break_across_three_slides_with_the_heading_marked_as_a_continuation_and_all_nine_present(self):
        nine = [f"จุดที่ควรดู {i}: ควรตรวจสอบรายการ" for i in range(1, 10)]
        nar = dict(self.computed["fallback_narrative"], watch=nine, written_by="model", model="m")
        res, texts, _ = self.render(nar, formats=("pptx",))
        self.assertFalse(res.get("refused"))
        pptx = texts["pptx"]
        for line in nine:
            self.assertTrue(any(line in t for t in pptx), line)
        heads = [t for t in pptx if re.search(r"\(\s*\d+\s*/\s*3\s*\)|\d+/3", t) or ("3" in t and "ส่วน" in t)]
        self.assertGreaterEqual(len(heads), 3, "the heading is repeated on each slide of the broken list, marked as a part (AM22 §1.5)")

    def test_every_figure_offered_to_the_model_has_a_citation_the_gate_accepts(self):
        # a line that is zero with no row behind it (income tax in a month without one) has a citation with no
        # source; offered to the model, "income tax was {income_tax_m}" failed the run's gate (8 of 16 measured runs)
        from agent_market.packages.financial_report.scripts import metrics
        ids = {c["id"]: c for c in self.computed["citations"]}
        for key, f in self.computed["figures"].items():
            if f.get("cite"):
                c = ids[f["cite"]]
                self.assertTrue(c["sources"] or c.get("derived_from"), f"{key} → {f['cite']} has no source")
        lines = {l["key"]: l for l in self.computed["lines"]}
        unsourced = [k for k, l in lines.items() if l.get("cite") and not (ids[l["cite"]]["sources"] or ids[l["cite"]].get("derived_from"))]
        self.assertTrue(unsourced, "the fixture has a line with nothing behind it (else this test proves nothing)")
        for k in unsourced:
            self.assertNotIn(k, self.computed["figures"])
            self.assertNotIn(k + "_m", self.computed["figures"])
        self.assertTrue(metrics)

    def test_a_comma_after_a_figure_is_the_sentences_and_the_gate_finds_the_figure(self):
        from agent_market.deliverables import quality
        self.assertEqual(quality.NUMBER.findall("Revenue was 12,480,300, and 3,821, then (41,730)."),
                         ["12,480,300", "3,821", "(41,730)"])
        key = next(k for k, f in self.computed["figures"].items() if f.get("cite") and re.fullmatch(r"[\d,]+", f["text"]))
        fb = self.computed["fallback_narrative"]
        nar = dict(fb, summary=fb["summary"] + " {" + key + "}, " + fb["summary"][:10], written_by="model", model="m")
        res, _texts, _ = self.render(nar)
        self.assertTrue(res["passed"], [(a["name"], c["name"], c["detail"][:160]) for a in res["artifacts"]
                                        for c in a["quality"]["checks"] if not c["ok"]])

    def test_what_the_character_count_cannot_see_the_renderer_still_refuses(self):
        # 400 W's are within the summary's 480-character budget and far wider than the statement's frame
        wide = dict(self.computed["fallback_narrative"], summary="W" * 400, written_by="model", model="m")
        res, texts, out = self.render(wide, formats=("pptx",))
        self.assertEqual([r["format"] for r in res["refused"]], ["pptx"])
        self.assertFalse(os.path.exists(os.path.join(out, "r.pptx")))
        self.assertFalse(res["passed"])


if __name__ == "__main__":
    unittest.main()
