#!/usr/bin/env python3
"""Extract text content from a file for the chat-edge-api.

Usage:
    extract_text.py <path> <content_type>

Prints the extracted text to stdout. Exits non-zero on any failure so
the caller (file_service.go) knows to fall back to the
"unsupported" placeholder.

Dependencies (install in the chat-edge-api container):
    pip install pdfplumber python-docx openpyxl
"""
from __future__ import annotations

import sys
from pathlib import Path


def _emit(text: str) -> None:
    # The Go side reads stdout verbatim, so just write through.
    sys.stdout.write(text)
    sys.stdout.flush()


def _extract_pdf(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # noqa: BLE001 — surface clean error
        raise SystemExit(f"pdfplumber not installed: {exc}")

    chunks: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(chunks)


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise SystemExit(f"python-docx not installed: {exc}")

    doc = Document(str(path))
    parts: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    # Tables: emit row-by-row, tab-separated, so the AI can read the
    # structure even without proper markdown rendering.
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace("\t", " ") for cell in row.cells]
            parts.append("\t".join(cells))
    return "\n".join(parts)


def _extract_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise SystemExit(f"openpyxl not installed: {exc}")

    wb = load_workbook(str(path), data_only=True, read_only=True)
    out: list[str] = []
    for sheet in wb.worksheets:
        out.append(f"--- Sheet: {sheet.title} ---")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                out.append("\t".join(cells))
        out.append("")
    return "\n".join(out)


def _extract_pptx(path: Path) -> str:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise SystemExit(f"python-pptx not installed: {exc}")

    prs = Presentation(str(path))
    out: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        lines: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    txt = "".join(run.text for run in para.runs).strip()
                    if txt:
                        lines.append(txt)
            # Tables: emit row-by-row, tab-separated, mirroring the docx
            # path so the AI reads the structure.
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip().replace("\t", " ") for c in row.cells]
                    if any(cells):
                        lines.append("\t".join(cells))
        if lines:
            out.append(f"--- Slide {i} ---\n" + "\n".join(lines))
    return "\n\n".join(out)


PDF_CT = "application/pdf"
DOCX_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_CT = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
PPTX_CT = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: extract_text.py <path> <content_type>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    content_type = argv[2].lower()

    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 1

    try:
        if content_type == PDF_CT or path.suffix.lower() == ".pdf":
            _emit(_extract_pdf(path))
        elif content_type == DOCX_CT or path.suffix.lower() == ".docx":
            _emit(_extract_docx(path))
        elif content_type == XLSX_CT or path.suffix.lower() == ".xlsx":
            _emit(_extract_xlsx(path))
        elif content_type == PPTX_CT or path.suffix.lower() == ".pptx":
            _emit(_extract_pptx(path))
        else:
            print(f"unsupported content type: {content_type}", file=sys.stderr)
            return 3
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"extraction failed: {exc}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
