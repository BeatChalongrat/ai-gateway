"""Real OOXML round trips; no model, credentials or Office installation needed.

Run: python -m unittest discover -s services/chat-edge-api/scripts/tests -v
"""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from docx.shared import Pt as WordPt
from pptx import Presentation
from pptx.util import Inches, Pt
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docx_generator import generate_docx
from pptx_generator import generate_pptx
from excel_executor import execute_plan
from pdf_generator import generate_pdf


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OfficeEdits(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def deck(self):
        path = self.root / "source.pptx"
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(8), Inches(6)
        for name in ["first", "second", "third"]:
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            frame = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text_frame
            frame.text = name
        paragraph = prs.slides[0].shapes[0].text_frame.add_paragraph()
        for text, bold in [("Before old", True), (" name", False), (" after old name", False)]:
            run = paragraph.add_run()
            run.text = text
            run.font.bold, run.font.size = bold, Pt(23)
        table = prs.slides[0].shapes.add_table(1, 1, Inches(1), Inches(2), Inches(3), Inches(1)).table
        table.cell(0, 0).text = "old name"
        group = prs.slides[0].shapes.add_group_shape()
        group.shapes.add_textbox(Inches(1), Inches(4), Inches(3), Inches(1)).text_frame.text = "old name"
        prs.save(path)
        return path

    def test_pptx_replaces_across_runs_tables_groups_without_changing_source(self):
        source = self.deck()
        original = digest(source)
        output = self.root / "edited.pptx"
        result = generate_pptx({"actions": [{"type": "replace_text", "find": "old name", "replace": "ใหม่"}]}, str(output), str(source))
        saved = Presentation(output)
        paragraph = saved.slides[0].shapes[0].text_frame.paragraphs[1]
        self.assertEqual(paragraph.text, "Before ใหม่ after ใหม่")
        self.assertTrue(paragraph.runs[0].font.bold)
        self.assertEqual(paragraph.runs[0].font.size, Pt(23))
        self.assertFalse(paragraph.runs[2].font.bold)
        self.assertEqual(saved.slides[0].shapes[1].table.cell(0, 0).text, "ใหม่")
        self.assertEqual(saved.slides[0].shapes[2].shapes[0].text, "ใหม่")
        self.assertEqual((saved.slide_width, saved.slide_height), (Inches(8), Inches(6)))
        self.assertEqual(len(saved.slides), 3)
        self.assertEqual(result["slides_created"], 0)
        self.assertIn("4 occurrence", result["change_log"][0]["detail"])
        self.assertEqual(digest(source), original)

    def test_pptx_ordered_move_notes_delete_and_append(self):
        source = self.deck()
        output = self.root / "edited.pptx"
        result = generate_pptx({"actions": [
            {"type": "move_slide", "slide": 3, "to": 1},
            {"type": "set_notes", "slide": 1, "text": "บันทึกผู้บรรยาย"},
            {"type": "delete_slide", "slide": 3},
        ], "slides": [{"type": "content", "title": "appended"}]}, str(output), str(source))
        saved = Presentation(output)
        self.assertEqual([s.shapes[0].text.split("\n")[0] for s in saved.slides], ["third", "first", "appended"])
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
        self.assertEqual(saved.slides[0].notes_slide.notes_text_frame.text, "บันทึกผู้บรรยาย")
        self.assertEqual(result["slides_created"], 1)
        self.assertEqual(result["change_log"][0]["action"], 1)

    def test_pptx_invalid_edits_do_not_publish_output(self):
        source = self.deck()
        for action in [
            {"type": "replace_text", "find": "absent", "replace": "x"},
            {"type": "replace_text", "find": "first", "replace": "x", "slide": 2},
            {"type": "delete_slide", "slide": 0},
            {"type": "move_slide", "slide": True, "to": 1},
            {"type": "move_slide", "slide": 1, "to": 4},
            {"type": "unknown", "slide": 1},
        ]:
            with self.subTest(action=action):
                output = self.root / "invalid.pptx"
                with self.assertRaises(ValueError):
                    generate_pptx({"actions": [action]}, str(output), str(source))
                self.assertFalse(output.exists())

    def test_pptx_requires_source_and_retains_one_slide(self):
        output = self.root / "invalid.pptx"
        with self.assertRaises(ValueError):
            generate_pptx({"actions": [{"type": "set_notes", "slide": 1, "text": "x"}]}, str(output))
        source = self.deck()
        with self.assertRaises(ValueError):
            generate_pptx({"actions": [{"type": "delete_slide", "slide": 1}] * 3}, str(output), str(source))
        self.assertFalse(output.exists())

    def test_missing_source_never_silently_creates_a_new_document(self):
        for extension, generator in [("docx", generate_docx), ("pptx", generate_pptx)]:
            with self.subTest(extension=extension):
                output = self.root / f"output.{extension}"
                with self.assertRaises(FileNotFoundError):
                    generator({}, str(output), str(self.root / f"missing.{extension}"))
                self.assertFalse(output.exists())

    def test_docx_preserves_template_and_replaces_all_stories(self):
        source, output = self.root / "source.docx", self.root / "edited.docx"
        doc = Document()
        doc.styles["Normal"].font.name = "Cambria"
        doc.styles["Normal"].font.size = WordPt(19)
        p = doc.add_paragraph()
        p.add_run("Before old").bold = True
        p.add_run(" name after old name").italic = True
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "old name"
        doc.sections[0].header.paragraphs[0].text = "old name"
        doc.sections[0].footer.paragraphs[0].text = "old name"
        doc.save(source)
        original = digest(source)
        result = generate_docx({"document_settings": {"language": "th"}, "actions": [
            {"type": "replace_text", "find": "old name", "replace": "ใหม่"}
        ]}, str(output), str(source))
        saved = Document(output)
        self.assertTrue(result["success"])
        self.assertEqual(saved.styles["Normal"].font.name, "Cambria")
        self.assertEqual(saved.styles["Normal"].font.size, WordPt(19))
        self.assertEqual(saved.paragraphs[0].text, "Before ใหม่ after ใหม่")
        self.assertTrue(saved.paragraphs[0].runs[0].bold)
        self.assertTrue(saved.paragraphs[0].runs[1].italic)
        self.assertEqual(saved.tables[0].cell(0, 0).text, "ใหม่")
        self.assertEqual(saved.sections[0].header.paragraphs[0].text, "ใหม่")
        self.assertEqual(saved.sections[0].footer.paragraphs[0].text, "ใหม่")
        self.assertEqual(digest(source), original)

    def test_docx_missing_text_reports_failure(self):
        result = generate_docx({"actions": [{"type": "replace_text", "find": "absent", "replace": "x"}]}, str(self.root / "failed.docx"))
        self.assertFalse(result["success"])
        self.assertEqual(result["actions_failed"], 1)

    def test_docx_explicit_typography_and_creation_defaults(self):
        source, output = self.root / "source.docx", self.root / "edited.docx"
        generate_docx({}, str(source))
        self.assertEqual(Document(source).styles["Normal"].font.name, "Arial")
        generate_docx({"document_settings": {"font_family": "Sarabun", "base_font_size": 16}}, str(output), str(source))
        self.assertEqual(Document(output).styles["Normal"].font.name, "Sarabun")
        self.assertEqual(Document(output).styles["Normal"].font.size, WordPt(16))

    def test_excel_edits_preserve_formulas_styles_and_source(self):
        source, output, plan = self.root / "source.xlsx", self.root / "edited.xlsx", self.root / "plan.json"
        wb = Workbook()
        ws = wb.active
        ws.title = "Budget"
        ws["A1"], ws["A2"], ws["A3"] = 10, 20, "=SUM(A1:A2)"
        ws["A1"].font = Font(name="Sarabun", size=16, bold=True)
        ws["A1"].number_format = "#,##0.00"
        wb.save(source)
        wb.close()
        original = digest(source)
        plan.write_text(json.dumps({"actions": [{"type": "write_cells", "sheet_name": "Budget", "cells": [{"cell": "A1", "value": 30}]}]}), encoding="utf-8")
        result = execute_plan(str(plan), str(source), str(output))
        saved = load_workbook(output)
        self.addCleanup(saved.close)
        self.assertTrue(result["success"])
        self.assertEqual(saved["Budget"]["A1"].value, 30)
        self.assertEqual(saved["Budget"]["A3"].value, "=SUM(A1:A2)")
        self.assertEqual(saved["Budget"]["A1"].font.name, "Sarabun")
        self.assertTrue(saved["Budget"]["A1"].font.bold)
        self.assertEqual(saved["Budget"]["A1"].number_format, "#,##0.00")
        self.assertEqual(digest(source), original)
        # openpyxl preserves formulas but does not calculate their values.
        cached = load_workbook(output, data_only=True)
        self.addCleanup(cached.close)
        self.assertIsNone(cached["Budget"]["A3"].value)

    def test_excel_failed_action_is_not_a_successful_plan(self):
        source, output, plan = self.root / "source.xlsx", self.root / "edited.xlsx", self.root / "plan.json"
        wb = Workbook()
        wb.save(source)
        wb.close()
        plan.write_text(json.dumps({"actions": [{"type": "rename_sheet", "sheet_name": "missing", "new_name": "new"}]}), encoding="utf-8")
        self.assertFalse(execute_plan(str(plan), str(source), str(output))["success"])

    def test_pdf_contains_english_and_thai_text(self):
        output = self.root / "report.pdf"
        result = generate_pdf({"document_settings": {"language": "th"}, "actions": [
            {"type": "add_heading", "text": "รายงาน Office"},
            {"type": "add_paragraph", "text": "ทดสอบเอกสาร Test 123"},
        ]}, str(output))
        self.assertTrue(result["success"])
        text = "".join(page.extract_text() for page in PdfReader(output).pages)
        self.assertIn("Test 123", text)
        self.assertIn("รายงาน", text)

    def test_pdf_failed_action_is_not_a_successful_plan(self):
        result = generate_pdf({"actions": [
            {"type": "add_paragraph", "text": "valid"},
            {"type": "add_heading", "level": "invalid", "text": "invalid"},
        ]}, str(self.root / "failed.pdf"))
        self.assertFalse(result["success"])
        self.assertEqual(result["actions_failed"], 1)


if __name__ == "__main__":
    unittest.main()
