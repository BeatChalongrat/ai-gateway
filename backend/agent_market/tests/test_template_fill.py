"""AM2 Phase E — filling a customer's own Word / Excel / PowerPoint template.

1. Every expectation in fixtures/templates/expected.json, except the
   LibreOffice-only ones ("recalculated" and the "pdf" section: the
   converter service recalculates and renders).
2. The new behaviour of the shared scripts: excel_executor.py writes by
   defined name and refuses formula / protected / merged cells;
   docx_generator.py fills split markers, headers, footers, tables,
   repeating rows and plain-text content controls, and reports the rest.
3. What those scripts did before is unchanged: coordinate writes (over a
   formula too), write_table, merges, charts, replace_text. These pass on
   the code before this change as well — that is the point.

Runs inside the sandbox image (the lane mounts the checkout's scripts at
/sandbox/scripts). template_fill is imported inside the tests, so the
regression tests run even where it cannot be imported.
"""

import hashlib
import json
import os
import tempfile
import unittest
import zipfile

import openpyxl
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree
from openpyxl.styles import Protection
from openpyxl.workbook.defined_name import DefinedName
from pptx import Presentation

import docx_generator as dg
import excel_executor as ex

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(os.path.dirname(HERE), "fixtures", "templates")
with open(os.path.join(TPL, "expected.json"), encoding="utf-8") as _f:
    EXP = json.load(_f)
VALUES = EXP["values"]
T = EXP["templates"]
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"


def _tf():
    from agent_market.deliverables import template_fill

    return template_fill


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _xml(path, part):
    with zipfile.ZipFile(path) as z:
        return z.read(part).decode("utf-8")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ─── 1. expected.json ──────────────────────────────────────────
