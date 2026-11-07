"""AM5 — a document that is left out is never left out quietly.

The day-one trap of AM4: the first question card a person meets ("VAT is not
7% of the base") recommended "ตัดเอกสารนี้ออก". Taking it left a real purchase
out of the month, and nothing afterwards said so.

  * The answer a card recommends is never the one that leaves a purchase out.
    (A card that needs the person to look at the document recommends nothing;
    what a skipped card records is still to leave the document out — a number
    is never accepted without a decision — and the card says that this is
    what skipping does.)
  * Every document left out is on a list the round keeps —
    "เอกสารที่ยังไม่ได้รวม" — in computed.json, in the round's summary and in
    the register itself, each with what bringing it back takes.
  * Bringing one back is one decision: recorded as printed with an exception
    for whoever reviews, or, when a value is needed first, its question again.

Runs inside the sandbox image.
"""

from __future__ import annotations

import os
import unittest

from agent_market.tests import samplekit as S

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "fixtures", "e2", "docs")
SHEET = "เอกสารที่ยังไม่ได้รวม"

CLEAN = ("d01_clean_full_tax_invoice.pdf", "ใบกำกับภาษี ก.ย.69 ร้านเครื่องเขียน.pdf")
VAT = ("f02_vat_mismatch.pdf", "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf")
NO_TAX_ID = ("f05_missing_seller_tax_id.pdf", "ใบกำกับภาษี ไม่มีเลขผู้เสียภาษี.pdf")
WRONG_BUYER = ("f04_wrong_buyer.pdf", "ใบกำกับภาษี ออกในชื่ออื่น.pdf")
LEAVES_OUT = ("exclude", "reupload")


def run(*docs, answers=None):
    files = [(os.path.join(DOCS, src), name) for src, name in docs]
    return S.run_files("invoice_processing", files, defaults=S.e2_fixture_defaults(),
                       formats=["xlsx"], answers=answers)


def entry(res, name):
    return next(e for e in res["computed"]["not_included"] if e["name"] == name)


class TheRecommendedAnswer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = sorted(n for n in os.listdir(DOCS) if n[:1] in "fsp")
        cls.res = run(*[(n, f"เอกสาร{i:02d}{os.path.splitext(n)[1]}") for i, n in enumerate(names)])
        cls.findings = cls.res["first_findings"]

    def test_the_vat_card_recommends_recording_the_purchase(self):
        f = next(f for f in self.findings if f["key"].endswith(":vat_7_percent"))
        self.assertEqual(f["recommended"], "accept_as_printed")
        self.assertEqual(f["options"][0]["value"], "accept_as_printed", "the recommended answer is the first one")

    def test_no_card_recommends_leaving_a_purchase_out(self):
        bad = [(f["key"], f["recommended"]) for f in self.findings
               if f.get("recommended") in LEAVES_OUT and f["code"] != "e2_duplicate"]
        self.assertEqual(bad, [])
        self.assertTrue(any(f.get("recommended") for f in self.findings))

    def test_a_card_whose_skip_leaves_the_document_out_says_so(self):
        for f in self.findings:
            self.assertEqual(bool(f.get("leaves_out")), f["default"] in LEAVES_OUT, f["key"])

    def test_a_card_that_needs_the_document_read_recommends_nothing(self):
        for f in self.findings:
            if f["code"] in ("e2_low_confidence", "e2_source_conflict", "e2_field_missing", "e2_doc_type", "e2_page_quality"):
                self.assertIsNone(f.get("recommended"), f["key"])
        wrong = next((f for f in self.findings if f["key"].endswith(":buyer_match")), None)
        if wrong is not None:
            self.assertIsNone(wrong.get("recommended"), "an invoice in another company's name may not be yours at all")


