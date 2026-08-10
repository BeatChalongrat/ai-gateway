#!/usr/bin/env python3
"""Tests for scripts/preview_data.py (Phase 16).

Builds real xlsx / csv / docx / pptx fixtures with the same libraries
the sandbox image carries, runs the script as the sandbox runs it
(argv = <file> <params.json>), and asserts on the JSON it prints.

Run from services/chat-edge-api:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "preview_data.py"

sys.path.insert(0, str(ROOT / "scripts"))
import preview_data  # noqa: E402


def run(path: Path, **params) -> tuple[int, dict, str]:
    """Invoke the script the way sandbox_runner.py does."""
    with tempfile.TemporaryDirectory() as tmp:
        params_path = Path(tmp) / "params.json"
        params_path.write_text(json.dumps(params), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), str(path), str(params_path)],
            capture_output=True, text=True, encoding="utf-8",
        )
    payload = {}
    if proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            payload = {"__raw": proc.stdout}
    return proc.returncode, payload, proc.stderr


class CellRendering(unittest.TestCase):
    """A grid is supposed to look like the file, not like repr()."""

    def test_numbers_lose_the_float_noise(self):
        self.assertEqual(preview_data.cell_text(5), "5")
        self.assertEqual(preview_data.cell_text(5.0), "5")
        self.assertEqual(preview_data.cell_text(0.1 + 0.2), "0.3")
        self.assertEqual(preview_data.cell_text(1234.56), "1234.56")

    def test_dates_drop_a_midnight_time(self):
        self.assertEqual(
            preview_data.cell_text(dt.datetime(2026, 8, 6, 0, 0)), "2026-08-06")
        self.assertEqual(
            preview_data.cell_text(dt.datetime(2026, 8, 6, 14, 30)),
            "2026-08-06 14:30")

    def test_blanks_and_booleans(self):
        self.assertEqual(preview_data.cell_text(None), "")
        self.assertEqual(preview_data.cell_text(True), "TRUE")
        self.assertEqual(preview_data.cell_text(False), "FALSE")

    def test_a_long_cell_is_capped(self):
        out = preview_data.cell_text("x" * 5000)
        self.assertLessEqual(len(out), preview_data.MAX_CELL_CHARS + 1)

    def test_column_letters_pass_z(self):
        self.assertEqual(preview_data.column_letter(1), "A")
        self.assertEqual(preview_data.column_letter(26), "Z")
        self.assertEqual(preview_data.column_letter(27), "AA")


class ParamClamping(unittest.TestCase):
    def test_out_of_range_and_garbage_are_clamped(self):
        self.assertEqual(preview_data.clamp(9999, 1, 500, 200), 500)
        self.assertEqual(preview_data.clamp(-5, 0, 500, 200), 0)
        self.assertEqual(preview_data.clamp("abc", 1, 500, 200), 200)
        self.assertEqual(preview_data.clamp(None, 1, 500, 200), 200)


class TypeRouting(unittest.TestCase):
    def test_extension_wins_when_the_type_is_generic(self):
        self.assertEqual(
            preview_data.kind_for("application/octet-stream", "a.xlsx"), "xlsx")
        self.assertEqual(preview_data.kind_for("", "a.docx"), "docx")
        self.assertEqual(preview_data.kind_for("", "a.pptx"), "pptx")
        self.assertEqual(preview_data.kind_for("text/csv", "a.txt"), "csv")

    def test_unsupported_types_are_refused(self):
        self.assertEqual(preview_data.kind_for("application/pdf", "a.pdf"), "")
        self.assertEqual(preview_data.kind_for("application/zip", "a.zip"), "")


class Spreadsheets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from openpyxl import Workbook

        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "book.xlsx"
        wb = Workbook()
        first = wb.active
        first.title = "ยอดขาย"          # Thai sheet name must survive
        first.append(["ชื่อ", "จำนวน", "วันที่"])
        for i in range(1, 251):
            first.append([f"แถว {i}", i, dt.datetime(2026, 1, 1)])
        second = wb.create_sheet("Notes")
        second.append(["only", "two", "columns"])
        wb.save(cls.path)

        cls.empty = Path(cls.tmp.name) / "empty.xlsx"
        empty = Workbook()
        empty.save(cls.empty)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_first_sheet_is_returned_with_every_sheet_name(self):
        code, out, err = run(self.path, content_type="", filename="book.xlsx")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["kind"], "sheet")
        sheet = out["sheet"]
        self.assertEqual(sheet["names"], ["ยอดขาย", "Notes"])
        self.assertEqual(sheet["name"], "ยอดขาย")
        self.assertEqual(sheet["columns"], ["A", "B", "C"])
        self.assertEqual(sheet["rows"][0], ["ชื่อ", "จำนวน", "วันที่"])
        # 1 header + 250 data rows.
        self.assertEqual(sheet["total_rows"], 251)

    def test_a_second_sheet_is_selectable(self):
        code, out, err = run(self.path, filename="book.xlsx", sheet=1)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["sheet"]["name"], "Notes")
        self.assertEqual(out["sheet"]["index"], 1)

    def test_a_sheet_index_past_the_end_falls_back_to_the_last(self):
        code, out, err = run(self.path, filename="book.xlsx", sheet=99)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["sheet"]["name"], "Notes")

    def test_pagination_walks_the_rows_without_repeating(self):
        _, first, _ = run(self.path, filename="book.xlsx", offset=0, limit=100)
        _, second, _ = run(self.path, filename="book.xlsx", offset=100, limit=100)
        self.assertEqual(len(first["sheet"]["rows"]), 100)
        self.assertTrue(first["sheet"]["has_more"])
        self.assertEqual(second["sheet"]["offset"], 100)
        self.assertNotEqual(first["sheet"]["rows"][0], second["sheet"]["rows"][0])
        # Row 101 of the file = the 100th data row.
        self.assertEqual(second["sheet"]["rows"][0][0], "แถว 100")

    def test_the_last_page_says_there_is_no_more(self):
        code, out, err = run(self.path, filename="book.xlsx", offset=200, limit=200)
        self.assertEqual(code, 0, err)
        self.assertFalse(out["sheet"]["has_more"])

    def test_the_row_budget_is_a_hard_cap(self):
        code, out, err = run(self.path, filename="book.xlsx", limit=99999)
        self.assertEqual(code, 0, err)
        self.assertLessEqual(len(out["sheet"]["rows"]), preview_data.MAX_ROWS)

    def test_an_empty_workbook_is_not_an_error(self):
        code, out, err = run(self.empty, filename="empty.xlsx")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["sheet"]["rows"], [])
        self.assertFalse(out["sheet"]["has_more"])

    def test_a_formula_yields_its_cached_value_never_the_formula(self):
        from openpyxl import Workbook

        path = Path(self.tmp.name) / "formula.xlsx"
        wb = Workbook()
        ws = wb.active
        ws["A1"] = 2
        ws["B1"] = "=A1*3"
        wb.save(path)

        code, out, err = run(path, filename="formula.xlsx")
        self.assertEqual(code, 0, err)
        # openpyxl wrote no cached value, so the cell is blank. What
        # matters is that the FORMULA TEXT never reaches the client:
        # a preview must not show, or run, the document's own logic.
        self.assertNotIn("=A1*3", json.dumps(out, ensure_ascii=False))


class CsvFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_quoted_separators_stay_inside_one_cell(self):
        self.path.write_text(
            'name,note\n"Doe, John",ok\nplain,fine\n', encoding="utf-8")
        code, out, err = run(self.path, content_type="text/csv", filename="data.csv")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["sheet"]["rows"][1], ["Doe, John", "ok"])
        self.assertEqual(out["sheet"]["total_rows"], 3)

    def test_an_excel_bom_does_not_stick_to_the_first_header(self):
        self.path.write_bytes("﻿a,b\n1,2\n".encode("utf-8"))
        code, out, err = run(self.path, filename="data.csv")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["sheet"]["rows"][0], ["a", "b"])

    def test_short_rows_are_padded_to_the_widest(self):
        self.path.write_text("a,b,c\n1\n", encoding="utf-8")
        code, out, err = run(self.path, filename="data.csv")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["sheet"]["rows"][1], ["1", "", ""])


class WordDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from docx import Document

        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "doc.docx"
        doc = Document()
        doc.add_heading("รายงานประจำเดือน", level=1)
        doc.add_paragraph("บทนำของเอกสาร")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "หัว"
        table.cell(0, 1).text = "ค่า"
        table.cell(1, 0).text = "A"
        table.cell(1, 1).text = "1"
        doc.add_heading("สรุป", level=2)
        doc.add_paragraph("ปิดท้าย")
        doc.save(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_headings_keep_their_level(self):
        code, out, err = run(self.path, filename="doc.docx")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["kind"], "document")
        blocks = out["document"]["blocks"]
        headings = [b for b in blocks if b["type"] == "heading"]
        self.assertEqual(headings[0]["text"], "รายงานประจำเดือน")
        self.assertEqual(headings[0]["level"], 1)
        self.assertEqual(headings[1]["level"], 2)

    def test_a_table_stays_in_document_order(self):
        # The flat text extractor moves every table to the end; the
        # preview must keep the table between the two headings, which
        # is the whole point of showing structure.
        _, out, _ = run(self.path, filename="doc.docx")
        types = [b["type"] for b in out["document"]["blocks"]]
        self.assertEqual(
            types, ["heading", "paragraph", "table", "heading", "paragraph"])

    def test_the_table_rows_come_back_as_cells(self):
        _, out, _ = run(self.path, filename="doc.docx")
        table = next(b for b in out["document"]["blocks"] if b["type"] == "table")
        self.assertEqual(table["rows"][0], ["หัว", "ค่า"])

    def test_blocks_paginate(self):
        code, out, err = run(self.path, filename="doc.docx", offset=0, limit=2)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["document"]["blocks"]), 2)
        self.assertTrue(out["document"]["has_more"])
        self.assertEqual(out["document"]["total_blocks"], 5)


class Presentations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pptx import Presentation
        from pptx.util import Inches

        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "deck.pptx"
        prs = Presentation()
        layout = prs.slide_layouts[1]
        for i in range(1, 4):
            slide = prs.slides.add_slide(layout)
            slide.shapes.title.text = f"สไลด์ {i}"
            slide.placeholders[1].text = f"เนื้อหา {i}"
        blank = prs.slides.add_slide(prs.slide_layouts[6])
        table = blank.shapes.add_table(
            2, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
        table.cell(0, 0).text = "col"
        table.cell(0, 1).text = "val"
        prs.save(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_slide_is_its_own_entry_with_a_title(self):
        code, out, err = run(self.path, filename="deck.pptx")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["kind"], "slides")
        slides = out["slides"]["slides"]
        self.assertEqual(out["slides"]["total_slides"], 4)
        self.assertEqual(slides[0]["index"], 1)
        self.assertEqual(slides[0]["title"], "สไลด์ 1")
        self.assertIn("เนื้อหา 1",
                      [b.get("text") for b in slides[0]["blocks"]])

    def test_the_title_is_not_repeated_in_the_body(self):
        _, out, _ = run(self.path, filename="deck.pptx")
        first = out["slides"]["slides"][0]
        self.assertNotIn(first["title"], [b.get("text") for b in first["blocks"]])

    def test_a_slide_table_comes_through(self):
        _, out, _ = run(self.path, filename="deck.pptx")
        last = out["slides"]["slides"][-1]
        table = next(b for b in last["blocks"] if b["type"] == "table")
        self.assertEqual(table["rows"][0], ["col", "val"])

    def test_slides_paginate(self):
        code, out, err = run(self.path, filename="deck.pptx", offset=2, limit=1)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["slides"]["slides"]), 1)
        self.assertEqual(out["slides"]["slides"][0]["index"], 3)
        self.assertTrue(out["slides"]["has_more"])


class Failures(unittest.TestCase):
    def test_an_unsupported_type_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            path.write_bytes(b"%PDF-1.4")
            code, _, _ = run(path, content_type="application/pdf", filename="a.pdf")
            self.assertEqual(code, 3)

    def test_a_corrupt_workbook_fails_loudly_rather_than_printing_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.xlsx"
            path.write_bytes(b"this is not a zip archive")
            code, out, _ = run(path, filename="broken.xlsx")
            self.assertEqual(code, 4)
            self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
