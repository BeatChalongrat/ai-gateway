"""AM9 (A): a working paper is read on paper — a row's description beside its amounts.

AM8 printed the bank paper on 22 pages with the row's number repeated on every page across, but the
matched pairs' description printed on page 5 and their amounts on page 6: a person holding two sheets
had to pair them by number. The AM8 file itself is kept here (fixtures/print/am8_bank_paper.xlsx) and
must fail the print gate; the papers the agents print now must pass it, and every row they list for the
runner's print proof names itself in words next to its amounts.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from openpyxl import Workbook, load_workbook

from agent_market.deliverables import workpaper
from agent_market.tests import samplekit

HERE = os.path.dirname(os.path.abspath(__file__))
AM8_BANK = os.path.join(HERE, "fixtures", "print", "am8_bank_paper.xlsx")
PAPERS = {"bank_reconciliation": "รายการที่จับคู่", "po_matching": "ผลตรวจรายบรรทัด", "expense_audit": "รายการเบิก"}


class Lead(unittest.TestCase):
    def test_identity_and_amounts_move_first_and_the_columns_are_renumbered(self):
        heads = ["no", "tier", "date", "description", "amount", "evidence"]
        rows = [[1, "exact", "1 ก.ย.", "ค่าเช่า", -45000, "GL แถว 8"]]
        h, r, w, cols = workpaper.lead(heads, rows, [7, 18, 14, 36, 16, 40], ["no", "description", "amount"],
                                       money_cols=(5,), wrap_cols=(4, 6))
        self.assertEqual(h, ["no", "description", "amount", "tier", "date", "evidence"])
        self.assertEqual(r, [[1, "ค่าเช่า", -45000, "exact", "1 ก.ย.", "GL แถว 8"]])
        self.assertEqual(w, [7, 36, 16, 18, 14, 40])
        self.assertEqual(cols, {"money_cols": (3,), "wrap_cols": (2, 6)})

    def test_a_column_that_is_not_there_is_an_error_not_a_silent_reorder(self):
        with self.assertRaises(ValueError):
            workpaper.lead(["a"], [], None, ["b"])

    def test_amounts_print_as_the_money_format_does(self):
        self.assertEqual([workpaper.money_text(v) for v in (-45000, 0, 1234.5, 0.005, -0.004)],
                         ["(45,000.00)", "-", "1,234.50", "0.01", "-"])


class TheAM8Paper(unittest.TestCase):
    def test_the_am8_bank_paper_fails_the_print_gate_for_its_number_only_identity(self):
        res = workpaper.check(AM8_BANK)
        self.assertFalse(res["ok"])
        self.assertIn("มีแค่เลขลำดับ", res["detail"])

    def test_a_wide_sheet_whose_amounts_print_without_its_identity_fails(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "face"
        t = wb.create_sheet("pairs")
        t.append(["no"] + [f"c{i}" for i in range(14)] + ["amount"])
        for n in range(3):
            row = t.append([n + 1] + ["x" * 30] * 14 + [1000.0 * (n + 1)])
        for c in range(1, 17):
            t.column_dimensions[t.cell(row=1, column=c).column_letter].width = 30
        for r in range(2, 5):
            t.cell(row=r, column=16).number_format = '#,##0.00;[Red](#,##0.00);"-"'
        t.freeze_panes = "A2"
        path = os.path.join(tempfile.mkdtemp(), "wide.xlsx")
        wb.save(path)
        wb = load_workbook(path)
        workpaper.setup_workbook(wb, doc_name="wide.xlsx")
        wb["pairs"]._print_cols = None    # what a writer that repeats nothing would leave
        wb.save(path)
        self.assertIn("ไม่ได้กำหนดคอลัมน์ระบุรายการ", workpaper.check(path)["detail"])


class TheAgentsPapers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = {}
        for pkg in PAPERS:
            res = samplekit.render_sample(pkg, out_dir=tempfile.mkdtemp(), formats=["xlsx"])
            cls.out[pkg] = res["paths"]["xlsx"]

    def test_every_paper_passes_the_print_gate(self):
        for pkg, path in self.out.items():
            with self.subTest(pkg):
                res = workpaper.check(path)
                self.assertTrue(res["ok"], res["detail"])

    def test_on_every_page_across_that_prints_an_amount_the_rows_description_prints_too(self):
        for pkg, path in self.out.items():
            wb = load_workbook(path)
            for ws in wb.worksheets[1:]:
                ident = workpaper.title_cols(ws)
                with self.subTest(pkg=pkg, sheet=ws.title):
                    self.assertTrue(ident and ident[0] == 1, ident)
                    for band in workpaper.bands(ws):
                        self.assertTrue(set(ident) <= set(band), (band, ident))

    def test_the_wide_detail_sheet_leads_with_number_and_description(self):
        # AM10: the detail sheet no longer runs across pages (test_am10_print); its identity still leads and names the row
        for pkg, sheet in PAPERS.items():
            ws = load_workbook(self.out[pkg])[sheet]
            with self.subTest(pkg):
                ident = workpaper.title_cols(ws)
                self.assertGreaterEqual(len(ident), 2)
                worded = [ws.cell(row=5, column=c).value for c in ident if isinstance(ws.cell(row=5, column=c).value, str)]
                self.assertTrue(any(len(w) > 3 for w in worded), worded)

    def test_the_print_proof_gets_every_row_with_an_amount_named_in_words(self):
        for pkg, path in self.out.items():
            rows = workpaper.print_rows(path)
            with self.subTest(pkg):
                detail = [r for r in rows if r["sheet"] == PAPERS[pkg]]
                self.assertTrue(detail)
                self.assertTrue(all(r["amounts"] and any(len(t) > 3 and not t.isdigit() for t in r["identity"]) for r in detail),
                                detail[:2])


if __name__ == "__main__":
    unittest.main()
