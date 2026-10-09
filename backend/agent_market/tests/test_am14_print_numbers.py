"""AM14: the rows the print proof is given state a number as a number.

``workpaper.print_rows`` lists, for every row with an amount, what the runner looks for on the printed
page. It named a summary row by its own amount as well — the cell's value as Python writes it, ``3600`` —
and the page prints ``3,600.00``: an invoice-to-PO run with anything received and not billed failed its
print proof and delivered nothing. A row's name is its words; its amounts are stated as numbers
(``values``), at the two decimals the working papers print, and the runner reads the page's numbers to
find them (services/chat-edge-api … runner/printnumbers.go, with a fixture for every printed shape).
"""
import os
import tempfile
import unittest
from decimal import Decimal

from openpyxl import Workbook

from agent_market.deliverables import workpaper

MONEY = '#,##0.00;[Red](#,##0.00);"-"'
QTY = '#,##0.######;[Red](#,##0.######);"-"'


def _paper(path: str) -> None:
    """The shape of the paper that failed: a summary above the table, label and amount in the columns
    that repeat on every page, then the table's rows."""
    wb = Workbook()
    wb.active.title = "สรุป"
    ws = wb.create_sheet("ค้างระหว่างรับของกับเรียกเก็บ")
    ws["A1"] = "รับของแล้วยังไม่ถูกเรียกเก็บ และเรียกเก็บแล้วยังไม่ได้รับของ"
    ws["A4"], ws["B4"] = "รับของแล้วยังไม่ถูกเรียกเก็บ (บาท)", 3600
    ws["A5"], ws["B5"] = "เรียกเก็บแล้วยังไม่ได้รับของ (บาท)", 0
    ws["A6"], ws["B6"] = "ปรับปรุงยอดยกมา (บาท)", Decimal("-1234.505")
    ws.append([])
    ws.append(["PO", "บรรทัด", "รายการ", "หน่วย", "ราคาต่อหน่วยใน PO", "รับจริงสะสม", "มูลค่า"])
    ws.append(["PO6909-008", "10", "ปากกาลูกลื่น สีน้ำเงิน", "กล่อง", 360, 1500, 3852.004999])
    for ref in ("B4", "B5", "B6", "E9", "G9"):
        ws[ref].number_format = MONEY
    ws["F9"].number_format = QTY
    ws.print_title_cols = "A:C"
    wb.save(path)


class ARowIsNamedByItsWordsAndItsAmountsAreNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.dir.name, "paper.xlsx")
        _paper(cls.path)
        cls.rows = {r["row"]: r for r in workpaper.print_rows(cls.path) if r["sheet"] != "สรุป"}

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_a_summary_row_is_not_named_by_its_own_amount(self):
        # the live failure: identity was [label, "3600"], and no page prints 3600
        self.assertEqual(self.rows[4]["identity"], ["รับของแล้วยังไม่ถูกเรียกเก็บ (บาท)"])
        self.assertEqual(self.rows[6]["identity"], ["ปรับปรุงยอดยกมา (บาท)"])
        for r in self.rows.values():
            for word in r["identity"]:
                self.assertNotIn(word, {"3600", "0", "-1234.505", "3852.004999"}, r)

    def test_an_amount_is_stated_as_a_number_at_the_decimals_it_prints_with(self):
        self.assertEqual(self.rows[4]["values"], ["3600.00"])
        self.assertEqual(self.rows[6]["values"], ["-1234.51"])     # a half rounds away from zero, as the format prints it
        self.assertEqual(self.rows[9]["values"], ["360.00", "3852.00"])
        # the printed form is still said, for a runner older than this engine and for a person reading a row
        self.assertEqual(self.rows[4]["amounts"], ["3,600.00"])
        self.assertEqual(self.rows[6]["amounts"], ["(1,234.51)"])

    def test_a_zero_prints_as_a_dash_and_is_not_a_number_to_find(self):
        self.assertEqual(self.rows[5]["amounts"], ["-"])
        self.assertEqual(self.rows[5]["values"], [])
        self.assertEqual(self.rows[5]["identity"], ["เรียกเก็บแล้วยังไม่ได้รับของ (บาท)"])

    def test_a_row_of_the_table_keeps_its_name(self):
        # a quantity is not an amount of money: it is neither among the values nor taken out of the name
        self.assertEqual(self.rows[9]["identity"], ["PO6909-008", "10", "ปากกาลูกลื่น สีน้ำเงิน"])
        self.assertEqual(self.rows[9]["amounts"], ["360.00", "3,852.00"])

    def test_every_value_is_a_plain_decimal(self):
        for r in self.rows.values():
            for v in r["values"]:
                self.assertRegex(v, r"^-?\d+\.\d\d$", r)
                self.assertNotEqual(Decimal(v), 0, r)


if __name__ == "__main__":
    unittest.main()
