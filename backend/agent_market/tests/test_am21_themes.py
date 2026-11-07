"""AM21 §6.2 and §6.3 — themes, not a theme; the customer's own brand, refused when it would hurt.

The same run rendered under two themes is the same document in two looks: every figure, every table
cell, every citation the same, and only the colours, the cover treatment and the table style different.
That check is what proves content and presentation came apart. A brand's colour that fails contrast,
or a typeface the sandbox lacks, is refused with its reason and the theme's own value stays.
"""

from __future__ import annotations

import collections
import os
import re
import tempfile
import unittest

from agent_market.deliverables import theme as theme_mod
from agent_market.deliverables.look.tokens import LOOK
from agent_market.deliverables.render import render_all
from agent_market.tests import dockit, evalkit
from agent_market.tests.test_deliverables import REQ, computed_for

CITE = re.compile(r"\b[A-Za-z]{1,3}\d{1,3}\b")


def _numbers_and_cites(texts: list[str]) -> tuple[set, set]:
    nums, cites = set(), set()
    for t in texts:
        nums.update(dockit.numbers_of(t))
        cites.update(CITE.findall(t))
    return nums, cites


class TheSameRunUnderTwoThemes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])
        cls.out = {}
        cls.res = {}
        for tid in ("navy", "teal"):
            out = tempfile.mkdtemp()
            res = render_all(cls.computed, {**REQ, "theme": {"id": tid, "company": "บริษัท ทดสอบ จำกัด"}, "formats": ["docx", "pptx", "xlsx"]},
                             in_dir=out, out_dir=out)
            cls.out[tid], cls.res[tid] = out, res

    def test_both_pass_the_gate(self):
        for tid, res in self.res.items():
            self.assertTrue(res["passed"], (tid, [c for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]))
            self.assertEqual(res["theme"]["id"], tid)

    def test_every_figure_table_cell_and_citation_is_the_same_cell_for_cell(self):
        for fmt in ("docx", "pptx", "xlsx"):
            a = evalkit.texts(os.path.join(self.out["navy"], REQ["names"][fmt]))
            b = evalkit.texts(os.path.join(self.out["teal"], REQ["names"][fmt]))
            with self.subTest(fmt=fmt):
                if fmt == "xlsx":
                    self.assertEqual(a, b)   # a workbook: every cell, in order
                else:
                    # a report or a deck: the texts are the same except the theme's own name
                    na, ca = _numbers_and_cites(a)
                    nb, cb = _numbers_and_cites(b)
                    self.assertEqual(na, nb)
                    self.assertEqual(ca, cb)
                    # and every word is there the same number of times: the two covers lay the same
                    # content out differently (a band holds a tile in one cell, a rule treatment in two
                    # paragraphs), so the comparison is of words, not of the frames they sit in
                    names = {theme_mod.said("docs.theme.navy"), theme_mod.said("docs.theme.teal")}
                    strip = lambda ts: [re.sub("|".join(map(re.escape, names)), "", t) for t in ts]  # noqa: E731
                    bag = lambda ts: collections.Counter(w for t in strip(ts) for w in t.split())  # noqa: E731
                    self.assertEqual(bag(a), bag(b))

    def test_the_two_looks_differ(self):
        import zipfile

        def colours(path, part_prefix):
            with zipfile.ZipFile(path) as z:
                xml = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.startswith(part_prefix))
            return set(re.findall(r'(?:w:fill|w:color|val)="([0-9A-F]{6})"', xml))

        navy = colours(os.path.join(self.out["navy"], REQ["names"]["docx"]), "word/styles")
        teal = colours(os.path.join(self.out["teal"], REQ["names"]["docx"]), "word/styles")
        self.assertIn(LOOK.theme("navy")["palette"]["primary"], navy)
        self.assertIn(LOOK.theme("teal")["palette"]["primary"], teal)
        self.assertNotIn(LOOK.theme("navy")["palette"]["primary"], teal)


class TheCustomersBrand(unittest.TestCase):
    def test_a_primary_colour_with_enough_contrast_is_taken(self):
        t = theme_mod.resolve({"id": "navy", "palette": {"primary": "#1B3A5C"}})
        self.assertEqual(t["palette"]["primary"], "1B3A5C")
        self.assertEqual(t["refused"], [])

    def test_a_colour_that_fails_contrast_is_refused_with_the_reason_and_the_themes_stays(self):
        t = theme_mod.resolve({"id": "navy", "palette": {"primary": "FFFF99"}})
        self.assertEqual(t["palette"]["primary"], LOOK.theme("navy")["palette"]["primary"])
        self.assertEqual(len(t["refused"]), 1)
        r = t["refused"][0]
        self.assertEqual(r["what"], "palette.primary")
        self.assertIn("4.5", r["why"])

    def test_only_the_brand_tokens_may_be_set(self):
        t = theme_mod.resolve({"id": "navy", "palette": {"negative": "00FF00"}})
        self.assertEqual(t["palette"]["negative"], LOOK.theme("navy")["palette"]["negative"])
        self.assertEqual(t["refused"][0]["what"], "palette.negative")

    def test_a_typeface_the_sandbox_lacks_is_refused_and_the_family_stays(self):
        t = theme_mod.resolve({"id": "navy", "fonts": {"thai": "Comic Sans MS"}})
        self.assertEqual(t["fonts"], {"thai": LOOK.family, "latin": LOOK.family})
        self.assertEqual(t["refused"][0]["what"], "fonts.thai")
        self.assertIn("Comic Sans MS", t["refused"][0]["why"])

    def test_the_run_records_what_was_refused(self):
        computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx"])
        out = tempfile.mkdtemp()
        res = render_all(computed, {**REQ, "theme": {"id": "navy", "palette": {"primary": "FFFF99"}}, "formats": ["xlsx"]}, in_dir=out, out_dir=out)
        self.assertTrue(res["passed"])
        self.assertEqual([r["what"] for r in res["theme"]["refused"]], ["palette.primary"])


if __name__ == "__main__":
    unittest.main()
