"""AM23 §3.1/§3.2 — the model's name comes off every file, and one neutral line that an admin can switch off.

Permanent fixtures, for every agent and every file it makes from its own sample:

  * a model drafted the words and the organisation has the line on → the one neutral line is in the file's closing
    block, and it names no model, no vendor and not the platform; switched off → it is nowhere;
  * the rules composed the words → their sentence is in the file whatever the switch says, and it names no model;
    a model drafted them → that sentence is nowhere in any file (the fixture that catches it left in);
  * no file names a model this run used — the one that wrote the words or one that read a scan — and the gate that
    reads the finished file back fails one that does.
"""

from __future__ import annotations

import copy
import importlib
import os
import shutil
import tempfile
import unittest

from agent_market.deliverables.look import commentary
from agent_market.engine import words
from agent_market.tests import samplekit

PACKAGES = ("bank_reconciliation", "expense_audit", "invoice_processing", "po_matching", "procurement", "financial_report")
MODEL, VISION = "qwen3.8-27b-fp8", "typhoon-ocr-test-7b"


def formats_of(pkg: str) -> list[str]:
    return [o["format"] for o in samplekit.manifest(pkg)["outputs"] if o["format"] != "pdf"]


def carries_commentary(pkg: str, fmt: str) -> bool:
    """The files whose words a model drafts: every workbook's commentary sheet, the report and the deck — not the
    financial report's table of figures, nor procurement's purchase-request draft."""
    if pkg == "financial_report":
        return fmt in ("docx", "pptx")
    if pkg == "procurement":
        return fmt == "xlsx"
    return True


class TheClosingLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.computed = {}
        for pkg in PACKAGES:
            r = samplekit.render_sample(pkg, out_dir=tempfile.mkdtemp(dir=cls.tmp))
            cls.computed[pkg] = r["computed"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, pkg: str, narrative: dict, switch=None, models=None):
        """The package's own render on its sample's computed table; the texts of every file it made."""
        computed = copy.deepcopy(self.computed[pkg])
        if models is not None:
            computed["models"] = models
        out = tempfile.mkdtemp(dir=self.tmp)
        self._last_out = out
        fmts = formats_of(pkg)
        req = {"formats": fmts, "names": {f: f"{pkg}.{f}" for f in fmts}, "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"},
               "assumptions": [], "narrative": narrative,
               "run": {"id": "t", "date": "2026-10-17", "agent_version": "t", "preparer": "ผู้ทดสอบ", "organisation": "บริษัท ทดสอบ จำกัด"},
               "model_info": {"model": narrative.get("model"), "self_hosted": True}}
        if switch is not None:
            req["assistant_line"] = switch
        mod = importlib.import_module(f"agent_market.packages.{pkg}")
        res = mod.render(computed, req, in_dir=out, out_dir=out)
        texts = {a["format"]: commentary.file_texts(os.path.join(out, a["name"])) for a in res["artifacts"]}
        return res, texts

    def model_wrote(self, pkg: str) -> dict:
        return dict(self.computed[pkg]["fallback_narrative"], written_by="model", model=MODEL)

    def rules_wrote(self, pkg: str) -> dict:
        return dict(self.computed[pkg]["fallback_narrative"], written_by="rules")

    @staticmethod
    def failed(res) -> list:
        return [(a["name"], c["name"], c.get("detail")) for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]

    def test_the_neutral_line_is_in_the_closing_block_when_on_and_nowhere_when_switched_off(self):
        line = words.SHARED("doc.assistant.line")
        for pkg in PACKAGES:
            for switch in (None, True, False):
                with self.subTest(pkg=pkg, switch=switch):
                    res, texts = self.render(pkg, self.model_wrote(pkg), switch)
                    self.assertTrue(res["passed"], self.failed(res))
                    self.assertTrue(texts, pkg)
                    for fmt, ts in texts.items():
                        if switch is False or not carries_commentary(pkg, fmt):
                            self.assertNotIn(line, ts, (pkg, fmt))
                            continue
                        self.assertEqual(sum(1 for t in ts if t == line), 1, (pkg, fmt))
                        # the closing block, never the cover: a document's and a deck's closing pages; a workbook's
                        # commentary sheet, under its references (test_the_workbook_line_is_under_the_references)
                    if switch is not False and pkg == "financial_report":
                        self.assert_closing(res, line)

    def assert_closing(self, res, line: str):
        """The report's line is in its last section, after the sources with their hashes and the references; the
        deck's on its last slide."""
        from docx import Document
        from pptx import Presentation

        for a in res["artifacts"]:
            path = os.path.join(self._last_out, a["name"])
            if a["format"] == "docx":
                paras = [p.text for p in Document(path).paragraphs]
                at = paras.index(line)
                sources = paras.index(words.SHARED("docs.section.sources"))
                citations = paras.index(words.SHARED("docs.section.citations"))
                self.assertGreater(at, citations)
                self.assertGreater(citations, sources)
                self.assertEqual(paras[at:].count(line), 1)
            elif a["format"] == "pptx":
                slides = list(Presentation(path).slides)
                last = [p.text for s in slides[-1:] for sh in s.shapes if sh.has_text_frame for p in sh.text_frame.paragraphs]
                self.assertIn(line, last, "the deck's line is on its closing slide")

    def test_the_workbook_line_is_under_the_references_of_its_commentary_never_on_the_face(self):
        from openpyxl import load_workbook

        line = words.SHARED("doc.assistant.line")
        for pkg in ("bank_reconciliation", "expense_audit", "invoice_processing", "po_matching", "procurement"):
            with self.subTest(pkg=pkg):
                out = tempfile.mkdtemp(dir=self.tmp)
                mod = importlib.import_module(f"agent_market.packages.{pkg}")
                req = {"formats": ["xlsx"], "names": {"xlsx": "w.xlsx"}, "theme": {"id": "navy"}, "narrative": self.model_wrote(pkg),
                       "run": {"id": "t", "date": "2026-10-17", "agent_version": "t"}, "model_info": {"model": MODEL, "self_hosted": True}}
                mod.render(copy.deepcopy(self.computed[pkg]), req, in_dir=out, out_dir=out)
                wb = load_workbook(os.path.join(out, "w.xlsx"))
                where = [(ws.title, c.row) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value == line]
                self.assertEqual(len(where), 1, (pkg, where))
                sheet, row = where[0]
                self.assertEqual(sheet, words.SHARED("doc.sheet.commentary"), pkg)
                self.assertNotEqual(sheet, wb.worksheets[0].title, (pkg, "never on the face"))
                refs = next(c.row for r in wb[sheet].iter_rows() for c in r if c.value == words.SHARED("doc.commentary.refs"))
                self.assertGreater(row, refs, (pkg, "under the references"))

    def test_the_line_names_no_model_no_vendor_and_not_the_platform(self):
        for lang in ("th", "en"):
            with words.speaking(lang):
                for key in ("doc.assistant.line", "doc.commentary.rules_composed"):
                    s = words.SHARED(key).lower()
                    for name in ("model", "โมเดล", "qwen", "deepseek", "openai", "anthropic", "claude", "gpt", "sran", "สราญ"):
                        self.assertNotIn(name, s, (lang, key))

    def test_the_rules_sentence_is_in_the_file_of_a_model_away_run_whatever_the_switch(self):
        sentence = words.SHARED("doc.commentary.rules_composed")
        for pkg in PACKAGES:
            for switch in (True, False):
                with self.subTest(pkg=pkg, switch=switch):
                    res, texts = self.render(pkg, self.rules_wrote(pkg), switch)
                    self.assertTrue(res["passed"], self.failed(res))
                    for fmt, ts in texts.items():
                        if carries_commentary(pkg, fmt):
                            self.assertTrue(any(sentence in t for t in ts), (pkg, fmt))
                    self.assertFalse(any(words.SHARED("doc.assistant.line") in ts for ts in texts.values()), pkg)

    def test_the_rules_sentence_is_absent_from_every_file_of_every_model_present_run(self):
        sentence = words.SHARED("doc.commentary.rules_composed")
        for pkg in PACKAGES:
            for switch in (True, False):
                with self.subTest(pkg=pkg, switch=switch):
                    res, texts = self.render(pkg, self.model_wrote(pkg), switch)
                    self.assertTrue(res["passed"], self.failed(res))
                    for fmt, ts in texts.items():
                        self.assertFalse(any(sentence in t for t in ts), (pkg, fmt))

    def test_no_file_of_any_agent_names_a_model_this_run_used(self):
        models = [{"model": VISION, "self_hosted": True, "documents": ["d1"]}]
        for pkg in PACKAGES:
            with self.subTest(pkg=pkg):
                res, texts = self.render(pkg, self.model_wrote(pkg), True, models=models)
                self.assertTrue(res["passed"], self.failed(res))
                for fmt, ts in texts.items():
                    for name in (MODEL, VISION):
                        self.assertFalse(any(name.lower() in t.lower() for t in ts), (pkg, fmt, name))
                checks = [c for a in res["artifacts"] for c in a["quality"]["checks"] if c["name"] == "file.no_model"]
                self.assertEqual(len(checks), len(res["artifacts"]), (pkg, "every file is read back for it"))

    def test_the_gate_fails_a_file_that_names_a_model(self):
        names = commentary.model_names({"model": MODEL}, {"models": [{"model": VISION}]}, {"model_info": {"model": MODEL}})
        self.assertEqual(names, sorted({MODEL, VISION}))
        self.assertTrue(commentary.no_model_name(["ตัวเลขทุกตัวมาจากสคริปต์"], names)["ok"])
        bad = commentary.no_model_name(["คำอธิบายเขียนด้วย QWEN3.8-27B-FP8 ที่รันในองค์กร"], names)
        self.assertFalse(bad["ok"])
        self.assertIn(MODEL, bad["detail"])

    def test_the_commentary_gate_holds_the_switch(self):
        pkg = "bank_reconciliation"
        out = tempfile.mkdtemp(dir=self.tmp)
        from agent_market.packages import bank_reconciliation as mod

        req = {"formats": ["xlsx"], "names": {"xlsx": "b.xlsx"}, "theme": {"id": "navy"}, "narrative": self.model_wrote(pkg),
               "run": {"id": "t", "date": "2026-10-17", "agent_version": "t"}, "model_info": {"model": MODEL, "self_hosted": True}}
        res = mod.render(copy.deepcopy(self.computed[pkg]), req, in_dir=out, out_dir=out)
        self.assertTrue(res["passed"], self.failed(res))
        path = os.path.join(out, "b.xlsx")
        self.assertTrue(commentary.check(path, self.model_wrote(pkg), self.computed[pkg], req)["ok"])
        # the same file read as if the organisation had switched the line off: it carries a line it should not
        self.assertFalse(commentary.check(path, self.model_wrote(pkg), self.computed[pkg], {**req, "assistant_line": False})["ok"])


if __name__ == "__main__":
    unittest.main()
