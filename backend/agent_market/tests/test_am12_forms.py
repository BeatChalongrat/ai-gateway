"""A purchase request in the organisation's own form — a form with nothing in it that says where to write (AM12).

Two forms of different shapes (tests/prformkit.py: Word in Thai, Excel in English) and one for something
else altogether. What is asserted is read from the delivered file, opened again:

  * the lines go into the rows under the headings they were mapped by — one row each; a Word form that
    has fewer rows than lines gets its last empty row again, an Excel form is never given rows (its
    formulas would not move) and says which lines did not fit;
  * totals and tax are the round's computed ones, in the cells beside their labels; a formula of the
    form's own is not written over;
  * a blank drawn as a line after its label takes its value, and the label stays;
  * every blank the form expects filled and the delivered file still holds empty is named — on the
    file's own face, under “ช่องที่ยังกรอกไม่ได้”, with what no web page can tell (payment terms, how
    long the price holds, whether the seller is one the organisation buys from) and why. The list is
    walked from the file by code: a blank nobody mapped is on it;
  * an agent that does not ask for these readings reads a form exactly as it did before.

The refusal (a form the agent has almost nothing for) is the control plane's decision on the slots read
here: the runner's integration test carries it through, with the words a person is shown.
"""

import copy
import datetime
import json
import os
import shutil
import tempfile
import unittest

from docx import Document
from openpyxl import load_workbook

from agent_market.deliverables import form_regions as fr
from agent_market.deliverables import template_fill as tf
from agent_market.deliverables import template_out as to
from agent_market.engine import step
from agent_market.packages import procurement as P
from agent_market.tests import formkit, prformkit as K

PKG = os.path.dirname(os.path.abspath(P.__file__))
READS = tuple(P.MANIFEST["templates"]["reads"])
T = lambda key, **kw: P.words.t("th", key, **kw)  # noqa: E731


def sample_computed(tmp: str, lines: int = 1) -> dict:
    """The package's own sample, chosen — and, for a form with several lines, the same line again as lines
    2 … n (each its own number and quantity), so that a table of items has something to hold."""
    base = os.path.join(PKG, "sample-data")
    with open(os.path.join(base, "research.json"), encoding="utf-8") as f:
        research = json.load(f)
    today = datetime.date(2026, 10, 6)
    defaults = {"proc_vat_rate": "7.00", "proc_sample_pick": "first"}
    pre = P.preflight([], message="x", defaults=defaults, answers={}, today=today, proposals={"brief": research["brief"]}, lang="th")
    c = P.compute([], {}, pre["period"], answers={}, defaults=defaults, today=today, proposals=research, in_dir=base, out_dir=tmp, lang="th")
    first = c["request"]["lines"][0]
    for n in range(2, lines + 1):
        more = copy.deepcopy(first)
        more.update(no=n, quantity=str(n), amount=f"{3200 * n}.00", description=f"{first['description']} (รายการที่ {n})")
        c["request"]["lines"].append(more)
    return c


def docx_rows(path: str) -> list[list[list[str]]]:
    return [[[c.text for c in r.cells] for r in t.rows] for t in Document(path).tables]


def docx_text(path: str) -> str:
    d = Document(path)
    return "\n".join([p.text for p in d.paragraphs] + [c.text for t in d.tables for r in t.rows for c in r.cells])


class FormsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.inp, cls.out = os.path.join(cls.tmp, "in"), os.path.join(cls.tmp, "out")
        os.makedirs(cls.inp)
        os.makedirs(cls.out)
        cls.forms = {os.path.basename(p): p for p in K.write_all(os.path.join(cls.tmp, "forms"))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def slots(self, name: str, reads=READS) -> list[dict]:
        return to.slots(tf.inspect(self.forms[name], reads=reads))

    def fill(self, name: str, mapping: dict, lines: int = 1, computed: dict | None = None):
        """The render step with the form as the round's template — the way the runner runs it."""
        ext = os.path.splitext(name)[1]
        computed = computed or sample_computed(self.out, lines)
        with open(os.path.join(self.inp, "computed.json"), "w", encoding="utf-8") as f:
            json.dump(computed, f, ensure_ascii=False)
        shutil.copy(self.forms[name], os.path.join(self.inp, "template" + ext))
        step.JOB_IN, step.JOB_OUT = self.inp, self.out
        res = step.run({"op": "render", "package": "procurement", "computed": "computed.json", "formats": [], "names": {},
                        "theme": {"id": "navy", "company": "บริษัท ทดสอบ จำกัด"}, "run": {"preparer": "สมชาย ใจดี", "organisation": "บริษัท ทดสอบ จำกัด"},
                        "template": {"input": "template" + ext, "name": "filled" + ext, "saved": {"slots": self.slots(name), "mapping": mapping}}})
        self.assertTrue(res["ok"], res)
        art = next(a for a in res["artifacts"] if a["kind"] == "template")
        return art, os.path.join(self.out, "filled" + ext), res, computed

    def checks(self, art: dict) -> dict:
        return {c["name"]: c for c in art["quality"]["checks"]}


class ReadingAForm(FormsTest):
    def test_the_word_form_is_read_as_blanks_a_table_of_items_and_drawn_lines(self):
        sl = {s["key"]: s for s in self.slots("request_form.docx")}
        items = sl[K.DOCX_ITEMS]
        self.assertEqual((items["kind"], items["rows"], items["columns"]), ("table", K.DOCX_ROWS, ["c0", "c1", "c2", "c3", "c4", "c5"]))
        self.assertEqual([items["column_labels"][c] for c in items["columns"]], K.DOCX_HEADS)
        # the model that proposes a mapping is shown each column's heading
        for head in K.DOCX_HEADS:
            self.assertIn(head, items["context"])
        beside = [s for s in sl.values() if s["kind"] == "target" and s.get("side") == "right"]
        self.assertEqual([s["label"] for s in beside], K.DOCX_BESIDE + K.DOCX_TOTALS)
        # no cell of the items table is also offered as a blank of its own
        self.assertFalse([k for k in sl if k.startswith("table1.r") and int(k.split(".r")[1].split("c")[0]) <= K.DOCX_ROWS])
        drawn = {s["label"]: s for s in sl.values() if s.get("side") == "inline"}
        self.assertEqual({k: v["required"] for k, v in drawn.items()}, {"เงื่อนไขการชำระเงิน": True, "หมายเหตุ": True, "ลงชื่อ": False})

    def test_the_excel_form_is_read_as_blanks_and_a_grid_of_items_whose_rows_carry_their_numbers(self):
        sl = {s["key"]: s for s in self.slots("request_form.xlsx")}
        items = sl[K.XLSX_ITEMS]
        self.assertEqual((items["rows"], items["columns"]), (K.XLSX_ROWS, ["B", "C", "D", "E", "F", "G"]))
        self.assertEqual([items["column_labels"][c] for c in items["columns"]], K.XLSX_HEADS)
        self.assertEqual(items["where"], "PR!B9:G14")
        required = sorted(k for k, s in sl.items() if s["kind"] == "target" and s.get("required"))
        self.assertEqual(required, sorted(["PR!C4", "PR!C5", "PR!C6", "PR!F4", "PR!F5", "PR!F6", "PR!G16", "PR!G17", "PR!C19"]))
        # the subtotal is the form's own formula: not a blank
        self.assertNotIn("PR!G15", sl)

    def test_an_agent_that_does_not_ask_reads_a_form_as_before(self):
        for name in ("request_form.docx", "request_form.xlsx"):
            ins = tf.inspect(self.forms[name])
            self.assertEqual((ins.get("regions") or [], ins.get("lines") or []), ([], []), name)
            self.assertFalse([s for s in to.slots(ins) if s["kind"] == "table" or s.get("side") == "inline"], name)
        # the forms the other agents' tests are made of read the same with and without the new readings
        # where they have neither a table of items nor a line with a label the agent asked to read
        folder = os.path.join(self.tmp, "am11")
        for path in formkit.write_all(folder):
            before = to.slots(tf.inspect(path))
            self.assertFalse([s for s in before if fr.ROWS_RE.match(s["key"]) or fr.LINE_RE.match(s["key"])], path)
            self.assertEqual([s["key"] for s in to.slots(tf.inspect(path, reads=("rows",)))], [s["key"] for s in before], path)

    def test_what_is_not_a_table_of_items_is_not_read_as_one(self):
        d = Document()
        two = d.add_table(rows=4, cols=2)          # two headings: too few to be a table of items
        two.cell(0, 0).text, two.cell(0, 1).text = "รายการ", "จำนวนเงิน"
        one = d.add_table(rows=2, cols=4)          # four headings over ONE empty row: a strip of fields
        for i, label in enumerate(["ผู้ขอ", "แผนก", "วันที่", "เลขที่"]):
            one.cell(0, i).text = label
        full = d.add_table(rows=3, cols=3)         # a table that already holds its rows
        for r, row in enumerate([["ลำดับ", "รายการ", "จำนวน"], ["1", "ปากกา", "10"], ["2", "ดินสอ", "5"]]):
            for c, text in enumerate(row):
                full.cell(r, c).text = text
        path = os.path.join(self.tmp, "not_items.docx")
        d.save(path)
        ins = tf.inspect(path, reads=READS)
        self.assertEqual(ins["regions"], [])
        self.assertEqual(len([t for t in ins["targets"] if t["cell"].startswith("table1.")]), 4)
        text = "ชื่อ: ______  แผนก: ______ บาท  ...."        # four dots are punctuation, not a line
        found = fr.lines_in(text)
        self.assertEqual([(b["n"], b["label"], b["colon"], text[b["start"]:b["end"]]) for b in found],
                         [(0, "ชื่อ", True, "______"), (1, "แผนก", True, "______")])


class FillingTheWordForm(FormsTest):
    def test_one_line_its_totals_and_every_gap_named_on_the_file(self):
        art, path, res, computed = self.fill("request_form.docx", K.DOCX_MAPPING)
        header, items = docx_rows(path)[0], docx_rows(path)[1]
        self.assertEqual([r[1] for r in header], ["", "6 ต.ค. 2569", "สมชาย ใจดี", "", "บริษัท บ้านเครื่องเขียน จำกัด", "0105559876543",
                                                  "สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ", "30 พฤศจิกายน 2569"])
        self.assertEqual(items[0], K.DOCX_HEADS)
        self.assertEqual(items[1], ["1", "กระดานไวท์บอร์ดแม่เหล็ก ขนาด 120 x 240 ซม. · WB-120240-M", "6", "แผ่น", "3,200.00", "19,200.00"])
        self.assertEqual(items[2:5], [[""] * 6] * 3)                     # the rows the round has no line for stay empty
        self.assertEqual([(r[4], r[5]) for r in items[5:8]], [("รวมเป็นเงิน", "19,200.00"), ("ภาษีมูลค่าเพิ่ม", "1,256.07"), ("รวมทั้งสิ้น", "19,200.00")])
        # the amounts are the round's computed ones, not anything a form or a model added up
        tot = computed["request"]["totals"]
        self.assertEqual((tot["subtotal"], tot["vat"], tot["total"]), ("19200.00", "1256.07", "19200.00"))

        ck = self.checks(art)
        self.assertTrue(ck["template.beside_label"]["ok"] and ck["template.gaps_printed"]["ok"], ck)
        # a draft with blanks still empty is not "every slot filled", and says which
        self.assertFalse(art["quality"]["passed"])
        self.assertFalse(ck["template.every_slot"]["ok"])
        unfilled = {u["label"]: u["reason"] for u in art["quality"]["unfilled"]}
        self.assertEqual(unfilled, {"เลขที่ใบขอซื้อ": "not_mapped", "แผนก": "not_mapped", "เงื่อนไขการชำระเงิน": "not_mapped", "หมายเหตุ": "not_mapped"})

        # … on the face of the file: its own blanks, and what no page can tell, each with why
        gaps = Document(path).tables[-1]
        listed = {r.cells[0].text: r.cells[1].text for r in gaps.rows[1:]}
        text = docx_text(path)
        self.assertIn(T("gaps.title"), text)
        for label in unfilled:
            self.assertEqual(listed[f"{T('gaps.form_blank')}: {label}"], T("gaps.why.not_mapped"))
        for code, why in (("payment_terms", "never_on_a_page"), ("price_validity", "never_on_a_page"), ("supplier_listed", "not_on_list"),
                          ("tax_id", "as_shown"), ("legal_name", "as_shown"), ("shipping", "not_to_address"), ("lead_time", "not_committed")):
            self.assertIn(T(f"gap.{code}"), listed, code)
            self.assertTrue(listed[T(f"gap.{code}")].startswith(T(f"gap.{code}.{why}").rstrip(":")), code)
        self.assertEqual(len(listed), len(computed["request"]["gaps"]) + len(unfilled))
        self.assertEqual(ck["template.gaps_printed"]["values"], len(listed))
        # the drawn lines nobody had a value for are as the form drew them
        self.assertIn(K.DOCX_LINE, text)
        self.assertIn(K.DOCX_SIGN, text)

    def test_more_lines_than_rows_and_the_form_gets_its_empty_row_again(self):
        art, path, _, computed = self.fill("request_form.docx", K.DOCX_MAPPING, lines=6)
        items = docx_rows(path)[1]
        self.assertEqual(len(items), 1 + 6 + len(K.DOCX_TOTALS))        # four rows of the form, two more like them
        self.assertEqual([r[0] for r in items[1:7]], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual([r[2] for r in items[1:7]], ["6", "2", "3", "4", "5", "6"])
        self.assertTrue(all("WB-120240-M" in r[1] for r in items[1:7]))
        # the totals are still under the lines, beside their labels
        self.assertEqual([r[4] for r in items[7:]], K.DOCX_TOTALS)
        self.assertNotIn(K.DOCX_ITEMS, [u["slot"] for u in art["quality"]["unfilled"]])
        # the rows that were added are rows of the same table, drawn as the form's own are
        table = Document(path).tables[1]
        self.assertEqual({len(r.cells) for r in table.rows}, {6})

    def test_a_value_on_a_drawn_line_replaces_the_line_and_keeps_its_label(self):
        line = next(s["key"] for s in self.slots("request_form.docx") if s["label"] == "หมายเหตุ")
        art, path, _, computed = self.fill("request_form.docx", {**K.DOCX_MAPPING, line: "source_page"})
        url = computed["request"]["lines"][0]["url"]
        paragraph = next(p.text for p in Document(path).paragraphs if p.text.startswith("หมายเหตุ"))
        self.assertEqual(" ".join(paragraph.split()), f"หมายเหตุ: {url}")
        self.assertNotIn("หมายเหตุ", {u["label"] for u in art["quality"]["unfilled"]})
        self.assertIn(K.DOCX_LINE, docx_text(path))                      # the line beside it, nobody's, is untouched

    def test_the_read_back_is_of_the_file_not_of_what_the_filler_says(self):
        art, path, _, computed = self.fill("request_form.docx", K.DOCX_MAPPING, lines=2)
        sl = self.slots("request_form.docx")
        region = next(g for g in tf.inspect(self.forms["request_form.docx"], reads=READS)["regions"] if g["key"] == K.DOCX_ITEMS)
        lines = to.template_values(sl, K.DOCX_MAPPING, P.template_values(computed, {}))[K.DOCX_ITEMS]
        self.assertEqual(fr.docx_rows_hold(Document(path), region, lines), (True, ""))
        # a figure changed in the delivered file
        d = Document(path)
        d.tables[1].cell(2, 5).text = "1.00"
        wrong = os.path.join(self.tmp, "wrong.docx")
        d.save(wrong)
        self.assertEqual(fr.docx_rows_hold(Document(wrong), region, lines), (False, "not_in_file"))
        # a heading that is no longer the one the column was mapped under
        d = Document(path)
        d.tables[1].cell(0, 4).text = "ส่วนลด"
        d.save(wrong)
        self.assertEqual(fr.docx_rows_hold(Document(wrong), region, lines), (False, "wrong_label"))
        # a gap taken off the file's face
        gaps = P.template_gaps(computed)
        d = Document(path)
        last = d.tables[-1]
        gone = last.rows[1].cells[0].text
        last._tbl.remove(last.rows[1]._tr)
        d.save(wrong)
        printed = {**gaps, "rows": [[r.cells[0].text, r.cells[1].text] for r in Document(path).tables[-1].rows[1:]]}
        self.assertEqual(fr.gaps_printed(path, "docx", printed), [])
        self.assertEqual(fr.gaps_printed(wrong, "docx", printed), [gone])

    def test_nothing_chosen_and_the_form_is_not_filled_at_all(self):
        computed = sample_computed(self.out)
        computed["request"] = {"lines": [], "gaps": [], "passed": ["x"], "totals": None}
        _, _, res, _ = (None, None, None, None)
        with open(os.path.join(self.inp, "computed.json"), "w", encoding="utf-8") as f:
            json.dump(computed, f, ensure_ascii=False)
        shutil.copy(self.forms["request_form.docx"], os.path.join(self.inp, "template.docx"))
        step.JOB_IN, step.JOB_OUT = self.inp, self.out
        res = step.run({"op": "render", "package": "procurement", "computed": "computed.json", "formats": [], "names": {}, "theme": {"id": "navy"},
                        "template": {"input": "template.docx", "name": "none.docx", "saved": {"slots": self.slots("request_form.docx"), "mapping": K.DOCX_MAPPING}}})
        self.assertTrue(res["ok"], res)
        self.assertEqual([a for a in res["artifacts"] if a["kind"] == "template"], [])
        self.assertFalse(os.path.exists(os.path.join(self.out, "none.docx")))


class FillingTheExcelForm(FormsTest):
    def test_lines_as_numbers_in_the_grid_and_the_forms_own_formula_untouched(self):
        art, path, _, computed = self.fill("request_form.xlsx", K.XLSX_MAPPING, lines=3)
        ws = load_workbook(path)["PR"]
        self.assertEqual([ws[f"B{r}"].value for r in range(9, 15)], [1, 2, 3, 4, 5, 6])      # the form's own numbering
        self.assertEqual([(ws[f"D{r}"].value, ws[f"F{r}"].value, ws[f"G{r}"].value) for r in (9, 10, 11)],
                         [(6, 3200, 19200), (2, 3200, 6400), (3, 3200, 9600)])
        self.assertEqual([ws[f"C{r}"].value for r in (12, 13, 14)], [None, None, None])
        self.assertEqual(ws["G15"].value, "=SUM(G9:G14)")
        self.assertEqual(ws["G9"].number_format, "#,##0.00")
        ck = self.checks(art)
        self.assertTrue(ck["template.formulas_kept"]["ok"] and ck["template.beside_label"]["ok"] and ck["template.gaps_printed"]["ok"], ck)
        self.assertTrue(art["meta"]["needs_recalc"])
        self.assertEqual({u["label"] for u in art["quality"]["unfilled"]}, set(K.XLSX_NEVER.values()))
        # the last sheet is the gaps, and the form's own sheet is still the first
        book = load_workbook(path)
        self.assertEqual(book.sheetnames[0], "PR")
        last = book.worksheets[-1]
        self.assertEqual(last["A1"].value, T("gaps.title"))
        listed = {a: b for a, b in last.iter_rows(min_row=5, max_col=2, values_only=True) if a}
        for label in K.XLSX_NEVER.values():
            self.assertEqual(listed[f"{T('gaps.form_blank')}: {label}"], T("gaps.why.not_mapped"))
        # what no page can tell is said of the line it is true of (with several lines, each gap names its line)
        self.assertEqual(listed[f"{T('gaps.line', n='1')} · {T('gap.payment_terms')}"], T("gap.payment_terms.never_on_a_page"))

    def test_more_lines_than_rows_and_the_form_says_which_did_not_fit(self):
        art, path, _, computed = self.fill("request_form.xlsx", K.XLSX_MAPPING, lines=8)
        ws = load_workbook(path)["PR"]
        self.assertEqual([ws[f"D{r}"].value for r in range(9, 15)], [6, 2, 3, 4, 5, 6])
        # no row was pushed down: what stood under the grid is where it stood
        self.assertEqual((ws["F15"].value, ws["G15"].value, ws["F16"].value, ws["B19"].value), ("Subtotal", "=SUM(G9:G14)", "VAT", "Approved by:"))
        items = next(u for u in art["quality"]["unfilled"] if u["slot"] == K.XLSX_ITEMS)
        self.assertEqual(items["reason"], "rows_short")
        last = load_workbook(path).worksheets[-1]
        listed = {a: b for a, b in last.iter_rows(min_row=5, max_col=2, values_only=True) if a}
        self.assertEqual(listed[f"{T('gaps.form_blank')}: {' | '.join(K.XLSX_HEADS)}"], T("gaps.why.rows_short"))
        self.assertFalse(art["quality"]["passed"])


class FillingThePdfForm(FormsTest):
    """A PDF with fields to type in. The fields' own names say nothing ("f1" … "f12"): a field is read under
    the words the page prints beside it, or under its own description."""

    def test_the_fields_are_read_under_the_words_printed_beside_them(self):
        sl = self.slots("request_form.pdf")
        self.assertEqual([(s["key"], s["label"]) for s in sl], [("pdf:" + name, label) for name, label in K.PDF_FIELDS] + [("pdf:" + K.PDF_SAID[0], K.PDF_SAID[1])])
        self.assertTrue(all(s["kind"] == "target" and s["required"] and s["side"] == "field" for s in sl))
        ins = tf.inspect(self.forms["request_form.pdf"], reads=READS)
        self.assertEqual(ins["not_offered"], [{"name": "urgent", "kind": "/Btn"}])       # a check box is not written
        # the same page with no fields is a picture of a form: nothing in it takes a value
        self.assertEqual(self.slots("printed_form.pdf"), [])

    def test_values_are_in_the_fields_drawn_in_a_font_that_has_thai_and_the_gaps_are_a_last_page(self):
        from pypdf import PdfReader

        from agent_market.deliverables import pdf_form

        art, path, _, computed = self.fill("request_form.pdf", K.PDF_MAPPING)
        values = pdf_form.field_values(path)
        self.assertEqual({k: values[k] for k in K.PDF_MAPPING}, {
            "pdf:f1": "สมชาย ใจดี", "pdf:f2": "6 ต.ค. 2569", "pdf:f3": "บริษัท บ้านเครื่องเขียน จำกัด", "pdf:f4": "0105559876543",
            "pdf:f5": "สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ", "pdf:f6": "กระดานไวท์บอร์ดแม่เหล็ก ขนาด 120 x 240 ซม. · WB-120240-M",
            "pdf:f7": "6", "pdf:f8": "3,200.00", "pdf:f9": "19,200.00"})
        self.assertEqual({values[k] for k in K.PDF_NEVER}, {""})
        # still a form — a person can go on typing in it — and each value written is drawn by this code, in
        # Sarabun: a viewer is told not to draw the values again in the form's own font, which has no Thai
        reader = PdfReader(path)
        self.assertEqual(reader.trailer["/Root"]["/AcroForm"]["/NeedAppearances"], False)
        drawn = 0
        for a in reader.pages[0]["/Annots"]:
            a = a.get_object()
            if "pdf:" + str(a.get("/T")) in K.PDF_MAPPING:
                fonts = a["/AP"]["/N"]["/Resources"]["/Font"]
                self.assertTrue(any("Sarabun" in str(f.get_object().get("/BaseFont")) for f in fonts.values()), a.get("/T"))
                drawn += 1
        self.assertEqual(drawn, len(K.PDF_MAPPING))
        ck = self.checks(art)
        self.assertTrue(ck["template.gaps_printed"]["ok"] and ck["template.fits"]["ok"], ck)
        self.assertEqual({u["label"]: u["reason"] for u in art["quality"]["unfilled"]}, {label: "not_mapped" for label in K.PDF_NEVER.values()})
        self.assertFalse(art["quality"]["passed"])
        # the form's own page is still the first; the gaps are the page after it
        self.assertEqual(len(reader.pages), 2)
        gaps = P.template_gaps(computed)
        rows = list(gaps["rows"]) + [[f"{gaps['form_blank']}: {label}", gaps["form_why"]["not_mapped"]] for label in K.PDF_NEVER.values()]
        self.assertEqual(ck["template.gaps_printed"]["values"], len(rows))
        self.assertEqual(fr.gaps_printed(path, "pdf", {**gaps, "rows": rows, "first_page": 1}), [])
        # … read from the file: without that page, every one of them is missing
        from pypdf import PdfWriter

        w = PdfWriter()
        w.add_page(reader.pages[0])
        short = os.path.join(self.tmp, "short.pdf")
        with open(short, "wb") as f:
            w.write(f)
        self.assertEqual(len(fr.gaps_printed(short, "pdf", {**gaps, "rows": rows, "first_page": 1})), len(rows))

    def test_a_value_too_long_for_its_field_is_said_to_be_cut_not_left_looking_complete(self):
        computed = sample_computed(self.out)
        computed["request"]["lines"][0]["description"] = "รายการยาวมาก " * 60
        art, path, _, _ = self.fill("request_form.pdf", K.PDF_MAPPING, computed=computed)
        ck = self.checks(art)
        self.assertFalse(ck["template.fits"]["ok"])
        self.assertIn("รายการที่ขอซื้อ", ck["template.fits"]["detail"])
        self.assertFalse(art["quality"]["passed"])


class TheFixturesAreTheKits(unittest.TestCase):
    """The forms the runner's integration lane and the walks read from disk are the ones this kit builds."""

    def test_each_committed_form_reads_as_a_freshly_built_one(self):
        fixtures = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "forms")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name, make in K.FORMS.items():
            committed = os.path.join(fixtures, name)
            self.assertTrue(os.path.exists(committed), name)
            fresh = make(os.path.join(tmp, name))
            strip = lambda sl: [{k: v for k, v in s.items() if k != "context"} for s in sl]  # noqa: E731
            self.assertEqual(strip(to.slots(tf.inspect(committed, reads=READS))), strip(to.slots(tf.inspect(fresh, reads=READS))), name)
            # not a word of this product or its owner in any of them — in what a person reads, or in the file's parts
            if name.endswith(".pdf"):
                import pdfplumber

                with pdfplumber.open(committed) as pdf, open(committed, "rb") as f:
                    raw = f.read().lower() + "".join((p.extract_text() or "") for p in pdf.pages).lower().encode("utf-8")
            else:
                import zipfile

                with zipfile.ZipFile(committed) as z:
                    raw = b"".join(z.read(n) for n in z.namelist()).lower()
            for word in (b"sran", b"gbtech", b"gb tech", b"chalongrat"):
                self.assertNotIn(word, raw, (name, word))


if __name__ == "__main__":
    unittest.main()
