#!/usr/bin/env python3
"""PowerPoint deep inspector — outputs the presentation structure as JSON.

Sibling of docx_inspector.py / pdf_inspector.py / excel_inspector.py.
Output is cached server-side in
chat.conversation_files.inspection_json so the message-service can
fold the deck structure into the system prompt on every send.

Usage:  python3 pptx_inspector.py <file_path>
Output: JSON on stdout
"""

import json
import os
import sys
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


# Bounded budgets — keep the JSON small enough to live inside an
# LLM system prompt even for 60-slide decks. Per-slide detail
# stays comprehensive; text + speaker-note previews are capped.
TEXT_PREVIEW = 200
NOTE_PREVIEW = 200
TITLE_PREVIEW = 150
OUTLINE_TITLE_PREVIEW = 80
HEADER_PREVIEW_PER_TABLE = 10


def _safe_inches(val) -> float | None:
    try:
        return round(float(val.inches), 2)
    except Exception:
        return None


def _aspect_ratio(w: float, h: float) -> str:
    if h <= 0:
        return "1:1"
    ratio = w / h
    if abs(ratio - 16 / 9) < 0.05:
        return "16:9"
    if abs(ratio - 16 / 10) < 0.05:
        return "16:10"
    if abs(ratio - 4 / 3) < 0.05:
        return "4:3"
    return f"{round(ratio, 2)}:1"


def _is_title_placeholder(shape) -> bool:
    """True when a shape's placeholder slot is the slide title.
    Guarded because not every shape has a placeholder_format and
    older pptx files have inconsistent metadata."""
    try:
        pf = getattr(shape, "placeholder_format", None)
        if pf is None:
            return False
        idx = getattr(pf, "idx", None)
        # idx 0 is the title placeholder per the OOXML spec.
        return idx == 0
    except Exception:
        return False


def _is_subtitle_placeholder(shape) -> bool:
    try:
        pf = getattr(shape, "placeholder_format", None)
        if pf is None:
            return False
        return getattr(pf, "idx", None) == 1
    except Exception:
        return False


