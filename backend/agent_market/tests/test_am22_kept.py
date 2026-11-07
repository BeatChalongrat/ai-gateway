"""AM22 §2.6 — three things no vendor was found to do, kept on purpose (docs/agent-market/ARCHITECTURE.md, "A
customer's form: what is kept on purpose"). Each test fails if its feature is taken out:

1. the finished file is opened again and the values read back — a value removed from the file after the fill is
   found missing, whatever the filler reported;
2. a formula cell and a locked cell are refused, the formulas are checked unchanged, and ``needs_recalc`` is set so
   the runner has the converter recalculate the form (runner/template.go recalcTemplate; its Go side is held by
   runner/am22_test.go TestTheFilledFormIsRecalculatedAndReadBack);
3. a new version of a form is inspected again and diffed against the saved one — kept, added, removed, and moved
   when the same address now holds something else — and only what is kept carries its mapping.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from openpyxl import load_workbook

from agent_market.deliverables import template_fill as tf
from agent_market.deliverables import template_out as to
from agent_market.engine import step
from agent_market.tests import am22forms as K

READS = ("rows", "lines", "pdf")


class Kept(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.forms = {os.path.basename(p): p for p in K.write_all(os.path.join(cls.tmp, "forms"))}
        cls.form = cls.forms["bank_reconciliation_form.xlsx"]
        cls.slots = to.slots(tf.inspect(cls.form, reads=READS))
        cls.mapping = K.mapping_for(K.BANK, cls.slots)
        cls.values = {"bank_balance": "1,000.00", "book_balance": "990.00", "company": "บริษัท ทดสอบ จำกัด", "period": "กันยายน 2569"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fill(self, src=None):
        out = os.path.join(tempfile.mkdtemp(dir=self.tmp), "out.xlsx")
        rep = to.fill(src or self.form, out, {"slots": self.slots, "mapping": self.mapping}, self.values)
        return rep, out

    def test_1_the_finished_file_is_opened_again_and_read_back(self):
        rep, out = self.fill()
        key = next(k for k, v in self.mapping.items() if v == "bank_balance")
        slot = next(s for s in self.slots if s["key"] == key)
        self.assertTrue(tf.holds(out, "xlsx", slot, "1,000.00"))
        # the value taken out of the file after the fill: the read-back finds it missing, whatever the filler said
        wb = load_workbook(out)
        sheet, _, coord = key.rpartition("!")
        wb[sheet][coord].value = None
        wb.save(out)
        tvals = to.template_values(self.slots, self.mapping, self.values)
        checks, unfilled = to.verify(self.form, out, self.slots, self.mapping, tvals, rep)
        self.assertIn(key, {u["slot"] for u in unfilled})
        self.assertFalse({c["name"]: c for c in checks}["template.every_slot"]["ok"])

    def test_2_a_formula_and_a_locked_cell_are_refused_and_the_form_is_recalculated(self):
        ref = K.formula_ref(K.BANK)
        rep = tf.fill(self.form, os.path.join(tempfile.mkdtemp(dir=self.tmp), "f.xlsx"), {ref: "5.00"}, {ref: ref})
        self.assertEqual(rep["refused"].get(ref), "formula_cell")
        self.assertTrue(rep["needs_recalc"], "the form's own formulas are recalculated (by the converter, in the runner)")
        self.assertIn(ref, rep["formulas_kept"])
        locked = self.forms["bank_reconciliation_locked.xlsx"]
        key = next(k for k, v in self.mapping.items() if v == "bank_balance")
        rep = tf.fill(locked, os.path.join(tempfile.mkdtemp(dir=self.tmp), "l.xlsx"), {key: "1.00"}, {key: key})
        self.assertEqual(rep["refused"].get(key), "protected_sheet")

    def test_3_a_new_version_is_inspected_again_and_diffed(self):
        # the step the runner runs for an upload, with the saved template: diff and carry-over come back with it
        d = tempfile.mkdtemp(dir=self.tmp)
        inp, out = os.path.join(d, "in"), os.path.join(d, "out")
        os.makedirs(inp)
        os.makedirs(out)
        shutil.copy(self.forms["bank_reconciliation_moved.xlsx"], os.path.join(inp, "template.xlsx"))
        step.JOB_IN, step.JOB_OUT = inp, out
        with open(os.path.join(K.PKGS, "bank_reconciliation", "agent.json"), encoding="utf-8") as f:
            spec = json.load(f)["templates"]
        res = step.run({"op": "inspect_template", "package": "bank_reconciliation", "file": {"name": "v2.xlsx", "input": "template.xlsx"},
                        "spec": spec, "saved": {"slots": self.slots, "mapping": self.mapping}})
        self.assertTrue(res["ok"], res)
        self.assertFalse(res["diff"]["first"])
        self.assertEqual(len(res["diff"]["moved"]), 2)
        for k in res["diff"]["moved"]:
            self.assertNotIn(k, res["proposal"], "a moved slot is confirmed again, never carried")
        for k in res["diff"]["kept"]:
            if k in self.mapping:
                self.assertEqual(res["proposal"].get(k), self.mapping[k], "a kept slot keeps its mapping")


if __name__ == "__main__":
    unittest.main()
