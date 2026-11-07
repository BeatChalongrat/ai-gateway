"""AM22 Part 2 — the customer's own form, for the bank reconciliation, the expense audit and the PO matching.

What is asserted is read from the delivered file, opened again (CARD-RULES §6c: the refusal case is the test):

  * each form (Excel and Word, and the expense claim's PDF) comes back incomplete, as it must — it has blanks nothing
    in the round can fill (a reviewer, an approver, a department, a VAT line the round did not compute) — with every
    one of them named on its face under “ช่องที่ยังกรอกไม่ได้”, and every value the round has in its blank;
  * what the round has no concept of (an exchange rate, a cash advance, a credit-note section with no credit note) is
    taken off the face — the Excel row hidden, the Word row removed — recorded in the run's JSON and never on the
    gap list (§2.2); the package says which, the filler never guesses from emptiness;
  * the refusals (§2.4): two labels that could take one balance are both offered and neither is proposed by code; a
    blank inside a merged range is refused by name and a blank that is a merged range is filled at its top-left and
    read again as still one range; the form's own formula is refused and unchanged; a locked blank of a protected
    sheet is refused; a new version whose addresses are the same and whose labels moved is "moved", not "kept"; and
    a round with nothing to put in a form makes no file and says why.

The forms are tests/am22forms.py's, committed under each package's evals/fixtures/forms (and checked to be the kit's).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest

from openpyxl import load_workbook

from agent_market.deliverables import template_fill as tf
from agent_market.deliverables import template_out as to
from agent_market.engine import step, words
from agent_market.tests import am22forms as K

READS = ("rows", "lines", "pdf")
GAPS_TITLE = "ช่องที่ยังกรอกไม่ได้"


def computed_of(package: str) -> dict:
    """The round each form is filled from: the package's sample (bank) or its first eval case."""
    if package == "bank_reconciliation":
        from agent_market.tests import bankkit, samplekit

        st = samplekit.self_test(package)
        base = samplekit.package_dir(package)
        files = [(os.path.join(base, rel), os.path.basename(rel)) for rel in st["files"]]
        return bankkit.run_files(files, defaults=samplekit.self_test_defaults(package), message=st.get("message", ""))["computed"]
    from agent_market.tests import expensekit, pokit

    kit = {"expense_audit": expensekit, "po_matching": pokit}[package]
    cases = kit.load_cases()
    cases = cases["cases"] if isinstance(cases, dict) else cases
    return kit.run_case(cases[0])["computed"]


