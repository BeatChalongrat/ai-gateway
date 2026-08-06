#!/usr/bin/env python3
"""Structured preview data for one attachment — Phase 16.

Sibling of extract_text.py, and deliberately NOT a replacement for it.
extract_text.py produces the flat text the MODEL reads; this produces
the structure a HUMAN reads:

    xlsx/xlsm  -> a paginated window of one sheet's cells, plus the
                  names of every sheet, so the client can draw a real
                  grid with sheet tabs instead of a tab-separated dump.
    docx       -> ordered blocks (heading / paragraph / list / table)
                  so the reader keeps the document's shape.
    pptx       -> one entry per slide, each with its title and blocks.

Usage:
    preview_data.py <file> <params.json>

params.json (an input file, not argv: the sandbox runner resolves every
argv entry to a path under /job/in, so scalars cannot be passed there):

    {"content_type": str, "filename": str,
     "sheet": int, "offset": int, "limit": int}

Output: one JSON object on stdout. Exit codes mirror extract_text.py:
0 ok, 1 bad input, 2 usage, 3 unsupported type, 4 failure.

Runs INSIDE the hardened sandbox (no network, read-only rootfs, uid
65534) using the libraries already baked into the image — openpyxl,
python-docx, python-pptx. Formulas are never evaluated: the workbook is
opened data_only so a formula cell yields the value Excel last computed
(None when the file was written by a tool that stored no cached value,
which is reported as an empty cell rather than as the formula text).

Every budget below is a hard cap. A preview must stay cheap even when
the document is a million rows — the client asks for the next window.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

# ── Budgets ────────────────────────────────────────────────────────────
MAX_ROWS = 500            # rows per request (client pages with offset)
DEFAULT_ROWS = 200
MAX_COLS = 60             # columns rendered; wider sheets are flagged
MAX_CELL_CHARS = 300      # one cell's rendered text

MAX_BLOCKS = 300          # docx blocks per request
DEFAULT_BLOCKS = 150
MAX_SLIDES = 50           # pptx slides per request
DEFAULT_SLIDES = 25

MAX_TABLE_ROWS = 50       # rows kept inside ONE table block
MAX_TABLE_COLS = 20
MAX_TEXT_CHARS = 4000     # one paragraph / bullet

XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_CT = "application/vnd.ms-excel.sheet.macroEnabled.12"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_CT = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# ── Shared helpers ─────────────────────────────────────────────────────

def clamp(value: Any, low: int, high: int, fallback: int) -> int:
    """Coerce an untrusted number into [low, high]. Anything unparseable
    falls back rather than raising — the caller already validated, and a
    preview must not 500 on a stray query string."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, n))


def cell_text(value: Any) -> str:
    """Render one cell the way a spreadsheet shows it.

    str(value) is not good enough: it turns 5 into "5" but 5.0 into
    "5.0", 0.1+0.2 into "0.30000000000000004", and a date into
    "2026-08-06 00:00:00". The grid is meant to look like the file.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, _dt.datetime):
        text = (
            value.strftime("%Y-%m-%d")
            if (value.hour, value.minute, value.second) == (0, 0, 0)
            else value.strftime("%Y-%m-%d %H:%M")
        )
        return text
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, _dt.time):
        return value.strftime("%H:%M")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        if float(value).is_integer() and abs(value) < 1e15:
            return str(int(value))
        # %.10g drops binary-float noise without inventing precision.
        return f"{value:.10g}"
    return truncate(str(value), MAX_CELL_CHARS)


def truncate(text: str, limit: int) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text if len(text) <= limit else text[:limit] + "…"


def column_letter(index: int) -> str:
    """1 -> A, 27 -> AA. Local so the csv path needs no openpyxl."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def sheet_payload(
    names: list[str], index: int, name: str, grid: list[list[str]],
    offset: int, total_rows: int | None, col_count: int, wide: bool,
    returned_limit: int,
) -> dict[str, Any]:
    # A blank sheet still declares a dimension of "A1", so max_row says 1
    # while iterating yields nothing. Reading the start of the sheet and
    # getting no rows is the only trustworthy signal that it is empty.
    if not grid and offset == 0:
        total_rows = 0
    known_total = total_rows if total_rows is not None else offset + len(grid)
    has_more = (
        offset + len(grid) < known_total
        if total_rows is not None
        else len(grid) == returned_limit
    )
    return {
        "kind": "sheet",
        "sheet": {
            "names": names,
            "index": index,
            "name": name,
            "columns": [column_letter(i + 1) for i in range(col_count)],
            "rows": grid,
            "offset": offset,
            "total_rows": known_total,
            "has_more": has_more,
            "columns_truncated": wide,
        },
    }


