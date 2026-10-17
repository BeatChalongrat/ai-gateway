"""AM24 Part 5 — the deck's slide number reads as the slide's number to anything that reads the deck's text.

The slide-number field was written with the cached text "1" on every slide (AM21 §8): PowerPoint and LibreOffice
draw the field's own number, so the picture was right, but search, a screen reader and any check that reads the
deck back read "1" on every slide. Each field now carries its slide's number.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from pptx import Presentation

from agent_market.tests import samplekit

SLIDENUM = "{http://schemas.openxmlformats.org/drawingml/2006/main}fld"


class TheSlideNumber(unittest.TestCase):
    def test_every_slide_number_field_reads_its_slides_number(self):
        for pkg in ("financial_report", "bank_reconciliation"):
            with self.subTest(pkg=pkg):
                d = tempfile.mkdtemp()
                try:
                    samplekit.render_sample(pkg, out_dir=d, formats=["pptx"])
                    decks = [f for f in os.listdir(d) if f.endswith(".pptx")]
                    self.assertEqual(len(decks), 1, decks)
                    prs = Presentation(os.path.join(d, decks[0]))
                    seen = []
                    for n, slide in enumerate(prs.slides, start=1):
                        for fld in slide.shapes._spTree.iter(SLIDENUM):
                            if fld.get("type") == "slidenum":
                                seen.append((n, "".join(t.text or "" for t in fld)))
                    self.assertGreater(len(seen), 2, "the deck's slides carry the field")
                    self.assertEqual(seen, [(n, str(n)) for n, _ in seen])
                finally:
                    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