class FormCase(unittest.TestCase):
    SPEC: dict = {}

    @classmethod
    def setUpClass(cls):
        if not cls.SPEC:
            raise unittest.SkipTest("base")
        cls.pkg = cls.SPEC["package"]
        cls.tmp = tempfile.mkdtemp()
        cls.forms = {os.path.basename(p): p for p in K.write_all(os.path.join(cls.tmp, "forms")) if os.path.basename(p).startswith(cls.pkg)}
        cls.computed = computed_of(cls.pkg)
        cls.W = words.of(f"agent_market.packages.{cls.pkg}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def slots(self, name: str) -> list[dict]:
        return to.slots(tf.inspect(self.forms[name], reads=READS))

    def fill(self, name: str, *, mapping: dict | None = None, slots: list | None = None, computed: dict | None = None,
             template_file: str | None = None):
        """The render step with the form as the round's own template — the way the runner runs it."""
        ext = os.path.splitext(name)[1]
        d = tempfile.mkdtemp(dir=self.tmp)
        inp, out = os.path.join(d, "in"), os.path.join(d, "out")
        os.makedirs(inp)
        os.makedirs(out)
        sl = slots if slots is not None else self.slots(name)
        m = mapping if mapping is not None else K.mapping_for(self.SPEC, sl, pdf=ext == ".pdf")
        with open(os.path.join(inp, "computed.json"), "w", encoding="utf-8") as f:
            json.dump(computed or self.computed, f, ensure_ascii=False)
        shutil.copy(self.forms[template_file or name], os.path.join(inp, "template" + ext))
        step.JOB_IN, step.JOB_OUT = inp, out
        res = step.run({"op": "render", "package": self.pkg, "computed": "computed.json", "formats": [], "names": {},
                        "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"},
                        "run": {"preparer": "สมชาย ใจดี", "organisation": "บริษัท ทดสอบ จำกัด", "date": "2026-10-17"},
                        "template": {"input": "template" + ext, "name": "filled" + ext, "saved": {"slots": sl, "mapping": m}}})
        self.assertTrue(res["ok"], res)
        arts = [a for a in res["artifacts"] if a["kind"] == "template"]
        return (arts[0] if arts else None), os.path.join(out, "filled" + ext), res, sl, m

    def checks(self, art: dict) -> dict:
        return {c["name"]: c for c in art["quality"]["checks"]}

    def never(self) -> set:
        return {label for g in ("beside", "money", "sign") for label, field in self.SPEC[g] if field is None}

    # ── the form comes back incomplete, with its gaps on its face, every value the round has in its blank ──
    def _incomplete(self, name: str):
        art, path, res, sl, m = self.fill(name)
        self.assertIsNotNone(art, res)
        checks = self.checks(art)
        self.assertFalse(art["quality"]["passed"], "a form with blanks nothing can fill is a draft")
        self.assertFalse(checks["template.every_slot"]["ok"])
        # every blank nothing can fill is named, on the delivered file's own face
        named = {u["label"] for u in art["quality"]["unfilled"]}
        kind = os.path.splitext(name)[1][1:]
        never = self.never() if kind != "pdf" else {label for _, label, f in K.PDF_FIELDS if f is None}
        for label in never:
            self.assertIn(label, named, f"{name}: {label} is named as a gap")
        self.assertTrue(checks["template.gaps_printed"]["ok"], checks["template.gaps_printed"])
        # what the round has, is in its blank
        values = self.pkg_module().template_values(self.computed, {"run": {"date": "2026-10-17", "preparer": "สมชาย ใจดี"}})
        off = {o["key"] for o in art["meta"]["not_applicable"]}
        tvals = to.template_values(sl, {k: v for k, v in m.items() if k.split(".", 1)[0] not in off}, values)
        held = 0
        for s in sl:
            if s["kind"] in ("target",) and s["key"] in tvals and str(tvals[s["key"]]).strip():
                if s["key"].startswith("line@"):
                    # a drawn line after its label: read by its label (a row taken off above it moved its paragraph)
                    from docx import Document

                    paras = [p.text for p in Document(path).paragraphs if p.text.startswith(s["label"])]
                    self.assertTrue(any(str(tvals[s["key"]]) in p for p in paras), f"{name}: {s['label']}")
                else:
                    self.assertTrue(tf.holds(path, kind, s, tvals[s["key"]]), f"{name}: {s['label']}")
                held += 1
        self.assertGreater(held, 4)
        return art, path, sl, m

    def pkg_module(self):
        import importlib

        return importlib.import_module(f"agent_market.packages.{self.pkg}")

    def test_the_excel_form_comes_back_incomplete_with_its_gaps_on_its_face_and_its_own_formula_kept(self):
        art, path, sl, m = self._incomplete(f"{self.pkg}_form.xlsx")
        checks = self.checks(art)
        self.assertTrue(checks["template.formulas_kept"]["ok"])
        self.assertTrue(art["meta"]["needs_recalc"], "the form's own formula is recalculated by the converter")
        wb = load_workbook(path)
        ws = wb[self.SPEC["sheet"]]
        self.assertTrue(str(ws[K.formula_ref(self.SPEC).split("!")[1]].value).startswith("="), "the formula is not written over")
        # the merged header and the merged blanks are still one range each
        self.assertIn("B1:F1", [r.coord for r in ws.merged_cells.ranges])
        for s in sl:
            if s.get("merged") and s["key"] in m:
                self.assertIn(s["merged"], [r.coord for r in ws.merged_cells.ranges], s["label"])
        # the list's rows hold the round's lines, as many as the form has rows for
        self.assertIn(wb.sheetnames[-1], (GAPS_TITLE, "Fields that could not be filled"))

    def test_the_word_form_comes_back_incomplete_with_its_gaps_on_its_face(self):
        self._incomplete(f"{self.pkg}_form.docx")

    def test_what_the_round_has_no_concept_of_is_off_the_face_recorded_and_not_a_gap(self):
        gaps = self.pkg_module().template_gaps(self.computed)
        na = gaps["not_applicable"]
        self.assertTrue(na, "each package names at least one field this round has no concept of")
        for name in (f"{self.pkg}_form.xlsx", f"{self.pkg}_form.docx"):
            art, path, res, sl, m = self.fill(name)
            meta = art["meta"]["not_applicable"]
            self.assertTrue(meta, name)
            self.assertEqual({o["field"] for o in meta}, {f for f in na if f in set(v.split(".", 1)[0] for v in m.values())})
            for o in meta:
                self.assertEqual(o["why"], na[o["field"]])
                self.assertIn(o["done"], ("hidden", "removed"), (name, o))
            # not on the gap list, on the card or on the face
            named = {u["label"] for u in art["quality"]["unfilled"]}
            for o in meta:
                self.assertNotIn(o["label"], named)
            self.assertTrue(self.checks(art)["template.not_applicable"]["ok"], self.checks(art)["template.not_applicable"])
            if name.endswith(".xlsx"):
                ws = load_workbook(path)[self.SPEC["sheet"]]
                for o in meta:
                    if "!" in o["key"]:
                        row = int("".join(ch for ch in o["key"].split("!")[1] if ch.isdigit()))
                        self.assertTrue(ws.row_dimensions[row].hidden, o)
            else:
                from docx import Document

                text = "\n".join(c.text for t in Document(path).tables for r in t.rows for c in r.cells)
                for o in meta:
                    self.assertNotIn(o["label"].split(" | ")[0], text, o)

    # ── the refusals (§2.4) ──
    def test_two_labels_that_could_take_one_value_are_both_offered_and_neither_is_picked_by_code(self):
        sl = self.slots(f"{self.pkg}_twin.xlsx")
        spec = _templates(self.pkg)
        twins = [s for s in sl if s["kind"] == "target" and s.get("required") and s["label"] in _twin_labels(self.SPEC)]
        self.assertEqual(len(twins), 2, [s["label"] for s in sl])
        proposal = to.propose(sl, spec)
        for s in twins:
            self.assertNotIn(s["key"], proposal, "code proposes identity only: the model proposes, a person confirms")
        self.assertTrue(spec["fields"][self.SPEC["money"][0][1]].get("once"), "the balance is a field one slot takes")

    def test_a_blank_inside_a_merged_range_is_refused_by_name_and_a_blank_that_is_one_is_filled_and_kept_whole(self):
        name = f"{self.pkg}_merged.xlsx"
        sl = self.slots(name)
        inside = [s for s in sl if s.get("reason") == "merged_cell"]
        self.assertEqual(len(inside), 1)
        self.assertFalse(inside[0]["fillable"])
        art, path, res, _, m = self.fill(name, slots=sl)
        un = {u["label"]: u for u in art["quality"]["unfilled"]}
        self.assertIn(inside[0]["label"], un)
        self.assertEqual(un[inside[0]["label"]]["reason"], "merged_cell")
        whole = next(s for s in sl if s.get("merged") == "C3:E4")
        if whole["key"] in m:
            ws = load_workbook(path)[self.SPEC["sheet"]]
            self.assertIn("C3:E4", [r.coord for r in ws.merged_cells.ranges])
            self.assertTrue(str(ws["C3"].value or "").strip())

    def test_the_forms_own_formula_is_refused_and_left_as_it_was(self):
        name = f"{self.pkg}_form.xlsx"
        sl = self.slots(name)
        ref = K.formula_ref(self.SPEC)
        self.assertNotIn(ref, {s["key"] for s in sl}, "a formula cell is not offered as a blank")
        field = self.SPEC["money"][0][1]
        sl2 = sl + [{"key": ref, "kind": "target", "label": "formula", "where": ref, "fillable": True, "reason": None}]
        m = K.mapping_for(self.SPEC, sl)
        m[ref] = field
        art, path, res, _, _ = self.fill(name, slots=sl2, mapping=m)
        self.assertFalse(art["quality"]["passed"])
        self.assertEqual(art["meta"]["refused"].get(ref), "formula_cell")
        self.assertTrue(self.checks(art)["template.formulas_kept"]["ok"])
        ws = load_workbook(path)[self.SPEC["sheet"]]
        self.assertTrue(str(ws[ref.split("!")[1]].value).startswith("="))

    def test_a_locked_blank_of_a_protected_sheet_is_refused_and_named(self):
        self.assertEqual(self.slots(f"{self.pkg}_locked.xlsx"), [], "a sheet whose blanks are all locked offers none")
        # the saved mapping of the form, used on a new version of it that is protected: every write refused, named
        sl = self.slots(f"{self.pkg}_form.xlsx")
        art, path, res, _, m = self.fill(f"{self.pkg}_form.xlsx", slots=sl, template_file=f"{self.pkg}_locked.xlsx")
        self.assertFalse(art["quality"]["passed"])
        self.assertTrue(set(art["meta"]["refused"].values()) <= {"protected_sheet"}, art["meta"]["refused"])
        self.assertTrue(art["meta"]["refused"])
        named = {u["reason"] for u in art["quality"]["unfilled"]}
        self.assertIn("protected_sheet", named)

    def test_a_new_version_whose_addresses_are_the_same_and_whose_labels_moved_is_moved_not_kept(self):
        old = self.slots(f"{self.pkg}_form.xlsx")
        new = self.slots(f"{self.pkg}_moved.xlsx")
        saved = {"slots": old, "mapping": K.mapping_for(self.SPEC, old)}
        dif = to.diff(saved, new)
        swapped = {s["key"] for s in old} & {s["key"] for s in new}
        moved = set(dif["moved"])
        self.assertEqual(len(moved), 2, dif)
        self.assertTrue(moved <= swapped)
        carried = to.carry_over(saved, new, {})
        for k in moved:
            self.assertNotIn(k, carried, "a moved slot's old mapping is not carried into the cell that now means something else")
        self.assertTrue(set(dif["kept"]) and not (set(dif["kept"]) & moved))

    def test_a_word_forms_saved_mapping_is_carried_to_its_next_version(self):
        # its blanks' keys have a dot in them ("table0.r1c1"): split at the first dot, nothing was ever carried
        sl = self.slots(f"{self.pkg}_form.docx")
        saved = {"slots": sl, "mapping": K.mapping_for(self.SPEC, sl)}
        carried = to.carry_over(saved, copy.deepcopy(sl), {})
        self.assertEqual(carried, saved["mapping"])
        self.assertTrue(any(k.startswith("table0.") for k in carried))

    def test_a_round_with_nothing_to_put_in_a_form_makes_no_file_and_says_why(self):
        c = copy.deepcopy(self.computed)
        if self.pkg == "bank_reconciliation":
            c["statement"]["bank"]["balance"] = c["statement"]["book"]["balance"] = None
        elif self.pkg == "expense_audit":
            c["claims"] = []
        else:
            c["invoices"] = []
        art, path, res, _, _ = self.fill(f"{self.pkg}_form.xlsx", computed=c)
        self.assertIsNone(art)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(res["template_not_made"]["why"], self.W("form.not_made"))