class TheListOfWhatWasLeftOut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run(CLEAN, VAT, NO_TAX_ID, CLEAN)  # every card skipped; the clean invoice sent twice

    def test_every_document_left_out_is_on_the_list(self):
        c = self.res["computed"]
        left_out = sorted(d["name"] for d in c["documents"] if d["status"] != "included")
        self.assertEqual(sorted(e["name"] for e in c["not_included"]), left_out)
        self.assertEqual(len(left_out), 3)
        self.assertEqual(c["totals"]["excluded"], 3)
        for e in c["not_included"]:
            self.assertTrue(e["reason"] and e["how"], e)
            self.assertRegex(e["reason"], r"[฀-๿]")

    def test_the_round_summary_carries_the_list(self):
        s = self.res["summary"]
        self.assertEqual([e["name"] for e in s["not_included"]], [e["name"] for e in self.res["computed"]["not_included"]])
        for e in s["not_included"]:
            self.assertEqual(set(e), {"doc_key", "name", "reason", "detail", "how", "can_restore"})

    def test_what_bringing_it_back_takes(self):
        vat = entry(self.res, VAT[1])
        self.assertTrue(vat["can_restore"])
        key = next(iter(vat["restore"]["set"]))
        self.assertTrue(key.startswith("e2_check_failed:") and key.endswith(":vat_7_percent"))
        self.assertEqual(vat["restore"]["set"][key]["value"], "accept_as_printed")
        self.assertEqual(vat["restore"]["drop"], [])
        # a document that needs a value first comes back as its question
        tax = entry(self.res, NO_TAX_ID[1])
        self.assertTrue(tax["can_restore"])
        self.assertEqual(tax["restore"]["set"], {})
        self.assertTrue(all(k.startswith("e2_field_missing:") for k in tax["restore"]["drop"]) and tax["restore"]["drop"])
        # the same file sent twice is already in the register: nothing to bring back
        twice = [e for e in self.res["computed"]["not_included"] if e["name"] == CLEAN[1]]
        self.assertEqual(len(twice), 1)
        self.assertFalse(twice[0]["can_restore"])

    def test_the_register_lists_them_on_their_own_sheet(self):
        from openpyxl import load_workbook

        wb = load_workbook(self.res["paths"]["xlsx"], read_only=True)
        self.assertIn(SHEET, wb.sheetnames)
        rows = [r for r in wb[SHEET].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
        self.assertEqual(sorted(r[0] for r in rows), sorted(e["name"] for e in self.res["computed"]["not_included"]))
        for r in rows:
            self.assertTrue(r[1] and r[3], r)
        summary = " ".join(str(v) for row in wb["สรุป"].iter_rows(values_only=True) for v in row if v is not None)
        self.assertIn("เอกสารที่ยังไม่ได้รวม", summary)
        self.assertTrue(self.res["render"]["passed"], self.res["render"])

    def test_a_round_with_nothing_left_out_says_so(self):
        from openpyxl import load_workbook

        res = run(CLEAN)
        self.assertEqual(res["computed"]["not_included"], [])
        self.assertEqual(res["summary"]["not_included"], [])
        wb = load_workbook(res["paths"]["xlsx"], read_only=True)
        self.assertIn(SHEET, wb.sheetnames)
        self.assertTrue(res["render"]["passed"], res["render"])


class BringingItBack(unittest.TestCase):
    def test_a_purchase_brought_back_is_recorded_with_an_exception(self):
        before = run(CLEAN, VAT)
        vat = entry(before, VAT[1])
        answers = dict(before["answers"])
        for k in vat["restore"]["drop"]:
            answers.pop(k, None)
        answers.update({k: v["value"] for k, v in vat["restore"]["set"].items()})
        after = run(CLEAN, VAT, answers=answers)
        c = after["computed"]
        self.assertEqual(c["not_included"], [])
        self.assertEqual(c["totals"]["included"], 2)
        self.assertGreater(float(c["totals"]["thb"]["total"]), float(before["computed"]["totals"]["thb"]["total"]))
        notes = [e for e in c["exceptions"] if e["name"] == VAT[1]]
        self.assertEqual([e["code"] for e in notes], ["accepted_as_printed"])
        self.assertEqual(notes[0]["severity"], "recorded")

    def test_a_document_that_needs_a_value_comes_back_as_its_question(self):
        before = run(CLEAN, NO_TAX_ID)
        tax = entry(before, NO_TAX_ID[1])
        answers = {k: v for k, v in before["answers"].items() if k not in tax["restore"]["drop"]}
        after = run(CLEAN, NO_TAX_ID, answers=answers)
        self.assertIn(tax["restore"]["drop"][0], [f["key"] for f in after["first_findings"]])


if __name__ == "__main__":
    unittest.main()
