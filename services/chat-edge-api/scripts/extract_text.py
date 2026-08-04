#!/usr/bin/env python3
"""Extract text content from a file for the chat-edge-api.

Usage:
    extract_text.py <path> <content_type>

Prints the extracted text to stdout. Exits non-zero on any failure so
the caller (file_service.go) knows to fall back to the
"unsupported" placeholder.

Runs INSIDE the hardened sandbox (no network, read-only rootfs, uid
65534, tmpfs /tmp). Dependencies live in sandbox/Dockerfile.sandbox:
    pdfplumber python-docx openpyxl python-pptx     (text layers)
    pypdfium2 pytesseract pillow + tesseract-ocr    (scanned PDFs)

OCR (v2)
--------
A scanned PDF has no text layer, so the old code returned an empty
string and the model was told the document was blank. Pages WITHOUT a
usable text layer are now rendered and passed through Tesseract with
``tha+eng``; pages that DO have text keep using it verbatim, so a
normal PDF costs exactly what it used to and never touches OCR. Mixed
documents (a typed report with a scanned annex) get the right
treatment per page.

The first stdout line is a machine-readable meta header consumed and
stripped by file_service.go — it carries the OCR confidence the vision
fallback needs to decide whether Tesseract actually understood the
page. Older images that predate this emit no header; the Go side
treats its absence as "text layer only", so the two can be rolled out
independently.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Marker for the meta header. Must match extractMetaPrefix in
# internal/service/file_service.go.
META_PREFIX = "#SRAN-EXTRACT-META "

# A page whose text layer yields fewer than this many non-space
# characters is treated as "no usable text" and sent to OCR. Scanned
# pages often carry a stray header/page-number in the text layer, so
# the bar cannot be simply "empty".
MIN_TEXT_CHARS_PER_PAGE = 24

# OCR is the expensive path; bound it. Both are overridable through the
# sandbox job env for a deployment with different economics.
MAX_OCR_PAGES = int(os.environ.get("EXTRACT_MAX_OCR_PAGES", "20"))
# 200 dpi measured better than 150 on a real Thai/English scanned
# transcript: 150 turned "Mr Chalongrat Thawonbundit" into Thai noise and
# lost ~16% of the characters, for only 5 s/page. Not worth it.
OCR_DPI = int(os.environ.get("EXTRACT_OCR_DPI", "200"))
OCR_LANGS = os.environ.get("EXTRACT_OCR_LANGS", "tha+eng")
# --oem 1 = LSTM only, skipping the legacy engine the default (3) keeps
# as a fallback. Measured 22.0 s vs 25.1 s per page on the same scan with
# byte-identical output and the same confidence.
OCR_CONFIG = os.environ.get("EXTRACT_OCR_CONFIG", "--oem 1")

_THAI_FIRST, _THAI_LAST = "฀", "๿"

# Gap/height ratio above which a break between two THAI boxes is treated
# as a real space. Measured on a scanned Thai page: gaps between glyphs
# of one word run 0.10-0.45 and a genuine phrase space measured 0.37, so
# the two OVERLAP and no threshold separates them cleanly. This one is
# therefore set high — only unmistakable layout gaps (measured 1.7-8.6)
# reopen a Thai run. See the joining rationale below for why erring
# toward "no space" is the right side to fail on for Thai.
_THAI_SPACE_RATIO = 0.8


def _is_thai(ch: str) -> bool:
    return _THAI_FIRST <= ch <= _THAI_LAST


def _join_words(words: list[tuple[str, int, int, int]]) -> str:
    """Join Tesseract word boxes back into a line.

    Each entry is (text, left, width, height).

    Rebuilding a line from boxes is only hard because of Thai. Thai has
    no word spaces, so Tesseract splits one Thai run into many boxes;
    joining those with " " invents spaces that were never printed
    (``อตรแรแลญู`` came back as ``อ ต ร แร แล ญู``). Latin boxes DO sit
    either side of a real space and must keep it.

    So the script decides the default and the geometry only overrides an
    unmistakable case: no space across a Thai/Thai boundary unless the
    pixel gap is far too wide to be glyph spacing, a space everywhere
    else. Measured against image_to_string on a real scan, the Thai
    characters come out identical (similarity 1.000) and Latin is
    unchanged.

    The residual cost is that ordinary phrase spaces inside Thai are
    lost. That is the right way to be wrong here: Thai is normally
    written without spaces between words, so an unspaced run stays
    readable, whereas spaces injected inside a word corrupt it.
    """
    out = ""
    prev_right: int | None = None
    prev_height = 0
    for text, left, width, height in words:
        if not out:
            out = text
        else:
            joined_thai = _is_thai(out[-1]) and _is_thai(text[0])
            gap = left - prev_right if prev_right is not None else 0
            reference = max(prev_height, height, 1)
            if joined_thai and gap < reference * _THAI_SPACE_RATIO:
                out += text
            else:
                out += " " + text
        prev_right = left + width
        prev_height = height
    return out


def _emit(text: str, meta: dict | None = None) -> None:
    # The Go side reads stdout verbatim after stripping the header.
    if meta is not None:
        sys.stdout.write(META_PREFIX + json.dumps(meta, ensure_ascii=True) + "\n")
    sys.stdout.write(text)
    sys.stdout.flush()


def _ocr_page(pdf_path: Path, page_index: int) -> tuple[str, float]:
    """Render one page and OCR it. Returns (text, mean confidence 0-100).

    ONE Tesseract pass. The first version called image_to_string for the
    text and image_to_data for the confidence, which OCR'd every page
    twice — measured 23.4 s of pure waste per page on a real scan, about
    half the total extraction time. image_to_data already carries the
    words, so the text is rebuilt from them instead.

    Confidence is the mean over words Tesseract was willing to score. It
    is what tells the caller "OCR ran but produced noise", which text
    length alone cannot — a page of garbage is still long.
    """
    import pypdfium2 as pdfium
    import pytesseract

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_index]
        # pypdfium2 takes a scale relative to 72 dpi.
        bitmap = page.render(scale=OCR_DPI / 72.0)
        image = bitmap.to_pil()
    finally:
        doc.close()

    data = pytesseract.image_to_data(
        image,
        lang=OCR_LANGS,
        config=OCR_CONFIG,
        output_type=pytesseract.Output.DICT,
    )

    # Group words back into their source lines. Tesseract numbers every
    # word by block/paragraph/line, so sorting on that triple restores
    # reading order without any geometry maths.
    lines: dict[tuple[int, int, int], list[tuple[str, int, int, int]]] = {}
    confidences: list[float] = []
    words = data.get("text", [])
    for i, raw_word in enumerate(words):
        word = str(raw_word).strip()
        if not word:
            continue
        key = (
            data["block_num"][i],
            data["par_num"][i],
            data["line_num"][i],
        )
        lines.setdefault(key, []).append(
            (
                word,
                int(data["left"][i]),
                int(data["width"][i]),
                int(data["height"][i]),
            )
        )
        try:
            value = float(data["conf"][i])
        except (TypeError, ValueError, KeyError):
            continue
        # -1 marks a region Tesseract declined to score; counting it
        # would drag the mean toward zero for no reason.
        if value >= 0:
            confidences.append(value)

    text = "\n".join(_join_words(v) for _, v in sorted(lines.items()))
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return text.strip(), mean_conf


def _extract_pdf(path: Path) -> tuple[str, dict]:
    try:
        import pdfplumber
    except ImportError as exc:  # noqa: BLE001 — surface clean error
        raise SystemExit(f"pdfplumber not installed: {exc}")

    # Pass 1: the text layer, page by page.
    page_texts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        total_pages = len(pdf.pages)
        for page in pdf.pages:
            page_texts.append((page.extract_text() or "").strip())

    needs_ocr = [
        i for i, t in enumerate(page_texts)
        if len(t.replace(" ", "")) < MIN_TEXT_CHARS_PER_PAGE
    ]

    meta: dict = {
        "mode": "text",
        "pages": total_pages,
        "ocr_pages": 0,
        "ocr_avg_confidence": None,
        "ocr_truncated": False,
        "ocr_error": None,
    }

    ocr_confidences: list[float] = []
    if needs_ocr:
        attempted = needs_ocr[:MAX_OCR_PAGES]
        meta["ocr_truncated"] = len(needs_ocr) > len(attempted)
        for i in attempted:
            try:
                text, conf = _ocr_page(path, i)
            except Exception as exc:  # noqa: BLE001
                # One bad page must not lose the pages that did work.
                meta["ocr_error"] = str(exc)[:200]
                continue
            if text:
                # Mark the provenance so the model — and the vision
                # fallback that may follow — can tell OCR text from a
                # real text layer.
                page_texts[i] = "[OCR] " + text
                ocr_confidences.append(conf)

        meta["ocr_pages"] = len(ocr_confidences)
        if ocr_confidences:
            meta["ocr_avg_confidence"] = round(
                sum(ocr_confidences) / len(ocr_confidences), 1
            )
        meta["mode"] = "ocr" if not any(
            t and not t.startswith("[OCR] ") for t in page_texts
        ) else "mixed"

    chunks = [
        f"--- Page {i + 1} ---\n{t}"
        for i, t in enumerate(page_texts)
        if t
    ]
    return "\n\n".join(chunks), meta


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
            text, meta = _extract_pdf(path)
            if not text.strip():
                # Every page was blank AND OCR produced nothing. Say so
                # instead of exiting 0 with an empty body, which the
                # model reads as "the document is empty".
                print(
                    "pdf has no text layer and OCR produced no text "
                    f"(pages={meta['pages']}, ocr_pages={meta['ocr_pages']}, "
                    f"ocr_error={meta['ocr_error']})",
                    file=sys.stderr,
                )
                return 5
            _emit(text, meta)
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
