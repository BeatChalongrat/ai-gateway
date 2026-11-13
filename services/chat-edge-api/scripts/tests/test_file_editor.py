import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import file_editor as editor


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, source, changes, **window):
        before = source.read_bytes()
        info = editor.execute(source, {"mode": "inspect", **window}, self.root / "unused")
        dest = self.root / ("edited" + source.suffix)
        editor.execute(source, {"mode": "edit", "sha256": info["sha256"], "changes": changes, **window}, dest)
        self.assertEqual(before, source.read_bytes())
        return dest

    def test_xlsx_types_formulas_styles_and_other_sheets(self):
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font
        source = self.root / "test.xlsx"
        book = Workbook(); sheet = book.active
        sheet["A1"] = 2; sheet["A1"].font = Font(bold=True)
        sheet["B1"] = "=A1*3"; book.create_sheet("Other")["A1"] = "preserve"
        book.save(source)
        out = self.edit(source, [{"id": "A1", "value": "4", "type": "number"}, {"id": "C1", "value": "=1+2", "type": "text"}])
        result = load_workbook(out)
        self.assertEqual(result.active["A1"].value, 4)
        self.assertTrue(result.active["A1"].font.bold)
        self.assertEqual(result.active["B1"].value, "=A1*3")
        self.assertEqual(result.active["C1"].data_type, "s")
        self.assertEqual(result["Other"]["A1"].value, "preserve")

    def test_wrong_hash_and_external_formula_refused(self):
        from openpyxl import Workbook
        source = self.root / "test.xlsx"; Workbook().save(source)
        for sha, value in [("0" * 64, "=1+2"), (hashlib.sha256(source.read_bytes()).hexdigest(), "=WEBSERVICE(\"https://example.com\")")]:
            with self.assertRaises(ValueError):
                editor.execute(source, {"mode": "edit", "sha256": sha, "changes": [{"id":"A1","value":value,"type":"formula"}]}, self.root/"bad.xlsx")

    def test_docx_table_and_header(self):
        from docx import Document
        source = self.root/"test.docx"; doc = Document()
        doc.add_paragraph("Original"); doc.add_table(rows=1,cols=1).cell(0,0).text="Cell"
        doc.sections[0].header.paragraphs[0].text="Header"; doc.save(source)
        info=editor.execute(source,{"mode":"inspect"},self.root/"unused")
        fields={f["value"]:f["id"] for f in info["fields"]}
        out=self.edit(source,[{"id":fields["Cell"],"value":"Changed"},{"id":fields["Header"],"value":"Updated"}])
        doc=Document(out);self.assertEqual(doc.paragraphs[0].text,"Original")
        self.assertEqual(doc.tables[0].cell(0,0).text,"Changed")
        self.assertEqual(doc.sections[0].header.paragraphs[0].text,"Updated")

    def test_docx_replacement_removes_old_hyperlink_text(self):
        from docx import Document
        from docx.oxml import OxmlElement
        source=self.root/"link.docx";doc=Document();p=doc.add_paragraph()
        p.add_run("Before ").bold=True
        link=OxmlElement("w:hyperlink");run=OxmlElement("w:r");text=OxmlElement("w:t")
        text.text="old link";run.append(text);link.append(run);p._p.append(link);doc.save(source)
        out=self.edit(source,[{"id":"body/p/0","value":"Replacement"}])
        p=Document(out).paragraphs[0]
        self.assertEqual(p.text,"Replacement");self.assertTrue(p.runs[0].bold)

    def test_empty_pptx_paragraph_and_repair_preserve_text(self):
        from pptx import Presentation
        from pptx.util import Inches
        source=self.root/"test.pptx";deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[6])
        shape=slide.shapes.add_textbox(-Inches(1),0,Inches(4),Inches(2));deck.save(source)
        info=editor.execute(source,{"mode":"inspect"},self.root/"unused")
        out=self.edit(source,[{"id":info["fields"][0]["id"],"value":"Preserve these words"}])
        repaired=self.root/"repair.pptx";report=editor.repair_pptx(out,repaired)
        self.assertTrue(report["text_preserved"])
        result=Presentation(repaired);self.assertEqual(result.slides[0].shapes[0].text,"Preserve these words")
        self.assertGreater(result.slides[0].shapes[0].left,0)

    def test_csv_injection_and_json_validation(self):
        source=self.root/"data.csv";source.write_text("Name,Value\nA,2\n",encoding="utf-8")
        out=self.edit(source,[{"id":"R2C1","value":"=HYPERLINK(1)"}])
        self.assertIn("'=HYPERLINK",out.read_text(encoding="utf-8-sig"))
        source=self.root/"data.json";source.write_text('{"ok":true}',encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):self.edit(source,[{"id":"content","value":"broken"}])

    def test_pdf_note_preserves_existing_content(self):
        from reportlab.pdfgen import canvas
        from pypdf import PdfReader
        source=self.root/"test.pdf";c=canvas.Canvas(str(source));c.drawString(100,500,"Existing text");c.save()
        out=self.edit(source,[{"id":"page/1/note","value":"Reviewed"}])
        text=PdfReader(out).pages[0].extract_text()
        self.assertIn("Existing text",text);self.assertIn("Reviewed",text)

    def test_duplicate_field_and_nonfinite_number_rejected(self):
        from openpyxl import Workbook
        source=self.root/"test.xlsx";Workbook().save(source)
        for changes in [[{"id":"A1","value":"x"}]*2,[{"id":"A1","value":"nan","type":"number"}]]:
            with self.assertRaises(ValueError):self.edit(source,changes)

    def test_sheet_window_is_exact(self):
        from openpyxl import Workbook,load_workbook
        source=self.root/"test.xlsx";book=Workbook();sheet=book.create_sheet("second");sheet["A55"]="before";book.save(source)
        out=self.edit(source,[{"id":"A55","value":"after"}],sheet="second",offset=50)
        self.assertEqual(load_workbook(out)["second"]["A55"].value,"after")


if __name__ == "__main__":
    unittest.main()