def _templates(pkg: str) -> dict:
    with open(os.path.join(K.PKGS, pkg, "agent.json"), encoding="utf-8") as f:
        return json.load(f)["templates"]


def _twin_labels(spec: dict) -> set:
    if spec is K.BANK:
        return {"ยอดตามธนาคาร", "ยอดตามบัญชี"}
    first = spec["money"][0][0]
    return {first, first + " (ตามระบบ)"}


class BankForm(FormCase):
    SPEC = K.BANK

    def test_an_item_nobody_classified_has_a_blank_side_and_no_amount_guessed(self):
        # every eval case was run through the form (AM22 probe): an unclassified item has no side and no effect yet
        c = copy.deepcopy(self.computed)
        c["items"].append({"id": "x1", "side": None, "type": "unclassified", "type_label": "", "date": None, "ref": "",
                           "description": "?", "amount": "120.00", "effect": None})
        values = self.pkg_module().template_values(c, {"run": {}})
        self.assertEqual(values["items"][-1]["side"], "")
        self.assertEqual(values["items"][-1]["amount"], "120.00")


class ExpenseForm(FormCase):
    SPEC = K.EXPENSE

    def test_the_pdf_claim_takes_its_values_in_its_fields_and_its_gaps_on_a_last_page(self):
        from agent_market.deliverables import pdf_form

        art, path, res, sl, m = self.fill("expense_audit_form.pdf")
        self.assertFalse(art["quality"]["passed"])
        self.assertTrue(self.checks(art)["template.gaps_printed"]["ok"])
        vals = pdf_form.field_values(path)
        values = self.pkg_module().template_values(self.computed, {"run": {"date": "2026-10-17"}})
        self.assertEqual(vals["pdf:f5"], values["claimed_thb"])
        self.assertEqual(vals["pdf:f6"], values["pass_thb"])
        # the advance the agent does not see: left empty (a PDF cannot lose a field), recorded, not on the gap list
        na = {o["field"]: o for o in art["meta"]["not_applicable"]}
        self.assertEqual(na["advance_cleared"]["done"], "left_blank")
        self.assertFalse(vals["pdf:f8"].strip())
        self.assertNotIn("เงินทดรองจ่ายที่หักล้าง (บาท)", {u["label"] for u in art["quality"]["unfilled"]})
        for label in ("แผนก", "ภาษีมูลค่าเพิ่ม 7% (บาท)", "ผู้อนุมัติ"):
            self.assertIn(label, {u["label"] for u in art["quality"]["unfilled"]})


