"""Every figure a person can ask "why" about has its provenance in computed.json (AM4).

The runner answers "why is this figure what it is" only from these records — the label, the
formula in Thai, the parts it adds up (file, row or document, each part's own value) and what was
left out with the reason. A figure without a record gets "I don't know", never a model's guess, so
every figure on each package's summary must have one, and its parts must add up to it.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from agent_market.tests import samplekit as S


def _sum(parts: list[dict]) -> Decimal:
    return sum((Decimal(str(p["value"])) for p in parts if p.get("value") is not None), Decimal(0))


class Provenance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {p: S.render_sample(p, formats=["xlsx"])
                    for p in ("financial_report", "invoice_processing", "expense_audit", "bank_reconciliation")}

    def _cites(self, package: str) -> dict:
        cites = {c["id"]: c for c in self.runs[package]["computed"]["citations"]}
        for c in cites.values():
            self.assertTrue(c["label"] and c["formula"], c)
            self.assertNotRegex(c["formula"], r"^[A-Za-z_ ]+$", "the formula is written for a person, in Thai")
        return cites

    def test_every_line_of_the_financial_report_has_a_chain(self):
        comp = self.runs["financial_report"]["computed"]
        cites = self._cites("financial_report")
        for line in comp["lines"]:
            c = cites[line["cite"]]
            self.assertEqual(c["value"], line["current"], line["key"])
            # a line no row contributed to is 0.00 and says so by having no parts
            self.assertTrue(c.get("sources") or c.get("derived_from") or Decimal(c["value"]) == 0, line["key"])

    def test_the_invoice_summary_figures_add_up_from_their_documents(self):
        comp = self.runs["invoice_processing"]["computed"]
        thb = comp["totals"]["thb"]
        cites = self._cites("invoice_processing")
        for cid, field in (("i1", "subtotal"), ("i2", "vat_amount"), ("i3", "total"), ("i4", "wht_amount"),
                           ("i5", "net_payable"), ("i6", "payable")):
            self.assertEqual(cites[cid]["value"], thb[field], cid)
            self.assertEqual(_sum(cites[cid]["sources"]), Decimal(thb[field]), cid)
        self.assertEqual(cites["i7"]["value"], str(comp["totals"]["included"]))
        # the net payable counts only documents that print one — and each part says so
        notes = {p["note"] for p in cites["i5"]["sources"]}
        self.assertTrue(notes <= {"ตามที่เอกสารพิมพ์", "เอกสารไม่ได้พิมพ์ค่านี้ จึงไม่ได้นับ"}, notes)

    def test_the_reconciliation_figures_add_up_from_their_rows(self):
        comp = self.runs["bank_reconciliation"]["computed"]
        st = comp["statement"]
        cites = self._cites("bank_reconciliation")
        for n, side in ((1, "bank"), (2, "book")):
            balance, adjusted = cites[f"b{n}"], cites[f"a{n}"]
            self.assertEqual(balance["value"], st[side]["balance"])
            self.assertEqual(adjusted["value"], st[side]["adjusted"])
            # the balance is a cell of the file; the adjusted balance is that balance and every reconciling item
            self.assertTrue(balance["sources"] and balance["sources"][0]["file"] and balance["sources"][0]["row"], side)
            self.assertEqual(Decimal(balance["value"]) + _sum(adjusted["sources"]), Decimal(adjusted["value"]), side)
            self.assertTrue(all(p["file"] and p["row"] and p["note"] for p in adjusted["sources"]), side)
        self.assertEqual(cites["d1"]["value"], "0.00")
        self.assertEqual(Decimal(cites["a1"]["value"]) - Decimal(cites["a2"]["value"]), Decimal(cites["d1"]["value"]))

    def test_the_expense_summary_amounts_add_up_from_their_claims(self):
        comp = self.runs["expense_audit"]["computed"]
        t = comp["totals"]
        cites = self._cites("expense_audit")
        self.assertEqual(cites["e1"]["value"], t["claimed"])
        self.assertEqual(_sum(cites["e1"]["sources"]), Decimal(t["claimed"]))
        for cid, outcome in (("e2", "pass"), ("e3", "exception"), ("e4", "question")):
            self.assertEqual(_sum(cites[cid]["sources"]), Decimal(t["claimed_by_outcome"][outcome]), cid)


if __name__ == "__main__":
    unittest.main()
