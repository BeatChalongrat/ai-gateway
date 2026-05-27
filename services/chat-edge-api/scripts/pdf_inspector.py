#!/usr/bin/env python3
"""PDF deep inspector — outputs the structure as JSON.

Sibling of docx_inspector.py / excel_inspector.py. Output is cached
server-side in chat.conversation_files.inspection_json so the
message-service can fold it into the system prompt without
re-shelling on every send.

Usage:  python3 pdf_inspector.py <file_path>
Output: JSON on stdout
"""

import json
import os
import sys
from typing import Any

import pdfplumber
from pypdf import PdfReader


# Bounded budgets — keep the inspection JSON small enough to live
# inside the LLM system prompt. Per-page detail is comprehensive;
# the content preview tops out at 3 pages × 500 chars.
TABLES_PER_PAGE = 5
PREVIEW_PAGE_LIMIT = 3
PREVIEW_PAGE_CHARS = 500


def inspect_pdf(path: str) -> dict[str, Any]:
    file_size = os.path.getsize(path)
    reader = PdfReader(path)

    result: dict[str, Any] = {
        "file_type": "pdf",
        "file_size_bytes": file_size,
        "metadata": {},
        "page_count": len(reader.pages),
        "pages": [],
        "content_summary": {
            "total_word_count": 0,
            "total_table_count": 0,
            "total_image_count": 0,
            "has_forms": False,
            "form_field_count": 0,
            "is_encrypted": bool(reader.is_encrypted),
            "scanned_page_count": 0,
        },
        "content_preview": [],
    }

    meta = reader.metadata
    if meta:
        result["metadata"] = {
            "title": str(meta.title) if meta.title else "",
            "author": str(meta.author) if meta.author else "",
            "subject": str(meta.subject) if meta.subject else "",
            "creator": str(meta.creator) if meta.creator else "",
            "producer": str(meta.producer) if meta.producer else "",
        }

    try:
        fields = reader.get_fields()
    except Exception:
        fields = None
    if fields:
        result["content_summary"]["has_forms"] = True
        result["content_summary"]["form_field_count"] = len(fields)

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_info: dict[str, Any] = {
                "page_number": i + 1,
                "width": round(float(page.width), 1),
                "height": round(float(page.height), 1),
                "orientation": (
                    "landscape" if page.width > page.height else "portrait"
                ),
            }

            text = page.extract_text() or ""
            words = text.split()
            page_info["word_count"] = len(words)
            result["content_summary"]["total_word_count"] += len(words)

            images = list(page.images or [])
            page_info["image_count"] = len(images)
            result["content_summary"]["total_image_count"] += len(images)

            # Scanned-page heuristic: lots of pixels, very little
            # extractable text. The exact threshold is empirical; 10
            # words is below what any genuine page would carry.
            if len(words) < 10 and len(images) > 0:
                page_info["likely_scanned"] = True
                result["content_summary"]["scanned_page_count"] += 1
            else:
                page_info["likely_scanned"] = False

            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            page_info["table_count"] = len(tables)
            result["content_summary"]["total_table_count"] += len(tables)

            if tables:
                page_info["tables"] = []
                for t in tables[:TABLES_PER_PAGE]:
                    if not t:
                        continue
                    page_info["tables"].append(
                        {
                            "rows": len(t),
                            "columns": len(t[0]) if t[0] else 0,
                            "headers": [
                                str(c)[:50] for c in (t[0] or [])[:10]
                            ],
                        }
                    )

            result["pages"].append(page_info)

            if i < PREVIEW_PAGE_LIMIT and text.strip():
                result["content_preview"].append(
                    {"page": i + 1, "text": text[:PREVIEW_PAGE_CHARS]}
                )

    return result


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Usage: pdf_inspector.py <file_path>"}))
        return 1
    try:
        result = inspect_pdf(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
