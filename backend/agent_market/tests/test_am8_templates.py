"""AM8 (B): a customer's form — the model maps it, the code checks the filled file.

The output audit filled 20 templates through the production filler; six were wrong and reported passed:
a mapped slot still showing {{missing_total}}, Thai text cut off inside a fixed-height row, a master's
marker left in place, a named placeholder and a named text box still holding "999", and a chart still
showing 999/888 under a new title. Each is built here again, the same way, and must now be either filled
right (supported) or refused before work starts / not passed with the slot named (never silent).
"""

from __future__ import annotations

import os
import tempfile
import unittest

from docx import Document
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.shared import Inches, Pt
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches as PI

from agent_market.deliverables import template_fill as tf
from agent_market.deliverables import template_out as to


def _doc():
    d = Document()
    d.styles["Normal"].font.name = "Sarabun"
    d.styles["Normal"].font.size = Pt(14)
    return d


def _deck():
    p = Presentation()
    p.slide_width, p.slide_height = PI(10), PI(5.625)
    sl = p.slides.add_slide(p.slide_layouts[5])
    sl.shapes.title.text = "{{period}}"
    return p, sl


def _run(obj, ext, values, mapping=None):
    tmp = tempfile.mkdtemp()
    src, dst = os.path.join(tmp, "before" + ext), os.path.join(tmp, "after" + ext)
    obj.save(src)
    sl = to.slots(tf.inspect(src))
    if mapping is None:
        mapping = {s["key"]: s["key"] for s in sl if s["fillable"]}
    rep = to.fill(src, dst, {"slots": sl, "mapping": mapping}, values)
    return sl, rep, dst


def _check(rep, name):
    return next(c for c in rep["checks"] if c["name"] == name)


