"""The documents' own words, in both languages (AM18).

AM14 moved what an engine says on a screen into the manifests; the files it writes stayed Thai. This card
moves the documents' own words — sheet names, headings, labels, notes — the same way, one package at a
time, and holds what a document in two languages must keep: the same figures to the cell (test_am14_words,
``_the_paper_is_the_thai_papers_twin``), names that fit, and what one run writes another run can read back
in either language.
"""

import os
import re
import tempfile
import unittest

from openpyxl import Workbook

from agent_market.engine import words
from agent_market.tests import bankkit as K
from agent_market.tests.test_package_words_ratchet import DOCUMENTS_DONE

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXCEL_SHEET = re.compile(r"[/\\?*\[\]:]")


class TheDocumentsWordsFit(unittest.TestCase):
    def test_every_sheet_name_fits_excel_in_both_languages(self):
        for pkg in DOCUMENTS_DONE:
            w = words.of(f"agent_market.packages.{pkg}")
            keys = [k for k in w.strings if k.startswith("doc.sheet.")]
            self.assertGreater(len(keys), 3, pkg)
            for k in keys:
                for text in (w.strings[k], w.english_of(k)):
                    self.assertLessEqual(len(text), 31, f"{pkg} {k}: {text!r}")
                    self.assertIsNone(EXCEL_SHEET.search(text), f"{pkg} {k}: {text!r}")

    def test_a_document_key_is_said_in_both_languages_and_in_english_without_thai(self):
        for pkg in DOCUMENTS_DONE:
            w = words.of(f"agent_market.packages.{pkg}")
            for k in (k for k in w.strings if k.startswith("doc.")):
                en = w.english_of(k)
                self.assertTrue(en.strip(), f"{pkg} {k}")
                self.assertIsNone(re.search(r"[฀-๿]", re.sub(r"“[^”]*”", "", en)), f"{pkg} {k}: {en!r}")

    def test_a_table_of_headings_is_said_when_read(self):
        from agent_market.packages.bank_reconciliation.scripts.workbook import H_ITEMS

        with words.speaking("th"):
            self.assertEqual(H_ITEMS[3], "วันที่")
            self.assertEqual(H_ITEMS.index("วันที่"), 3)
        with words.speaking("en"):
            self.assertEqual(H_ITEMS[3], "Date")
            self.assertEqual(H_ITEMS.index("Date"), 3)
            self.assertEqual(list(H_ITEMS)[:2], ["No.", "Side adjusted"])
        self.assertEqual(H_ITEMS.of("doc.col.date"), 3)
        self.assertEqual(len(H_ITEMS), 14)

    def test_a_date_a_document_prints_is_read_back_in_either_language(self):
        self.assertEqual(words.date_read("5 ก.ย. 2569"), (2026, 9, 5))
        self.assertEqual(words.date_read("5 Sep 2026"), (2026, 9, 5))
        self.assertEqual(words.date_read("30 Sep 2026"), (2026, 9, 30))
        self.assertIsNone(words.date_read("September 2026"))
        self.assertIsNone(words.date_read(""))
        with words.speaking("en"):
            self.assertEqual(words.date_read(words.date("2026-09-05")), (2026, 9, 5))
        with words.speaking("th"):
            self.assertEqual(words.date_read(words.date("2026-09-05")), (2026, 9, 5))


class TheBankPaperCarriedToNextMonth(unittest.TestCase):
    """The sheet of items carried forward comes back next month as a file. A paper written in English must be
    read by a Thai run, and the other way round — by the sheet's name, its headings and the kinds it prints."""

    @classmethod
    def setUpClass(cls):
        case = next(c for c in K.load_cases()["cases"] if c["id"] == "clean_kbank_layout")
        cls.th = K.run_case(case)
        cls.en = K.run_case(case, lang="en")
        words.use("th")

    def _sheet_as_file(self, res, lang) -> str:
        from agent_market.packages.bank_reconciliation.checks.inputs import CARRIED_SHEET
        from agent_market.packages.bank_reconciliation.words import W

        with words.speaking(lang):
            title = W(CARRIED_SHEET)
        rows = res["sheets"][title]
        wb = Workbook()
        ws = wb.active
        ws.title = title
        for r in rows:
            ws.append(r)
        path = os.path.join(tempfile.mkdtemp(), f"carried_{lang}.xlsx")
        wb.save(path)
        return path

    def test_the_english_papers_sheet_is_read_by_a_thai_run_and_the_thai_one_by_an_english_run(self):
        from agent_market.packages import bank_reconciliation as BR

        want = len(self.th["computed"]["carried_out"])
        self.assertGreater(want, 0)
        self.assertEqual(len(self.en["computed"]["carried_out"]), want)
        for lang, res in (("en", self.en), ("th", self.th)):
            path = self._sheet_as_file(res, lang)
            for reader in ("th", "en"):
                with words.speaking(reader):
                    up = BR.parse(path, os.path.basename(path), {})
                self.assertEqual(up.kind, "carried", f"{lang} paper read by a {reader} run: {up.kind} {getattr(up, 'meta', '')}")
                rows = BR.dump(up)["data"].get("rows") or []
                self.assertEqual(len(rows), want, f"{lang} paper read by a {reader} run: {len(rows)} of {want} items")
                self.assertEqual(sorted(r["kind"] for r in rows), sorted(i["type"] for i in self.th["computed"]["items"]
                                                                        if i["id"] in self.th["computed"]["carried_out"]))


if __name__ == "__main__":
    unittest.main()
