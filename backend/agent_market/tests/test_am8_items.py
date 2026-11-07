"""AM8 (D): an item's confidence tells the truth.

The audit: the invoice ใบกำกับภาษี_IV6909-0015.pdf prints "แฟ้มเอกสาร 3 นิ้ว สีน้ำเงิน"; the register said
"3 นิ้ว สีน้ำเงิน", with confidence 1 and the status "ยืนยันแล้ว". The first word was taken for part of the
code column, the item's confidence never looked at its name, and a threshold wrote a person's word.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from agent_market.engine.e2 import cards, decisions, parse
from agent_market.engine.e2.words import Word, build_lines
from agent_market.tests.e2kit import _norm_core

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(os.path.dirname(HERE), "packages", "invoice_processing", "sample-data")


def _row(y, *cells):
    """Words of one table row: (text, x0, x1)."""
    return [Word(text=t, x0=a, y0=y, x1=b, y1=y + 10, page=1, size=10) for t, a, b in cells]


HEADER = _row(100, ("ลำดับ", 40, 60), ("รหัส", 90, 110), ("รายการ", 210, 250), ("จำนวน", 330, 360),
              ("ราคาต่อหน่วย", 400, 460), ("จำนวนเงิน", 500, 550))


class TheSampleInvoice(unittest.TestCase):
    def test_the_item_name_is_read_whole_and_no_machine_reading_says_confirmed(self):
        from agent_market.packages.invoice_processing.scripts.workbook import InvoiceWorkbook, check, item_status
        from agent_market.deliverables.theme import resolve
        from agent_market.tests.samplekit import render_sample

        res = render_sample("invoice_processing")
        computed = res["computed"]
        names = [l["description"] for l in computed["line_items"] if l["name"] == "ใบกำกับภาษี_IV6909-0015.pdf"]
        self.assertTrue(any(_norm_core(n) == _norm_core("แฟ้มเอกสาร 3 นิ้ว สีน้ำเงิน") for n in names), names)
        statuses = {item_status(l) for l in computed["line_items"]}
        self.assertNotIn("ยืนยันแล้ว", statuses)
        self.assertFalse([s for s in statuses if s.startswith("ยืนยัน")])
        out = os.path.join(tempfile.mkdtemp(), "r.xlsx")
        InvoiceWorkbook(computed, resolve(None)).write(out)
        from openpyxl import load_workbook

        cells = [c for row in load_workbook(out)["รายการสินค้า"].iter_rows(min_row=5, values_only=True) for c in row]
        self.assertNotIn("ยืนยันแล้ว", cells)
        self.assertTrue(all(c["ok"] for c in check(out, computed)), [c for c in check(out, computed) if not c["ok"]])


class ARowThatShowsMoreThanTheItemCarries(unittest.TestCase):
    def _items(self, row):
        items, _ = parse.line_items(build_lines(HEADER + row))
        return items

    def test_a_row_with_no_code_keeps_the_first_word_of_its_name(self):
        items = self._items(_row(120, ("1", 48, 52), ("ค่าขนส่งสินค้า", 140, 200), ("กรุงเทพฯ", 205, 245),
                                 ("3", 342, 348), ("4,500.00", 420, 455), ("13,500.00", 510, 550)))
        self.assertEqual(items[0]["description"].value, "ค่าขนส่งสินค้า กรุงเทพฯ")

    def test_words_no_column_took_lower_the_confidence_and_ask(self):
        # words printed in the row that no column takes (here between the price and the amount): the item
        # may not carry everything the row says
        items = self._items(_row(120, ("1", 48, 52), ("FD-3B", 90, 112), ("แฟ้มเอกสาร", 140, 190), ("30", 342, 348),
                                 ("65.00", 440, 460), ("สีน้ำเงิน", 470, 495), ("1,950.00", 515, 550)))
        it = items[0]
        self.assertEqual(it["description"].value, "แฟ้มเอกสาร")
        self.assertEqual(it.get("left_out"), ["สีน้ำเงิน"])
        self.assertLess(it["description"].confidence, 0.95)
        self.assertIn("text_left_out", it["description"].checks)

    def test_the_question_becomes_a_card_and_the_answer_names_the_item(self):
        from agent_market.engine.e2.extract import _questions

        items = self._items(_row(120, ("1", 48, 52), ("FD-3B", 90, 112), ("แฟ้มเอกสาร", 140, 190), ("30", 342, 348),
                                 ("65.00", 440, 460), ("สีน้ำเงิน", 470, 495), ("1,950.00", 515, 550)))
        qs = _questions({}, items, [], [], "full_tax_invoice", False)
        q = next(q for q in qs if q.code == "item_text")
        self.assertEqual(q.subject, "line1")
        doc = {"document": {"name": "a.pdf"}, "questions": [q.to_dict()], "fields": {}}
        card = next(f for f in cards.findings_for("d1", doc) if f.code == "e2_item_text")
        self.assertEqual(card.default, "unconfirmed")
        self.assertIn("สีน้ำเงิน", card.question)
        full = card.options[0].value["accept"]
        d = decisions.for_document("d1", {"e2_item_text:d1:line1": {"accept": full}})
        self.assertEqual(d.item_names, {1: full})
        self.assertIn(1, d.judge_kwargs({})["item_names"])
        typed = decisions.for_document("d1", {"e2_item_text:d1:line1": {"typed": "แฟ้มเอกสาร 3 นิ้ว สีน้ำเงิน"}})
        self.assertEqual(typed.item_names[1], "แฟ้มเอกสาร 3 นิ้ว สีน้ำเงิน")
        skipped = decisions.for_document("d1", {"e2_item_text:d1:line1": "unconfirmed"})
        self.assertEqual(skipped.item_names, {})
        self.assertEqual(skipped.excluded, None)  # a name left unconfirmed never leaves the document out


if __name__ == "__main__":
    unittest.main()