def inspect_pptx(path: str) -> dict[str, Any]:
    prs = Presentation(path)

    width_in = _safe_inches(prs.slide_width) if prs.slide_width else 10.0
    height_in = _safe_inches(prs.slide_height) if prs.slide_height else 5.625

    result: dict[str, Any] = {
        "file_type": "pptx",
        "file_size_bytes": os.path.getsize(path),
        "metadata": {},
        "dimensions": {
            "width_inches": width_in,
            "height_inches": height_in,
            "aspect_ratio": _aspect_ratio(width_in or 10.0, height_in or 5.625),
        },
        "slide_count": len(prs.slides),
        "slides": [],
        "content_summary": {
            "total_text_shapes": 0,
            "total_tables": 0,
            "total_charts": 0,
            "total_images": 0,
            "total_other_shapes": 0,
            "has_speaker_notes": False,
        },
        "content_outline": [],
        "fonts_used": [],
        "slide_layouts_used": [],
    }

    # ── Metadata ─────────────────────────────────────────────
    if prs.core_properties is not None:
        props = prs.core_properties
        result["metadata"] = {
            "title": props.title or "",
            "author": props.author or "",
            "subject": props.subject or "",
            "created": str(props.created) if props.created else None,
            "modified": str(props.modified) if props.modified else None,
            "last_modified_by": props.last_modified_by or "",
        }

    fonts_seen: set[str] = set()
    layouts_seen: set[str] = set()

    # ── Per-slide walk ───────────────────────────────────────
    for slide_idx, slide in enumerate(prs.slides):
        layout_name = (
            slide.slide_layout.name if slide.slide_layout else "Unknown"
        )
        layouts_seen.add(layout_name)

        slide_info: dict[str, Any] = {
            "slide_number": slide_idx + 1,
            "layout_name": layout_name,
            "title": None,
            "subtitle": None,
            "text_blocks": [],
            "tables": [],
            "charts": [],
            "images": [],
            "shapes_count": 0,
            "has_speaker_notes": False,
            "speaker_notes_preview": None,
        }
        outline_entry: dict[str, Any] = {
            "slide_number": slide_idx + 1,
            "title": None,
            "content_types": [],
        }

        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text or ""
                if _is_title_placeholder(shape) and text.strip():
                    slide_info["title"] = text[:TITLE_PREVIEW]
                    outline_entry["title"] = text[:OUTLINE_TITLE_PREVIEW]
                elif _is_subtitle_placeholder(shape) and text.strip():
                    slide_info["subtitle"] = text[:TITLE_PREVIEW]

                if text.strip():
                    bullet_count = sum(
                        1 for p in shape.text_frame.paragraphs if p.text.strip()
                    )
                    slide_info["text_blocks"].append(
                        {
                            "text_preview": text[:TEXT_PREVIEW],
                            "paragraph_count": bullet_count,
                        }
                    )
                    result["content_summary"]["total_text_shapes"] += 1

                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        if run.font and run.font.name:
                            fonts_seen.add(run.font.name)

            if shape.has_table:
                table = shape.table
                rows = len(table.rows)
                cols = len(table.columns)
                headers: list[str] = []
                if rows:
                    for cell in list(table.rows[0].cells)[:HEADER_PREVIEW_PER_TABLE]:
                        headers.append((cell.text or "")[:50])
                slide_info["tables"].append(
                    {"rows": rows, "columns": cols, "headers": headers}
                )
                result["content_summary"]["total_tables"] += 1
                outline_entry["content_types"].append("table")

            if shape.has_chart:
                chart = shape.chart
                chart_info: dict[str, Any] = {
                    "chart_type": str(chart.chart_type) if chart.chart_type else "unknown",
                    "series_count": 0,
                    "series_names": [],
                }
                try:
                    series_list = list(chart.series)
                    chart_info["series_count"] = len(series_list)
                    for s in series_list:
                        name = getattr(s, "name", None)
                        if name:
                            chart_info["series_names"].append(str(name)[:50])
                except Exception:
                    pass
                slide_info["charts"].append(chart_info)
                result["content_summary"]["total_charts"] += 1
                outline_entry["content_types"].append("chart")

            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                slide_info["images"].append(
                    {
                        "alt_text": shape.name or "",
                        "width_inches": _safe_inches(shape.width) if shape.width else None,
                        "height_inches": _safe_inches(shape.height) if shape.height else None,
                    }
                )
                result["content_summary"]["total_images"] += 1
                outline_entry["content_types"].append("image")

            # Count "other shapes" — non-picture, non-table, non-chart,
            # non-placeholder. Excludes text-frame-only shapes that
            # we already tallied in total_text_shapes.
            if shape.shape_type not in (
                MSO_SHAPE_TYPE.PICTURE,
                MSO_SHAPE_TYPE.TABLE,
                MSO_SHAPE_TYPE.CHART,
                MSO_SHAPE_TYPE.PLACEHOLDER,
            ) and not shape.has_text_frame:
                slide_info["shapes_count"] += 1
                result["content_summary"]["total_other_shapes"] += 1

        # Speaker notes
        if slide.has_notes_slide:
            try:
                notes_text = (
                    slide.notes_slide.notes_text_frame.text or ""
                ).strip()
            except Exception:
                notes_text = ""
            if notes_text:
                slide_info["has_speaker_notes"] = True
                slide_info["speaker_notes_preview"] = notes_text[:NOTE_PREVIEW]
                result["content_summary"]["has_speaker_notes"] = True

        # Fallback title from the first text block when no
        # placeholder-title was found.
        if not slide_info["title"] and slide_info["text_blocks"]:
            slide_info["title"] = slide_info["text_blocks"][0]["text_preview"][:OUTLINE_TITLE_PREVIEW]
            outline_entry["title"] = slide_info["title"]
        if not outline_entry["title"]:
            outline_entry["title"] = f"(Slide {slide_idx + 1} — no title)"
        if slide_info["text_blocks"]:
            outline_entry["content_types"].append("text")

        result["slides"].append(slide_info)
        result["content_outline"].append(outline_entry)

    result["fonts_used"] = sorted(fonts_seen)
    result["slide_layouts_used"] = sorted(layouts_seen)
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Usage: pptx_inspector.py <file_path>"}))
        return 1
    try:
        result = inspect_pptx(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
