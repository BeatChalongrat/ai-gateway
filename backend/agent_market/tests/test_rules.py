"""Rules on ledger rows (AM1b: duplicates the fixtures found)."""

import datetime as dt
import unittest
from decimal import Decimal as D

from agent_market.engine.ledger import LedgerRow
from agent_market.packages.financial_report.scripts.rules import duplicate_docs


def row(n, doc, acct, dr, cr, desc="ค่าโฆษณา - บจก. มีเดียพลัส"):
    return LedgerRow(file="gl.csv", sheet="gl", row=n, date=dt.date(2026, 9, 14), account_code=acct, account_name="",
                     description=desc, debit=D(dr), credit=D(cr), currency="THB", cost_center="", doc_no=doc,
                     amount_cols="G:H", raw_indicator="")


def filler(start):
    return [row(start + i, f"IV256909-{i:04d}", "4100-01", "0", str(1000 + i), "ขายเชื่อ") for i in range(20)]


class Duplicates(unittest.TestCase):
    def test_a_document_exported_twice_under_the_same_number(self):
        doc = [("5500-01", "29444.50", "0"), ("1154-00", "2061.12", "0"), ("2155-02", "0", "883.34"), ("2111-01", "0", "30622.28")]
        rows = filler(100) + [row(10 + i, "PV256909-0042", a, dr, cr) for i, (a, dr, cr) in enumerate(doc)] \
            + [row(14 + i, "PV256909-0042", a, dr, cr) for i, (a, dr, cr) in enumerate(doc)]
        groups = duplicate_docs(rows)
        self.assertEqual(len(groups), 1, "the second copy of PV256909-0042 must be found")
        self.assertEqual(groups[0]["rows"], [14, 15, 16, 17], "only the copy is dropped, the original stays")
        self.assertEqual(groups[0]["original_rows"], [10, 11, 12, 13])

    def test_a_document_with_one_line_repeated_is_not_a_duplicate(self):
        rows = filler(100) + [row(10, "PV256909-0050", "5400-05", "100.00", "0"), row(11, "PV256909-0050", "5400-05", "100.00", "0"),
                              row(12, "PV256909-0050", "2111-01", "0", "200.00")]
        self.assertEqual(duplicate_docs(rows), [])


if __name__ == "__main__":
    unittest.main()
