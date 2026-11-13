import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from office_templates import catalog, create, inspect_file


class OfficeTemplates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def document(self):
        return {"title": "รายงานโครงการ Project review", "language": "th", "sections": [
            {"heading": "ผลการทำงาน", "body": "ข้อมูลจากผู้ใช้ <not markup> & facts", "bullets": ["ตรวจข้อมูลแล้ว", "กำหนดผู้รับผิดชอบ"],
             "table": {"headers": ["รายการ", "จำนวน"], "rows": [["งาน", 12]]}, "sources": ["User-provided data, 2026-11-12"], "notes": "Decision requested"}]}

    def test_every_catalog_template_creates_reopenable_output(self):
        for template in catalog()["templates"]:
            with self.subTest(template=template["id"]):
                data = self.document()
                key = template["id"]
                if key == "pdf.invoice":
                    data.update(invoice_number="INV-001", customer="Example", currency="THB", items=[{"description": "บริการ", "quantity": 2, "unit_price": "10.25"}])
                elif key == "xlsx.budget":
                    data["rows"] = [{"item": "Engineering", "planned": 500, "actual": 420}]
                elif key == "xlsx.tracker":
                    data["rows"] = [{"task": "Review", "owner": "Owner", "status": "Open", "due_date": "2026-11-30"}]
                elif key in ("xlsx.analysis", "csv.data_export"):
                    data.update(columns=[{"key": "id", "label": "ID", "type": "text"}, {"key": "value", "label": "Amount", "type": "number"}], rows=[{"id": "00123", "value": 12.5}])
                path = self.root / (key + "." + template["format"])
                result = create(key, data, path)
                self.assertGreater(result["bytes"], 20)
                self.assertEqual(result["filename"], path.name)
                self.assertEqual(inspect_file(path)["filename"], path.name)

    def test_budget_formulas_reference_actual_rows_and_print_settings(self):
        from openpyxl import load_workbook
        path = self.root / "budget.xlsx"
        result = create("xlsx.budget", {"title": "Budget", "rows": [{"item": "A", "planned": 100, "actual": 80}, {"item": "B", "planned": 50, "actual": 60}]}, path)
        wb = load_workbook(path)
        ws = wb.active
        self.assertEqual(ws["D2"].value, "=B2-C2")
        self.assertEqual(ws["B4"].value, "=SUM(B2:B3)")
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(ws.page_setup.fitToWidth, 1)
        self.assertEqual(result["missing_cached_values"], 5)
        wb.close()

    def test_literal_identifiers_and_formulas_are_distinct_in_xlsx(self):
        from openpyxl import load_workbook
        path = self.root / "analysis.xlsx"
        create("xlsx.analysis", {"title": "Data", "columns": [{"key": "id"}, {"key": "text"}, {"key": "n", "type": "number"}, {"key": "calculated", "type": "formula"}], "rows": [{"id": "00123", "text": "=1+1", "n": 3, "calculated": "=C2*2"}]}, path)
        wb = load_workbook(path)
        ws = wb.active
        self.assertEqual(ws["A2"].value, "00123")
        self.assertEqual(ws["B2"].data_type, "s")
        self.assertEqual(ws["D2"].data_type, "f")
        wb.close()

    def test_csv_quotes_unicode_newlines_and_formula_text(self):
        path = self.root / "data.csv"
        create("csv.data_export", {"columns": [{"key": "text"}, {"key": "amount", "type": "number"}], "rows": [{"text": 'ไทย, "quoted"\nnext', "amount": -12}, {"text": " =SUM(A1)", "amount": 0}, {"text": "00123", "amount": 1}]}, path)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][0], 'ไทย, "quoted"\nnext')
        self.assertEqual(rows[1][1], "-12.0")
        self.assertEqual(rows[2][0], "' =SUM(A1)")
        self.assertEqual(rows[3][0], "00123")

    def test_csv_rejects_formula_column(self):
        with self.assertRaisesRegex(ValueError, "CSV"):
            create("csv.data_export", {"columns": [{"key": "x", "type": "formula"}], "rows": [{"x": "=1+1"}]}, self.root / "bad.csv")

    def test_invalid_numeric_and_missing_budget_are_not_silently_zero(self):
        for row in ({"item": "A", "planned": 10}, {"item": "A", "planned": "NaN", "actual": 5}):
            with self.assertRaises(ValueError):
                create("xlsx.budget", {"title": "Budget", "rows": [row]}, self.root / "bad.xlsx")
            self.assertFalse((self.root / "bad.xlsx").exists())

    def test_no_overwrite_and_no_extension_mismatch(self):
        path = self.root / "original.docx"
        create("docx.business_report", self.document(), path)
        before = hashlib.sha256(path.read_bytes()).digest()
        with self.assertRaises(FileExistsError):
            create("docx.business_report", self.document(), path)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())
        with self.assertRaises(ValueError):
            create("docx.business_report", self.document(), self.root / "wrong.pdf")

    def test_presentation_paginates_and_retains_speaker_notes(self):
        from pptx import Presentation
        data = self.document()
        data["sections"][0]["bullets"] = ["Fact " + str(i) for i in range(12)]
        path = self.root / "deck.pptx"
        result = create("pptx.executive_brief", data, path)
        prs = Presentation(path)
        self.assertGreaterEqual(len(prs.slides), 5)
        self.assertIn("Decision requested", prs.slides[1].notes_slide.notes_text_frame.text)
        self.assertEqual(result["warnings"], [])

    def test_pdf_invoice_totals_and_markup_escaping(self):
        from pypdf import PdfReader
        path = self.root / "invoice.pdf"
        create("pdf.invoice", {"title": "Invoice", "invoice_number": "INV-001", "customer": "A & B <literal>", "currency": "THB", "items": [{"description": "Service", "quantity": 3, "unit_price": "10.25"}]}, path)
        text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
        self.assertIn("30.75", text)
        self.assertIn("A & B <literal>", text)

    def test_table_only_slide_has_no_empty_duplicate(self):
        from pptx import Presentation
        path = self.root / "table.pptx"
        create("pptx.project_update", {"title": "Update", "sections": [{"heading": "Status", "table": {"headers": ["Task", "Status"], "rows": [["Review", "Done"]]}}]}, path)
        self.assertEqual(len(Presentation(path).slides), 2)

    def test_missing_report_content_is_an_error(self):
        with self.assertRaises(ValueError):
            create("docx.proposal", {"title": "Proposal"}, self.root / "empty.docx")
        self.assertFalse((self.root / "empty.docx").exists())


if __name__ == "__main__":
    unittest.main()