# ── Spreadsheets ───────────────────────────────────────────────────────

def read_xlsx(path: Path, sheet: int, offset: int, limit: int) -> dict[str, Any]:
    from openpyxl import load_workbook

    # data_only: a formula cell yields Excel's last computed value. The
    # formula itself is never evaluated here — that would mean running
    # the document's own logic to draw a preview.
    wb = load_workbook(str(path), data_only=True, read_only=True)
    try:
        names = list(wb.sheetnames)
        if not names:
            return sheet_payload([], 0, "", [], offset, 0, 0, False, limit)
        index = max(0, min(sheet, len(names) - 1))
        ws = wb[names[index]]

        total = ws.max_row if isinstance(ws.max_row, int) and ws.max_row > 0 else None
        max_col = ws.max_column if isinstance(ws.max_column, int) else 0
        wide = max_col > MAX_COLS
        cols = min(max_col, MAX_COLS) if max_col > 0 else 0

        grid: list[list[str]] = []
        if limit > 0:
            for row in ws.iter_rows(
                min_row=offset + 1, max_row=offset + limit,
                max_col=cols if cols > 0 else None, values_only=True,
            ):
                grid.append([cell_text(v) for v in row])

        # A sheet whose dimension element is missing reports no columns;
        # fall back to the widest row actually read so the grid still
        # gets headers.
        if cols == 0 and grid:
            cols = max(len(r) for r in grid)
        # openpyxl pads short rows to the sheet width only sometimes —
        # square the grid so the client can index columns safely.
        for r in grid:
            if len(r) < cols:
                r.extend([""] * (cols - len(r)))

        return sheet_payload(names, index, names[index], grid, offset,
                             total, cols, wide, limit)
    finally:
        wb.close()


def read_csv(path: Path, offset: int, limit: int, delimiter: str) -> dict[str, Any]:
    """CSV/TSV through the stdlib reader, so quoted separators and
    embedded newlines survive — the thing a naive split() gets wrong."""
    grid: list[list[str]] = []
    total = 0
    # utf-8-sig: Excel writes a BOM, and it would otherwise show up
    # glued to the first header cell.
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for i, row in enumerate(csv.reader(handle, delimiter=delimiter)):
            total += 1
            if offset <= i < offset + limit:
                grid.append([truncate(c, MAX_CELL_CHARS) for c in row[:MAX_COLS]])
    cols = max((len(r) for r in grid), default=0)
    for r in grid:
        if len(r) < cols:
            r.extend([""] * (cols - len(r)))
    return sheet_payload(["CSV"], 0, "CSV", grid, offset, total, cols, False, limit)


# ── Word ───────────────────────────────────────────────────────────────

def heading_level(style_name: str) -> int:
    """'Heading 2' -> 2, 'Title' -> 1, anything else -> 0. Mirrors
    docx_inspector.py's rule so the two agree on what a heading is."""
    name = (style_name or "").strip().lower()
    if name == "title":
        return 1
    if not name.startswith("heading"):
        return 0
    digits = "".join(c for c in name if c.isdigit())
    try:
        return min(int(digits), 6) if digits else 1
    except ValueError:
        return 1


def table_block(table) -> dict[str, Any]:
    return {"type": "table", "rows": table_rows(table)}


def table_rows(table) -> list[list[str]]:
    """Bounded cell text for a docx or pptx table.

    list() before slicing on purpose: python-pptx's row/cell collections
    only accept an integer index, so a slice raises inside the library
    rather than returning a short list.
    """
    rows: list[list[str]] = []
    for row in list(table.rows)[:MAX_TABLE_ROWS]:
        rows.append([
            truncate(cell.text.strip(), MAX_CELL_CHARS)
            for cell in list(row.cells)[:MAX_TABLE_COLS]
        ])
    return rows


def read_docx(path: Path, offset: int, limit: int) -> dict[str, Any]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))

    # Walking the body's children keeps paragraphs and tables in the
    # order they appear. doc.paragraphs + doc.tables (what the text
    # extractor uses) loses that: every table lands after every
    # paragraph, so a report reads as prose then a pile of numbers.
    blocks: list[dict[str, Any]] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, doc)
            text = paragraph.text.strip()
            if not text:
                continue
            style = ""
            try:
                style = paragraph.style.name or ""
            except (AttributeError, KeyError):
                style = ""
            level = heading_level(style)
            if level:
                blocks.append({"type": "heading", "level": level,
                               "text": truncate(text, MAX_TEXT_CHARS)})
            elif "list" in style.lower():
                blocks.append({"type": "list",
                               "text": truncate(text, MAX_TEXT_CHARS)})
            else:
                blocks.append({"type": "paragraph",
                               "text": truncate(text, MAX_TEXT_CHARS)})
        elif tag == "tbl":
            blocks.append(table_block(Table(child, doc)))

    window = blocks[offset:offset + limit]
    return {
        "kind": "document",
        "document": {
            "blocks": window,
            "offset": offset,
            "total_blocks": len(blocks),
            "has_more": offset + len(window) < len(blocks),
        },
    }


