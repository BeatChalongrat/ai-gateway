"""No build marker, card marker or product name on a customer's document (AM21).

The owner's own report carried “[AM1b]” on its cover, in the running header of every page, on the deck's
cover and byline and in the PDF's Author field: a company name typed with a card's marker in front of it
during a walkthrough, printed on a document an auditor reads. ``check`` opens a finished file and reads
every text it prints and its metadata, and fails naming where such a marker — or our own product's name —
appears; ``hits`` is the same rule on one text, for a setting before it is saved.
"""

from __future__ import annotations

import os
import re
import zipfile

from ...engine.words import SHARED as S
from ..reread import check as _check

# [AM1b] · [FIX122] · [AM-21] · [NIGHT2] · [HOTFIX]: a bracketed build or card marker
MARKER = re.compile(r"\[(?:AM|FIX|NIGHT|HOTFIX|TEST|E)[- ]?\d*[A-Za-z]?\]")
# the product's own name on a customer's paper
PRODUCT = re.compile(r"\bSRAN\b|(?i:\bAgent Market\b)")
CHECK = "look.markers"


def hits(text: str) -> list[str]:
    """The markers and product names a text carries, in order, each once."""
    out: list[str] = []
    for m in list(MARKER.finditer(text or "")) + list(PRODUCT.finditer(text or "")):
        if m.group(0) not in out:
            out.append(m.group(0))
    return out


def _props(z: zipfile.ZipFile) -> str:
    try:
        core = z.read("docProps/core.xml").decode("utf-8", "replace")
    except KeyError:
        return ""
    return " ".join(re.findall(r">([^<]+)<", core))


def texts_of(path: str) -> list[tuple[str, str]]:
    """Every text a finished file prints, with where it is: (where, text)."""
    out: list[tuple[str, str]] = []
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        from docx import Document

        doc = Document(path)
        out += [(S("gate.where.body"), p.text) for p in doc.paragraphs]
        out += [(S("gate.where.table"), c.text) for t in doc.tables for row in t.rows for c in row.cells]
        for sec in doc.sections:
            out += [(S("gate.where.header"), p.text) for p in sec.header.paragraphs]
            out += [(S("gate.where.header"), p.text) for t in sec.header.tables for row in t.rows for c in row.cells for p in c.paragraphs]
            out += [(S("gate.where.footer"), p.text) for p in sec.footer.paragraphs]
            out += [(S("gate.where.footer"), p.text) for t in sec.footer.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    elif ext == ".pptx":
        from pptx import Presentation

        for i, slide in enumerate(Presentation(path).slides, start=1):
            where = S("gate.where.slide", i=i)
            for sh in slide.shapes:
                if sh.has_text_frame:
                    out.append((where, sh.text_frame.text))
                if getattr(sh, "has_table", False) and sh.has_table:
                    out += [(where, c.text) for row in sh.table.rows for c in row.cells]
            if slide.has_notes_slide:
                out.append((where, slide.notes_slide.notes_text_frame.text))
    elif ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path)
        for ws in wb.worksheets:
            where = S("gate.where.sheet", sheet=ws.title)
            out.append((where, ws.title))
            out += [(where, str(v)) for row in ws.iter_rows(values_only=True) for v in row if isinstance(v, str)]
            for part in (ws.oddHeader, ws.oddFooter, ws.evenHeader, ws.evenFooter):
                for side in (part.left, part.center, part.right):
                    if side.text:
                        out.append((where, side.text))
    elif ext == ".pdf":
        from pypdf import PdfReader

        r = PdfReader(path)
        meta = r.metadata or {}
        out += [(S("gate.where.metadata"), str(v)) for v in meta.values() if v]
        for i, page in enumerate(r.pages[:60], start=1):
            out.append((S("gate.where.page", i=i), page.extract_text() or ""))
        return [(w, t) for w, t in out if t]
    if ext in (".docx", ".pptx", ".xlsx", ".xlsm"):
        with zipfile.ZipFile(path) as z:
            out.append((S("gate.where.metadata"), _props(z)))
    return [(w, t) for w, t in out if t]


def check(path: str) -> dict:
    """The gate check: no marker and no product name anywhere in the file, metadata included."""
    found: list[str] = []
    n = 0
    for where, text in texts_of(path):
        n += 1
        for h in hits(text):
            line = S("gate.markers.found", marks=h, where=where)
            if line not in found:
                found.append(line)
    if found:
        return _check(CHECK, False, "; ".join(found[:4]), S("gate.markers.label"), n)
    return _check(CHECK, True, S("gate.markers.ok", n=n), S("gate.markers.label"), n)
