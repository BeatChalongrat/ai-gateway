"""The reviewer's sign-off on the face of the files (AM3, deliverables/signoff.py).

Stamps the REAL files each package delivers from its own sample data (the self-test's files):
the financial report's .docx, .pptx and .xlsx, the invoice register and the expense audit
workbook. Runs inside the sandbox image.
"""

import os
import re
import shutil
import tempfile
import unittest
import zipfile

from agent_market.deliverables import content, quality, signoff
from agent_market.deliverables.theme import resolve
from agent_market.engine import step
from agent_market.tests import samplekit

REVIEW = {"reviewer": "สมหญิง ใจดี", "reviewer_email": "somying@example.co.th", "at_label": "28 ก.ย. 2569 10:15 น.",
          "note": "ตรวจยอดกับงบทดลองแล้ว ตรงกัน", "preparer": "สมชาย รักงาน",
          "agent": "รายงานการเงิน + สไลด์ผู้บริหาร 1.2.0", "run_ref": "a1b2c3d4"}


def _stamp(files: dict[str, str], review: dict, extra: list[dict] | None = None) -> tuple[dict, str]:
    """Run the op the way the runner does: inputs in /job/in, stamped files in /job/out under their own names."""
    base = tempfile.mkdtemp(prefix="am_stamp_")
    inp, out = os.path.join(base, "in"), os.path.join(base, "out")
    os.makedirs(inp)
    os.makedirs(out)
    req_files = []
    for fmt, path in files.items():
        name = os.path.basename(path)
        shutil.copy(path, os.path.join(inp, "in_" + name))
        req_files.append({"name": name, "input": "in_" + name, "format": fmt})
    step.JOB_IN, step.JOB_OUT = inp, out
    res = step.run({"op": "stamp_review", "files": req_files + (extra or []), "review": review})
    return res, out


def _parts(path: str) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _docx_text(path: str) -> str:
    from docx import Document

    doc = Document(path)
    first = next(iter(doc.element.body.iterchildren()))
    return "".join(t.text or "" for t in first.iter("{%s}t" % signoff.W_NS))


