"""AM23 §1.5 and Part 4 — the refusal is the test. Permanent fixtures, for each of the four agents whose job is the
workbook:

  1. a deck asked for from 800 lines → refused before any file is written, with the alternative computed from the
     run's own amounts (the rows at or above a round amount, counted), and a deck rendered anyway writes no file;
  2. a report or a deck asked for from a run with nothing to say — everything matched, no exception, and a
     commentary with no point to make → refused, offering the workbook — never a page with a heading and white space;
  3. a kind of file the agent does not write (.xls among them) → refused by name, no file;
  plus: the deck a person picks (the rows at or above the threshold) is made, and says what it shows.
"""

from __future__ import annotations

import copy
import importlib
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

from agent_market.deliverables import document
from agent_market.deliverables.look import commentary
from agent_market.engine import words
from agent_market.tests import samplekit

FOUR = ("bank_reconciliation", "expense_audit", "invoice_processing", "po_matching")


def many(pkg: str, computed: dict, n: int = 800) -> dict:
    """The sample's computed table with ``n`` rows in the table a reader needs: amounts 1..n baht."""
    c = copy.deepcopy(computed)
    if pkg == "bank_reconciliation":
        one = dict(c["items"][0])
        c["items"] = [dict(one, id=f"i{k:04d}", amount=f"{k}.00", ref=f"R{k:04d}") for k in range(1, n + 1)]
    elif pkg == "expense_audit":
        one = dict(next(x for x in c["claims"] if x.get("outcome") != "pass") if any(x.get("outcome") != "pass" for x in c["claims"])
                   else dict(c["claims"][0], outcome="exception", outcome_label="exception"))
        c["claims"] = [dict(one, id=f"C{k:04d}", key=f"C{k:04d}", amount=f"{k}.00") for k in range(1, n + 1)]
    elif pkg == "invoice_processing":
        one = dict(c["documents"][0])
        c["documents"] = [dict(one, doc_key=f"d{k:04d}", name=f"d{k:04d}.pdf", document_number=f"IV{k:04d}", total=f"{k}.00",
                               thb_total=f"{k}.00") for k in range(1, n + 1)]
        c["exceptions"] = [{"doc_key": f"d{k:04d}", "reason": "Recorded with a note", "code": "x", "severity": "recorded"}
                           for k in range(1, n + 1)]
    else:
        one = dict(c["invoices"][0], status="blocked", status_label="Blocked", reason_labels=["x"])
        c["invoices"] = [dict(one, key=f"k{k:04d}", number=f"IV{k:04d}", total=f"{k}.00", value_thb=f"{k}.00", currency="THB")
                         for k in range(1, n + 1)]
    return c


def nothing(pkg: str, computed: dict) -> dict:
    """The sample's computed table with nothing in it for a document to say."""
    c = copy.deepcopy(computed)
    c["exceptions"] = []
    if pkg == "bank_reconciliation":
        c["items"] = []
        c["gate"] = dict(c.get("gate") or {}, passed=True)
    elif pkg == "expense_audit":
        c["claims"] = [dict(x, outcome="pass") for x in c["claims"]]
    elif pkg == "invoice_processing":
        c["documents"] = [dict(d, status="included") for d in c["documents"]]
    else:
        c["invoices"] = [dict(i, status="pass") for i in c["invoices"]]
    return c


class TheRefusalIsTheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.computed = {pkg: samplekit.render_sample(pkg, out_dir=tempfile.mkdtemp(dir=cls.tmp))["computed"] for pkg in FOUR}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, pkg, formats, **kw):
        return {"formats": list(formats), "names": {f: f"{pkg}.{f}" for f in ("xlsx", "docx", "pptx", "xls", "pdf")},
                "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"}, "run": {"id": "t", "date": "2026-10-17", "agent_version": "t"},
                "model_info": {"model": None, "self_hosted": None}, **kw}

    def mod(self, pkg):
        return importlib.import_module(f"agent_market.packages.{pkg}")

    def test_1_a_deck_of_800_lines_is_refused_with_an_alternative_from_the_runs_own_amounts(self):
        cap = document.deck_capacity()
        self.assertEqual(cap, 20, "ten rows a slide, two slides (look.json: pptx table rows × parts_max)")
        for pkg in FOUR:
            with self.subTest(pkg=pkg):
                c = many(pkg, self.computed[pkg])
                refusals = self.mod(pkg).plan(c, self.req(pkg, ["xlsx", "pptx"]))
                self.assertEqual([r["format"] for r in refusals], ["pptx"])
                r = refusals[0]
                self.assertEqual(r["code"], "deck_long")
                # amounts 1..800: no round amount leaves 1–20 rows (500 leaves 301, 1,000 none) — the 20th largest, 781, does
                self.assertIn("800", r["question"])
                self.assertIn("781", r["question"])
                self.assertIn(f"{cap}", r["question"])
                opts = [o["value"] for o in r["options"]]
                self.assertEqual(opts[0], {"formats": ["xlsx"]}, "first: the workbook alone (what the round makes anyway)")
                over = opts[1]
                self.assertEqual(over["formats"], ["pptx"])
                th = Decimal(over["deck_over"])
                rows = [Decimal(k) for k in range(1, 801)]
                self.assertTrue(0 < sum(1 for v in rows if v >= th) <= cap, th)
                # one question, answerable in one tap: two to four options, each with a label
                self.assertTrue(2 <= len(r["options"]) <= 4 and all(o["label"] for o in r["options"]))
                # a deck rendered anyway writes no file: the catalogue refuses a table past its slides
                out = tempfile.mkdtemp(dir=self.tmp)
                res = self.mod(pkg).render(c, self.req(pkg, ["pptx"]), in_dir=out, out_dir=out)
                self.assertEqual(res["artifacts"], [])
                self.assertEqual([x["format"] for x in res["refused"]], ["pptx"])
                self.assertFalse(os.path.exists(os.path.join(out, f"{pkg}.pptx")))
                # the report holds 800 rows: it shows the largest twenty and says the workbook has every one
                self.assertEqual(self.mod(pkg).plan(c, self.req(pkg, ["docx"])), [])

    def test_the_alternative_is_computed_from_the_amounts_not_invented(self):
        self.assertEqual(document.threshold_for([Decimal(x) for x in (5, 1200, 3000, 950, 40, 1001)], 3), Decimal(1000))
        self.assertEqual(document.threshold_for(["10.00"] * 30, 20), None, "thirty equal amounts: no threshold leaves twenty")
        self.assertEqual(document.threshold_for([Decimal(k) for k in range(1, 801)], 20), Decimal(781))
        self.assertEqual(document.threshold_for(["2500.00", "100.00"], 20), Decimal(1))

    def test_the_deck_a_person_picks_holds_the_rows_over_the_threshold_and_says_so(self):
        for lang in ("th", "en"):
            for pkg in FOUR:
                with self.subTest(pkg=pkg, lang=lang):
                    c = many(pkg, self.computed[pkg])
                    with words.speaking(lang):
                        r = self.mod(pkg).plan(c, self.req(pkg, ["pptx"]))[0]
                        th = r["options"][1]["value"]["deck_over"]
                        out = tempfile.mkdtemp(dir=self.tmp)
                        res = self.mod(pkg).render(c, self.req(pkg, ["pptx"], deck_over=th), in_dir=out, out_dir=out)
                    fails = [(a["name"], k["name"], k.get("detail")) for a in res["artifacts"] for k in a["quality"]["checks"] if not k["ok"]]
                    self.assertTrue(res["passed"], (fails, res.get("refused")))
                    texts = " ".join(commentary.file_texts(os.path.join(out, f"{pkg}.pptx")))
                    m = sum(1 for k in range(1, 801) if Decimal(k) >= Decimal(th))
                    # the deck says what it shows: how many of the 800, at or above which amount
                    self.assertIn(f"{m}", texts)
                    self.assertIn(f"{Decimal(th):,.0f}", texts)
                    self.assertIn("800", texts)

    def test_2_a_document_from_a_run_with_nothing_to_say_is_refused_offering_the_workbook(self):
        for pkg in FOUR:
            for fmt in ("docx", "pptx"):
                with self.subTest(pkg=pkg, fmt=fmt):
                    c = nothing(pkg, self.computed[pkg])
                    quiet = {"summary": "—", "movements": [], "watch": [], "next_steps": [], "written_by": "rules"}
                    refusals = self.mod(pkg).plan(c, self.req(pkg, [fmt], narrative=quiet))
                    self.assertEqual([r["code"] for r in refusals], ["nothing_to_say"])
                    self.assertEqual(refusals[0]["options"][0]["value"], {"formats": ["xlsx"]})
                    self.assertIn(words.SHARED(f"doc.plan.doc.{fmt}"), refusals[0]["question"])
            with self.subTest(pkg=pkg, what="a clean run whose commentary has points to make has something to say"):
                said = {"summary": "—", "movements": [], "watch": ["ตรวจรายการที่ยอดสูงที่สุดกับเอกสารต้นฉบับ"], "next_steps": [],
                        "written_by": "model", "model": "m"}
                self.assertEqual(self.mod(pkg).plan(nothing(pkg, self.computed[pkg]), self.req(pkg, ["docx", "pptx"], narrative=said)), [])

    def test_3_a_kind_of_file_the_agent_does_not_write_is_refused_by_name(self):
        for pkg in FOUR:
            for fmt in ("xls", "doc", "odt"):
                with self.subTest(pkg=pkg, fmt=fmt):
                    out = tempfile.mkdtemp(dir=self.tmp)
                    res = self.mod(pkg).render(copy.deepcopy(self.computed[pkg]), self.req(pkg, [fmt]), in_dir=out, out_dir=out)
                    self.assertEqual(res["artifacts"], [])
                    self.assertEqual([r["format"] for r in res["refused"]], [fmt])
                    self.assertIn(f".{fmt}", res["refused"][0]["check"]["detail"])
                    self.assertEqual(os.listdir(out), [])
                    self.assertNotIn(fmt, [o["format"] for o in samplekit.manifest(pkg)["outputs"]])


if __name__ == "__main__":
    unittest.main()
