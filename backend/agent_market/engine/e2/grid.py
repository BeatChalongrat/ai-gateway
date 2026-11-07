"""A table printed in a PDF, as a grid of cells (AM6).

A bank hands a customer a statement as a PDF and nothing else. The page is read the way E2 reads
an invoice — the words of the text layer with where each of them stands (words.py), the Thai marks
the layer lost put back where the document proves them (marks.py) — and the table is rebuilt from
the geometry:

  * the header is the first line that holds at least ``least`` column headers the caller knows;
  * amounts are set flush right: the right edges of the amounts of all rows fall into a few
    places, and each place is the column whose header stands over it;
  * every other word is text, and belongs to the text column that begins at or before it — so a
    long description does not spill into the amount beside it, and a cheque number inside a
    description is not taken for an amount;
  * a line that repeats the header (the next page's) is dropped; a line with a text in one column
    and nothing else continues the row above it.

What cannot be read this way is not guessed. A page without a text layer (a scan, a photograph of
a statement) is reported as such (``scanned``) and the caller says so to the person; nothing is
read from it by OCR here: one misread digit in a balance is a reconciliation that does not tie
for a reason nobody can find.

    read(path, is_header) → {"rows": [[cell, …]], "where": [(page, line)], "header_row": int,
                              "pages": int, "scanned": [page numbers], "columns": int} | None
"""

from __future__ import annotations

import re

from . import thai_text as T

_AMOUNT = re.compile(r"^[-(]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?-?$")
MIN_TEXT_CHARS = 24
EDGE = 3.0          # points: how far two right edges may be apart and still be one column's
MAX_HEADER_WORDS = 4


def _text(w) -> str:
    return T.fix_text_layer(w.text).strip()


def _is_amount(text: str) -> bool:
    return bool(_AMOUNT.match(T.arabic(text).replace(" ", "")))


def _header_cells(line, is_header) -> list:
    """The header line as cells: the longest run of words that is a header the caller knows is one
    cell ("ยอด คงเหลือ"); a word that is part of none is a cell by itself."""
    words = [w for w in line.words if _text(w)]
    out, i = [], 0
    while i < len(words):
        hit = None
        # words of one header stand a word space apart; a wider gap is the gap between two columns
        # ("วันที่" and "รายการ" are two headers, though "วันที่รายการ" is one in another bank's file)
        far = i + 1
        while far < len(words) and far - i < MAX_HEADER_WORDS and words[far].x0 - words[far - 1].x1 <= 0.8 * max(words[far].h, 1.0):
            far += 1
        for j in range(far, i, -1):
            text = " ".join(_text(w) for w in words[i:j])
            if is_header(text) or is_header(text.replace(" ", "")):
                hit = j
                break
        j = hit or i + 1
        out.append({"x0": min(w.x0 for w in words[i:j]), "x1": max(w.x1 for w in words[i:j]),
                    "text": " ".join(_text(w) for w in words[i:j]), "known": hit is not None})
        i = j
    return out


def _anchors(lines: list, header: list) -> dict:
    """{column: the right edge its amounts are set against}, from the amounts of every row."""
    edges = sorted(w.x1 for l in lines for w in l.words if _is_amount(_text(w)))
    clusters: list = []
    for x in edges:
        if clusters and x - clusters[-1][-1] <= EDGE:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    out: dict = {}
    for c in clusters:
        if len(c) < 2:
            continue
        at = sorted(c)[len(c) // 2]
        # the header that stands over this edge: the nearest one, by its own right edge or its span
        best = min(range(len(header)), key=lambda j: min(abs(header[j]["x1"] - at), abs((header[j]["x0"] + header[j]["x1"]) / 2 - at))
                   if not header[j]["x0"] <= at <= header[j]["x1"] + EDGE else 0.0)
        if best not in out or abs(header[best]["x1"] - at) < abs(header[best]["x1"] - out[best]):
            out[best] = at
    return out


def read(path: str, is_header, *, least: int = 3, ocr=None) -> dict | None:
    """``is_header(text) -> bool``: whether a text is a column header the caller knows."""
    from .extract import _restore_marks
    from .words import build_lines, pdf_text_words

    fonts: dict = {}
    words, pages = pdf_text_words(path, fonts)
    by_page: dict = {}
    for w in words:
        by_page[w.page] = by_page.get(w.page, 0) + len(w.text)
    scanned = [p.number for p in pages if by_page.get(p.number, 0) < MIN_TEXT_CHARS]
    if not words or len(scanned) == len(pages):
        return {"rows": [], "where": [], "header_row": None, "pages": len(pages), "scanned": scanned, "columns": 0}
    _restore_marks(path, [w for w in words if w.page not in scanned], fonts, ocr)
    lines = build_lines(words)
    header, at = None, None
    for i, l in enumerate(lines):
        cells = _header_cells(l, is_header)
        if sum(1 for c in cells if c["known"]) >= least:
            header, at = cells, i
            break
    if header is None:
        return None
    names = {T.skel(c["text"]).lower() for c in header if c["known"]}
    body = []
    for l in lines[at + 1:]:
        cells = _header_cells(l, is_header)
        if sum(1 for c in cells if c["known"] and T.skel(c["text"]).lower() in names) >= least:
            continue                                               # the header again, on the next page
        body.append(l)
    anchors = _anchors(body, header)
    text_cols = [j for j in range(len(header)) if j not in anchors]
    rows, where = [], []
    n_on_page: dict = {}
    number = {}
    for l in lines:
        n_on_page[l.page] = n_on_page.get(l.page, 0) + 1
        number[id(l)] = (l.page, n_on_page[l.page])
    for l in lines[:at]:
        rows.append([" ".join(_text(w) for w in l.words if _text(w))])      # what stands above the table: one cell a line
        where.append(number[id(l)])
    rows.append([c["text"] for c in header])
    where.append(number[id(lines[at])])
    for l in body:
        row: list = [None] * len(header)

        def put(j, text):
            row[j] = text if row[j] is None else f"{row[j]} {text}"
        for w in l.words:
            text = _text(w)
            if not text:
                continue
            if _is_amount(text):
                near = [j for j, x in anchors.items() if abs(w.x1 - x) <= EDGE]
                if near:
                    put(min(near, key=lambda j: abs(w.x1 - anchors[j])), text)
                    continue
            mine = [j for j in text_cols if header[j]["x0"] <= w.x0 + EDGE]
            put(mine[-1] if mine else (text_cols[0] if text_cols else 0), text)
        filled = [j for j, v in enumerate(row) if v is not None]
        if not filled:
            continue
        if len(filled) == 1 and filled[0] in text_cols and len(rows) - 1 > at and rows[-1][filled[0]] is not None \
                and not T.parse_date(T.arabic(row[filled[0]])):
            rows[-1][filled[0]] = f"{rows[-1][filled[0]]} {row[filled[0]]}"       # the row above, continued
            continue
        rows.append(row)
        where.append(number[id(l)])
    return {"rows": rows, "where": where, "header_row": at, "pages": len(pages), "scanned": scanned, "columns": len(header)}
