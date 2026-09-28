"""AM4 — a name or a description is recorded as printed when a PDF's text layer lost its Thai marks.

A PDF written by Chrome/Skia (the e2 fixtures are Chrome prints, as are many Thai accounting
systems' PDFs) draws a Thai mark in a shifted position with its own glyph and gives that glyph no
text: the text layer has a NUL where the mark was. AM2 matched labels and parties on consonant
skeletons, so numbers and tax IDs were right, but the register recorded
"บริษัทไทยออฟฟศซัพพลายจำกัด" for "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด" and "3 นิว สีนาเงิน" for
"3 นิ้ว สีน้ำเงิน".

The printed text is the fixture's HTML source (what Chrome printed), read here without the engine.
"""

from __future__ import annotations

import glob
import html
import importlib
import json
import os
import re
import unittest

X = importlib.import_module("agent_market.engine.e2.extract")

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures")
_MARKS = set("ัิีึืฺุู็่้๊๋์ํ๎")

# The page shows these words' lost marks, but the engine does not put them back: the lost glyph is
# used once in the document, the font cannot name it, and two readings of the page did not agree
# on it. A word the document cannot prove stays as the text layer wrote it — never a guessed mark.
UNPROVEN_NAMES = {
    ("f05_missing_seller_tax_id", "seller_name"): {"โซลูชั่นส์"},   # bold; one reading said ้ (it is ่)
    ("f07_foreign_currency_usd", "buyer_name"): {"โซลูชั่นส์"},     # bold; one reading only
}
UNPROVEN_WORDS = {
    "d03_abbreviated_retail": {"น้ำดื่ม"},
    "h06_pos_vatable": {"น้ำมันพืช"},
}


def _clusters(s: str) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for ch in s.replace("ำ", "ํา"):
        if ch in _MARKS and out:
            out[-1][1].append(ch)
        else:
            out.append((ch, []))
    return out


def less_marks(got: str, printed: str) -> bool:
    """``got`` is ``printed`` with some of its marks missing, and nothing else different."""
    g, p = _clusters(got), _clusters(printed)
    return [b for b, _ in g] == [b for b, _ in p] and all(set(a) <= set(b) for (_, a), (_, b) in zip(g, p))


def _documents():
    """(set, expected) of every golden document read from a PDF text layer."""
    for s in ("e2", "e2_holdout"):
        for p in sorted(glob.glob(os.path.join(FIX, s, "expected", "*.json"))):
            with open(p, encoding="utf-8") as f:
                exp = json.load(f)
            if exp["class"] in ("digital", "etax_pdf"):
                yield s, exp