# ── PowerPoint ─────────────────────────────────────────────────────────

def slide_blocks(slide, title: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs).strip()
                if not text or text == title:
                    continue
                blocks.append({
                    "type": "list",
                    "level": min(int(getattr(para, "level", 0) or 0), 5),
                    "text": truncate(text, MAX_TEXT_CHARS),
                })
        if getattr(shape, "has_table", False):
            blocks.append({"type": "table", "rows": table_rows(shape.table)})
    return blocks


def read_pptx(path: Path, offset: int, limit: int) -> dict[str, Any]:
    from pptx import Presentation

    prs = Presentation(str(path))
    all_slides = list(prs.slides)
    window = all_slides[offset:offset + limit]

    slides: list[dict[str, Any]] = []
    for i, slide in enumerate(window, start=offset + 1):
        title = ""
        try:
            if slide.shapes.title is not None:
                title = (slide.shapes.title.text or "").strip()
        except (AttributeError, ValueError):
            title = ""
        slides.append({
            "index": i,
            "title": truncate(title, MAX_TEXT_CHARS),
            "blocks": slide_blocks(slide, title),
        })

    return {
        "kind": "slides",
        "slides": {
            "slides": slides,
            "offset": offset,
            "total_slides": len(all_slides),
            "has_more": offset + len(window) < len(all_slides),
        },
    }


# ── Dispatch ───────────────────────────────────────────────────────────

def kind_for(content_type: str, filename: str) -> str:
    """Which reader handles this file. Extension wins when the content
    type is missing or generic (uploads regularly arrive as
    application/octet-stream)."""
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = Path(filename or "").suffix.lower()
    if ct in (XLSX_CT, XLSM_CT) or ext in (".xlsx", ".xlsm"):
        return "xlsx"
    if ct in ("text/csv", "application/csv") or ext == ".csv":
        return "csv"
    if ct in ("text/tab-separated-values",) or ext == ".tsv":
        return "tsv"
    if ct == DOCX_CT or ext == ".docx":
        return "docx"
    if ct == PPTX_CT or ext == ".pptx":
        return "pptx"
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: preview_data.py <file> <params.json>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 1

    try:
        params = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"unreadable params: {exc}", file=sys.stderr)
        return 1
    if not isinstance(params, dict):
        print("params must be an object", file=sys.stderr)
        return 1

    kind = kind_for(str(params.get("content_type") or ""),
                    str(params.get("filename") or path.name))
    if not kind:
        print("unsupported content type", file=sys.stderr)
        return 3

    offset = clamp(params.get("offset"), 0, 5_000_000, 0)
    sheet = clamp(params.get("sheet"), 0, 512, 0)

    try:
        if kind == "xlsx":
            out = read_xlsx(path, sheet, offset,
                            clamp(params.get("limit"), 1, MAX_ROWS, DEFAULT_ROWS))
        elif kind in ("csv", "tsv"):
            out = read_csv(path, offset,
                           clamp(params.get("limit"), 1, MAX_ROWS, DEFAULT_ROWS),
                           "\t" if kind == "tsv" else ",")
        elif kind == "docx":
            out = read_docx(path, offset,
                            clamp(params.get("limit"), 1, MAX_BLOCKS, DEFAULT_BLOCKS))
        else:
            out = read_pptx(path, offset,
                            clamp(params.get("limit"), 1, MAX_SLIDES, DEFAULT_SLIDES))
    except Exception as exc:  # noqa: BLE001 — surface every failure to Go
        print(f"preview failed: {exc}", file=sys.stderr)
        return 4

    # ensure_ascii=False so Thai sheet names and cells survive the trip,
    # and written as BYTES: stdout's text encoding follows the locale, so
    # on a host without a UTF-8 locale the encoder raises on the first
    # Thai character and the whole preview fails. The Go side reads raw
    # bytes and expects UTF-8 either way.
    sys.stdout.buffer.write(
        json.dumps(out, ensure_ascii=False, default=str).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
