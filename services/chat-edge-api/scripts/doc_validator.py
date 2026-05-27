#!/usr/bin/env python3
"""Document validator — checks .docx and .pdf files are well-formed.

Called by the Go DocService after every generator run. The exit
contract matches the Excel validator: well-formed JSON on stdout
even on failure, exit non-zero only when the script itself crashes.
Generator output that "opens but has warnings" stays valid=true so
the user can still download it.

Usage:  python3 doc_validator.py <file_path>
Output: JSON report on stdout
"""

import json
import os
import sys
from typing import Any


def validate_docx(path: str) -> dict[str, Any]:
    from docx import Document

    result: dict[str, Any] = {
        "valid": True,
        "file_type": "docx",
        "file_size_bytes": os.path.getsize(path),
        "errors": [],
        "warnings": [],
        "stats": {},
    }
    try:
        doc = Document(path)
        result["stats"]["paragraph_count"] = len(doc.paragraphs)
        result["stats"]["table_count"] = len(doc.tables)
        result["stats"]["section_count"] = len(doc.sections)

        text_content = "".join(p.text for p in doc.paragraphs).strip()
        if not text_content and not doc.tables:
            result["warnings"].append("Document appears empty (no text or tables)")

        for i, table in enumerate(doc.tables):
            row_lengths = {len(row.cells) for row in table.rows}
            if len(row_lengths) > 1:
                result["warnings"].append(
                    f"Table {i + 1} has inconsistent column counts: {sorted(row_lengths)}"
                )

        result["stats"]["word_count"] = len(text_content.split())
    except Exception as exc:  # noqa: BLE001
        result["valid"] = False
        result["errors"].append(f"Failed to open: {exc}")
    return result


def validate_pdf(path: str) -> dict[str, Any]:
    from pypdf import PdfReader

    result: dict[str, Any] = {
        "valid": True,
        "file_type": "pdf",
        "file_size_bytes": os.path.getsize(path),
        "errors": [],
        "warnings": [],
        "stats": {},
    }
    try:
        reader = PdfReader(path)
        result["stats"]["page_count"] = len(reader.pages)
        if not reader.pages:
            result["warnings"].append("PDF has 0 pages")

        total_text = ""
        for i, page in enumerate(reader.pages):
            try:
                total_text += page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                result["warnings"].append(
                    f"Page {i + 1} text extraction failed: {exc}"
                )
        word_count = len(total_text.split())
        result["stats"]["word_count"] = word_count
        if word_count == 0 and reader.pages:
            result["warnings"].append(
                "No extractable text found (might be image-only)"
            )
    except Exception as exc:  # noqa: BLE001
        result["valid"] = False
        result["errors"].append(f"Failed to open: {exc}")
    return result


def validate(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {
            "valid": False,
            "errors": [f"File not found: {path}"],
            "warnings": [],
            "stats": {},
        }
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return validate_docx(path)
    if ext == ".pdf":
        return validate_pdf(path)
    return {
        "valid": False,
        "errors": [f"Unsupported file type: {ext}"],
        "warnings": [],
        "stats": {},
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Usage: doc_validator.py <file_path>"}))
        return 1
    try:
        result = validate(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
