"""AM11 (D) — a customer's form with no marker in it: what is read, what is filled, what is said.

AM10 walked a form that carried {{markers}}. The forms here carry nothing (formkit.py): a label and a
blank, beside it or under it, in Thai and in English. What must hold:

* the reader finds each blank by its label, says on which side the label is, and tells a blank the form
  expects filled from a title or a note that merely has an empty cell next to it;
* a filled form is opened again and read: each value in its own blank, its label still beside it;
* a partly filled form is not a pass. The gap list is the file's, not the mapping's — a blank nobody
  mapped is as empty as one mapped to nothing, and both are named;
* what the reader does not take for a blank (a line of underscores, a signature line) is left exactly
  as it was — and is not in the gap list either: that limit is stated, not hidden.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import openpyxl
from docx import Document

from agent_market.deliverables import template_fill as tf
from agent_market.deliverables import template_out as to

from . import formkit as fk


def _make(name):
    path = os.path.join(tempfile.mkdtemp(), name)
    fk.FORMS[name](path)
    return path


def _fill(src, mapping, values=None, slots=None):
    sl = slots if slots is not None else to.slots(tf.inspect(src))
    dst = os.path.join(os.path.dirname(src), "filled" + os.path.splitext(src)[1])
    rep = to.fill(src, dst, {"slots": sl, "mapping": mapping}, dict(values or fk.VALUES))
    return sl, rep, dst


def _check(rep, name):
    return next(c for c in rep["checks"] if c["name"] == name)


def _table_rows(path, index):
    t = Document(path).tables[index]
    return [[c.text for c in row.cells] for row in t.rows]


class WhatIsReadAsABlank(unittest.TestCase):
    def test_word_a_blank_beside_its_label_and_a_blank_under_its_heading(self):
        ins = tf.inspect(_make("monthly_memo.docx"))
        self.assertEqual(ins["markers"], [])
        self.assertEqual(ins["controls"], [])
        got = {t["cell"]: t["label"] for t in ins["targets"]}
        self.assertEqual(got, {
            "table0.r0c1": "ชื่อบริษัท", "table0.r1c1": "งวดบัญชี", "table0.r2c1": "รายได้จากการขาย (บาท)",
            "table0.r3c1": "กำไรขั้นต้น (บาท)", "table1.r1c0": "ค่าใช้จ่ายรวม (บาท)", "table1.r1c1": "กำไรสุทธิ (บาท)"})
        info = ins["target_info"]
        self.assertEqual({k: v["side"] for k, v in info.items()}, {
            "table0.r0c1": "right", "table0.r1c1": "right", "table0.r2c1": "right", "table0.r3c1": "right",
            "table1.r1c0": "below", "table1.r1c1": "below"})
        self.assertTrue(all(v["required"] for v in info.values()))
        self.assertEqual(info["table1.r1c1"]["label_at"], "table1.r0c1")

    def test_excel_colon_labels_drawn_blanks_under_headings_and_what_is_only_a_title(self):
        ins = tf.inspect(_make("board_cover.xlsx"))
        self.assertEqual(ins["markers"], [])
        self.assertFalse(any(n["fillable"] for n in ins["names"]))
        got = {t["cell"]: t["label"] for t in ins["targets"]}
        self.assertEqual(got, {
            "Cover!C4": "Company", "Cover!C5": "Period", "Cover!C6": "Revenue (THB)", "Cover!C7": "Gross profit (THB)",
            "Cover!C12": "Cash at bank (THB)",
            "Cover!B10": "Total expenses (THB)", "Cover!C10": "Net profit (THB)", "Cover!D10": "Headcount",
            # a title and a note: an empty cell beside them, offered on the mapping screen, never a gap
            "Cover!C2": "Monthly board pack — cover sheet", "Cover!C16": "Figures are in Thai baht."})
        info = ins["target_info"]
        expected = sorted(k for k, v in info.items() if v["required"])
        self.assertEqual(expected, ["Cover!B10", "Cover!C10", "Cover!C12", "Cover!C4", "Cover!C5", "Cover!C6",
                                    "Cover!C7", "Cover!D10"])
        self.assertEqual(info["Cover!D10"], {"required": True, "side": "below", "label_at": "Cover!D9"})
        # the form's own formula is not a blank, and neither is the empty cell under its label
        self.assertNotIn("Cover!C14", got)
        self.assertNotIn("Cover!B15", got)

    def test_a_plain_column_of_labels_is_a_list_of_fields_with_no_border_and_no_colon(self):
        path = os.path.join(tempfile.mkdtemp(), "plain.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"], ws["A2"], ws["A3"] = "บริษัท", "งวด", "ยอดรวม"
        ws["D1"] = "หมายเหตุ"  # alone: a word with an empty cell beside it
        wb.save(path)
        info = tf.inspect(path)["target_info"]
        self.assertEqual({k: v["required"] for k, v in info.items()},
                         {"Sheet!B1": True, "Sheet!B2": True, "Sheet!B3": True, "Sheet!E1": False})

    def test_a_letter_has_no_blank_at_all(self):
        ins = tf.inspect(_make("plain_letter.docx"))
        self.assertEqual(to.slots(ins), [])

    def test_the_purchase_request_has_twelve_blanks_the_form_expects_filled(self):
        sl = to.slots(tf.inspect(_make("purchase_request.docx")))
        self.assertEqual([s["label"] for s in sl], fk.REQUEST_LABELS)
        self.assertTrue(all(s["kind"] == "target" and s["required"] and s["fillable"] for s in sl))


class AFilledFormIsOpenedAgain(unittest.TestCase):
    def test_word_every_value_sits_in_its_blank_beside_its_label(self):
        src = _make("monthly_memo.docx")
        sl, rep, dst = _fill(src, fk.MEMO_MAPPING)
        self.assertTrue(rep["passed"], rep["checks"])
        self.assertEqual(rep["unfilled"], [])
        v = fk.VALUES
        self.assertEqual(_table_rows(dst, 0), [
            ["ชื่อบริษัท", v["company"]], ["งวดบัญชี", v["period"]],
            ["รายได้จากการขาย (บาท)", v["revenue"]], ["กำไรขั้นต้น (บาท)", v["gross_profit"]]])
        self.assertEqual(_table_rows(dst, 1), [["ค่าใช้จ่ายรวม (บาท)", "กำไรสุทธิ (บาท)"],
                                              [v["total_expenses"], v["net_profit"]]])
        self.assertEqual(_check(rep, "template.every_slot")["values"], 6)
        self.assertTrue(_check(rep, "template.beside_label")["ok"])
        # what the reader does not take for a blank is exactly as it was
        after = [p.text for p in Document(dst).paragraphs]
        self.assertIn(fk.MEMO_LINE, after)
        self.assertIn(fk.MEMO_SIGN, after)
        # and the form itself was not written to
        self.assertEqual(_table_rows(src, 0)[0], ["ชื่อบริษัท", ""])

    def test_excel_six_of_eight_is_a_draft_that_names_the_two_it_lacks(self):
        src = _make("board_cover.xlsx")
        sl, rep, dst = _fill(src, fk.COVER_MAPPING)
        self.assertFalse(rep["passed"])
        self.assertEqual({u["slot"]: (u["label"], u["reason"]) for u in rep["unfilled"]},
                         {k: (label, "not_mapped") for k, label in fk.COVER_NO_VALUE.items()})
        every = _check(rep, "template.every_slot")
        self.assertFalse(every["ok"])
        self.assertEqual(every["values"], 8)
        ws = openpyxl.load_workbook(dst)["Cover"]
        v = fk.VALUES
        self.assertEqual([ws["B4"].value, ws["C4"].value], ["Company:", v["company"]])
        self.assertEqual([ws["B5"].value, ws["C5"].value], ["Period:", v["period"]])
        # an amount goes in as a number, under the heading it belongs to
        self.assertEqual([ws["B6"].value, float(ws["C6"].value)], ["Revenue (THB):", 12480300.0])
        self.assertEqual([ws["B9"].value, float(ws["B10"].value)], ["Total expenses (THB)", 10733058.0])
        self.assertEqual([ws["C9"].value, float(ws["C10"].value)], ["Net profit (THB)", 1747242.0])
        self.assertEqual([ws["D9"].value, ws["D10"].value], ["Headcount", None])
        self.assertEqual([ws["B12"].value, ws["C12"].value], ["Cash at bank (THB):", None])
        # an amount is written the way it reads — thousands separated, its own decimals (the financial report's
        # figures are whole baht since AM23, as its report and workbook print them) — when the form's blank said
        # nothing about it; it is still a number, so the form's own formula works
        self.assertEqual(ws["C6"].number_format, "#,##0")
        self.assertEqual(ws["B10"].number_format, "#,##0")
        self.assertIsInstance(ws["C6"].value, (int, float))
        self.assertEqual(ws["C4"].number_format, "General")  # a name is not an amount
        # the title and the note got nothing, and are no gap
        self.assertIsNone(ws["C2"].value)
        self.assertIsNone(ws["C16"].value)
        # the form's own formula is as it was
        self.assertEqual(ws["C14"].value, '=IF(N(C6)=0,"",N(C10)/N(C6))')
        self.assertTrue(_check(rep, "template.formulas_kept")["ok"])

    def test_a_blank_that_has_a_number_format_of_its_own_keeps_it(self):
        src = _make("board_cover.xlsx")
        wb = openpyxl.load_workbook(src)
        wb["Cover"]["C6"].number_format = "#,##0"
        wb.save(src)
        _, rep, dst = _fill(src, fk.COVER_MAPPING)
        self.assertEqual(openpyxl.load_workbook(dst)["Cover"]["C6"].number_format, "#,##0")

    def test_one_field_of_six_is_not_every_field(self):
        """What AM10 would have delivered as "กรอกแบบฟอร์มให้แล้ว … ทุกช่องมีค่า"."""
        src = _make("monthly_memo.docx")
        sl, rep, dst = _fill(src, {"table0.r0c1": "company"})
        self.assertFalse(rep["passed"])
        self.assertEqual(sorted(u["label"] for u in rep["unfilled"]),
                         sorted(fk.MEMO_BESIDE[1:] + fk.MEMO_OVER))
        self.assertTrue(all(u["reason"] == "not_mapped" for u in rep["unfilled"]))
        self.assertEqual(_table_rows(dst, 0)[0], ["ชื่อบริษัท", fk.VALUES["company"]])

    def test_a_mapped_field_the_run_has_no_value_for_is_named_with_that_reason(self):
        src = _make("monthly_memo.docx")
        values = {k: v for k, v in fk.VALUES.items() if k != "company"}
        sl, rep, dst = _fill(src, fk.MEMO_MAPPING, values)
        self.assertFalse(rep["passed"])
        self.assertEqual([(u["label"], u["reason"]) for u in rep["unfilled"]], [("ชื่อบริษัท", "no_value")])

    def test_a_value_beside_another_label_than_the_one_it_was_mapped_for_is_in_the_wrong_slot(self):
        src = _make("monthly_memo.docx")
        sl = to.slots(tf.inspect(src))
        # the mapping was confirmed for the blank under "งวดบัญชี"; the form that is filled has another label there
        d = Document(src)
        d.tables[0].cell(1, 0).text = "เลขที่เอกสาร"
        d.save(src)
        _, rep, dst = _fill(src, fk.MEMO_MAPPING, slots=sl)
        self.assertFalse(rep["passed"])
        beside = _check(rep, "template.beside_label")
        self.assertFalse(beside["ok"])
        self.assertIn("งวดบัญชี", beside["detail"])
        self.assertIn(("งวดบัญชี", "wrong_label"), [(u["label"], u["reason"]) for u in rep["unfilled"]])

    def test_a_form_saved_before_am11_keeps_what_it_was_told(self):
        """Slots saved without ``required`` (an install's form from before this card) are judged as they were."""
        src = _make("monthly_memo.docx")
        sl = [{k: v for k, v in s.items() if k not in ("required", "side", "label_at")} for s in to.slots(tf.inspect(src))]
        _, rep, _ = _fill(src, {"table0.r0c1": "company"}, slots=sl)
        self.assertTrue(rep["passed"])


if __name__ == "__main__":
    unittest.main()