class POForm(FormCase):
    SPEC = K.PO

    def test_a_credit_note_section_is_taken_off_when_the_round_has_none_and_filled_when_it_has_one(self):
        self.assertFalse(self.computed.get("notes"))
        art, path, res, sl, m = self.fill("po_matching_form.docx")
        meta = {o["field"]: o for o in art["meta"]["not_applicable"]}
        self.assertEqual(meta["credit_notes"]["done"], "removed")
        from docx import Document

        heads = [c.text for t in Document(path).tables for r in t.rows for c in r.cells]
        self.assertNotIn("เลขที่ใบลดหนี้", heads)
        # a round with a credit note: the section is the round's, and it is filled
        c = copy.deepcopy(self.computed)
        c["notes"] = [{"number": "CN6909-0001", "date": "2026-09-20", "supplier": "บริษัท ทดสอบ จำกัด", "total": "-1070.00"}]
        art, path, res, sl, m = self.fill("po_matching_form.docx", computed=c)
        self.assertEqual(art["meta"]["not_applicable"], [])
        text = "\n".join(c.text for t in Document(path).tables for r in t.rows for c in r.cells)
        self.assertIn("CN6909-0001", text)


class TheCommittedFormsAreTheKits(unittest.TestCase):
    def test_each_committed_form_reads_as_a_freshly_built_one(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        fresh = {os.path.basename(p): p for p in K.write_all(tmp)}
        n = 0
        for pkg, spec in K.SPECS.items():
            for name in K.forms(spec):
                committed = os.path.join(K.folder(pkg), name)
                self.assertTrue(os.path.exists(committed), committed)
                strip = lambda sl: [{k: v for k, v in s.items() if k != "context"} for s in sl]  # noqa: E731
                self.assertEqual(strip(to.slots(tf.inspect(committed, reads=READS))), strip(to.slots(tf.inspect(fresh[name], reads=READS))), name)
                n += 1
        self.assertEqual(n, 19)


if __name__ == "__main__":
    unittest.main()


class ThePOPapersNeverWrittenWords(unittest.TestCase):
    def test_the_words_the_gate_refuses_are_the_words_the_manifest_declares(self):
        import json as _json
        from agent_market.packages.po_matching.scripts import match
        never = match.never_written()
        self.assertEqual(set(never), {"อนุมัติแล้ว", "จ่ายแล้ว", "approved", "paid"})
        here = os.path.dirname(os.path.abspath(__file__))
        manifest = _json.load(open(os.path.join(here, "..", "packages", "po_matching", "agent.json"), encoding="utf-8"))
        self.assertEqual(sorted(manifest["narrative_never"]), sorted(never),
                         "the runner holds a model's answer to the manifest's words; the gate refuses the file's")
