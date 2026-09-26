"""AM2 Phase E — a run's values in the customer's own template.

The mapping screen's data (slots, a first proposal by name, the diff against
a saved version) and the fill through engine/step.py with the gate, on the
Phase A template fixtures (fixtures/templates).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile

from agent_market.deliverables import template_fill as tf
from agent_market.deliverables import template_out as to

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TPL = os.path.join(ROOT, "fixtures", "templates")


def _spec(pkg: str) -> dict:
    with open(os.path.join(ROOT, "packages", pkg, "agent.json"), encoding="utf-8") as f:
        return json.load(f)["templates"]


INVOICE = _spec("invoice_processing")


def _slots(name):
    return to.slots(tf.inspect(os.path.join(TPL, name)))


class MappingScreen(unittest.TestCase):
    def test_named_ranges_propose_by_name_and_explain_what_cannot_be_filled(self):
        sl = {s["key"]: s for s in _slots("xlsx_named_ranges.xlsx")}
        prop = to.propose(list(sl.values()), INVOICE)
        self.assertEqual({k: prop.get(k) for k in ("CompanyName", "Period", "Subtotal", "VatAmount")},
                         {"CompanyName": "company", "Period": "period", "Subtotal": "subtotal_thb", "VatAmount": "vat_thb"})
        self.assertEqual((sl["GrandTotal"]["fillable"], sl["GrandTotal"]["reason"]), (False, "formula_cell"))
        self.assertNotIn("GrandTotal", prop)                       # a formula is never offered as an input
        self.assertEqual(sl["LinesTable"]["kind"], "table")
        self.assertEqual(sl["LinesTable"]["columns"], ["A", "B", "C"])
        self.assertEqual(prop.get("LinesTable"), "lines")

    def test_markers_controls_lists_and_labelled_cells(self):
        prop = to.propose(_slots("docx_placeholders.docx"), INVOICE)
        self.assertEqual({k: prop.get(k) for k in ("company_name", "period", "invoice_count", "total_amount", "vat_amount")},
                         {"company_name": "company", "period": "period", "invoice_count": "documents",
                          "total_amount": "total_thb", "vat_amount": "vat_thb"})
        self.assertNotIn("prepared_by", prop)                      # nothing of the agent's is a preparer's name
        cc = {s["key"]: s for s in _slots("docx_content_controls.docx")}
        self.assertTrue(cc["CompanyName"]["fillable"])
        self.assertEqual((cc["Status"]["fillable"], cc["Status"]["reason"]), (False, "not_plain_text"))
        rr = to.propose(_slots("docx_repeating_rows.docx"), INVOICE)
        self.assertEqual((rr.get("line"), rr.get("line.amount"), rr.get("line.description")),
                         ("lines", "lines.amount", "lines.description"))
        nm = to.propose(_slots("xlsx_no_markers.xlsx"), INVOICE)
        self.assertEqual(nm, {"Sheet1!B1": "company", "Sheet1!B2": "period", "Sheet1!B3": "total_thb"})

    def test_a_new_version_asks_only_what_changed(self):
        old = _slots("docx_placeholders.docx")
        saved = {"slots": old, "mapping": {"company_name": "company", "prepared_by": "company_tax_id"}}
        new = [s for s in old if s["key"] != "vat_amount"] + [{"key": "due_date", "kind": "marker", "label": "due_date",
                                                               "fillable": True, "reason": None}]
        d = to.diff(saved, new)
        self.assertEqual((d["added"], d["removed"]), (["due_date"], ["vat_amount"]))
        shown = to.carry_over(saved, new, to.propose(new, INVOICE))
        self.assertEqual(shown["prepared_by"], "company_tax_id")   # the person's earlier choice is kept
        self.assertEqual(to.diff(None, new)["first"], True)


class Fill(unittest.TestCase):
    """Through engine/step.py, as the runner does: the invoice register's values in a customer's workbook."""

    @classmethod
    def setUpClass(cls):
        from agent_market.engine import step
        from agent_market.packages import invoice_processing as inv
        from agent_market.tests import invoicekit as K

        cls.tmp = tempfile.mkdtemp()
        cls.inp, cls.out = os.path.join(cls.tmp, "in"), os.path.join(cls.tmp, "out")
        os.makedirs(cls.inp)
        os.makedirs(cls.out)
        docs = [inv.parse(os.path.join(K.E2, "docs", n), n, {}) for n in ("d01_clean_full_tax_invoice.pdf",
                                                                          "d05_wht_service_invoice.pdf")]
        cls.computed = inv.compute(docs, {}, "2026-09", defaults=dict(K.load_cases()["company"]), today=K.TODAY)
        with open(os.path.join(cls.inp, "computed.json"), "w", encoding="utf-8") as f:
            json.dump(cls.computed, f, ensure_ascii=False)
        step.JOB_IN, step.JOB_OUT = cls.inp, cls.out
        cls.step = step

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, template: str, mapping: dict):
        shutil.copy(os.path.join(TPL, template), os.path.join(self.inp, "template" + os.path.splitext(template)[1]))
        sl = _slots(template)
        res = self.step.run({"op": "render", "package": "invoice_processing", "computed": "computed.json", "formats": [],
                             "names": {}, "theme": {"id": "navy"},
                             "template": {"input": "template" + os.path.splitext(template)[1],
                                          "name": "filled" + os.path.splitext(template)[1],
                                          "saved": {"slots": sl, "mapping": mapping}}})
        self.assertTrue(res["ok"], res)
        return next(a for a in res["artifacts"] if a["kind"] == "template"), os.path.join(self.out, "filled" + os.path.splitext(template)[1])

    def test_named_ranges_write_input_cells_only_and_ask_for_recalculation(self):
        from openpyxl import load_workbook

        mapping = {"CompanyName": "company", "Period": "period", "Subtotal": "subtotal_thb", "VatAmount": "vat_thb",
                   "LinesTable": "lines", "LinesTable.A": "lines.description", "LinesTable.B": "lines.quantity",
                   "LinesTable.C": "lines.amount"}
        art, path = self.render("xlsx_named_ranges.xlsx", mapping)
        self.assertTrue(art["quality"]["passed"], art["quality"])
        self.assertTrue(art["meta"]["needs_recalc"])
        ws = load_workbook(path)["สรุป"]
        t = self.computed["totals"]["thb"]
        self.assertEqual(ws["B2"].value, self.computed["company"])
        self.assertAlmostEqual(float(ws["B5"].value), float(t["subtotal"]), places=2)
        self.assertEqual(str(ws["B7"].value).replace(" ", ""), "=B5+B6")          # the formula is never written over
        self.assertTrue(str(ws["A10"].value).startswith("บริษัท"))
        with zipfile.ZipFile(path) as z:
            self.assertIn('fullCalcOnLoad="1"', z.read("xl/workbook.xml").decode("utf-8"))

    def test_word_markers_and_repeating_rows(self):
        from docx import Document

        art, path = self.render("docx_repeating_rows.docx", {"period": "period", "total_amount": "total_thb", "line": "lines",
                                                             "line.description": "lines.description",
                                                             "line.quantity": "lines.quantity", "line.amount": "lines.amount"})
        self.assertTrue(art["quality"]["passed"], art["quality"])
        text = "\n".join(c.text for t in Document(path).tables for r in t.rows for c in r.cells)
        self.assertIn("IV6909-0015", text)
        self.assertNotIn("{{", text)

    def test_a_mapped_place_that_cannot_be_filled_fails_the_gate(self):
        # Status is a drop-down control: mapping a value to it is reported, never silently skipped
        art, _ = self.render("docx_content_controls.docx", {"CompanyName": "company", "Status": "period"})
        checks = {c["name"]: c for c in art["quality"]["checks"]}
        self.assertTrue(checks["template.filled"]["ok"])
        self.assertTrue(art["meta"]["not_filled"] or not checks["template.no_silent_gap"]["ok"])


if __name__ == "__main__":
    unittest.main()
