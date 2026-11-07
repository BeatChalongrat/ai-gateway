"""AM23 §3.3 — what makes a commentary read machine-made, in the files: headings written from what each part found
(the rules' and a model's), printed in every file a commentary is, read back by the gates; and no hedge of the
package's in any finished file — the same list, its manifest's, that the runner refuses a model's answer for.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from agent_market.deliverables.look import catalogue, commentary
from agent_market.engine import step, words
from agent_market.tests import samplekit

FIVE = ("bank_reconciliation", "expense_audit", "invoice_processing", "po_matching", "financial_report")


def _render(pkg: str, computed: dict, narrative: dict | None, formats=("xlsx", "docx", "pptx"), lang: str | None = None) -> tuple[dict, str]:
    d = tempfile.mkdtemp(prefix="am23_comm_")
    inp, out = os.path.join(d, "in"), os.path.join(d, "out")
    os.makedirs(inp)
    os.makedirs(out)
    with open(os.path.join(inp, "computed.json"), "w", encoding="utf-8") as f:
        json.dump(computed, f, ensure_ascii=False, default=str)
    step.JOB_IN, step.JOB_OUT = inp, out
    req = {"op": "render", "package": pkg, "computed": "computed.json", "formats": list(formats), "names": {},
           "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"},
           "run": {"id": "t", "date": "2026-10-17", "organisation": "บริษัท ทดสอบ จำกัด", "kind": "run"}}
    if narrative is not None:
        req["narrative"] = narrative
    if lang:
        req["lang"] = lang
    res = step.run(req)
    words.use(None)
    assert res.get("ok"), res
    return res, out


class TheRulesHeadingsSayWhatEachPartFound(unittest.TestCase):
    def test_every_agents_rules_write_their_own_headings_and_every_file_prints_them(self):
        for pkg in FIVE:
            for lang in (None, "en"):
                with self.subTest(pkg=pkg, lang=lang or "th"):
                    d = tempfile.mkdtemp()
                    try:
                        r = samplekit.render_sample(pkg, out_dir=d, formats=["xlsx", "docx", "pptx"], lang=lang)
                        comp, fb = r["computed"], r["computed"]["fallback_narrative"]
                        bad = [(a["name"], c["name"], c.get("detail")) for a in r["render"]["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]
                        self.assertEqual(bad, [])
                        generic = comp["headings_generic"]
                        words.use(lang)
                        heads = {p: commentary.heading(fb, p, comp) for p in ("summary", "movements", "watch", "next_steps")
                                 if p == "summary" or fb.get(p)}
                        words.use(None)
                        self.assertNotEqual(heads["summary"], generic["summary"], "the summary's heading says the result")
                        for part, h in heads.items():
                            self.assertNotIn("{", h, f"{part}: every figure printed")
                        for a in r["render"]["artifacts"]:
                            if pkg == "financial_report" and a["format"] == "xlsx":
                                continue   # (its workbook prints no commentary: the report and the deck do)
                            texts = "".join("".join(commentary.file_texts(os.path.join(d, a["name"]))).split())
                            self.assertIn("".join(heads["summary"].split()), texts, a["name"])
                    finally:
                        words.use(None)
                        shutil.rmtree(d, ignore_errors=True)


class AModelsHeadingsArePrintedInEveryFile(unittest.TestCase):
    def test_the_headings_of_a_model_are_the_ones_printed_and_read_back(self):
        words.use(None)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        r = samplekit.render_sample("bank_reconciliation", out_dir=d, formats=["xlsx"])
        comp = r["computed"]
        fb = dict(comp["fallback_narrative"])
        nar = {**fb, "written_by": "model", "model": "m",
               "headings": {"summary": "ยอดสองฝั่งเท่ากันที่ {adjusted_bank}", "movements": "รายการกระทบยอด {items} รายการที่ยังเปิดอยู่",
                            "next_steps": "ร่างรายการปรับปรุงที่ต้องตรวจก่อนลงบัญชี"}}
        res, out = _render("bank_reconciliation", comp, nar)
        bad = [(a["name"], c["name"], c.get("detail")) for a in res["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]
        self.assertEqual(bad, [])
        want = [f"ยอดสองฝั่งเท่ากันที่ {comp['figures']['adjusted_bank']['text']}", f"รายการกระทบยอด {comp['figures']['items']['text']} รายการที่ยังเปิดอยู่",
                "ร่างรายการปรับปรุงที่ต้องตรวจก่อนลงบัญชี"]
        for a in res["artifacts"]:
            texts = "\n".join(commentary.file_texts(os.path.join(out, a["name"])))
            for h in want:
                self.assertIn(h, texts, a["name"])
            for generic in (words.SHARED("doc.commentary.movements"), words.SHARED("doc.commentary.next_steps")):
                self.assertNotIn(generic, texts.split("\n"), f"{a['name']}: the generic heading is not printed where the part has its own")


class NoHedgeInAnyFinishedFile(unittest.TestCase):
    def test_the_gate_reads_the_manifests_hedges_in_both_languages_and_fails_a_file_with_one(self):
        for pkg in FIVE + ("procurement",):
            hedges = words.manifest(f"agent_market.packages.{pkg}")["narrative_hedges"]
            th = [h for h in hedges if any("฀" <= ch <= "๿" for ch in h)]
            self.assertTrue(th and len(th) < len(hedges), f"{pkg}: hedges in both languages")
        words.use(None)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        r = samplekit.render_sample("expense_audit", out_dir=d, formats=["xlsx"])
        comp = r["computed"]
        hedge = words.manifest("agent_market.packages.expense_audit")["narrative_hedges"][0]
        nar = {**comp["fallback_narrative"], "written_by": "model", "model": "m",
               "watch": [f"รายการที่เกินวงเงินต่อครั้ง {hedge}กับใบเสร็จ"]}
        res, _ = _render("expense_audit", comp, nar, formats=("xlsx", "docx"))
        self.assertFalse(res["passed"])
        for a in res["artifacts"]:
            chk = next(c for c in a["quality"]["checks"] if c["name"] == "file.no_hedge")
            self.assertFalse(chk["ok"], a["name"])
            self.assertIn(hedge, chk["detail"])


class TheLimitsTellTheHeadingsTitle(unittest.TestCase):
    def test_every_format_tells_its_heading_limit_and_the_packages_that_print_headings_send_their_generic_ones(self):
        for fmt in ("xlsx", "docx", "pptx"):
            for lang in ("th", "en"):
                lim = catalogue.writing_limits(fmt, lang)
                self.assertGreater(lim["heading_chars"], 20)
                self.assertTrue(all(isinstance(v, int) for v in lim.values()), "numbers only: the runner reads a map of numbers")
        self.assertEqual(catalogue.writing_limits("xlsx", "en")["heading_chars"], catalogue.writing_limits("pptx", "en")["heading_chars"],
                         "the tightest title holds it, whichever file prints it")


if __name__ == "__main__":
    unittest.main()
