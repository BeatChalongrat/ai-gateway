"""AM24 §1.2 — a commentary's Thai, read by the rules e2/marks.py already holds for the PDF repair: a mark written
twice on one letter, or a cluster's marks in another order, is a slip with one reading and is mended to it; two tone
marks, a mark on a vowel or on a space, have none and are never mended. The runner sends an answer with a slip back
and uses the mended text only when no attempt is free of them (runner/marks_am24.go); it asks through the engine's
platform op ``thai_marks``.
"""

from __future__ import annotations

import glob
import json
import os
import re
import unittest

from agent_market.engine import step
from agent_market.engine.e2 import marks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Slips(unittest.TestCase):
    def test_the_slips_seen_on_live_are_mended_to_their_one_reading(self):
        # the four of the AM23 live walk's report, and its heading
        for written, want in (("ร่่าง", "ร่าง"), ("กัับ", "กับ"), ("ว่่า", "ว่า"),
                              ("รอบกันยายน 2569 ลงตัว ยอดปรับปรุงเท่ากััน", "รอบกันยายน 2569 ลงตัว ยอดปรับปรุงเท่ากัน"),
                              ("น้้ำ", "น้ำ"), ("ก่ี", "กี่"), ("ว่่า {x} ได้้", "ว่า {x} ได้")):
            got = marks.mend(written)
            self.assertTrue(got["slips"], written)
            self.assertEqual(got["mended"], want, written)

    def test_a_slip_with_no_one_reading_is_never_mended(self):
        for written in ("ก่้", "เก็่บ", "นำ้", "abc ่x", "่ต้น"):
            got = marks.mend(written)
            self.assertTrue(got["slips"], written)
            self.assertIsNone(got["mended"], written)

    def test_thai_as_thai_writes_it_has_no_slip(self):
        for text in ("ที่", "น้ำ", "ค่ำ", "สิทธิ์", "พันธุ์", "ทั้ง", "ปั้น", "ผู้ใช้", "ก็ได้", "ศาสตร์", "กำไร", "ฤดู", "ๆ",
                     "Revenue {revenue} บาท", ""):
            self.assertEqual(marks.mend(text), {"slips": [], "mended": None}, text)

    def test_where_a_slip_is_and_what_it_was(self):
        got = marks.slips("ยอดเท่ากััน")
        self.assertEqual(got, [{"at": 7, "written": "กัั", "as": "กั"}])

    def test_every_thai_word_the_packages_and_the_engine_ship_has_no_slip(self):
        # the check reads a model's words by the rules every word of ours already keeps: a word of ours with a slip
        # would be a check that cannot tell a slip from Thai
        files = [p for p in glob.glob(os.path.join(ROOT, "**", "*"), recursive=True)
                 if os.path.isfile(p) and p.endswith((".json", ".md", ".yaml", ".yml"))
                 and os.sep + "tests" + os.sep not in p and "__pycache__" not in p]
        lines = 0
        found = []
        for p in files:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if re.search("[ก-๛]", line):
                        lines += 1
                        found += [(os.path.relpath(p, ROOT), s["written"]) for s in marks.slips(line)]
        self.assertGreater(lines, 3000, "the words were found")
        self.assertEqual(found, [])


class Op(unittest.TestCase):
    def test_the_platform_op_reads_each_text_and_needs_no_package(self):
        out = step.run({"op": "thai_marks", "texts": ["ร่่าง", "ปกติ", "ก่้"]})
        self.assertTrue(out["ok"])
        self.assertEqual([t["mended"] for t in out["texts"]], ["ร่าง", None, None])
        self.assertEqual([len(t["slips"]) for t in out["texts"]], [1, 0, 1])
        json.dumps(out, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