class Financial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = samplekit.render_sample("financial_report", formats=["docx", "pptx", "xlsx"])
        cls.res, cls.out = _stamp(cls.sample["paths"], REVIEW,
                                  extra=[{"name": "sample.pdf", "input": "nope.pdf", "format": "pdf"},
                                         {"name": "mine.xlsx", "input": "nope.xlsx", "format": "template"}])
        cls.stamped = {a["format"]: os.path.join(cls.out, a["name"]) for a in cls.res["artifacts"]}

    def test_every_supported_file_is_stamped_under_its_own_name(self):
        self.assertTrue(self.res["ok"])
        self.assertEqual({a["format"] for a in self.res["artifacts"]}, {"docx", "pptx", "xlsx"})
        for a in self.res["artifacts"]:
            self.assertTrue(a["stamped"])
            self.assertEqual(a["name"], os.path.basename(self.sample["paths"][a["format"]]))
            self.assertEqual(a["size"], os.path.getsize(self.stamped[a["format"]]))

    def test_unsupported_files_are_skipped_with_a_reason_never_an_error(self):
        skipped = {s["name"]: s["reason"] for s in self.res["skipped"]}
        self.assertEqual(set(skipped), {"sample.pdf", "mine.xlsx"})
        self.assertIn(".docx", skipped["sample.pdf"])
        self.assertTrue(all(r for r in skipped.values()))

    def test_docx_starts_with_the_stamp(self):
        text = _docx_text(self.stamped["docx"])
        for want in ("ตรวจทานแล้ว", "สมหญิง ใจดี", "28 ก.ย. 2569 10:15 น.", "สมชาย รักงาน", "a1b2c3d4",
                     "หมายเหตุ: ตรวจยอดกับงบทดลองแล้ว ตรงกัน"):
            self.assertIn(want, text)
        a = next(a for a in self.res["artifacts"] if a["format"] == "docx")
        self.assertTrue(a["checks"]["first_element_is_stamp"])
        self.assertEqual(a["checks"]["font"], "Sarabun")

    def test_stamped_docx_and_pptx_still_pass_the_quality_gate(self):
        req = {"theme": {"id": "navy", "company": ""}, "assumptions": [],
               "run": {"id": "sample", "date": samplekit.TODAY.isoformat(), "agent_version": "x"},
               "model_info": {"model": None, "self_hosted": None}}
        theme = resolve(req["theme"])
        model = content.build(self.sample["computed"], {**req, "theme": theme})
        checks = quality.check_docx(self.stamped["docx"], model, theme, req)
        self.assertTrue(all(c["ok"] for c in checks), [c for c in checks if not c["ok"]])
        deck = next(a for a in self.sample["render"]["artifacts"] if a["format"] == "pptx")
        pchecks = quality.check_pptx(self.stamped["pptx"], {"chart_bindings": deck["chart_bindings"]},
                                     model["computed"], theme)
        self.assertTrue(all(c["ok"] for c in pchecks), [c for c in pchecks if not c["ok"]])

    def test_pptx_stamp_is_inside_slide_one_below_its_text(self):
        from pptx import Presentation

        prs = Presentation(self.stamped["pptx"])
        box = [sh for sh in prs.slides[0].shapes if sh.name == signoff.PPTX_SHAPE]
        self.assertEqual(len(box), 1)
        b = box[0]
        self.assertGreaterEqual(b.left, 0)
        self.assertGreaterEqual(b.top, 0)
        self.assertLessEqual(b.left + b.width, prs.slide_width)
        self.assertLessEqual(b.top + b.height, prs.slide_height)
        self.assertIn("ผู้ตรวจทาน: สมหญิง ใจดี", b.text_frame.text)
        a = next(a for a in self.res["artifacts"] if a["format"] == "pptx")
        self.assertEqual(a["checks"]["overlaps"], [])
        self.assertTrue(a["checks"]["inside_slide"])
        for s in list(prs.slides)[1:]:
            self.assertFalse([sh for sh in s.shapes if sh.name == signoff.PPTX_SHAPE])

    def test_xlsx_gets_its_own_sheet_and_every_other_part_keeps_its_bytes(self):
        before, after = _parts(self.sample["paths"]["xlsx"]), _parts(self.stamped["xlsx"])
        changed = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml"}
        for name, data in before.items():
            if name not in changed:
                self.assertEqual(after[name], data, name)
        self.assertEqual(set(after) - set(before), {signoff.XLSX_PART})
        import openpyxl

        wb = openpyxl.load_workbook(self.stamped["xlsx"])
        self.assertEqual(wb.sheetnames[-1], "ตรวจทาน")
        self.assertEqual(wb.active.title, "ตรวจทาน")
        cells = " ".join(str(c.value) for row in wb["ตรวจทาน"].iter_rows() for c in row if c.value)
        self.assertIn("สมหญิง ใจดี", cells)
        self.assertIn("ตัวเลขไม่ได้เปลี่ยน", cells)
        # every original sheet reads the same (formulas as written)
        orig = openpyxl.load_workbook(self.sample["paths"]["xlsx"])
        for name in orig.sheetnames:
            self.assertEqual([[c.value for c in r] for r in orig[name].iter_rows()],
                             [[c.value for c in r] for r in wb[name].iter_rows()], name)

    def test_restamping_replaces_the_stamp(self):
        again, out = _stamp({f: p for f, p in self.stamped.items()}, {**REVIEW, "reviewer": "สมศักดิ์ ตรวจดี", "note": ""})
        paths = {a["format"]: os.path.join(out, a["name"]) for a in again["artifacts"]}
        text = _docx_text(paths["docx"])
        self.assertIn("สมศักดิ์ ตรวจดี", text)
        self.assertNotIn("หมายเหตุ", text)
        from docx import Document

        body = list(Document(paths["docx"]).element.body.iterchildren())
        self.assertEqual(sum(1 for el in body if signoff._is_stamp(el)), 1)
        from pptx import Presentation

        prs = Presentation(paths["pptx"])
        boxes = [sh for sh in prs.slides[0].shapes if sh.name == signoff.PPTX_SHAPE]
        self.assertEqual(len(boxes), 1)
        self.assertIn("สมศักดิ์ ตรวจดี", boxes[0].text_frame.text)
        import openpyxl

        wb = openpyxl.load_workbook(paths["xlsx"])
        self.assertEqual([n for n in wb.sheetnames if n.startswith("ตรวจทาน")], ["ตรวจทาน"])
        self.assertEqual(_parts(paths["xlsx"])["xl/workbook.xml"], _parts(self.stamped["xlsx"])["xl/workbook.xml"])

    def test_a_signed_file_names_no_organisation(self):
        # AM6: an agent installs at another company; the stamp, the author and the embedded font's key
        # are the same there, so none of them may carry this repository's owner's name
        for fmt, path in self.stamped.items():
            for name, data in _parts(path).items():
                self.assertIsNone(re.search(rb"sran", data, re.I), (fmt, name))

    def test_a_stamp_signed_before_am6_is_replaced_all_the_same(self):
        from docx import Document
        from pptx import Presentation

        base = tempfile.mkdtemp(prefix="am_oldstamp_")
        old = {}
        doc = Document(self.stamped["docx"])
        for el in doc.element.body.iterchildren():
            if signoff._is_stamp(el):
                el.find(f"{{{signoff.W_NS}}}tblPr/{{{signoff.W_NS}}}tblCaption").set(f"{{{signoff.W_NS}}}val", "sran-review-stamp")
        old["docx"] = os.path.join(base, os.path.basename(self.stamped["docx"]))
        doc.save(old["docx"])
        prs = Presentation(self.stamped["pptx"])
        for sh in prs.slides[0].shapes:
            if sh.name == signoff.PPTX_SHAPE:
                sh.name = "SRAN review stamp"
        old["pptx"] = os.path.join(base, os.path.basename(self.stamped["pptx"]))
        prs.save(old["pptx"])
        again, out = _stamp(old, {**REVIEW, "reviewer": "สมศักดิ์ ตรวจดี"})
        paths = {a["format"]: os.path.join(out, a["name"]) for a in again["artifacts"]}
        body = list(Document(paths["docx"]).element.body.iterchildren())
        self.assertEqual(sum(1 for el in body if signoff._is_stamp(el)), 1)
        self.assertIn("สมศักดิ์ ตรวจดี", _docx_text(paths["docx"]))
        names = [sh.name for sh in Presentation(paths["pptx"]).slides[0].shapes]
        self.assertEqual([n for n in names if "stamp" in n.lower()], [signoff.PPTX_SHAPE])