class TheAuditsSilentCases(unittest.TestCase):
    def test_a_mapped_slot_with_no_value_is_not_passed_and_named(self):
        d = _doc()
        d.add_paragraph("{{company}}")
        d.add_paragraph("ยอดรวม {{missing_total}} บาท")
        _, rep, dst = _run(d, ".docx", {"company": "บริษัท ทดสอบ จำกัด"})
        self.assertFalse(rep["passed"])
        self.assertFalse(_check(rep, "template.every_slot")["ok"])
        self.assertFalse(_check(rep, "template.no_marker_left")["ok"])
        self.assertIn("missing_total", [u["slot"] for u in rep["unfilled"]])
        self.assertEqual(next(u for u in rep["unfilled"] if u["slot"] == "missing_total")["why"], "รอบนี้ไม่มีค่าให้ช่องนี้")
        self.assertIn("ยังไม่ได้ค่า 1 ช่อง", _check(rep, "template.every_slot")["detail"])

    def test_an_empty_value_is_no_value_not_a_blank_slot_passed_off(self):
        d = _doc()
        d.add_paragraph("{{company}} · {{total}}")
        _, rep, _ = _run(d, ".docx", {"company": "", "total": "1,234.50"})
        self.assertFalse(rep["passed"])
        self.assertEqual([(u["slot"], u["why"]) for u in rep["unfilled"]], [("company", "รอบนี้ไม่มีค่าให้ช่องนี้")])

    def test_thai_text_cut_off_in_a_fixed_height_row_is_not_passed(self):
        d = _doc()
        t = d.add_table(rows=1, cols=1)
        t.rows[0].height = Inches(.3)
        t.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        t.cell(0, 0).text = "{{company}}"
        _, rep, _ = _run(d, ".docx", {"company": "บริษัท ทดสอบบัญชีและตรวจสอบเอกสาร จำกัด " * 18})
        self.assertFalse(rep["passed"])
        self.assertFalse(_check(rep, "template.fits")["ok"])
        self.assertTrue(any("ตาราง 1 แถว 1" in u["label"] for u in rep["unfilled"]))

    def test_a_marker_on_the_master_is_filled(self):
        p, _ = _deck()
        p.slide_master.shapes[0].text = "{{company}}"
        sl, rep, dst = _run(p, ".pptx", {"period": "กันยายน 2569", "company": "NEW COMPANY"})
        self.assertIn("company", {s["key"] for s in sl if s["fillable"]})
        self.assertTrue(rep["passed"], rep["checks"])
        self.assertEqual(tf.markers_left(dst), [])
        self.assertEqual(Presentation(dst).slide_master.shapes[0].text, "NEW COMPANY")

    def test_a_named_placeholder_is_a_slot_and_is_filled(self):
        p = Presentation()
        sl_ = p.slides.add_slide(p.slide_layouts[1])
        sl_.shapes.title.text = "{{period}}"
        sl_.placeholders[1].name = "Revenue"
        sl_.placeholders[1].text = "OLD REVENUE: 999"
        sl, rep, dst = _run(p, ".pptx", {"period": "กันยายน 2569", "shape:Revenue": "1,234.50"},
                            {"period": "period", "shape:Revenue": "shape:Revenue"})
        self.assertIn("shape:Revenue", {s["key"] for s in sl})
        self.assertTrue(rep["passed"], rep["checks"])
        texts = [sh.text_frame.text for sh in Presentation(dst).slides[0].shapes if sh.has_text_frame]
        self.assertIn("1,234.50", texts)
        self.assertFalse([t for t in texts if "999" in t])

    def test_a_named_text_box_is_a_slot_and_an_unmapped_one_is_left_as_the_person_chose(self):
        p, s = _deck()
        box = s.shapes.add_textbox(PI(1), PI(2), PI(7), PI(1))
        box.name = "Revenue"
        box.text = "OLD REVENUE: 999"
        sl, rep, dst = _run(p, ".pptx", {"period": "กันยายน 2569", "shape:Revenue": "1,234.50"})
        self.assertTrue(rep["passed"], rep["checks"])
        self.assertIn("1,234.50", [sh.text_frame.text for sh in Presentation(dst).slides[0].shapes if sh.has_text_frame])
        # the shapes PowerPoint named itself ("Title 1") are not offered: their names say nothing
        self.assertFalse([x for x in sl if x["kind"] == "shape" and x["label"].startswith("Title")])

    def test_a_chart_the_filler_would_leave_with_old_numbers_is_refused_before_work_and_not_passed(self):
        p, s = _deck()
        data = CategoryChartData()
        data.categories = ["Revenue", "Cost"]
        data.add_series("THB", [999, 888])
        s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, PI(1), PI(1.5), PI(7), PI(3.5), data)
        sl, rep, _ = _run(p, ".pptx", {"period": "กันยายน 2569"})
        blocking = to.blocking(sl)
        self.assertEqual([b["reason"] for b in blocking], ["chart_not_updated"])
        self.assertFalse(rep["passed"])
        self.assertFalse(_check(rep, "template.no_static_chart")["ok"])

    def test_a_marker_where_the_filler_does_not_write_is_refused_before_work(self):
        # a marker inside a chart's own XML (its title): the filler never writes there
        p, s = _deck()
        data = CategoryChartData()
        data.categories = ["A"]
        data.add_series("S", [1])
        ch = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, PI(1), PI(1.5), PI(7), PI(3.5), data).chart
        ch.has_title = True
        ch.chart_title.text_frame.text = "{{period}}"
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "t.pptx")
        p.save(src)
        reasons = {b["reason"] for b in to.blocking(to.slots(tf.inspect(src)))}
        self.assertEqual(reasons, {"unsupported_location", "chart_not_updated"})


class WhatIsSupported(unittest.TestCase):
    def test_a_fully_filled_word_form_passes_after_being_read_back(self):
        d = _doc()
        d.add_paragraph("{{company}}")
        d.add_paragraph("ยอดรวม {{total}} บาท")
        _, rep, _ = _run(d, ".docx", {"company": "บริษัท ทดสอบ จำกัด", "total": "1,234.50"})
        self.assertTrue(rep["passed"], rep["checks"])
        self.assertEqual(rep["unfilled"], [])
        self.assertEqual(_check(rep, "template.every_slot")["values"], 2)


if __name__ == "__main__":
    unittest.main()
