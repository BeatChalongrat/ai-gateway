#!/usr/bin/env python3
"""Word document deep inspector — outputs the structure as JSON.

Sibling of excel_inspector.py: same contract (single JSON line on
stdout, exit non-zero with {"error": ...} on failure). The output is
cached server-side in chat.conversation_files.inspection_json and
folded into the system prompt on every send.

Usage:  python3 docx_inspector.py <file_path>
Output: JSON on stdout
"""

import json
import os
import sys
from typing import Any

from docx import Document
from docx.oxml.ns import qn


# Bounded budgets — the inspection JSON ships into the system prompt
# unmodified. Sample previews + heading outline are capped so a
# 200-page document doesn't explode the LLM context.
PREVIEW_ITEM_LIMIT = 20
HEADING_OUTLINE_LIMIT = 60
PARAGRAPH_TEXT_PREVIEW = 150


def _heading_level(style_name: str) -> int:
    """Parse 'Heading 1' / 'Heading 2' / 'Heading' style names into a
    1-based level. Returns 0 for non-heading styles."""
    if not style_name or not style_name.lower().startswith("heading"):
        return 0
    digits = "".join(c for c in style_name if c.isdigit())
    if not digits:
        return 1
    try:
        return int(digits)
    except ValueError:
        return 1


def inspect_docx(path: str) -> dict[str, Any]:
    doc = Document(path)

    result: dict[str, Any] = {
        "file_type": "docx",
        "file_size_bytes": os.path.getsize(path),
        "metadata": {},
        "sections": [],
        "content_structure": {
            "paragraph_count": 0,
            "table_count": 0,
            "image_count": 0,
            "list_count": 0,
            "hyperlink_count": 0,
            "heading_outline": [],
        },
        "styles_used": [],
        "has_toc": False,
        "has_tracked_changes": False,
        "has_comments": False,
        "content_preview": [],
    }

    # ── Metadata ──────────────────────────────────────────────
    props = doc.core_properties
    result["metadata"] = {
        "title": props.title or "",
        "author": props.author or "",
        "created": str(props.created) if props.created else None,
        "modified": str(props.modified) if props.modified else None,
        "last_modified_by": props.last_modified_by or "",
        "revision": props.revision,
        "category": props.category or "",
        "subject": props.subject or "",
    }

    # ── Sections ──────────────────────────────────────────────
    for i, section in enumerate(doc.sections):
        try:
            pw = section.page_width.inches if section.page_width else None
            ph = section.page_height.inches if section.page_height else None
        except Exception:
            pw = ph = None
        sec_info: dict[str, Any] = {
            "index": i,
            "page_width_inches": round(pw, 2) if pw is not None else None,
            "page_height_inches": round(ph, 2) if ph is not None else None,
            "orientation": (
                "landscape" if (pw and ph and pw > ph) else "portrait"
            ),
            "margins": {
                "top": (
                    round(section.top_margin.inches, 2)
                    if section.top_margin else None
                ),
                "bottom": (
                    round(section.bottom_margin.inches, 2)
                    if section.bottom_margin else None
                ),
                "left": (
                    round(section.left_margin.inches, 2)
                    if section.left_margin else None
                ),
                "right": (
                    round(section.right_margin.inches, 2)
                    if section.right_margin else None
                ),
            },
            "has_header": bool(
                section.header
                and section.header.paragraphs
                and any(p.text.strip() for p in section.header.paragraphs)
            ),
            "has_footer": bool(
                section.footer
                and section.footer.paragraphs
                and any(p.text.strip() for p in section.footer.paragraphs)
            ),
        }
        result["sections"].append(sec_info)

    # ── Walk the body in document order ───────────────────────
    # Build an element→object index up front so we don't do a linear
    # scan for every <w:p> / <w:tbl> hit. python-docx exposes them
    # in document order via doc.paragraphs / doc.tables but doesn't
    # interleave the two; we walk the underlying XML to keep order.
    para_by_el = {p._element: p for p in doc.paragraphs}
    table_by_el = {t._element: t for t in doc.tables}

    styles_seen: set[str] = set()
    heading_outline: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []
    para_count = 0
    table_count = 0
    list_count = 0
    image_count = 0

    for element in doc.element.body:
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

        if tag == "p":
            p = para_by_el.get(element)
            if p is None:
                continue
            para_count += 1
            style_name = p.style.name if p.style else "Normal"
            styles_seen.add(style_name)

            lvl = _heading_level(style_name)
            if lvl and len(heading_outline) < HEADING_OUTLINE_LIMIT:
                heading_outline.append(
                    {"level": lvl, "text": (p.text or "")[:PARAGRAPH_TEXT_PREVIEW]}
                )

            pPr = p._element.find(qn("w:pPr"))
            if pPr is not None and pPr.find(qn("w:numPr")) is not None:
                list_count += 1

            # Inline + anchored images live under the paragraph's
            # drawing tree. Both shapes count.
            drawings = (
                p._element.findall(".//" + qn("wp:inline"))
                + p._element.findall(".//" + qn("wp:anchor"))
            )
            image_count += len(drawings)

            if len(preview) < PREVIEW_ITEM_LIMIT and (p.text or "").strip():
                preview.append(
                    {
                        "type": "paragraph",
                        "style": style_name,
                        "text": (p.text or "")[:PARAGRAPH_TEXT_PREVIEW],
                        "formatting": {
                            "bold": any(r.bold for r in p.runs if r.bold),
                            "italic": any(r.italic for r in p.runs if r.italic),
                        },
                    }
                )

        elif tag == "tbl":
            t = table_by_el.get(element)
            if t is None:
                continue
            table_count += 1
            rows = len(t.rows)
            cols = len(t.columns) if rows else 0
            headers: list[str] = []
            if rows:
                for cell in t.rows[0].cells[:10]:
                    headers.append((cell.text or "")[:50])
            if len(preview) < PREVIEW_ITEM_LIMIT:
                preview.append(
                    {
                        "type": "table",
                        "rows": rows,
                        "columns": cols,
                        "headers": headers,
                    }
                )

    # ── TOC detection ────────────────────────────────────────
    for p in doc.paragraphs:
        if p.style and p.style.name and "TOC" in p.style.name.upper():
            result["has_toc"] = True
            break
    for fld in doc.element.body.findall(".//" + qn("w:fldSimple")):
        instr = fld.get(qn("w:instr"), "")
        if "TOC" in (instr or "").upper():
            result["has_toc"] = True
            break

    # ── Tracked changes + comments ───────────────────────────
    body = doc.element.body
    if body.findall(".//" + qn("w:ins")) or body.findall(".//" + qn("w:del")):
        result["has_tracked_changes"] = True
    if body.findall(".//" + qn("w:commentRangeStart")):
        result["has_comments"] = True

    hyperlinks = body.findall(".//" + qn("w:hyperlink"))

    cs = result["content_structure"]
    cs["paragraph_count"] = para_count
    cs["table_count"] = table_count
    cs["image_count"] = image_count
    cs["list_count"] = list_count
    cs["hyperlink_count"] = len(hyperlinks)
    cs["heading_outline"] = heading_outline
    result["styles_used"] = sorted(styles_seen)
    result["content_preview"] = preview

    return result


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Usage: docx_inspector.py <file_path>"}))
        return 1
    try:
        result = inspect_docx(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
