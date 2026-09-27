"""A question card a stranger cannot answer is a defect (AM3).

The cards used to carry the engine's internal English ("VAT should be 700.00 at 7% of the base",
"same supplier and document number as …", "vision:qwen… read {'legal_form': …}", "row 2: 1530.00
printed") and raw field keys ("อ่านbank_accountได้"). Every card the E2 fixture pack and its held-out
set raise, and the setup cards, must now read as plain Thai: file names, document numbers, amounts
and a few printed abbreviations (VAT, PDF, XML, e-Tax, DPI) are the only Latin a person sees.
"""

import json
import os
import re
import unittest

from agent_market.engine.e2 import cards, extract
from agent_market.tests import e2kit

ENGLISH_LEAKS = re.compile(r"printed|computed|should be|same supplier|the document text|confidence|could not|"
                           r"vision:|\{'|legal_form|\bread\b|\bfailed\b|must show|[a-z]+_[a-z_]+", re.I)
ALLOWED = {"VAT", "PDF", "XML", "DPI", "Tax", "OCR"}


def _engine_words(text: str) -> list[str]:
    """What the engine itself wrote in a card, in Latin letters: the document label before the first ": "
    (the seller as printed, the number, the file) and quoted names, numbers and file names are the person's
    own data and are left out; what remains may only use the allowed abbreviations."""
    t = text.split(": ", 1)[1] if ": " in text else text
    t = re.sub(r"“[^”]*”", " ", t)
    t = re.sub(r"\S+\.(?:pdf|xml|jpg|jpeg|png|xlsx|csv)\b", " ", t, flags=re.I)
    t = re.sub(r"\S*\d\S*", " ", t)
    leaks = ENGLISH_LEAKS.findall(t)
    return leaks + [w for w in re.findall(r"[A-Za-z]{2,}", t) if w not in ALLOWED]


def _e2_cards():
    rows = []
    for folder in ("e2", "e2_holdout"):
        base = os.path.join(os.path.dirname(e2kit.E2), folder)
        org = e2kit._load(os.path.join(base, "org.json"))
        for fx in e2kit._load(os.path.join(base, "index.json"))["fixtures"]:
            exp = e2kit._load(os.path.join(base, "expected", fx["id"] + ".json"))
            hist = []
            for hid in exp.get("history") or []:
                h = e2kit._load(os.path.join(base, "expected", hid + ".json"))["truth"]
                hist.append({"name": hid, "seller_tax_id": (h["seller_tax_id"] or {}).get("value"),
                             "document_number": h["document_number"], "document_date": h["document_date"], "total": h["total"]})
            out = extract(os.path.join(base, fx["files"][0]), os.path.basename(fx["files"][0]), org=org, history=hist,
                          today=e2kit.TODAY)
            rows += [(fx["id"], f.to_dict()) for f in cards.findings_for("d" + fx["id"][:10], out)]
    return rows


class E2CardsReadAsThai(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = _e2_cards()

    def test_the_pack_raises_cards(self):
        self.assertGreater(len(self.rows), 20)

    def test_no_engine_english_in_a_card(self):
        bad = []
        for fid, f in self.rows:
            # the question after its document label, and every option description (an option's label may
            # quote a reading, which is the document's own data)
            for text in [f["question"]] + [": " + o["description"] for o in f["options"]]:
                words = _engine_words(text)
                if words:
                    bad.append((fid, f["code"], words, text))
        self.assertEqual(bad, [])

    def test_a_failed_check_says_what_it_found_in_numbers(self):
        vat = [f for fid, f in self.rows if fid == "f02_vat_mismatch" and f["code"] == "e2_check_failed"]
        self.assertTrue(vat)
        self.assertIn("7% ของยอดก่อน VAT คือ", vat[0]["question"])
        line = [f for fid, f in self.rows if fid == "f01_line_total_mismatch" and f["code"] == "e2_check_failed"]
        self.assertIn("รายการที่ 2 พิมพ์ 1,530.00", line[0]["question"])

    def test_every_field_a_card_names_has_a_thai_name(self):
        from agent_market.engine.e2.schema import FIELD_KINDS

        self.assertEqual(set(FIELD_KINDS) - set(cards.FIELD_TH), set())
        self.assertEqual(set(FIELD_KINDS) - set(cards.HEADER), set())

    def test_a_photo_card_names_the_file_not_an_ocr_guess_of_the_seller(self):
        p01 = [f for fid, f in self.rows if fid == "p01_photo_crumpled_thermal"]
        self.assertTrue(p01)
        for f in p01:
            self.assertTrue(f["question"].startswith("p01_photo_crumpled_thermal.jpg"), f["question"])


class SetupCardsReadAsThai(unittest.TestCase):
    def test_a_bad_policy_names_the_setting_not_its_key(self):
        from agent_market.packages.expense_audit.checks import cards as EC

        f = EC.no_policy(["categories.meals.per_claim: 'ประมาณ 2,000' ไม่ใช่จำนวนเงิน", "receipt_required_above: ยังไม่ได้กำหนด"])
        self.assertIn("หมวด meals · วงเงินต่อครั้ง", f.question)
        self.assertIn("ยอดที่ต้องมีใบเสร็จ: ยังไม่ได้กำหนด", f.question)
        self.assertNotIn("per_claim", f.question)

    def test_the_debit_credit_card_shows_a_row_of_the_persons_own_file(self):
        from agent_market.tests import evalkit

        case = next(c for c in evalkit.load_cases() if c["id"] == "sap_s_h_indicator")
        f = next(f for f in evalkit.run_case(case)["findings"] if f["code"] == "ambiguous_debit_credit")
        self.assertRegex(f["question"], r"ตัวอย่างจากไฟล์ของคุณ: แถว \d+ บัญชี \S+ เป็นบัญชีรายได้")

    def test_account_group_options_say_what_each_line_holds(self):
        from agent_market.packages.financial_report.checks.preflight import GROUP_HINT
        from agent_market.packages.financial_report.scripts.rules import GROUP_LABEL

        self.assertEqual(set(GROUP_LABEL) - set(GROUP_HINT), set())


if __name__ == "__main__":
    unittest.main()
