"""AM4 — the invoice register a person downloads is Thai, its exceptions included.

The AM4 walkthrough on the test stack answered a VAT card with its default
("ตัดเอกสารนี้ออก") and the register's ข้อยกเว้น sheet said, for that
document: "VAT should be 700.00 at 7% of the base" — the engine's own English
check message, copied as the exclusion's detail (the card the person saw was
Thai). The same English went into computed.json, which the run page's
exceptions tab shows. The detail is now the card's Thai question.
"""

from __future__ import annotations

import os
import re
import unittest

from agent_market.tests import samplekit as S

HERE = os.path.dirname(os.path.abspath(__file__))
E2_DOCS = os.path.join(os.path.dirname(HERE), "fixtures", "e2", "docs")
# what may stay in Latin letters: proper nouns and codes a Thai accountant reads as such
ALLOWED = re.compile(r"VAT|e-Tax|PDF|XML|USD|THB|DPI|qwen[\w.-]*|\.(pdf|xml|jpg|png|xlsx)\b")


def _english(text) -> list[str]:
    # a detail starts with the document's own label — seller, number, file ("Cloudnine Software Pte. Ltd.
    # เลขที่ CN-2026-00917 (เอกสาร17.pdf): …"): what the document prints is data, not the register's words
    words = re.sub(r"^.*?\(เอกสาร\d+\.\w+\):\s*", "", str(text or ""), count=1)
    return re.findall(r"[A-Za-z]{3,}", ALLOWED.sub("", words))


class TheRegisterIsThai(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = sorted(n for n in os.listdir(E2_DOCS) if n[:1] in "def" or n.startswith("s01"))
        # named the way a person names files (Thai), so a file name is never the English found
        files = [(os.path.join(E2_DOCS, n), f"เอกสาร{i:02d}{os.path.splitext(n)[1]}") for i, n in enumerate(names)]
        cls.res = S.run_files("invoice_processing", files, defaults=S.self_test_defaults("invoice_processing"),
                              formats=["xlsx"])

    def test_every_exception_detail_is_thai(self):
        ex = self.res["computed"]["exceptions"]
        self.assertTrue(any(e["severity"] == "excluded" for e in ex), "the pack has documents a card leaves out")
        bad = [(e["name"], e["code"], e.get("detail")) for e in ex if _english(e.get("detail"))]
        self.assertEqual(bad, [])

    def test_the_downloaded_register_has_no_english_exception(self):
        from openpyxl import load_workbook

        wb = load_workbook(self.res["paths"]["xlsx"], read_only=True)
        rows = [r for r in wb["ข้อยกเว้น"].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
        self.assertTrue(rows)
        bad = [(r[0], r[4]) for r in rows if _english(r[4])]
        self.assertEqual(bad, [])

    def test_how_each_value_was_read_is_said_in_thai(self):
        # the walkthrough's register said "document_scan" in the ที่มา sheet's อ่านด้วย column (the currency)
        from openpyxl import load_workbook

        wb = load_workbook(self.res["paths"]["xlsx"], read_only=True)
        rows = [r for r in wb["ที่มา"].iter_rows(min_row=5, values_only=True) if r and r[0] is not None]
        self.assertTrue(rows)
        bad = sorted({r[4] for r in rows if _english(r[4])})
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
