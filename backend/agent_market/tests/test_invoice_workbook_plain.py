"""AM3 — the invoice register reads right to a stranger (found by the stranger walkthrough).

The summary said "ยอดชำระสุทธิ 0" when no document printed a net payable (the person had to ask the
agent why); the checks sheet named each check by its failure card's header ("VAT ไม่ตรง" beside
"ผ่าน") and showed the engine's English ("duplicate", "no withholding tax on this document") that read
like problems with the documents. Now the summary shows the amount to pay (printed net payable, else
total − withholding), each check is named for what it looks at, and its notes are Thai.
"""

from __future__ import annotations

import re
import unittest
from decimal import Decimal

from openpyxl import load_workbook

from agent_market.tests import samplekit as S


class RegisterReadsRight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = S.render_sample("invoice_processing", formats=["xlsx"])
        cls.wb = load_workbook(cls.res["paths"]["xlsx"])
        cls.thb = cls.res["computed"]["totals"]["thb"]

    def test_the_summary_shows_what_is_to_be_paid(self):
        rows = {r[0]: r[1] for r in self.wb["สรุป"].iter_rows(min_row=5, values_only=True) if r and r[0]}
        self.assertIn("ยอดที่ต้องชำระ (บาท)", rows)
        want = Decimal(self.thb["total"]) - Decimal(self.thb["wht_amount"] or "0")
        self.assertGreater(want, 0)
        self.assertEqual(Decimal(str(rows["ยอดที่ต้องชำระ (บาท)"])).quantize(Decimal("0.01")), want)
        # every recorded document has its amount to pay in the register
        heads = [c.value for c in self.wb["ทะเบียน"][4]]
        col = heads.index("ยอดที่ต้องชำระ")
        for r in self.wb["ทะเบียน"].iter_rows(min_row=5, values_only=True):
            if r and r[0] is not None:
                self.assertIsNotNone(r[col], r)

    def test_checks_are_named_for_what_they_look_at_in_thai(self):
        ws = self.wb["การตรวจ"]
        heads = [c.value for c in ws[4]]
        self.assertEqual(heads[:6], ["ไฟล์", "ตรวจอะไร", "ผล", "ควรเป็น", "ที่พบ", "หมายเหตุ"])
        english = re.compile(r"[A-Za-z]{4,}")
        rows = [r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[0]]
        self.assertTrue(rows)
        for r in rows:
            self.assertNotIn(r[1], ("VAT ไม่ตรง", "ผู้ซื้อไม่ตรง", "เลขภาษีผิด", "ยอดรวม"), r)
            for cell in r[1:6]:
                if cell and not str(cell).replace(".", "").replace(",", "").replace("-", "").isdigit():
                    self.assertFalse(english.search(str(cell).replace("VAT", "")), r)

    def test_the_source_sheet_names_checks_in_thai(self):
        ws = self.wb["ที่มา"]
        heads = [c.value for c in ws[4]]
        col = heads.index("ผ่านการตรวจ")
        for r in ws.iter_rows(min_row=5, values_only=True):
            if r and r[0] and r[col]:
                self.assertFalse(re.search(r"[a-z_]{4,}", str(r[col])), r)


class EveryCheckOfTheEvalSetReadsInThai(unittest.TestCase):
    """Every check the eval set's documents produce (clean, failing, scanned, photographed, foreign
    currency, duplicates) is named and explained in Thai on the checks sheet."""

    maxDiff = None

    def test_no_engine_english_reaches_the_sheet(self):
        from agent_market.packages.invoice_processing.scripts.workbook import check_name, msg_th
        from agent_market.tests import invoicekit as K

        english = re.compile(r"[A-Za-z]{4,}")
        leaks = set()
        for case in K.load_cases()["cases"]:
            for c in K.run_case(case)["computed"]["checks"]:
                for v in (check_name(c["code"]), msg_th(c.get("expected")), msg_th(c.get("actual")), msg_th(c.get("message"))):
                    words = re.sub(r"\S*[_.0-9]\S*", "", str(v or ""))  # file names, model names and numbers are identifiers
                    if v and english.search(words.replace("VAT", "").replace("e-Tax", "")):
                        leaks.add(str(v)[:120])
        self.assertEqual(sorted(leaks), [])


if __name__ == "__main__":
    unittest.main()
