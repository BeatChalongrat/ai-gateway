"""AM23 Part 2 — every file of a round says the same number, read back from the finished files (deliverables/agree.py).

Every agent's sample, made as a workbook, a report and a deck, in Thai and in English: every figure printed in two or
more of them is printed the same way, and the cover's figures are found in all three. Part 4's fixture 6: a figure
printed two ways — the report's figures table with the number written another way, with another value, the workbook
face's cell under another number format — fails the two files that print it, naming the figure, both places and both
strings; and the customer's own form is held to the same figures as the files beside it.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

from docx import Document
from openpyxl import load_workbook

from agent_market.deliverables import agree
from agent_market.deliverables import template_fill as tf
from agent_market.deliverables import template_out as to
from agent_market.engine import step, words
from agent_market.tests import am22forms as K
from agent_market.tests import samplekit

PKGS = ("bank_reconciliation", "expense_audit", "invoice_processing", "po_matching", "financial_report")
FOUR = PKGS[:4]


def _render(pkg: str, lang: str | None = None) -> tuple[str, dict]:
    d = tempfile.mkdtemp(prefix="am23_agree_")
    return d, samplekit.render_sample(pkg, out_dir=d, formats=["xlsx", "docx", "pptx"], lang=lang)


def _pkg(name: str):
    return importlib.import_module(f"agent_market.packages.{name}")


class EveryFileSaysTheSameNumber(unittest.TestCase):
    def test_every_agents_files_agree_and_the_covers_figures_are_in_all_three(self):
        for pkg in PKGS:
            for lang in (None, "en"):
                with self.subTest(pkg=pkg, lang=lang or "th"):
                    d, r = _render(pkg, lang)
                    try:
                        ag = r["render"]["agree"]
                        self.assertTrue(ag["ok"], r["render"]["artifacts"])
                        self.assertEqual((ag["counts"]["files"], ag["counts"]["pairs"]), (3, 3), ag)
                        self.assertGreaterEqual(ag["counts"]["figures"], 5, ag)
                        checks = [c for a in r["render"]["artifacts"] for c in a["quality"]["checks"] if c["name"] == "files.agree"]
                        self.assertEqual(len(checks), 1, "a run-level check, counted once")
                        if pkg in FOUR:
                            words.use(lang)
                            res = agree.check(d, r["render"]["artifacts"], r["computed"], _pkg(pkg))
                            spec = importlib.import_module(f"agent_market.packages.{pkg}.scripts.document").spec(r["computed"])
                            for key in spec["tiles"]:
                                if r["computed"]["figures"][key]["text"].strip("0.,") == "":
                                    continue   # (a zero: the workbook prints the accounting dash)
                                self.assertEqual(len(res["where"].get(key) or []), 3, f"{key}: {res['where'].get(key)}")
                    finally:
                        words.use(None)
                        shutil.rmtree(d, ignore_errors=True)


class AFigurePrintedTwoWaysFails(unittest.TestCase):
    """Part 4, fixture 6: the same round's files, one of them changed after it was written."""

    @classmethod
    def setUpClass(cls):
        words.use(None)
        cls.d, cls.r = _render("bank_reconciliation")
        cls.view = cls.r["computed"]
        cls.fig = cls.view["figures"]["adjusted_bank"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def _copy(self) -> str:
        d = tempfile.mkdtemp(prefix="am23_agree_bad_")
        for a in self.r["render"]["artifacts"]:
            shutil.copy(os.path.join(self.d, a["name"]), os.path.join(d, a["name"]))
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def _report_figure(self, d: str, text: str) -> None:
        path = os.path.join(d, "sample.docx")
        doc = Document(path)
        label = agree._norm(self.fig["label"])
        done = 0
        for t in doc.tables:
            for row in t.rows:
                cells = row.cells
                if len(cells) == 3 and agree._norm(cells[0].text) == label and cells[2].text.strip() == self.fig["cite"]:
                    runs = cells[1].paragraphs[0].runs
                    runs[0].text = text
                    for run in runs[1:]:
                        run.text = ""
                    done += 1
        self.assertEqual(done, 1, "the report's figures table has the figure's row")
        doc.save(path)

    def _fails(self, d: str, want: str):
        res = agree.check(d, self.r["render"]["artifacts"], self.view, _pkg("bank_reconciliation"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["figure"], "adjusted_bank")
        detail = res["check"]["detail"]
        (p1, s1), (p2, s2) = res["places"]
        self.assertEqual({s1, s2}, {self.fig["text"], want})
        for part in (self.fig["label"], s1, s2, p1, p2):
            self.assertIn(part, detail)
        arts = json.loads(json.dumps(self.r["render"]["artifacts"]))
        self.assertFalse(agree.attach(res, arts))
        failed = sorted(a["name"] for a in arts if not a["quality"]["passed"])
        self.assertEqual(failed, res["bad"], "the two files that print it fail — no winner is picked")
        self.assertEqual(len(failed), 2)
        return res

    def test_the_same_figure_written_without_its_commas_fails_naming_both_places_and_both_strings(self):
        d = self._copy()
        self._report_figure(d, self.fig["text"].replace(",", ""))
        res = self._fails(d, self.fig["text"].replace(",", ""))
        self.assertIn("sample.docx", res["bad"])

    def test_another_value_beside_the_figures_label_fails(self):
        d = self._copy()
        other = f"{Decimal(self.fig['text'].replace(',', '')) + Decimal('0.01'):,.2f}"
        self._report_figure(d, other)
        self._fails(d, other)

    def test_the_workbook_face_under_another_number_format_fails_naming_its_cell(self):
        d = self._copy()
        path = os.path.join(d, "sample.xlsx")
        wb = load_workbook(path)
        ws = wb.worksheets[0]
        doc = importlib.import_module("agent_market.packages.bank_reconciliation.scripts.document")
        label = agree._norm(doc.face(self.view)["adjusted_bank"])
        row = next(row for row in ws.iter_rows() if agree._norm(row[0].value) == label)
        row[1].number_format = "0.00"
        wb.save(path)
        res = self._fails(d, self.fig["text"].replace(",", ""))
        self.assertIn("sample.xlsx", res["bad"])
        self.assertTrue(any(row[0].coordinate in p and ws.title in p for p, _ in res["places"]), res["places"])

    def test_a_zero_the_workbook_prints_as_a_dash_is_not_a_second_way(self):
        money = '#,##0.00;(#,##0.00);"-"'
        self.assertEqual(agree.printed(0, money), "-")
        self.assertEqual(agree.printed(-41730, money), "(41,730.00)")
        self.assertEqual(agree.printed(1538519.65, money), "1,538,519.65")
        self.assertEqual(agree.printed(7, "General"), "7")
        self.assertEqual(agree.printed(0.221, "0.0%"), "22.1%")
        res = agree.check(self.d, self.r["render"]["artifacts"], self.view, _pkg("bank_reconciliation"))
        self.assertTrue(res["ok"])
        self.assertNotIn("sample.xlsx", res["where"].get("difference") or [], "the workbook's dash is not compared")


class TheFinancialReportsWorkbookPrintsItsReportsFigures(unittest.TestCase):
    """What the check found first: until AM23 the financial report's workbook printed satang (3,820.55; 12,480,300.00)
    where its report and deck printed whole baht (3,821; 12,480,300) — the same figure, two roundings. The workbook
    now prints each figure the report's way, its cell keeping the exact value; put back, the old formats fail."""

    @classmethod
    def setUpClass(cls):
        words.use(None)
        cls.d, cls.r = _render("financial_report")
        cls.view = cls.r["computed"]
        cls.xlsx = next(a["name"] for a in cls.r["render"]["artifacts"] if a["format"] == "xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def _summary_row(self, ws, label: str):
        return next(row for row in ws.iter_rows(min_row=5) if agree._norm(row[0].value) == agree._norm(label))

    def test_the_summary_prints_the_reports_figure_and_keeps_the_exact_value(self):
        wb = load_workbook(os.path.join(self.d, self.xlsx))
        ws = wb.worksheets[0]
        line = next(l for l in self.view["lines"] if Decimal(str(l["current"])) != Decimal(str(l["current"])).to_integral_value())
        fig = self.view["figures"][line["key"]]
        cell = self._summary_row(ws, line["label"])[1]
        self.assertEqual(Decimal(str(cell.value)), Decimal(str(line["current"])), "the cell keeps the exact value")
        self.assertEqual(agree.printed(cell.value, cell.number_format), fig["text"])
        self.assertTrue(self.r["render"]["agree"]["ok"], self.r["render"]["agree"])
        self.assertIn(self.xlsx, self.r["render"]["agree"]["files"])

    def test_the_old_two_decimal_summary_fails_naming_both_strings(self):
        from agent_market.deliverables.look.xlsx_styles import MONEY

        d = tempfile.mkdtemp(prefix="am23_agree_fr_")
        self.addCleanup(shutil.rmtree, d, True)
        for a in self.r["render"]["artifacts"]:
            shutil.copy(os.path.join(self.d, a["name"]), os.path.join(d, a["name"]))
        path = os.path.join(d, self.xlsx)
        wb = load_workbook(path)
        ws = wb.worksheets[0]
        line = next(l for l in self.view["lines"] if Decimal(str(l["current"])) != Decimal(str(l["current"])).to_integral_value())
        for row in ws.iter_rows(min_row=5, max_row=4 + len(self.view["lines"])):
            for c in row[1:7]:
                if isinstance(c.value, (int, float)):
                    c.number_format = MONEY
        wb.save(path)
        res = agree.check(d, self.r["render"]["artifacts"], self.view, _pkg("financial_report"))
        self.assertFalse(res["ok"])
        strings = {s for _, s in res["places"]}
        fig = self.view["figures"][res["figure"]]
        self.assertIn(fig["text"], strings)
        self.assertEqual(len(strings), 2)
        exact = next(s for s in strings if s != fig["text"])
        self.assertTrue(agree.same_amount(exact, fig["text"]), (exact, fig["text"]))
        self.assertIn(self.xlsx, res["bad"])
        self.assertTrue(line)


class TheCustomersFormIsHeldToTheSameFigures(unittest.TestCase):
    def setUp(self):
        words.use(None)
        self.tmp = tempfile.mkdtemp(prefix="am23_agree_form_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        forms = {os.path.basename(p): p for p in K.write_all(os.path.join(self.tmp, "forms"))}
        self.form = next(p for n, p in sorted(forms.items()) if n.startswith("bank_reconciliation") and n.endswith(".xlsx"))
        from agent_market.tests.test_am22_forms import computed_of

        self.computed = computed_of("bank_reconciliation")

    def _render(self) -> dict:
        d = tempfile.mkdtemp(dir=self.tmp)
        inp, out = os.path.join(d, "in"), os.path.join(d, "out")
        os.makedirs(inp)
        os.makedirs(out)
        sl = to.slots(tf.inspect(self.form, reads=("rows", "lines", "pdf")))
        with open(os.path.join(inp, "computed.json"), "w", encoding="utf-8") as f:
            json.dump(self.computed, f, ensure_ascii=False)
        shutil.copy(self.form, os.path.join(inp, "template.xlsx"))
        step.JOB_IN, step.JOB_OUT = inp, out
        res = step.run({"op": "render", "package": "bank_reconciliation", "computed": "computed.json", "formats": ["xlsx", "docx"],
                        "names": {}, "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"},
                        "run": {"preparer": "สมชาย ใจดี", "organisation": "บริษัท ทดสอบ จำกัด", "date": "2026-10-17"},
                        "template": {"input": "template.xlsx", "name": "filled.xlsx",
                                     "saved": {"slots": sl, "mapping": K.mapping_for(K.BANK, sl)}}})
        self.assertTrue(res["ok"], res)
        return res

    def test_the_forms_balances_are_compared_with_the_workbook_and_the_report(self):
        res = self._render()
        self.assertTrue(res["agree"]["ok"], res["agree"])
        self.assertIn("filled.xlsx", res["agree"]["files"])
        self.assertEqual(res["agree"]["counts"]["pairs"], 3, res["agree"])
        self.assertFalse(any("_printed" in a for a in res["artifacts"]), "the form's figures are not handed on")

    def test_a_form_value_printed_another_way_fails_the_form_and_the_file_beside_it(self):
        import agent_market.packages.bank_reconciliation as bank

        real = bank.template_values

        def other(computed, req=None):
            v = real(computed, req)
            v["adjusted_bank"] = v["adjusted_bank"].replace(",", "")
            return v

        bank.template_values = other
        self.addCleanup(setattr, bank, "template_values", real)
        res = self._render()
        self.assertFalse(res["agree"]["ok"], res["agree"])
        self.assertIn("filled.xlsx", res["agree"]["bad"])
        self.assertFalse(res["passed"])
        form = next(a for a in res["artifacts"] if a["kind"] == "template")
        self.assertTrue(any(c["name"] == "files.agree" and not c["ok"] for c in form["quality"]["checks"]))


if __name__ == "__main__":
    unittest.main()