class TemplateExpectations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tf = _tf()
        cls.tmp = tempfile.mkdtemp()
        cls.src_sha = {n: _sha(os.path.join(TPL, n)) for n in T}
        cls.ins, cls.rep, cls.out = {}, {}, {}
        for name, t in T.items():
            src = os.path.join(TPL, name)
            cls.out[name] = os.path.join(cls.tmp, name)
            cls.ins[name] = cls.tf.inspect(src)
            cls.rep[name] = cls.tf.fill(src, cls.out[name], t.get("fill", VALUES), t.get("mapping"))

    def test_templates_themselves_are_not_changed(self):
        self.assertEqual({n: _sha(os.path.join(TPL, n)) for n in T}, self.src_sha)

    def test_inspect_finds_what_each_template_asks_for(self):
        for name in ("docx_placeholders.docx", "docx_repeating_rows.docx", "pptx_theme_chart.pptx"):
            self.assertEqual(sorted(self.ins[name]["markers"]), sorted(T[name]["markers"]), name)
            self.assertTrue(self.ins[name]["has_markers"], name)
        self.assertEqual(self.ins["docx_repeating_rows.docx"]["tables"][0]["repeat_row"], {"row": 1, "list": "line"})
        ctl = self.ins["docx_content_controls.docx"]["controls"]
        self.assertEqual([{"tag": c["tag"], "type": c["type"]} for c in ctl], T["docx_content_controls.docx"]["controls"])
        self.assertEqual([c["tag"] for c in ctl if c["fillable"]], ["CompanyName", "TotalAmount"])
        names = {n["key"]: n for n in self.ins["xlsx_named_ranges.xlsx"]["names"]}
        for key, where in T["xlsx_named_ranges.xlsx"]["names"].items():
            self.assertEqual(names[key]["ref"], where.split(" ")[0], key)
        self.assertEqual({k: n["reason"] for k, n in names.items() if not n["fillable"]},
                         T["xlsx_named_ranges.xlsx"]["expect"]["refused"])
        for name in ("docx_no_markers.docx", "xlsx_no_markers.xlsx"):
            self.assertFalse(self.ins[name]["has_markers"], name)
            self.assertEqual(self.ins[name]["targets"], T[name]["expect"]["targets"], name)

    def test_docx_placeholders(self):
        name = "docx_placeholders.docx"
        e, rep, out = T[name]["expect"], self.rep[name], self.out[name]
        self.assertEqual(sorted(rep["filled"]), sorted(e["filled"]))
        self.assertEqual(rep["not_filled_explained"], [])
        self.assertFalse(rep["mapping_screen"])
        parts = {p: _xml(out, p) for p in ("word/document.xml", "word/header1.xml", "word/footer1.xml")}
        if e["no_braces_left"]:
            for p, x in parts.items():
                self.assertNotIn("{{", x, p)
                self.assertNotIn("}}", x, p)
        doc = Document(out)
        # the marker Word split across two runs: filled, in the first run's formatting
        split = doc.paragraphs[1]
        self.assertEqual(split.text, f"จำนวนเอกสาร {VALUES[e['split_run_marker']]} ฉบับ")
        self.assertIn(VALUES[e["split_run_marker"]], split.runs[0].text)
        self.assertEqual(split.runs[0].font.size.pt, 14)
        self.assertEqual(split.runs[0].font.name, "TH Sarabun New")
        self.assertEqual(doc.paragraphs[0].text,
                         f"สรุปใบแจ้งหนี้ของ {VALUES['company_name']} ประจำงวด {VALUES['period']}")
        header = doc.sections[0].header.paragraphs[0].text
        footer = doc.sections[0].footer.paragraphs[0].text
        for k in e["in_header"]:
            self.assertIn(VALUES[k], header)
        for k in e["in_footer"]:
            self.assertIn(VALUES[k], footer)
        cells = [c.text for r in doc.tables[0].rows for c in r.cells]
        for k in e["in_table"]:
            self.assertIn(VALUES[k], cells)

    def test_docx_repeating_rows(self):
        name = "docx_repeating_rows.docx"
        e, rep, out = T[name]["expect"], self.rep[name], self.out[name]
        self.assertEqual(rep["rows"], {"line": e["repeat_rows"]})
        self.assertEqual(sorted(rep["filled"]), sorted(T[name]["markers"]))
        doc = Document(out)
        rows = [[c.text for c in r.cells] for r in doc.tables[0].rows]
        self.assertEqual(len(rows), 1 + e["repeat_rows"] + 1)
        if e["header_row_kept"]:
            self.assertEqual(rows[0], ["รายการ", "จำนวน", "จำนวนเงิน"])
        self.assertEqual([r[0] for r in rows[1:-1]], e["row_order"])
        self.assertEqual(rows[1:-1], [[l["description"], l["quantity"], l["amount"]] for l in VALUES["lines"]])
        if e["total_row_after_lines"]:
            self.assertEqual(rows[-1], ["รวม", "", VALUES["total_amount"]])
        for tr in doc.tables[0]._tbl.tr_lst[1:-1]:  # the template row's formatting, in every copy
            self.assertEqual(len(tr.xpath(".//w:b[@w:val='0']")), 3)
        if e["no_braces_left"]:
            self.assertNotIn("{{", _xml(out, "word/document.xml"))
        self.assertIn(VALUES["period"], doc.paragraphs[0].text)

    def test_docx_content_controls(self):
        name = "docx_content_controls.docx"
        e, rep, out = T[name]["expect"], self.rep[name], self.out[name]
        doc = Document(out)
        sdts = {dg.content_control_info(s)["tag"]: s for s in doc.element.body.iter(qn("w:sdt"))}
        for tag, text in e["filled_plain_text"].items():
            self.assertEqual("".join(t.text for t in sdts[tag].iter(qn("w:t"))), text, tag)
        for tag in e["placeholder_flag_cleared"]:
            self.assertIsNone(sdts[tag].find(qn("w:sdtPr")).find(qn("w:showingPlcHdr")), tag)
        self.assertEqual(sorted(rep["filled"]), sorted(e["filled_plain_text"]))
        explained = {x["tag"]: x for x in rep["not_filled_explained"] if x.get("tag")}
        self.assertEqual(sorted(explained), sorted(e["not_filled_explained"]))
        types = {c["tag"]: c["type"] for c in T[name]["controls"]}
        for tag, x in explained.items():
            self.assertEqual(x["type"], types[tag])
            self.assertEqual(x["reason"], "not_plain_text")
            self.assertTrue(x["alias"])
            # left exactly as it was: still showing its placeholder
            self.assertIsNotNone(sdts[tag].find(qn("w:sdtPr")).find(qn("w:showingPlcHdr")), tag)
        self.assertEqual(e["fallback"], "detect_and_explain_then_mapping_screen")
        self.assertTrue(rep["mapping_screen"])

    def test_docx_no_markers_goes_to_the_mapping_screen(self):
        name = "docx_no_markers.docx"
        e, rep = T[name]["expect"], self.rep[name]
        self.assertTrue(rep["mapping_screen"])
        self.assertEqual(rep["targets"], e["targets"])
        self.assertEqual(rep["filled"], [])
        self.assertTrue(rep["not_filled_explained"])  # silent_noop_forbidden
        # the mapping screen's answer fills the proposed cells
        out = os.path.join(self.tmp, "mapped.docx")
        keys = ["company_name", "period", "total_amount"]
        mapping = {t["cell"]: k for t, k in zip(e["targets"], keys)}
        rep2 = self.tf.fill(os.path.join(TPL, name), out, VALUES, mapping)
        self.assertEqual(rep2["filled"], [t["cell"] for t in e["targets"]])
        self.assertFalse(rep2["mapping_screen"])
        rows = [[c.text for c in r.cells] for r in Document(out).tables[0].rows]
        self.assertEqual(rows, [[t["label"], VALUES[k]] for t, k in zip(e["targets"], keys)])

    def test_xlsx_named_ranges(self):
        name = "xlsx_named_ranges.xlsx"
        e, rep, out, src = T[name]["expect"], self.rep[name], self.out[name], os.path.join(TPL, name)
        self.assertEqual(sorted(rep["filled"]), sorted(e["written"]))
        self.assertEqual(rep["refused"], e["refused"])
        self.assertTrue(rep["needs_recalc"])
        self.assertEqual(sorted(rep["formulas_kept"]), sorted(e["formulas_kept"]))
        self.assertNotIn("formulas_changed", rep)
        wb0, wb = openpyxl.load_workbook(src), openpyxl.load_workbook(out)
        for ref in e["formulas_kept"]:
            sheet, coord = ref.split("!")
            self.assertEqual(wb[sheet][coord].value, wb0[sheet][coord].value, ref)
            self.assertTrue(str(wb[sheet][coord].value).startswith("="), ref)
        ws = wb["สรุป"]
        fill = T[name]["fill"]
        self.assertEqual(ws["B2"].value, fill["CompanyName"])
        self.assertEqual(ws["B3"].value, fill["Period"])
        self.assertEqual((ws["B5"].value, ws["B6"].value), (fill["Subtotal"], fill["VatAmount"]))
        self.assertEqual(wb["ข้อมูลอ้างอิง"]["B1"].value, wb0["ข้อมูลอ้างอิง"]["B1"].value)  # refused, untouched
        lines = [[ws.cell(row=r, column=c).value for c in (1, 2, 3)] for r in range(10, 14)]
        self.assertEqual([l[0] for l in lines], [l["description"] for l in fill["LinesTable"]])
        if e["lines_written_as_numbers"]:
            for l in lines:
                self.assertTrue(_num(l[1]) and _num(l[2]), l)
            self.assertEqual([l[2] for l in lines], [12850.70, 85600.00, 53500.00, 16050.00])
            self.assertEqual([l[1] for l in lines], [1, 1, 1, 1])
            self.assertAlmostEqual(sum(l[2] for l in lines), e["recalculated"]["สรุป!C14"], places=2)
        for rng in e["merged_kept"]:
            self.assertIn(rng, [str(r) for r in ws.merged_cells.ranges])
        if e["chart_kept"]:
            self.assertEqual(len(ws._charts), 1)
            with zipfile.ZipFile(out) as z:
                self.assertTrue(any(n.startswith("xl/charts/chart") for n in z.namelist()))
        prot, prot0 = wb[e["protection_kept"]].protection, wb0[e["protection_kept"]].protection
        self.assertTrue(prot.sheet)
        self.assertEqual(prot.password, prot0.password)
        self.assertEqual(rep["parts_dropped"], [])
        self.assertFalse(rep["mapping_screen"])

    def test_xlsx_no_markers_goes_to_the_mapping_screen(self):
        name = "xlsx_no_markers.xlsx"
        e, rep = T[name]["expect"], self.rep[name]
        self.assertTrue(rep["mapping_screen"])
        self.assertEqual(rep["targets"], e["targets"])
        self.assertEqual(rep["filled"], [])
        self.assertTrue(rep["not_filled_explained"])  # silent_noop_forbidden
        out = os.path.join(self.tmp, "mapped.xlsx")
        keys = ["company_name", "period", "total_amount"]
        rep2 = self.tf.fill(os.path.join(TPL, name), out, VALUES, {t["cell"]: k for t, k in zip(e["targets"], keys)})
        self.assertEqual(rep2["filled"], [t["cell"] for t in e["targets"]])
        ws = openpyxl.load_workbook(out)["Sheet1"]
        self.assertEqual([ws["B1"].value, ws["B2"].value, ws["B3"].value],
                         [VALUES["company_name"], VALUES["period"], 168000.70])

    def test_pptx_theme_chart(self):
        name = "pptx_theme_chart.pptx"
        e, rep, out, src = T[name]["expect"], self.rep[name], self.out[name], os.path.join(TPL, name)
        self.assertEqual(sorted(rep["filled"]), sorted(e["filled"]))
        self.assertEqual(rep["not_filled_explained"], [])
        slide, slide0 = Presentation(out).slides[0], Presentation(src).slides[0]
        title = slide.shapes.title.text_frame
        self.assertEqual(title.text, f"สรุปค่าใช้จ่าย {VALUES['period']}")
        self.assertEqual(str(title.paragraphs[0].runs[0].font.color.rgb), e["title_color"])
        texts = [s.text_frame.text for s in slide.shapes if s.has_text_frame]
        self.assertIn(f"รวม {VALUES['total_amount']} บาท", texts)
        self.assertFalse(any("{{" in t for t in texts))
        if e["chart_kept"]:
            charts = [s for s in slide.shapes if s.has_chart]
            charts0 = [s for s in slide0.shapes if s.has_chart]
            self.assertEqual(len(charts), 1)
            self.assertEqual(list(charts[0].chart.plots[0].series[0].values),
                             list(charts0[0].chart.plots[0].series[0].values))
            self.assertEqual(list(charts[0].chart.plots[0].categories), list(charts0[0].chart.plots[0].categories))
        with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
            self.assertEqual(a.read("ppt/theme/theme1.xml"), b.read("ppt/theme/theme1.xml"))


