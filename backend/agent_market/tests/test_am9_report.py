"""AM9 (B, C): the Word report people see first — its label column, and an appendix that keeps every
range without taking half the report.

B: every number column took 2.15 cm and the table's grid was never set, so a fixed-layout table gave each
column an equal share and "อัตรากำไรขั้นต้น" broke over three lines. C: AM8 listed every source range in
full (right) as a 5-column table (21 pages); the appendix is now a two-column section of entries.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from docx import Document
from docx.oxml.ns import qn

from agent_market.deliverables import content
from agent_market.deliverables.fit import SAFETY, text_width_pt
from agent_market.deliverables.render import render_all
from agent_market.tests.test_deliverables import REQ, computed_for

EMU_PER_PT = 12700
CELL_PAD_PT = 9.0


class TheReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])
        out = tempfile.mkdtemp()
        render_all(cls.computed, {**REQ, "formats": ["docx"]}, in_dir=out, out_dir=out)
        cls.doc = Document(os.path.join(out, REQ["names"]["docx"]))
        cls.font = cls.doc.styles["Normal"].font.name

    def _pl(self):
        return next(t for t in self.doc.tables if t.rows[0].cells[0].text.strip() == "รายการ"
                    and any("อัตรากำไรขั้นต้น" in r.cells[0].text for r in t.rows))

    def test_the_label_column_holds_its_names_on_one_line_where_the_page_has_room(self):
        t = self._pl()
        grid = [int(g.get(qn("w:w"))) for g in t._tbl.tblGrid.findall(qn("w:gridCol"))]   # twips
        self.assertGreater(len(set(grid)), 2, f"the grid is not an equal share per column: {grid}")
        need_pt = text_width_pt("อัตรากำไรขั้นต้น", self.font, 9.5, False) * SAFETY + CELL_PAD_PT
        self.assertGreaterEqual(grid[0] / 20, need_pt, (grid, need_pt))
        self.assertEqual(max(range(len(grid)), key=lambda j: grid[j]), 0, f"the label column is the widest: {grid}")
        cell = next(r.cells[0] for r in t.rows if "อัตรากำไรขั้นต้น" in r.cells[0].text)
        self.assertNotIn("\n", cell.text.strip())

    def test_the_appendix_is_two_columns_and_lists_every_citation_with_every_range(self):
        body = self.doc.element.body
        sects = body.findall(f".//{qn('w:sectPr')}")
        last = sects[-1]
        cols = last.find(qn("w:cols"))
        self.assertIsNotNone(cols)
        self.assertEqual(cols.get(qn("w:num")), "2")
        text = "\n".join(p.text for p in self.doc.paragraphs)
        model = content.build(self.computed, REQ)
        rows = [r for s in model["sections"] if s["key"] == "citations" for b in s["blocks"] for r in b["rows"]]
        self.assertEqual(len(rows), len(self.computed["citations"]))
        for r in rows:
            with self.subTest(r[0]):
                self.assertIn(r[0], text)
                self.assertIn(r[3], text)             # the whole range, as the model lists it
        self.assertNotIn("และอีก", text)

    def test_the_body_text_size_is_unchanged(self):
        # AM21: a run sets no size of its own; the body's is its style's ("AM Body", based on Normal)
        from agent_market.tests.test_deliverables import effective_font

        sizes = {effective_font(r, p)[0] for p in self.doc.paragraphs if p.style.name in ("Normal", "AM Body") for r in p.runs
                 if len(r.text) > 40}
        self.assertIn(10.5, sizes)


if __name__ == "__main__":
    unittest.main()