def _printed_words(s: str, fid: str) -> set[str]:
    with open(os.path.join(FIX, s, "html", fid + ".html"), encoding="utf-8") as f:
        raw = re.sub(r"<style.*?</style>", " ", f.read(), flags=re.S)
    return set(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


class RecordedAsPrinted(unittest.TestCase):
    """Every golden text-layer document, read once."""

    @classmethod
    def setUpClass(cls):
        cls.docs = []
        for s, exp in _documents():
            state = X.read(os.path.join(FIX, s, exp["files"][0]))
            cls.docs.append((s, exp, state))

    def test_seller_and_buyer_names_are_recorded_as_printed(self):
        self.assertGreaterEqual(len(self.docs), 22)
        for s, exp, state in self.docs:
            fid = exp["id"]
            for who in ("seller_name", "buyer_name"):
                want = exp["truth"].get(who)
                got = state.fields.get(who)
                with self.subTest(fid=fid, field=who):
                    if want is None:
                        self.assertFalse(got is not None and got.present)
                        continue
                    value, core = got.value, got.normalized["core"]
                    unproven = UNPROVEN_NAMES.get((fid, who))
                    if unproven:
                        # every word as printed or with some marks missing: never a mark it does not print
                        self.assertEqual(len(value.split()), len(want["value"].split()), value)
                        for a, b in zip(value.split(), want["value"].split()):
                            self.assertTrue(a == b or (b in unproven and less_marks(a, b)), (a, b))
                        self.assertTrue(less_marks(core.replace(" ", ""), want["normalized"]["core"].replace(" ", "")))
                        continue
                    self.assertEqual(value, want["value"])
                    self.assertEqual(core, want["normalized"]["core"])

    def test_descriptions_are_recorded_as_printed(self):
        for s, exp, state in self.docs:
            fid = exp["id"]
            printed = _printed_words(s, fid)
            for it in state.items:
                f = it["description"]
                if not f.present:
                    continue
                for w in str(f.value).split():
                    with self.subTest(fid=fid, row=it["row"], word=w):
                        if w in printed:
                            continue
                        self.assertTrue(any(less_marks(w, p) for p in UNPROVEN_WORDS.get(fid, ())),
                                        f"{w!r} is not printed in {fid}")

    def test_the_seller_the_card_named(self):
        d01 = next(st for _, exp, st in self.docs if exp["id"] == "d01_clean_full_tax_invoice")
        self.assertEqual(d01.fields["seller_name"].value, "บริษัท ไทยออฟฟิศ ซัพพลาย จำกัด")
        self.assertIn("thai_marks_restored", d01.fields["seller_name"].checks)
        self.assertEqual(d01.fields["seller_tax_id"].normalized, "0105545009875")
        self.assertTrue(d01.items[2]["description"].value.endswith("3 นิ้ว สีน้ำเงิน"))


class MarksOnlyMarks(unittest.TestCase):
    """Putting marks back changes marks and nothing else."""

    def test_no_consonant_digit_or_letter_changes(self):
        W = importlib.import_module("agent_market.engine.e2.words")
        M = importlib.import_module("agent_market.engine.e2.marks")
        T = importlib.import_module("agent_market.engine.e2.thai_text")
        restored = 0
        for fid in ("d01_clean_full_tax_invoice", "d02_classic_hyphen_taxid", "d05_wht_service_invoice",
                    "d07_multipage", "f06_invalid_tax_id_checksum"):
            path = os.path.join(FIX, "e2", "docs", fid + ".pdf")
            fonts: dict = {}
            words, _pages = W.pdf_text_words(path, fonts)
            before = [(w.text, w.lost) for w in words]
            M.restore(path, words, fonts, *X._default_ocr())
            for (text, lost), w in zip(before, words):
                with self.subTest(fid=fid, word=text):
                    self.assertEqual(T.skel(w.text), T.skel(text))
                    if not lost:
                        self.assertEqual(w.text, text)      # a word the layer did not damage is never touched
                    elif w.text != text:
                        restored += 1
                        self.assertTrue(less_marks(text, w.text))
        self.assertGreater(restored, 30)

    def test_no_page_reading_leaves_the_text_layer(self):
        """Tesseract missing or failing: the words stay as the layer wrote them (less what the font
        itself names), and the document is still read."""
        def broken(*_a, **_k):
            raise RuntimeError("tesseract is not installed")

        st = X.read(os.path.join(FIX, "e2", "docs", "d01_clean_full_tax_invoice.pdf"), ocr=(broken, broken))
        self.assertEqual(st.fields["seller_name"].value, "บริษัท ไทยออฟฟศ ซัพพลาย จำกัด")
        self.assertEqual(st.fields["total"].normalized, X.T.q2("12850.70"))


class Gate(unittest.TestCase):
    """The rules that decide a lost glyph, on made-up words (no PDF, no OCR)."""

    @classmethod
    def setUpClass(cls):
        cls.M = importlib.import_module("agent_market.engine.e2.marks")

    def test_a_reading_counts_only_when_it_adds_marks_where_the_layer_lost_a_glyph(self):
        g = ("f", 1)
        add = self.M.added_marks
        self.assertEqual(add("ไทยออฟฟศ", ((7, g),), "ไทยออฟฟิศ"), {g: "ิ"})
        self.assertIsNone(add("ไทยออฟฟศ", ((7, g),), "ไทยออฟฟิส"))       # another consonant
        self.assertIsNone(add("ไทยออฟฟศ", ((7, g),), "ไท่ยออฟฟิศ"))      # a mark where nothing was lost
        self.assertIsNone(add("ที่ตัง", ((5, g),), "ทีตั้ง"))             # a printed mark missing
        self.assertIsNone(add("ไทยออฟฟศ", ((7, g),), "ไทยออฟฟศ"))       # the reading did not see it
        self.assertIsNone(add("ชัน", ((2, g),), "ชั่้น"))                  # two tone marks: not Thai
        self.assertIsNone(add("๑๒ที", ((4, g),), "๑๓ที่"))               # a digit differs

    def test_a_glyph_needs_two_agreeing_readings(self):
        v = self.M.by_votes
        self.assertIsNone(v(["้"]))
        self.assertEqual(v(["่", "่"]), "่")
        self.assertEqual(v(["่", "่", "่", "้"]), "่")
        self.assertIsNone(v(["่", "่", "้"]))
        self.assertIsNone(v([]))

    def test_a_word_is_rewritten_only_when_every_glyph_it_lost_is_known(self):
        a, b = ("f", 1), ("f", 2)
        rw = self.M.rewrite
        self.assertEqual(rw("ไทยออฟฟศ", ((7, a),), {a: "ิ"}), "ไทยออฟฟิศ")
        self.assertEqual(rw("สีนาเงิน", ((3, a),), {a: "ํ้"}), "สีน้ำเงิน")     # the nikhahit + tone ligature
        self.assertEqual(rw("จำนวนเงินรวมทังสิน", ((14, a), (17, b)), {a: "้", b: "้"}), "จำนวนเงินรวมทั้งสิ้น")
        self.assertIsNone(rw("จำนวนเงินรวมทังสิน", ((14, a), (17, b)), {a: "้", b: None}))
        self.assertIsNone(rw("ชั่น", ((3, a),), {a: "้"}))                    # two tone marks

    def test_a_composite_glyph_is_its_parts_not_what_one_reading_saw(self):
        class Font:
            tt = True

            def mapped(self, cid):
                return {10: "ํ"}.get(cid)

            def components(self, cid):
                return [10, 11] if cid == 12 else None

            def same_outline(self, cid):
                return None

        fm = {0: Font()}
        lig, part = (0, 12), (0, 11)
        # OCR reads น้ำ as น้า: the votes on the ligature lack the nikhahit the font says it holds
        self.assertEqual(self.M.decide({lig}, {lig: ["้", "้"]}, fm), {lig: "้ํ"})
        self.assertEqual(self.M.decide({lig}, {lig: ["้"]}, fm), {lig: None})
        self.assertEqual(self.M.decide({lig, part}, {lig: ["้"], part: ["้"]}, fm)[lig], "้ํ")


if __name__ == "__main__":
    unittest.main()