# ─── 2a. excel_executor.py: named targets ──────────────────────
def _book(path, protect=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"], ws["B1"] = "label", None
    ws["B3"] = "=SUM(B4:B5)"
    ws.merge_cells("D1:E1")
    ws["D1"] = "merged"
    wb.defined_names["Input"] = DefinedName("Input", attr_text="Data!$B$1")
    wb.defined_names["Total"] = DefinedName("Total", attr_text="Data!$B$3")
    wb.defined_names["Rows"] = DefinedName("Rows", attr_text="Data!$A$4:$B$5")
    wb.defined_names["Inside"] = DefinedName("Inside", attr_text="Data!$E$1")
    wb.defined_names["Rate"] = DefinedName("Rate", attr_text="0.07")
    ws.defined_names["Local"] = DefinedName("Local", attr_text="Data!$C$1")
    other = wb.create_sheet("Other")
    other.defined_names["Local"] = DefinedName("Local", attr_text="Other!$A$1")
    lock = wb.create_sheet("Lock")
    lock["A1"] = 1
    lock["B1"] = None
    lock["B1"].protection = Protection(locked=False)
    lock.protection.sheet = True
    wb.defined_names["LockedCell"] = DefinedName("LockedCell", attr_text="Lock!$A$1")
    wb.defined_names["OpenCell"] = DefinedName("OpenCell", attr_text="Lock!$B$1")
    wb.save(path)
    return path


class ExcelNamedTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = _book(os.path.join(self.tmp, "t.xlsx"))
        self.wb = openpyxl.load_workbook(self.src)

    def test_numbers_given_as_text(self):
        for given, want in (("12,850.70", 12850.7), ("4", 4), ("(1,200.00)", -1200.0), ("-3.5", -3.5),
                            ("๑๒๓", 123), ("168,000.70", 168000.7)):
            self.assertEqual(ex.as_number(given), want, given)
        for ident in ("0015", "0105551234567", "3101234567890", "0812345678", "IV6909-0015", "12,85", "1e5", "7%"):
            self.assertEqual(ex.as_number(ident), ident)

    def test_workbook_and_sheet_scoped_names(self):
        res = ex.write_names(self.wb, {"Input": "12,850.70", "Data!Local": "a", "Other!Local": "b"})
        self.assertEqual(res["refused"], {})
        self.assertEqual(self.wb["Data"]["B1"].value, 12850.7)
        self.assertEqual((self.wb["Data"]["C1"].value, self.wb["Other"]["A1"].value), ("a", "b"))
        # a bare sheet-level name: the sheet given as scope, else ambiguous (two sheets define it)
        self.assertEqual(ex.write_name(self.wb, "Local", "c", "Other")["status"], "written")
        self.assertEqual(self.wb["Other"]["A1"].value, "c")
        self.assertEqual(ex.write_name(self.wb, "Local", "d")["reason"], "ambiguous_name")
        self.assertEqual(ex.write_name(self.wb, "input", "x")["status"], "written")  # names ignore case

    def test_a_range_is_filled_row_by_row(self):
        rows = [{"item": "x", "amount": "1,000.50"}, {"item": "y", "amount": "2"}]
        e = ex.write_name(self.wb, "Rows", rows)
        self.assertEqual(e["status"], "written")
        self.assertEqual(e["ref"], "Data!A4:B5")
        ws = self.wb["Data"]
        self.assertEqual([[ws["A4"].value, ws["B4"].value], [ws["A5"].value, ws["B5"].value]], [["x", 1000.5], ["y", 2]])
        e = ex.write_name(self.wb, "Rows", [{"item": "z", "amount": "3"}], columns=["amount", "item"])
        self.assertEqual((ws["A4"].value, ws["B4"].value), (3, "z"))
        self.assertEqual(ex.write_name(self.wb, "Rows", [["p", 1], ["q", None]])["status"], "written")
        self.assertEqual((ws["A5"].value, ws["B5"].value), ("q", 2))  # None leaves B5 as it was

    def test_refusals_write_nothing(self):
        ws = self.wb["Data"]
        cases = {
            "Total": (1, "formula_cell"),
            "LockedCell": (2, "protected_sheet"),
            "Inside": ("x", "merged_cell"),
            "Nope": ("x", "unknown_name"),
            "Rate": (0.1, "not_a_cell_range"),
            "Missing!A1": ("x", "unknown_sheet"),
        }
        for key, (value, reason) in cases.items():
            e = ex.write_name(self.wb, key, value)
            self.assertEqual((e["status"], e["reason"]), ("refused", reason), key)
        self.assertEqual(ws["B3"].value, "=SUM(B4:B5)")
        self.assertEqual(self.wb["Lock"]["A1"].value, 1)
        too_many = [["a", 1], ["b", 2], ["c", 3]]
        self.assertEqual(ex.write_name(self.wb, "Rows", too_many)["reason"], "too_many_rows")
        self.assertEqual(ex.write_name(self.wb, "Rows", [["a", 1, 2]])["reason"], "too_many_columns")
        self.assertEqual(ex.write_name(self.wb, "Rows", "one value")["reason"], "shape_mismatch")
        self.assertEqual(ex.write_name(self.wb, "Input", ["a", "b"])["reason"], "shape_mismatch")
        self.assertIsNone(ws["A4"].value)
        # an unlocked cell on the protected sheet is an input cell
        self.assertEqual(ex.write_name(self.wb, "OpenCell", "7")["status"], "written")
        self.assertEqual(self.wb["Lock"]["B1"].value, 7)

    def test_text_that_looks_like_a_formula_stays_text(self):
        ex.write_name(self.wb, "Input", "=HYPERLINK(\"http://x\")")
        out = os.path.join(self.tmp, "o.xlsx")
        self.wb.save(out)
        cell = openpyxl.load_workbook(out)["Data"]["B1"]
        self.assertEqual((cell.value, cell.data_type), ("=HYPERLINK(\"http://x\")", "s"))

    def test_sheet_qualified_reference(self):
        e = ex.write_name(self.wb, "'Data'!C2", "9")
        self.assertEqual((e["status"], self.wb["Data"]["C2"].value), ("written", 9))

    def test_list_defined_names(self):
        names = {n["key"]: n for n in ex.list_defined_names(self.wb)}
        self.assertEqual(names["Input"]["ref"], "Data!B1")
        self.assertTrue(names["Input"]["fillable"])
        self.assertEqual((names["Total"]["reason"], names["Total"]["formula"]), ("formula_cell", True))
        self.assertEqual(names["LockedCell"]["reason"], "protected_sheet")
        self.assertEqual(names["Rate"]["reason"], "not_a_cell_range")
        self.assertEqual((names["Rows"]["cells"], names["Data!Local"]["scope"]), (4, "Data"))

    def _plan(self, plan):
        p = os.path.join(self.tmp, "plan.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        out = os.path.join(self.tmp, "out.xlsx")
        return ex.execute_plan(p, self.src, out), openpyxl.load_workbook(out)

    def test_write_cells_takes_a_defined_name(self):
        # Before: "ERROR in write_cells: Input is not a valid coordinate or range".
        res, wb = self._plan({"actions": [
            {"type": "write_cells", "sheet_name": "Data",
             "cells": [{"cell": "Input", "value": "5", "bold": True}, {"cell": "C2", "value": "5"},
                       {"name": "Total", "value": 1}]},
            {"type": "write_cells", "cells": [{"name": "Other!Local", "value": "o"}]}]})
        self.assertEqual(res["changes"], ["Wrote 2 cells in 'Data'; refused Total (formula_cell)", "Wrote 1 cells"])
        self.assertEqual(res["named"], {"written": ["Input", "Other!Local"], "refused": {"Total": "formula_cell"}})
        ws = wb["Data"]
        self.assertEqual((ws["B1"].value, ws["B1"].font.b, ws["C2"].value), (5, True, "5"))
        self.assertEqual(ws["B3"].value, "=SUM(B4:B5)")
        self.assertEqual(wb["Other"]["A1"].value, "o")

    def test_write_names_action(self):
        res, wb = self._plan({"actions": [{"type": "write_names", "values": {
            "Input": "1,234.50", "Rows": [["a", "1"], ["b", "2"]], "Total": 9, "LockedCell": 3}}]})
        self.assertEqual(res["changes"], ["Wrote 2 named targets; refused Total (formula_cell), LockedCell (protected_sheet)"])
        self.assertEqual(res["named"]["refused"], {"Total": "formula_cell", "LockedCell": "protected_sheet"})
        ws = wb["Data"]
        self.assertEqual([ws["B1"].value, ws["A4"].value, ws["B4"].value, ws["B5"].value], [1234.5, "a", 1, 2])
        self.assertTrue(wb["Lock"].protection.sheet)
        self.assertIn("D1:E1", [str(r) for r in ws.merged_cells.ranges])


# ─── 2b. docx_generator.py: markers, rows, content controls ────
def _sdt(parent, tag, kind, text, *, alias=None, bound=False, rpr_bold=False, placeholder_style=False, block=False):
    sdt = OxmlElement("w:sdt")
    pr = OxmlElement("w:sdtPr")
    if rpr_bold:
        rpr = OxmlElement("w:rPr")
        rpr.append(OxmlElement("w:b"))
        pr.append(rpr)
    if alias:
        a = OxmlElement("w:alias")
        a.set(qn("w:val"), alias)
        pr.append(a)
    tg = OxmlElement("w:tag")
    tg.set(qn("w:val"), tag)
    pr.append(tg)
    if kind == "checkbox":
        etree.SubElement(pr, W14 + "checkbox")
    elif kind:
        pr.append(OxmlElement(f"w:{kind}"))
    if bound:
        b = OxmlElement("w:dataBinding")
        b.set(qn("w:xpath"), "/root/x")
        pr.append(b)
    pr.append(OxmlElement("w:showingPlcHdr"))
    content = OxmlElement("w:sdtContent")
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    if placeholder_style:
        st = OxmlElement("w:rStyle")
        st.set(qn("w:val"), "PlaceholderText")
        rpr.append(st)
    rpr.append(OxmlElement("w:i"))
    r.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    if block:
        p = OxmlElement("w:p")
        p.append(r)
        content.append(p)
    else:
        content.append(r)
    sdt.append(pr)
    sdt.append(content)
    parent.append(sdt)
    return sdt


class DocxTemplateFill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_a_marker_split_over_three_runs_keeps_the_first_runs_format(self):
        doc = Document()
        p = doc.add_paragraph()
        a = p.add_run("ยอด {{tot")
        a.bold = True
        p.add_run("al_am")
        c = p.add_run("ount}} บาท และ {{missing}}")
        c.italic = True
        rep = dg.fill_placeholders(doc, {"total_amount": "168,000.70"})
        self.assertEqual(p.text, "ยอด 168,000.70 บาท และ {{missing}}")
        self.assertEqual(p.runs[0].text, "ยอด 168,000.70")
        self.assertTrue(p.runs[0].bold)
        self.assertEqual(rep["filled"], ["total_amount"])
        self.assertEqual(rep["not_filled"], [{"marker": "missing", "reason": "no_value", "where": "body"}])

    def test_mapping_and_values_that_are_not_text(self):
        doc = Document()
        p = doc.add_paragraph("{{ชื่อ}} {{lines}} {{company.name}} {{n}}")
        rep = dg.fill_placeholders(doc, {"company_name": "ก", "lines": [1], "company": {"name": "ข"}, "n": 4},
                                   {"ชื่อ": "company_name"})
        self.assertEqual(p.text, "ก {{lines}} ข 4")
        self.assertEqual(rep["not_filled"], [{"marker": "lines", "reason": "not_text", "where": "body"}])

    def test_repeating_rows(self):
        doc = Document()
        t = doc.add_table(rows=3, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = "รายการ", "เงิน"
        t.cell(1, 0).text, t.cell(1, 1).text = "{{line.name}} ({{period}})", "{{line.amt}}"
        t.cell(2, 0).text, t.cell(2, 1).text = "รวม", "{{total}}"
        rep = dg.fill_placeholders(doc, {"period": "ก.ย.", "total": "3", "lines": [
            {"name": "a", "amt": "1"}, {"name": "b"}]})
        rows = [[c.text for c in r.cells] for r in t.rows]
        self.assertEqual(rows, [["รายการ", "เงิน"], ["a (ก.ย.)", "1"], ["b (ก.ย.)", "{{line.amt}}"], ["รวม", "3"]])
        self.assertEqual(rep["rows"], {"line": 2})
        self.assertEqual(rep["not_filled"], [{"marker": "line.amt", "reason": "no_value", "where": "body"}])
        # no items: the template row goes, and the report says 0 rows
        doc2 = Document()
        t2 = doc2.add_table(rows=2, cols=1)
        t2.cell(0, 0).text, t2.cell(1, 0).text = "h", "{{item.x}}"
        self.assertEqual(dg.fill_placeholders(doc2, {"items": []})["rows"], {"item": 0})
        self.assertEqual([[c.text for c in r.cells] for r in t2.rows], [["h"]])

    def test_content_controls(self):
        doc = Document()
        p = doc.add_paragraph("บริษัท: ")
        name = _sdt(p._p, "Company", "text", "คลิก", rpr_bold=True, placeholder_style=True)
        body = doc.element.body
        block = _sdt(body, "Block", "text", "พิมพ์", block=True)
        body.sectPr.addprevious(block)  # sectPr stays last
        for tag, kind in (("Rich", None), ("Bound", "text"), ("Tick", "checkbox"), ("Pic", "picture"),
                          ("Combo", "comboBox"), ("Empty", "text")):
            _sdt(doc.add_paragraph()._p, tag, kind, "x", alias="ชื่อ " + tag, bound=(tag == "Bound"))
        rep = dg.fill_content_controls(doc, {"Company": "สรานเทค", "block_value": "บล็อก"}, {"Block": "block_value"})
        self.assertEqual(rep["filled"], [{"tag": "Company", "alias": None}, {"tag": "Block", "alias": None}])
        self.assertEqual({(x["tag"], x["type"], x["reason"]) for x in rep["not_filled"]}, {
            ("Rich", "richText", "not_plain_text"), ("Bound", "text", "data_bound"),
            ("Tick", "checkbox", "not_plain_text"), ("Pic", "picture", "not_plain_text"),
            ("Combo", "comboBox", "not_plain_text"), ("Empty", "text", "no_value")})
        self.assertTrue(all(x["alias"] == "ชื่อ " + x["tag"] for x in rep["not_filled"]))
        run = name.find(qn("w:sdtContent")).find(qn("w:r"))
        self.assertEqual(run.find(qn("w:t")).text, "สรานเทค")
        self.assertIsNotNone(run.find(qn("w:rPr")).find(qn("w:i")))  # the run's own formatting stays
        self.assertIsNone(run.find(qn("w:rPr")).find(qn("w:rStyle")))  # the grey placeholder style goes
        self.assertIsNone(name.find(qn("w:sdtPr")).find(qn("w:showingPlcHdr")))
        self.assertEqual("".join(t.text for t in block.iter(qn("w:t"))), "บล็อก")

    def test_plan_actions(self):
        src = os.path.join(TPL, "docx_content_controls.docx")
        out = os.path.join(self.tmp, "o.docx")
        res = dg.generate_docx({"actions": [
            {"type": "fill_content_controls", "values": VALUES, "mapping": {"CompanyName": "company_name"}},
            {"type": "fill_placeholders", "values": VALUES}]}, out, src)
        log = res["change_log"]
        self.assertEqual([c["status"] for c in log], ["ok", "ok"])
        # TotalAmount has no mapping entry: its tag's snake_case (total_amount) is a value
        self.assertEqual(log[0]["detail"], "Filled 2 content controls; not filled: ApprovalDate [date] (not_plain_text), "
                                           "Notes [richText] (not_plain_text), Status [dropDownList] (not_plain_text)")
        self.assertEqual(log[1]["detail"], "Filled 0 markers")


class MarkersNobodyFills(unittest.TestCase):
    def test_a_marker_in_a_slide_layout_is_reported_not_skipped(self):
        tf = _tf()
        tmp = tempfile.mkdtemp()
        prs = Presentation()
        layout = prs.slide_layouts[1]
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = "{{period}} {{unknown}}"
        for ph in layout.placeholders:
            if ph.placeholder_format.idx == 0:
                ph.text_frame.text = "{{company_name}}"
        src = os.path.join(tmp, "t.pptx")
        prs.save(src)
        rep = tf.fill(src, os.path.join(tmp, "o.pptx"), VALUES)
        self.assertEqual(rep["filled"], ["period"])
        got = {(x["marker"], x["reason"]) for x in rep["not_filled_explained"]}
        self.assertEqual(got, {("unknown", "no_value"), ("company_name", "unsupported_location")})
        self.assertTrue(rep["mapping_screen"])


# ─── 3. Unchanged behaviour ────────────────────────────────────
class UnchangedCoordinateWrites(unittest.TestCase):
    """What excel_executor.py did before for a plan with coordinates."""

    def test_coordinate_plan_as_before(self):
        tmp = tempfile.mkdtemp()
        src = _book(os.path.join(tmp, "t.xlsx"))
        plan = {"operation": "modify_workbook", "actions": [
            {"type": "write_cells", "sheet_name": "Data", "cells": [
                {"cell": "B3", "value": 42},                       # over a formula: a coordinate write does it
                {"cell": "C3", "value": "12,850.70"},              # text stays text
                {"cell": "C4", "value": "=B3*2", "bold": True, "number_format": "0.00"},
                {"cell": "$C$5", "value_formula": "=C4+1", "font_size": 14}]},
            {"type": "write_table", "sheet_name": "Data", "start_cell": "G1", "headers": ["h1", "h2"],
             "data": [[1, "x"], [2, "y"]]},
            {"type": "merge_cells", "sheet_name": "Data", "range": "A10:B10"},
            {"type": "create_chart", "sheet_name": "Data", "chart_type": "bar", "title": "T",
             "data_range": "G1:G3", "category_range": "H1:H3", "anchor_cell": "J2"},
            {"type": "write_cells", "sheet_name": "Data", "cells": [{"cell": "NotAName", "value": 1}]},
            {"type": "write_cells", "cells": [{"cell": "A1", "value": 1}]},
            {"type": "write_cells", "sheet_name": "Nope", "cells": [{"cell": "A1", "value": 1}]},
            {"type": "freeze_panes", "sheet_name": "Data", "cell": "A2"},
            {"type": "no_such_action"}]}
        p = os.path.join(tmp, "plan.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(plan, f)
        out = os.path.join(tmp, "out.xlsx")
        res = ex.execute_plan(p, src, out)
        self.assertEqual(set(res), {"success", "output_file", "changes", "action_count"})
        chart = res["changes"].pop(3)  # it prints openpyxl's Title object, as it always has
        self.assertTrue(chart.startswith("Created bar chart '<openpyxl.chart.title.Title object>"), chart)
        self.assertTrue(chart.endswith("' in 'Data' at J2"), chart)
        self.assertEqual(res["changes"], [
            "Wrote 4 cells in 'Data'",
            "Wrote table (2 rows) at G1 in 'Data'",
            "Merged cells A10:B10 in 'Data'",
            "ERROR in write_cells: NotAName is not a valid coordinate or range",
            "ERROR in write_cells: 'sheet_name'",
            "ERROR in write_cells: \"sheet 'Nope' not found\"",
            "Froze panes at A2 in 'Data'",
            "ERROR in no_such_action: unknown action"])
        self.assertEqual(res["action_count"], 9)
        ws = openpyxl.load_workbook(out)["Data"]
        self.assertEqual([ws["B3"].value, ws["C3"].value, ws["C4"].value, ws["C5"].value],
                         [42, "12,850.70", "=B3*2", "=C4+1"])
        self.assertTrue(ws["C4"].font.b)
        self.assertEqual((ws["C4"].number_format, ws["C5"].font.sz), ("0.00", 14))
        self.assertEqual([ws["G1"].value, ws["H3"].value], ["h1", "y"])
        self.assertTrue(ws["G1"].font.b)
        self.assertIn("A10:B10", [str(r) for r in ws.merged_cells.ranges])
        self.assertEqual(len(ws._charts), 1)
        self.assertEqual(ws.freeze_panes, "A2")


class UnchangedReplaceText(unittest.TestCase):
    """What docx_generator.py's replace_text did before: body paragraphs,
    one run at a time — a find split across runs is not found, a header is
    not searched."""

    def test_replace_text_as_before(self):
        tmp = tempfile.mkdtemp()
        doc = Document()
        doc.sections[0].header.paragraphs[0].text = "H {{name}}"
        p = doc.add_paragraph()
        p.add_run("Hello {{name}} and ")
        p.add_run("{{spl")
        p.add_run("it}}")
        doc.add_paragraph("{{name}} again")
        src = os.path.join(tmp, "s.docx")
        doc.save(src)
        out = os.path.join(tmp, "o.docx")
        res = dg.generate_docx({"actions": [{"type": "replace_text", "find": "{{name}}", "replace": "Ann"},
                                            {"type": "replace_text", "find": "{{split}}", "replace": "X"},
                                            {"type": "replace_text", "find": ""}]}, out, src)
        self.assertEqual(set(res), {"success", "mode", "output_path", "file_size_bytes", "actions_executed",
                                    "actions_failed", "change_log"})
        self.assertEqual([c["detail"] for c in res["change_log"]], [
            "Replaced '{{name}}' (2 run hits)", "Replaced '{{split}}' (0 run hits)",
            "Skipped replace_text (empty find)"])
        d = Document(out)
        self.assertEqual([x.text for x in d.paragraphs], ["Hello Ann and {{split}}", "Ann again"])
        self.assertEqual(d.sections[0].header.paragraphs[0].text, "H {{name}}")


if __name__ == "__main__":
    unittest.main()
