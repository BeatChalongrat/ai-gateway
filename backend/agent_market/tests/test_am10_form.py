"""AM10 — a render that fills the round's own form makes none of the agent's files again.

A form a person drops into a finished round is filled by a render job that asks for no format
(``formats: []``). Every package read "no formats" as "my defaults" (``req.get("formats") or [...]``),
so that job made the register — or the report and the deck — a second time beside the form. An
explicit empty list now means none; a request that names no formats still gets the defaults.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from agent_market.deliverables.formats import wanted_formats

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "gate_mutations")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class AnEmptyFormatListMeansNone(unittest.TestCase):
    def test_the_rule(self):
        self.assertEqual(wanted_formats({}, ["xlsx"]), ["xlsx"])
        self.assertEqual(wanted_formats({"formats": None}, ["docx", "pptx"]), ["docx", "pptx"])
        self.assertEqual(wanted_formats({"formats": []}, ["xlsx"]), [])
        self.assertEqual(wanted_formats({"formats": ["pptx"]}, ["docx", "pptx"]), ["pptx"])

    def test_the_invoice_agent_makes_no_register_when_asked_for_none(self):
        from agent_market.packages import invoice_processing as pkg

        computed = _load("invoice_computed.json")
        with tempfile.TemporaryDirectory() as out:
            res = pkg.render(computed, {"formats": [], "names": {}}, in_dir=out, out_dir=out)
            self.assertEqual(res["artifacts"], [])
            self.assertEqual(os.listdir(out), [])
            self.assertTrue(res["passed"])
            res = pkg.render(computed, {"names": {}}, in_dir=out, out_dir=out)
            self.assertEqual([a["format"] for a in res["artifacts"]], ["xlsx"])

    def test_the_report_agent_makes_no_report_when_asked_for_none(self):
        from agent_market.deliverables import render

        computed = _load("financial_computed.json")
        with tempfile.TemporaryDirectory() as out:
            res = render.render_all(computed, {"formats": [], "names": {}, "narrative": {}}, in_dir=out, out_dir=out)
            self.assertEqual(res["artifacts"], [])
            self.assertEqual(os.listdir(out), [])

    def test_no_package_reads_an_empty_list_as_its_default(self):
        """Every package's render goes through the one rule."""
        root = os.path.join(os.path.dirname(HERE), "packages")
        for pkg in sorted(os.listdir(root)):
            init = os.path.join(root, pkg, "__init__.py")
            if not os.path.isdir(os.path.join(root, pkg)) or not os.path.exists(init):
                continue
            with open(init, encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn('req.get("formats") or', src, pkg)


if __name__ == "__main__":
    unittest.main()
