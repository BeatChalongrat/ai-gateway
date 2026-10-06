"""AM11b E: a report that does what it was asked.

A person types "ไม่ต้องใส่กราฟ", "ขอสั้นๆ", "เรียงจากรายการที่ต่างมากที่สุด" with their files. A model reads
the words into the directives the package declares (agent.json "instructions"); the renderer applies
them — a section left out, a table's rows in another order — and the gate opens the finished file and
reads back that it was done. No figure changes: the computed table and every citation are the same with
or without a directive, and the sections a report always has stay.

The refusal cases are the test: a file that still has what was to be left out fails the gate; a table
that is not in the order asked fails it; a round with nothing to compare cannot be ordered by the
difference and says so instead of pretending.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
import zipfile

from docx import Document

from agent_market.deliverables import content, quality
from agent_market.deliverables.render import render_all
from agent_market.deliverables.theme import resolve
from agent_market.tests.test_deliverables import REQ, computed_for

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packages", "financial_report")


def _render(computed, apply, formats=("docx", "pptx")):
    out = tempfile.mkdtemp()
    req = {**REQ, "formats": list(formats)}
    if apply is not None:
        req["instructions"] = {"apply": apply}
    return render_all(computed, req, in_dir=out, out_dir=out), out, req


def _check(res, fmt, name):
    art = next(a for a in res["artifacts"] if a["format"] == fmt)
    return next((c for c in art["quality"]["checks"] if c["name"] == name), None)


def _headings(path):
    return [p.text.strip() for p in Document(path).paragraphs if p.style.name.startswith("Heading 1")]


def _media(path):
    with zipfile.ZipFile(path) as z:
        return [n for n in z.namelist() if n.startswith("word/media/")]


class AskedOfTheReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = computed_for(["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"])
        cls.alone = computed_for(["GL_2569-09.xlsx"])
        cls.default, cls.default_dir, _ = _render(cls.full, None)

    def test_a_round_nobody_asked_anything_of_is_as_it_was(self):
        res = self.default
        self.assertTrue(res["passed"])
        self.assertEqual(res["asked"], {"applied": [], "refused": []})
        self.assertIsNone(_check(res, "docx", "docx.asked"))  # the count of checks on a normal round does not grow
        self.assertEqual(len(_media(os.path.join(self.default_dir, "r_v1.docx"))), 3)

    def test_no_charts_leaves_them_out_of_the_report_and_the_deck(self):
        res, out, _ = _render(self.full, ["no_charts"])
        self.assertTrue(res["passed"], json.dumps(res["artifacts"], ensure_ascii=False)[:800])
        self.assertEqual(res["asked"], {"applied": ["no_charts"], "refused": []})
        path = os.path.join(out, "r_v1.docx")
        self.assertEqual(_media(path), [])
        self.assertNotIn(content.SECTION_TITLES["charts"], _headings(path))
        self.assertIn(content.SECTION_TITLES["accounts"], _headings(path))
        asked = _check(res, "docx", "docx.asked")
        self.assertTrue(asked and asked["ok"], asked)
        from pptx import Presentation

        prs = Presentation(os.path.join(out, "d_v1.pptx"))
        self.assertFalse([sh for s in prs.slides for sh in s.shapes if getattr(sh, "has_chart", False) and sh.has_chart])
        self.assertEqual(len(prs.slides), 8)

    def test_no_figure_changes(self):
        """A directive is not arithmetic: the P&L, the key figures and the citations are the default's."""
        res, out, _ = _render(self.full, ["no_charts", "sort_by_variance"])
        want = content.pl_table(self.full)

        def pl(path):
            return quality._find_table(Document(path).tables, want["columns"])

        self.assertEqual(pl(os.path.join(out, "r_v1.docx")), pl(os.path.join(self.default_dir, "r_v1.docx")))
        a, b = (next(x for x in r["artifacts"] if x["format"] == "docx")["citations"] for r in (res, self.default))
        self.assertEqual(a, b)

    def test_the_short_report_keeps_what_a_report_must_have(self):
        res, out, _ = _render(self.full, ["brief"])
        self.assertTrue(res["passed"])
        heads = _headings(os.path.join(out, "r_v1.docx"))
        for key in content.BRIEF_LEAVES_OUT:
            self.assertNotIn(content.SECTION_TITLES[key], heads)
        # the checks' results, the assumptions and where every figure came from are not the person's to drop
        for must in ("รายการที่ต้องตรวจสอบ", "ข้อสมมติที่ใช้ในรอบนี้", "ภาคผนวก ก · ที่มาของข้อมูล", "ภาคผนวก ข · ที่มาของตัวเลข"):
            self.assertIn(must, heads)
        self.assertTrue(_check(res, "docx", "docx.sections")["ok"])
        # the deck is already the short form: it keeps its charts
        self.assertEqual(next(a for a in res["artifacts"] if a["format"] == "pptx")["meta"]["slides"], 11)

    def test_the_account_table_is_ordered_by_the_difference_as_read_from_the_file(self):
        res, out, _ = _render(self.full, ["sort_by_variance"])
        self.assertTrue(res["passed"])
        block = content.account_table(self.full, by_variance=True)
        self.assertEqual(block["ordered_by"], "var_budget")
        got = quality._find_table(Document(os.path.join(out, "r_v1.docx")).tables, block["columns"])
        sizes = [abs(quality._cell_money(r[block["columns"].index("ต่างจากงบ")]) or 0) for r in got[1:]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertGreater(sizes[0], sizes[-1])
        # the same accounts, the same figures — only the order
        plain = content.account_table(self.full)
        self.assertEqual(sorted(map(tuple, block["rows"])), sorted(map(tuple, plain["rows"])))
        self.assertNotEqual(block["rows"], plain["rows"])
        self.assertIn("เรียงจากส่วนต่างจากงบประมาณมากไปน้อย", _check(res, "docx", "docx.asked")["detail"])

    def test_with_nothing_to_compare_the_order_is_refused_not_pretended(self):
        res, out, _ = _render(self.alone, ["sort_by_variance"])
        self.assertTrue(res["passed"])
        self.assertEqual(res["asked"], {"applied": [], "refused": ["sort_by_variance"]})
        self.assertIsNone(_check(res, "docx", "docx.asked"))
        self.assertEqual(content.account_table(self.alone, by_variance=True)["rows"], content.account_table(self.alone)["rows"])

    def test_an_id_the_package_does_not_declare_changes_nothing(self):
        res, _, _ = _render(self.full, ["by_department", "change_revenue"])
        self.assertEqual(res["asked"], {"applied": [], "refused": []})
        self.assertEqual(res["sections"], self.default["sections"])

    # ── the refusal cases: the gate, on files that were not made as asked ──

    def test_a_file_that_still_has_its_charts_fails_a_round_that_asked_for_none(self):
        theme = resolve(REQ["theme"])
        req = {**REQ, "formats": ["docx", "pptx"], "instructions": {"apply": ["no_charts"]}, "theme": theme}
        model = content.build(self.full, req)
        checks = quality.check_docx(os.path.join(self.default_dir, "r_v1.docx"), model, theme, req)
        bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
        self.assertIn("docx.asked", bad, json.dumps(checks, ensure_ascii=False)[:600])
        self.assertIn("แนวโน้มและองค์ประกอบ", bad["docx.asked"])
        self.assertIn("docx.charts", bad)
        deck = quality.check_pptx(os.path.join(self.default_dir, "d_v1.pptx"), {}, self.full, theme, asked=["no_charts"])
        figures = next(c for c in deck if c["name"] == "pptx.figures")
        self.assertFalse(figures["ok"])
        self.assertIn("ขอไม่ใส่กราฟ แต่ในไฟล์ยังมีกราฟ", figures["detail"])

    def test_a_table_left_in_the_ledgers_order_fails_a_round_that_asked_for_the_difference(self):
        theme = resolve(REQ["theme"])
        req = {**REQ, "formats": ["docx"], "instructions": {"apply": ["sort_by_variance"]}, "theme": theme}
        model = content.build(self.full, req)
        checks = quality.check_docx(os.path.join(self.default_dir, "r_v1.docx"), model, theme, req)
        asked = next(c for c in checks if c["name"] == "docx.asked")
        self.assertFalse(asked["ok"], asked)
        self.assertIn("ไม่ได้เรียงจากส่วนต่างมากไปน้อย", asked["detail"])

    def test_a_short_report_with_a_section_put_back_fails(self):
        theme = resolve(REQ["theme"])
        req = {**REQ, "formats": ["docx"], "instructions": {"apply": ["brief"]}, "theme": theme}
        model = content.build(self.full, req)
        res, out, _ = _render(self.full, ["no_charts"], formats=("docx",))  # has the movements and the accounts
        checks = quality.check_docx(os.path.join(out, "r_v1.docx"), model, theme, req)
        asked = next(c for c in checks if c["name"] == "docx.asked")
        self.assertFalse(asked["ok"], asked)
        self.assertIn("รายละเอียดรายบัญชี", asked["detail"])


class AskedOfTheExpenseAudit(unittest.TestCase):
    """A second agent, a workbook: its claims in the order a person asked for — the same claims, the
    same results, each still naming its row in the file it came from."""

    @classmethod
    def setUpClass(cls):
        from agent_market.packages import expense_audit as EA
        from agent_market.tests import expensekit as K

        cls.EA = EA
        pkg = os.path.dirname(EA.__file__)
        with open(os.path.join(pkg, "self-test.json"), encoding="utf-8") as f:
            st = json.load(f)
        with open(os.path.join(pkg, "agent.json"), encoding="utf-8") as f:
            cls.manifest = json.load(f)
        res = K.run_case({"id": "self_test", "expect": {}}, defaults=dict(cls.manifest["install"]["defaults"]),
                         files=[os.path.join(pkg, p) for p in st["files"]])
        cls.computed = res["computed"]

    def _render(self, apply):
        out = tempfile.mkdtemp()
        req = {"formats": ["xlsx"], "names": {"xlsx": "audit_v1.xlsx"}, "run": {"id": "t", "date": "2026-09-25", "agent_version": "t"}}
        if apply is not None:
            req["instructions"] = {"apply": apply}
        return self.EA.render(self.computed, req, in_dir=out, out_dir=out), os.path.join(out, "audit_v1.xlsx")

    def _claims(self, path):
        from openpyxl import load_workbook

        from agent_market.deliverables.reread import table
        from agent_market.packages.expense_audit.scripts.workbook import H_CLAIMS, S_CLAIMS, parts

        return table(parts(load_workbook(path), S_CLAIMS), H_CLAIMS)

    def test_the_claims_run_from_the_largest_amount_and_nothing_else_changes(self):
        plain, plain_path = self._render(None)
        res, path = self._render(["sort_by_amount"])
        self.assertTrue(plain["passed"] and res["passed"], json.dumps(res["artifacts"][0]["quality"], ensure_ascii=False)[:600])
        self.assertEqual(res["asked"], {"applied": ["sort_by_amount"], "refused": []})
        self.assertEqual(plain["asked"], {"applied": [], "refused": []})
        rows, before = self._claims(path), self._claims(plain_path)
        amounts = [r[9] for r in rows]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        self.assertNotEqual([r[1] for r in rows], [r[1] for r in before])  # (the sample is not in that order already)
        # the same claims with the same figures, results and rows of the source file — only the order
        key = lambda r: str(r[1])
        self.assertEqual(sorted(([r[1], r[2], r[9], r[11], r[15]] for r in rows), key=key),
                         sorted(([r[1], r[2], r[9], r[11], r[15]] for r in before), key=key))
        checks = {c["name"]: c for c in res["artifacts"][0]["quality"]["checks"]}
        self.assertTrue(checks["xlsx.asked"]["ok"], checks["xlsx.asked"])
        self.assertNotIn("xlsx.asked", {c["name"] for c in plain["artifacts"][0]["quality"]["checks"]})

    def test_a_workbook_left_in_the_files_order_fails_a_round_that_asked_for_the_amount(self):
        _, plain_path = self._render(None)
        view, applied = self.EA._as_asked(self.computed, {"instructions": {"apply": ["sort_by_amount"]}})
        self.assertEqual(applied, ["sort_by_amount"])
        got = self.EA._asked_check(plain_path, view, applied)
        self.assertFalse(got["ok"], got)
        self.assertIn("ไม่ได้เรียงตามนั้น", got["detail"])

    def test_its_manifest_declares_what_its_renderer_applies(self):
        spec = self.manifest["instructions"]
        self.assertEqual([d["id"] for d in spec["can"]], list(self.EA.DIRECTIVES))
        for d in spec["can"] + spec["cannot"]:
            self.assertIn(d["label"], self.manifest["en"])
        for d in spec["cannot"]:
            self.assertNotIn(d["id"], self.EA.DIRECTIVES)
            self.assertIn(d["why"], self.manifest["en"])


class TheManifestSaysWhatCanBeAsked(unittest.TestCase):
    """What the model may pick from is the package's own list, and every id in it is one the renderer knows."""

    def test_every_directive_declared_is_one_the_renderer_applies(self):
        with open(os.path.join(PKG, "agent.json"), encoding="utf-8") as f:
            m = json.load(f)
        spec = m["instructions"]
        self.assertEqual(sorted(d["id"] for d in spec["can"]), sorted(content.DIRECTIVES))
        for d in spec["can"] + spec["cannot"]:
            self.assertTrue(d["label"].strip(), d)
            self.assertIn(d["label"], m["en"], d["label"])  # said in English too
        for d in spec["cannot"]:
            self.assertNotIn(d["id"], content.DIRECTIVES)
            self.assertTrue(d["why"].strip())
            self.assertIn(d["why"], m["en"])
        ordered = next(d for d in spec["can"] if d["id"] == "sort_by_variance")
        self.assertTrue(ordered.get("unless"), "what is said when a round has nothing to compare")
        self.assertIn(ordered["unless"], m["en"])


if __name__ == "__main__":
    unittest.main()
