"""AM23 §1.2–§1.4 — the file a person asks for, from the four agents whose job is the workbook.

  * each of them writes a report and a deck from its own sample, which pass their gates read back from the finished
    file: every figure printed as the run's figures table says it, beside its own reference;
  * a document is not a conversion of the workbook: it is written from the computed table, shows the part of a long
    table a reader needs and says the workbook has every row;
  * the workbook a round makes is the same, cell for cell, whether or not a report or a deck was asked for with it,
    and a round that names no format makes the workbook alone — the defaults did not change;
  * a PDF is how a document prints: no package writes one (the runner converts the document the manifest names), and
    a package asked for a kind of file it does not write refuses it by name.
"""

from __future__ import annotations

import copy
import importlib
import os
import re
import shutil
import tempfile
import unittest

from agent_market.deliverables import document
from agent_market.deliverables.look import commentary
from agent_market.engine import words
from agent_market.tests import samplekit

FOUR = ("bank_reconciliation", "expense_audit", "invoice_processing", "po_matching")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
THAI = re.compile("[฀-๿]")


def cells(path: str) -> list:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    return [(ws.title, c.coordinate, c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None]


class TheFourWriteTheirOwnDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.computed = {}
        for lang in ("th", "en"):
            for pkg in FOUR:
                r = samplekit.render_sample(pkg, out_dir=tempfile.mkdtemp(dir=cls.tmp), lang=None if lang == "th" else lang)
                cls.computed[(pkg, lang)] = r["computed"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, pkg: str, formats, lang: str = "th", computed: dict | None = None, narrative: dict | None = None):
        mod = importlib.import_module(f"agent_market.packages.{pkg}")
        out = tempfile.mkdtemp(dir=self.tmp)
        req = {"names": {f: f"{pkg}.{f}" for f in ("xlsx", "docx", "pptx")}, "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"},
               "run": {"id": "t", "date": "2026-10-17", "agent_version": "t", "preparer": "ผู้ทดสอบ", "organisation": "บริษัท ทดสอบ จำกัด"},
               "model_info": {"model": None, "self_hosted": None}}
        if formats is not None:
            req["formats"] = list(formats)
        if narrative is not None:
            req["narrative"] = narrative
        with words.speaking(lang):
            res = mod.render(copy.deepcopy(computed or self.computed[(pkg, lang)]), req, in_dir=out, out_dir=out)
        return res, out

    @staticmethod
    def failed(res) -> list:
        return [(a["name"], c["name"], c.get("detail")) for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]] + \
            [(r.get("format"), (r.get("check") or {}).get("detail")) for r in res.get("refused") or []]

    def test_each_writes_a_report_and_a_deck_that_pass_their_gates_in_both_languages(self):
        for lang in ("th", "en"):
            for pkg in FOUR:
                with self.subTest(pkg=pkg, lang=lang):
                    res, out = self.render(pkg, ["xlsx", "docx", "pptx"], lang)
                    self.assertTrue(res["passed"], self.failed(res))
                    self.assertEqual(sorted(a["format"] for a in res["artifacts"]), ["docx", "pptx", "xlsx"])
                    self.assertEqual({a["kind"] for a in res["artifacts"] if a["format"] != "xlsx"}, {"report", "deck"})
                    figures = self.computed[(pkg, lang)]["figures"]
                    for a in res["artifacts"]:
                        if a["format"] == "xlsx":
                            continue
                        texts = commentary.file_texts(os.path.join(out, a["name"]))
                        squashed = "".join("".join(texts).split())
                        names = {c["name"] for c in a["quality"]["checks"]}
                        self.assertTrue({f"{a['format']}.figures", f"{a['format']}.closing", "file.no_model", "look.fit"} <= names, names)
                        for key, f in figures.items():
                            # every figure prints as the run's one figures table says (its label beside it in the report)
                            self.assertIn("".join(f["text"].split()), squashed, (pkg, a["format"], key))
                        if lang == "en":
                            # (a cell's text is broken between words over lines: compared without the breaks)
                            theirs = ["".join(w.split()) for w in self.customer_words(pkg)]
                            own = [t for t in texts if THAI.search(t)
                                   and not any("".join(t.split()) in w or w in "".join(t.split()) for w in theirs)]
                            self.assertEqual(own, [], (pkg, a["format"], own[:3]))

    def customer_words(self, pkg: str) -> list[str]:
        """What a person's own files hold that an English document prints as it is (names, descriptions)."""
        c = self.computed[(pkg, "en")]
        out = [c.get("company") or "", "บริษัท ทดสอบ จำกัด", "ผู้ทดสอบ"]
        for k in ("items", "claims", "documents", "invoices", "files"):
            for x in c.get(k) or []:
                if isinstance(x, dict):
                    out += [str(v) for v in x.values() if isinstance(v, str)]
                    for v in x.values():
                        if isinstance(v, dict):
                            out += [str(w) for w in v.values() if isinstance(w, str)]
        for files in (c.get("source") or {}).values():
            out += [str(f.get("name") or "") for f in files if isinstance(f, dict)]
        acc = c.get("account") or {}
        out += [str(v) for v in acc.values() if isinstance(v, str)]
        return [w for w in out if w and THAI.search(w)]

    def test_the_workbook_is_the_same_cell_for_cell_with_or_without_a_document(self):
        for pkg in FOUR:
            with self.subTest(pkg=pkg):
                alone, a = self.render(pkg, ["xlsx"])
                both, b = self.render(pkg, ["xlsx", "docx", "pptx"])
                self.assertTrue(alone["passed"] and both["passed"], (self.failed(alone), self.failed(both)))
                self.assertEqual(cells(os.path.join(a, f"{pkg}.xlsx")), cells(os.path.join(b, f"{pkg}.xlsx")))

    def test_a_round_that_names_no_format_makes_the_workbook_alone(self):
        for pkg in FOUR:
            with self.subTest(pkg=pkg):
                res, _ = self.render(pkg, None)
                self.assertEqual([a["format"] for a in res["artifacts"]], ["xlsx"])
                self.assertEqual(samplekit.manifest(pkg)["install"]["defaults"]["formats"], ["xlsx"])

    def test_a_long_table_shows_the_part_a_reader_needs_and_says_the_workbook_has_every_row(self):
        from agent_market.packages.bank_reconciliation.scripts import document as spec_mod

        c = copy.deepcopy(self.computed[("bank_reconciliation", "th")])
        item = dict(c["items"][0])
        c["items"] = [dict(item, id=f"i{n:03d}", amount=str(100 + n), ref=f"R{n:03d}") for n in range(30)]
        spec = spec_mod.spec(c)
        doc = document.content(c, {"run": {"date": "2026-10-17"}}, spec)
        model = document.report_model(doc)
        block = next(s for s in model["sections"] if s["key"] == "table")["blocks"]
        self.assertEqual(len(block[0]["rows"]), document.DOCX_ROWS)
        self.assertEqual(block[0]["rows"][0][4], "129.00", "the largest first")
        note = block[1]["runs"][0][0]
        self.assertIn("30", note)
        self.assertIn(spec["table"]["sheet"], note)

    def test_no_document_reads_the_workbook(self):
        src = open(os.path.join(ROOT, "deliverables", "document.py"), encoding="utf-8").read()
        for pkg in FOUR:
            src += open(os.path.join(ROOT, "packages", pkg, "scripts", "document.py"), encoding="utf-8").read()
        self.assertNotIn("load_workbook", src)
        self.assertNotIn("openpyxl", src)

    def test_no_package_writes_a_pdf_and_one_asked_for_a_kind_it_does_not_write_refuses_it_by_name(self):
        # Part 4, fixture 7: a PDF without a document to print is impossible — the runner converts the document the
        # manifest names, and no module here can write one
        for path, _dirs, files in os.walk(ROOT):
            if "tests" in path or "__pycache__" in path:
                continue
            for f in files:
                if f.endswith(".py"):
                    src = open(os.path.join(path, f), encoding="utf-8").read()
                    for lib in ("reportlab", "fpdf", "weasyprint", "pdfkit", "xhtml2pdf"):
                        self.assertNotIn(f"import {lib}", src, os.path.join(path, f))
        for pkg in FOUR:
            with self.subTest(pkg=pkg):
                res, _ = self.render(pkg, ["pdf"])
                self.assertEqual(res["artifacts"], [])
                self.assertEqual([r["format"] for r in res["refused"]], ["pdf"])
                self.assertIn(".pdf", res["refused"][0]["check"]["detail"])
                m = samplekit.manifest(pkg)
                self.assertEqual([o.get("of") for o in m["outputs"] if o["format"] == "pdf"], ["xlsx"])

    def test_the_po_documents_never_say_approved_or_paid(self):
        from agent_market.packages.po_matching.scripts.match import never_written

        for lang in ("th", "en"):
            res, out = self.render("po_matching", ["docx", "pptx"], lang)
            self.assertTrue(res["passed"], self.failed(res))
            for a in res["artifacts"]:
                low = " ".join(commentary.file_texts(os.path.join(out, a["name"]))).lower()
                for w in never_written():
                    self.assertNotIn(w.lower(), low, (lang, a["format"], w))
                self.assertIn(f"{a['format']}.never", {c["name"] for c in a["quality"]["checks"]})


if __name__ == "__main__":
    unittest.main()
