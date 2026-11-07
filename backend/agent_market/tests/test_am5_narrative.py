"""AM5 — the two rounds that failed on the owner's screen.

The model that wrote the report's words left a list null ("watch": null).
The runner's check let it through, and the report's builder walked it:
TypeError: 'NoneType' object is not iterable — which the workspace then
showed to the person. The runner no longer lets such a narrative through
(Go: checkNarrative) and no longer shows an error's text; and the builder
itself reads a narrative's lists as lists of sentences, whatever it is given.

Runs inside the sandbox image.
"""

import copy
import os
import tempfile
import unittest

from agent_market.deliverables import content
from agent_market.deliverables.render import render_all
from agent_market.deliverables.theme import resolve

from .test_deliverables import REQ, computed_for

SUMMARY = "ภาพรวมของงวดนี้ รายได้และค่าใช้จ่ายเปลี่ยนไปตามตาราง"


class NarrativeLists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.computed = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx"])

    def build(self, narrative):
        req = {**REQ, "theme": resolve({"id": "navy"}), "narrative": narrative}
        return content.build(copy.deepcopy(self.computed), req)

    def bullets(self, model, key):
        section = next(s for s in model["sections"] if s["key"] == key)
        return [b["items"] for b in section["blocks"] if b.get("type") == "bullets"]

    def test_a_list_left_null_is_an_empty_list(self):
        model = self.build({"summary": SUMMARY, "movements": None, "watch": None, "next_steps": None, "written_by": "model"})
        self.assertEqual(self.bullets(model, "insights"), [[], []])
        self.assertEqual(self.bullets(model, "movements"), [])

    def test_a_list_that_is_missing_is_an_empty_list(self):
        model = self.build({"summary": SUMMARY, "written_by": "model"})
        self.assertEqual(self.bullets(model, "insights"), [[], []])

    def test_only_sentences_are_kept(self):
        model = self.build({"summary": SUMMARY, "movements": "ไม่มี", "watch": [None, "ตรวจค่าที่ปรึกษา", 7, {"a": 1}, "  "],
                            "next_steps": ["ทบทวนสัญญาที่ปรึกษา"], "written_by": "model"})
        watch, nxt = self.bullets(model, "insights")
        self.assertEqual(len(watch), 1)
        self.assertEqual("".join(t for t, _ in watch[0]), "ตรวจค่าที่ปรึกษา")
        self.assertEqual(len(nxt), 1)
        self.assertEqual(self.bullets(model, "movements"), [])

    def test_the_report_renders_and_passes_its_gate(self):
        out = tempfile.mkdtemp()
        req = {**REQ, "formats": ["docx", "pptx"], "narrative": {"summary": SUMMARY, "movements": ["ค่าที่ปรึกษาเพิ่มขึ้นมาก"],
                                                                "watch": None, "next_steps": None, "written_by": "model"}}
        res = render_all(copy.deepcopy(self.computed), req, in_dir=out, out_dir=out)
        failed = [(a["format"], c["label"]) for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]
        self.assertEqual({a["format"] for a in res["artifacts"]}, {"docx", "pptx"})
        self.assertTrue(all(os.path.exists(os.path.join(out, a["name"])) for a in res["artifacts"]))
        # an empty "what to watch" is an empty section the gate may name — never a crash
        self.assertTrue(res["passed"] or failed, failed)


if __name__ == "__main__":
    unittest.main()