class Registers(unittest.TestCase):
    """The invoice register and the expense audit workbook: the same sheet, nothing else moves."""

    def _check(self, package):
        sample = samplekit.render_sample(package, formats=["xlsx"])
        res, out = _stamp({"xlsx": sample["paths"]["xlsx"]}, REVIEW)
        stamped = os.path.join(out, res["artifacts"][0]["name"])
        before, after = _parts(sample["paths"]["xlsx"]), _parts(stamped)
        for name, data in before.items():
            if name not in ("xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml"):
                self.assertEqual(after[name], data, f"{package}: {name}")
        import openpyxl

        wb = openpyxl.load_workbook(stamped)
        self.assertEqual(wb.active.title, "ตรวจทาน")

    def test_invoice_register(self):
        self._check("invoice_processing")

    def test_expense_audit_workbook(self):
        self._check("expense_audit")


class WorkbookShapes(unittest.TestCase):
    """Workbooks Excel wrote (a selected tab, cached formula values, a sheet-scoped name)."""

    def _excel_like(self) -> str:
        sample = samplekit.render_sample("invoice_processing", formats=["xlsx"])
        src = sample["paths"]["xlsx"]
        parts = _parts(src)
        wb = parts["xl/workbook.xml"].decode()
        wb = wb.replace("</sheets>", "</sheets><definedNames><definedName name=\"Total\" localSheetId=\"0\">"
                                     "'สรุป'!$B$2</definedName></definedNames>", 1) if "<definedNames>" not in wb else wb
        parts["xl/workbook.xml"] = wb.encode()
        s1 = parts["xl/worksheets/sheet1.xml"].decode()
        s1 = re.sub(r"<sheetView\b", '<sheetView tabSelected="1"', s1, count=1)
        # a formula cell with its cached value, as Excel writes it
        s1 = s1.replace("</sheetData>", '<row r="999"><c r="A999"><f>1+1</f><v>2</v></c></row></sheetData>', 1)
        parts["xl/worksheets/sheet1.xml"] = s1.encode()
        path = os.path.join(tempfile.mkdtemp(), "excel_like.xlsx")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for n, d in parts.items():
                z.writestr(n, d)
        return path

    def test_selected_tab_moves_to_the_stamp_and_cached_values_stay(self):
        src = self._excel_like()
        res, out = _stamp({"xlsx": src}, REVIEW)
        stamped = os.path.join(out, res["artifacts"][0]["name"])
        after = _parts(stamped)
        s1 = after["xl/worksheets/sheet1.xml"].decode()
        self.assertNotIn("tabSelected", s1)
        self.assertIn("<f>1+1</f><v>2</v>", s1)
        # nothing else in that sheet moved: only the attribute went
        self.assertEqual(s1, _parts(src)["xl/worksheets/sheet1.xml"].decode().replace(' tabSelected="1"', "", 1))
        self.assertIn('tabSelected="1"', after[signoff.XLSX_PART].decode())
        wb = after["xl/workbook.xml"].decode()
        # appended last: no sheet index moved, so the sheet-scoped name still points at sheet 0
        self.assertIn('localSheetId="0"', wb)
        n_sheets = len(re.findall(r"<sheet\b", wb))
        self.assertIn(f'activeTab="{n_sheets - 1}"', wb)
        self.assertEqual(res["artifacts"][0]["checks"]["unselected"], ["สรุป"])


class Lines(unittest.TestCase):
    def test_the_note_line_only_when_there_is_a_note(self):
        self.assertEqual(len(signoff.lines(REVIEW)), 3)
        self.assertEqual(len(signoff.lines({**REVIEW, "note": "  "})), 2)
        self.assertTrue(signoff.lines({})[0].startswith("ตรวจทานแล้ว"))


if __name__ == "__main__":
    unittest.main()
