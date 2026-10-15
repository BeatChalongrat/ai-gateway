"""AM21 — the contract: content and layout are separate, and the gate holds them apart.

* No caller places anything. The renderer is deliverables/look and the format writers; every other
  file of the engine — the packages, the content model, the engine — may not set a font, a size, a
  colour, a coordinate or a box. This test reads the generators' code and fails naming the file and
  the line.
* The scale is closed: a size or a colour that is not in look.json is refused, not approximated.
* The catalogue chooses, with a reason, the same way every time; content too long for every layout is
  refused naming the section (the refusal case is the test — CARD-RULES §6c).
* A bracketed build or card marker, or the product's name, in a deliverable fails the gate.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest

from agent_market.deliverables.look import catalogue, markers
from agent_market.deliverables.look.tokens import LOOK, LookError, contrast

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)   # backend/agent_market

# the renderer: the only code that may name a size, a colour, a font, a coordinate or a box
RENDERER = (
    "deliverables/look/",
    "deliverables/report_docx.py", "deliverables/deck_pptx.py", "deliverables/table_xlsx.py", "deliverables/charts.py",
    "deliverables/workpaper.py", "deliverables/signoff.py", "deliverables/fit.py",
    # the customer's own form is filled into the customer's own boxes: a renderer of their geometry (AM2, AM8)
    "deliverables/template_fill.py", "deliverables/template_out.py", "deliverables/form_regions.py", "deliverables/pdf_form.py",
)
# what a caller may not write
GEOMETRY = re.compile(
    r"\b(?:Pt|Inches|Cm|Emu|Mm)\(|\bRGBColor\b|\bFont\(|\bPatternFill\(|\bSide\(|\bBorder\(|\bAlignment\(|\bNamedStyle\(|"
    r"\.font\.(?:size|name|color|bold)\s*=|\.(?:width|height|left|top)\s*=|column_dimensions\[|row_dimensions\[|"
    r"add_textbox\(|add_shape\(|add_picture\(|slide_layouts\[|number_format\s*=|freeze_panes\s*=|"
    r"[\"'](?=[0-9A-F]*[A-F])[0-9A-F]{6}[\"']"
)
# not read: the tests and tools; the engine's readers of a customer's files (e2 reads scans, e3 matches, e4 reads
# snapshots: they measure what they read, they place nothing); the gate and the re-reader, which read sizes back
NOT_READ = ("tests/", "tools/", "__pycache__", "/evals/", "fixtures/", "engine/e2/", "engine/e3/", "engine/e4/",
            "deliverables/quality.py", "deliverables/reread.py")


def _sources():
    for folder, _, files in os.walk(ROOT):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(folder, f)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if any(n in rel for n in NOT_READ):
                continue
            yield rel, path


class NoCallerPlacesAnything(unittest.TestCase):
    def test_no_code_outside_the_renderer_sets_a_font_a_size_a_colour_a_coordinate_or_a_box(self):
        hits = []
        for rel, path in _sources():
            if rel.startswith(RENDERER) or rel in RENDERER:
                continue
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, start=1):
                    code = line.split("#", 1)[0]
                    if GEOMETRY.search(code):
                        hits.append(f"{rel}:{i}: {line.strip()[:90]}")
        self.assertEqual(hits, [], "a caller sets geometry or a look — that belongs to the renderer (deliverables/look):\n  " + "\n  ".join(hits[:40]))

    def test_the_renderer_itself_takes_its_numbers_from_look_json(self):
        # no hex colour and no point size is typed into the writers: they ask the scale and the palette
        hits = []
        for rel, path in _sources():
            if not rel.startswith(("deliverables/report_docx.py", "deliverables/deck_pptx.py", "deliverables/look/dotx.py",
                                   "deliverables/look/potx.py")):
                continue
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, start=1):
                    code = line.split("#", 1)[0]
                    if re.search(r"[\"'][0-9A-Fa-f]{6}[\"']", code) or re.search(r"\bPt\(\s*\d", code):
                        hits.append(f"{rel}:{i}: {line.strip()[:90]}")
        self.assertEqual(hits, [])


class TheScaleIsClosed(unittest.TestCase):
    def test_five_roles_per_format_going_down(self):
        self.assertEqual(LOOK.roles, ("display", "heading", "subheading", "body", "small"))
        for fmt in ("docx", "pptx", "xlsx"):
            sizes = LOOK.sizes(fmt)
            self.assertEqual(len(sizes), 5, fmt)
            self.assertEqual(sizes, sorted(sizes, reverse=True), fmt)

    def test_a_size_outside_the_scale_is_refused_not_approximated(self):
        with self.assertRaises(LookError) as cm:
            LOOK.pt("docx", "tiny")
        self.assertIn("display · heading · subheading · body · small", str(cm.exception))
        with self.assertRaises(LookError):
            LOOK.role_of("pptx", 11)
        self.assertEqual(LOOK.role_of("pptx", LOOK.pt("pptx", "body")), "body")

    def test_a_colour_outside_the_palette_is_refused(self):
        palette = LOOK.theme("navy")["palette"]
        with self.assertRaises(LookError) as cm:
            LOOK.color(palette, "pink")
        self.assertIn("primary", str(cm.exception))
        self.assertEqual(LOOK.color(palette, "primary"), "12264A")

    def test_every_theme_is_a_complete_set_and_two_differ_visibly(self):
        ids = LOOK.theme_ids
        self.assertGreaterEqual(len(ids), 2)
        navy, teal = LOOK.theme("navy"), LOOK.theme("teal")
        self.assertEqual(set(navy["palette"]), set(teal["palette"]))
        self.assertNotEqual(navy["palette"]["primary"], teal["palette"]["primary"])
        self.assertNotEqual(navy["cover"], teal["cover"])
        self.assertNotEqual(navy["table"], teal["table"])
        for tid in ids:
            t = LOOK.theme(tid)
            self.assertGreaterEqual(contrast(t["palette"]["primary"], t["palette"]["on_primary"]), 4.5, tid)
            self.assertGreaterEqual(contrast(t["palette"]["ink"], t["palette"]["paper"]), 7, tid)
            self.assertEqual(len(t["chart"]), len(set(t["chart"])), f"{tid}: a chart colour repeats")


class TheCatalogueChooses(unittest.TestCase):
    def test_the_same_content_gets_the_same_layout_with_a_recorded_reason(self):
        content = {"title": "หัวข้อ", "items": ["หนึ่ง", "สอง", "สาม"]}
        a = catalogue.choose("pptx", content, "a section")
        b = catalogue.choose("pptx", dict(content), "a section")
        self.assertEqual((a.layout, a.role, a.fill, a.reason), (b.layout, b.role, b.fill, b.reason))
        self.assertEqual(a.layout, "bullets")
        self.assertIn("bullets at body", a.reason)

    def test_too_many_points_break_across_slides_and_a_long_point_steps_down_the_scale(self):
        many = catalogue.choose("pptx", {"title": "t", "items": [f"ข้อ {i}" for i in range(9)]}, "s", kinds=("bullets",))
        self.assertEqual(len(many.parts), 3)
        self.assertEqual([len(p["items"]) for p in many.parts], [4, 4, 1])
        long = catalogue.choose("pptx", {"title": "t", "items": ["ก" * 140]}, "s", kinds=("bullets",))
        self.assertEqual(long.role, "small")

    def test_content_too_long_for_every_layout_is_refused_naming_the_section(self):
        with self.assertRaises(catalogue.DoesNotFit) as cm:
            catalogue.choose("pptx", {"title": "t", "items": ["ก" * 2000]}, "ข้อสังเกต", kinds=("bullets",))
        self.assertEqual(cm.exception.section, "ข้อสังเกต")
        self.assertIn("item 2000", cm.exception.why)
        with self.assertRaises(catalogue.DoesNotFit):
            catalogue.choose("docx", {"paragraph": "ก" * 5000}, "สรุปผู้บริหาร", kinds=("prose",))

    def test_the_catalogue_is_the_closed_set_the_card_names(self):
        self.assertEqual(list(LOOK.catalogue("pptx")), ["title", "section", "figure", "figures", "table", "chart", "bullets", "statement", "closing"])
        self.assertEqual(list(LOOK.catalogue("docx")), ["cover", "section", "table", "chart", "prose", "appendix"])
        for fmt in ("pptx", "docx", "xlsx"):
            for name, lay in LOOK.catalogue(fmt).items():
                self.assertTrue(lay["for"] and lay["refuses"] and lay["takes"], f"{fmt}.{name}")

    def test_the_limits_a_model_is_told_are_the_catalogues(self):
        lim = catalogue.writing_limits()
        self.assertEqual(lim["bullets_max"], LOOK.layout("pptx", "bullets")["takes"]["items"])
        self.assertEqual(lim["summary_chars"], LOOK.layout("pptx", "statement")["takes"]["text"])


class NoMarkerOnADocument(unittest.TestCase):
    def test_a_build_or_card_marker_and_the_products_name_are_found(self):
        self.assertEqual(markers.hits("[AM1b] บริษัท ตัวอย่าง จำกัด"), ["[AM1b]"])
        self.assertEqual(markers.hits("รายงาน [FIX122] · SRAN Agent Market"), ["[FIX122]", "SRAN", "Agent Market"])
        self.assertEqual(markers.hits("บริษัท สยามตัวอย่างการค้า จำกัด"), [])

    def test_a_workbook_with_a_marker_in_a_cell_fails_the_gate_naming_the_sheet(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "สรุป"
        ws["A1"] = "[AM1b] บริษัท ตัวอย่าง จำกัด"
        path = os.path.join(tempfile.mkdtemp(), "m.xlsx")
        wb.save(path)
        c = markers.check(path)
        self.assertFalse(c["ok"])
        self.assertIn("[AM1b]", c["detail"])
        self.assertIn("สรุป", c["detail"])

    def test_a_clean_workbook_passes(self):
        from openpyxl import Workbook

        wb = Workbook()
        wb.active["A1"] = "บริษัท สยามตัวอย่างการค้า จำกัด"
        path = os.path.join(tempfile.mkdtemp(), "ok.xlsx")
        wb.save(path)
        self.assertTrue(markers.check(path)["ok"])


if __name__ == "__main__":
    unittest.main()
