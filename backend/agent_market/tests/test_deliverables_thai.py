"""AM4 — the files a person downloads say things in Thai, not in the engine's codes.

A scan of every package's sample deliverables (the AM4 walkthrough found an
English exception in the invoice register, test_register_thai.py) found four
more places where a person reads an engine identifier instead of Thai:

* the financial workbook's รายบัญชี sheet, column หมวด: "revenue",
  "operating_expenses" … (the line each account adds up to);
* the executive deck's "รายการที่ต้องตรวจ" slide: "[budget_variance_high]
  ค่าที่ปรึกษา 480,000 สูงกว่างบ 3.2 เท่า …";
* the expense workbook's ผลตรวจแต่ละกฎ sheet, column อ้างอิงนโยบาย:
  "categories.meals.per_claim";
* its นโยบายที่ใช้ sheet, the policy document's "หมวดที่กล่าวถึง": category keys.

Columns headed as codes (รหัส, รหัสกฎ, รหัสเหตุผล — for whoever maintains the
agent) keep their codes; everything else is Thai.
"""

from __future__ import annotations

import os
import re
import unittest
import zipfile

from agent_market.tests import samplekit as S

CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


def _sample(package: str, formats: list[str]) -> dict:
    st = S.self_test(package)
    files = [(os.path.join(S.package_dir(package), rel), os.path.basename(rel)) for rel in st["files"]]
    return S.run_files(package, files, defaults=S.self_test_defaults(package), formats=formats, message=st.get("message", ""))


def _column(ws, header: str) -> list:
    rows = list(ws.iter_rows(values_only=True))
    for i, r in enumerate(rows):
        if r and header in r:
            j = r.index(header)
            return [x[j] for x in rows[i + 1:] if x and len(x) > j and x[j] is not None]
    return []


class TheFinancialFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _sample("financial_report", ["xlsx", "pptx"])

    def test_the_account_group_is_named_in_thai(self):
        from openpyxl import load_workbook

        ws = load_workbook(self.res["paths"]["xlsx"], read_only=True)["รายบัญชี"]
        groups = _column(ws, "หมวด")
        self.assertTrue(groups)
        self.assertEqual(sorted({g for g in groups if CODE.match(str(g))}), [])
        self.assertIn("รายได้", " ".join(map(str, groups)))

    def test_the_deck_does_not_show_an_exception_code(self):
        self.assertTrue(self.res["computed"]["exceptions"], "the sample has an item to check")
        z = zipfile.ZipFile(self.res["paths"]["pptx"])
        text = " ".join(re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "ignore"))
                        for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n))
        codes = {e["code"] for e in self.res["computed"]["exceptions"]}
        self.assertEqual(sorted(c for c in codes if c in text), [])


class TheExpenseFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _sample("expense_audit", ["xlsx"])

    def test_the_policy_reference_is_said_in_thai(self):
        from openpyxl import load_workbook

        ws = load_workbook(self.res["paths"]["xlsx"], read_only=True)["ผลตรวจแต่ละกฎ"]
        refs = _column(ws, "อ้างอิงนโยบาย")
        self.assertTrue(refs)
        self.assertEqual(sorted({r for r in refs if CODE.match(str(r))}), [])

    def test_the_exceptions_policy_reference_is_said_in_thai(self):
        from openpyxl import load_workbook

        ws = load_workbook(self.res["paths"]["xlsx"], read_only=True)["ข้อยกเว้น"]
        refs = _column(ws, "อ้างอิงนโยบาย")
        self.assertEqual(sorted({r for r in refs if CODE.match(str(r))}), [])

    def test_categories_mentioned_by_the_policy_document_are_named(self):
        from openpyxl import load_workbook

        ws = load_workbook(self.res["paths"]["xlsx"], read_only=True)["นโยบายที่ใช้"]
        mentioned = _column(ws, "หมวดที่กล่าวถึง")
        codes = [m for m in mentioned if any(CODE.match(x.strip()) for x in str(m).split(","))]
        self.assertEqual(codes, [])


if __name__ == "__main__":
    unittest.main()
