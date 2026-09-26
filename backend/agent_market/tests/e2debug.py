"""Dev aid: print E2's geometric lines and the fields it found for one fixture.

    python -m agent_market.tests.e2debug <fixture file under fixtures/e2/docs>
"""
import os
import sys

from agent_market.engine.e2 import extract, parse
from agent_market.engine.e2.extract import _read_image, _read_pdf
from agent_market.engine.e2.words import build_lines

HERE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(os.path.dirname(HERE), "fixtures", os.environ.get("E2KIT_SET", "e2"), "docs", sys.argv[1])
if path.endswith(".pdf"):
    words, pages, method = _read_pdf(path, None)
else:
    words, pages, tf = _read_image(path, None)
    method = f"ocr {tf}"
print("method", method, [(p.number, p.ocr_conf) for p in pages])
for l in build_lines(words):
    segs = " | ".join(s.text for s in parse.segments(l))
    conf = min((w.conf for w in l.words), default=1)
    print(f"p{l.page} y{l.y0:6.1f} c{conf:.2f}  {segs}")
out = extract(path, os.path.basename(path))
for k, f in out["fields"].items():
    if f["normalized"] is not None:
        print(f"  {k:16s} {f['normalized']!s:40.40s} conf={f['confidence']}")
print("  type", out["document_type"]["normalized"], "| items", len(out["line_items"]), "| q", [q["key"] for q in out["questions"]])
