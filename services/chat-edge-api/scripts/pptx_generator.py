#!/usr/bin/env python3
"""PowerPoint generator — applies a JSON plan to produce a .pptx deck.

Sibling of docx_generator.py / pdf_generator.py / excel_executor.py.
Two modes:

    create: when <source.pptx> is omitted, builds a fresh deck from
            the requested presentation_settings + slides plan.
    edit:   when <source.pptx> is provided, copies it to <output>
            and APPENDS the planned slides to the existing deck.
            Originals are never mutated.

Each slide is built by a dedicated handler (title / section_divider /
content / two_column / stats / table / chart / closing) and wrapped
in try/except so a single bad slide is recorded in the change log
without aborting the whole deck.

Usage:  python3 pptx_generator.py <plan.json> <output.pptx> [source.pptx]
Output: JSON result on stdout
"""

import json
import os
import shutil
import sys
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml import parse_xml
from pptx.util import Inches, Pt


# ─── Defaults ──────────────────────────────────────────────────
DEFAULT_COLORS: dict[str, str] = {
    "primary": "0A1230",     # Corporate navy
    "secondary": "0F1835",   # Dark blue
    "accent": "2E75B6",      # Accent blue
    "light_bg": "F6F7F9",    # Light background
    "text_dark": "1A1A2E",   # Dark text
    "text_light": "FFFFFF",  # White text
    "success": "10B981",     # Green
    "warning": "F59E0B",     # Amber
    "border": "E0E0E0",
}

# Default chart colour rotation for series — corporate accent first,
# then a balanced palette.
CHART_PALETTE = ["2E75B6", "10B981", "F59E0B", "8B5CF6", "EC4899", "06B6D4"]


# ─── Helpers ───────────────────────────────────────────────────
def hex_to_rgb(hex_str: str) -> RGBColor:
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def set_shape_fill(shape, color_hex: str) -> None:
    fill = shape.fill
    fill.solid()
    fill.fore_color.rgb = hex_to_rgb(color_hex)


def set_slide_background(slide, color_hex: str) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = hex_to_rgb(color_hex)


def add_text(
    shape, text: str, font_name: str, font_size: int,
    bold: bool = False, color_hex: str = "1A1A2E",
    alignment: int = PP_ALIGN.LEFT,
) -> None:
    """Add a single line of formatted text to a shape's first paragraph."""
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = alignment
    # Wipe any default text the first paragraph carried (text_frame
    # creates one empty run when the shape spawns).
    for r in list(p.runs):
        r._r.getparent().remove(r._r)
    run = p.add_run()
    run.text = text
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = hex_to_rgb(color_hex)


def add_bullets(shape, items: list[str], font_name: str, font_size: int,
                color_hex: str = "1A1A2E") -> None:
    """Add a bullet list. Each item gets a "• " prefix at level 0."""
    tf = shape.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.level = 0
        # Wipe default runs on the first paragraph.
        for r in list(p.runs):
            r._r.getparent().remove(r._r)
        # Stamp a real bullet character via the OOXML buChar element.
        # python-pptx's high-level API doesn't expose this directly;
        # raw XML is the documented escape hatch.
        pPr = p._pPr
        if pPr is None:
            pPr = parse_xml(
                '<a:pPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'lvl="0" indent="-228600" marL="228600">'
                '<a:buChar char="•"/></a:pPr>'
            )
            p._p.insert(0, pPr)
        else:
            # Drop existing bullet/number settings so a stray
            # buNone from a layout doesn't suppress ours.
            for child in list(pPr):
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag.startswith("bu"):
                    pPr.remove(child)
            pPr.append(parse_xml(
                '<a:buChar xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" char="•"/>'
            ))
        run = p.add_run()
        run.text = str(item)
        run.font.name = font_name
        run.font.size = Pt(font_size)
        run.font.color.rgb = hex_to_rgb(color_hex)


# ─── Slide builders ─────────────────────────────────────────────
def build_title_slide(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    set_slide_background(slide, colors["primary"])

    font = settings["font"]
    heading_font = font["heading"]
    body_font = font["body"]

    title = data.get("title", "")
    box = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(8), Inches(1.5))
    add_text(box, title, heading_font, 40, True, colors["text_light"], PP_ALIGN.LEFT)

    subtitle = data.get("subtitle", "")
    if subtitle:
        box2 = slide.shapes.add_textbox(Inches(1), Inches(3.0), Inches(8), Inches(0.8))
        add_text(box2, subtitle, body_font, 20, False, "B0C4DE", PP_ALIGN.LEFT)

    author = data.get("author", "")
    if author:
        box3 = slide.shapes.add_textbox(Inches(1), Inches(4.0), Inches(8), Inches(0.5))
        add_text(box3, author, body_font, 14, False, "8899AA", PP_ALIGN.LEFT)

    # Accent bar.
    line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(1), Inches(2.9), Inches(2), Inches(0.04)
    )
    set_shape_fill(line, colors["accent"])
    line.line.fill.background()
    return f"Title: {title[:60]}"


def build_section_divider(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_background(slide, colors["secondary"])

    heading_font = settings["font"]["heading"]
    title = data.get("title", "")
    box = slide.shapes.add_textbox(Inches(1), Inches(2.0), Inches(8), Inches(1.5))
    add_text(box, title, heading_font, 36, True, colors["text_light"], PP_ALIGN.CENTER)
    return f"Section: {title[:60]}"


def build_content_slide(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_background(slide, colors["light_bg"])

    font = settings["font"]
    heading_font = font["heading"]
    body_font = font["body"]
    heading_size = int(font.get("heading_size", 28))
    body_size = int(font.get("body_size", 16))

    title = data.get("title", "")
    box_title = slide.shapes.add_textbox(Inches(0.7), Inches(0.4), Inches(8.6), Inches(0.7))
    add_text(box_title, title, heading_font, heading_size, True, colors["text_dark"])

    y = 1.3
    body = data.get("body", "")
    if body:
        box_body = slide.shapes.add_textbox(Inches(0.7), Inches(y), Inches(8.6), Inches(1.0))
        add_text(box_body, body, body_font, body_size, False, colors["text_dark"])
        y += 1.0

    bullets = data.get("bullets") or []
    if bullets:
        box_b = slide.shapes.add_textbox(
            Inches(0.7), Inches(y), Inches(8.6), Inches(max(3.8 - (y - 1.3), 1.0))
        )
        add_bullets(box_b, bullets, body_font, body_size, colors["text_dark"])

    notes = data.get("speaker_notes", "")
    if notes:
        slide.notes_slide.notes_text_frame.text = notes

    parts = [f"Content: {title[:50]}"]
    if bullets:
        parts.append(f"{len(bullets)} bullets")
    if notes:
        parts.append("with notes")
    return " — ".join(parts)


def build_two_column_slide(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_background(slide, colors["light_bg"])

    font = settings["font"]
    heading_font = font["heading"]
    body_font = font["body"]
    heading_size = int(font.get("heading_size", 28))
    body_size = int(font.get("body_size", 16))

    title = data.get("title", "")
    box_title = slide.shapes.add_textbox(Inches(0.7), Inches(0.4), Inches(8.6), Inches(0.7))
    add_text(box_title, title, heading_font, heading_size, True, colors["text_dark"])

    def _column(x_in: float, col_data: dict) -> None:
        card = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(x_in), Inches(1.4), Inches(4.3), Inches(3.6)
        )
        set_shape_fill(card, "FFFFFF")
        card.line.fill.background()

        heading = col_data.get("heading", "")
        box_h = slide.shapes.add_textbox(
            Inches(x_in + 0.3), Inches(1.6), Inches(3.7), Inches(0.5)
        )
        add_text(box_h, heading, heading_font, 20, True, colors["accent"])

        bullets = col_data.get("bullets") or []
        if bullets:
            box_b = slide.shapes.add_textbox(
                Inches(x_in + 0.3), Inches(2.2), Inches(3.7), Inches(2.5)
            )
            add_bullets(box_b, bullets, body_font, body_size, colors["text_dark"])

    _column(0.5, data.get("left") or {})
    _column(5.2, data.get("right") or {})
    return f"Two-column: {title[:50]}"


def build_stats_slide(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_background(slide, colors["light_bg"])

    font = settings["font"]
    heading_font = font["heading"]
    body_font = font["body"]
    heading_size = int(font.get("heading_size", 28))

    title = data.get("title", "")
    box_title = slide.shapes.add_textbox(Inches(0.7), Inches(0.4), Inches(8.6), Inches(0.7))
    add_text(box_title, title, heading_font, heading_size, True, colors["text_dark"])

    stats = data.get("stats") or []
    if not stats:
        return f"Stats: {title[:50]} (empty)"

    num = min(len(stats), 4)
    total_width = 9.0
    gap = 0.3
    card_w = (total_width - gap * (num - 1)) / num
    start_x = 0.5

    for i in range(num):
        stat = stats[i]
        x = start_x + i * (card_w + gap)

        card = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.6), Inches(card_w), Inches(3.0)
        )
        set_shape_fill(card, "FFFFFF")
        card.line.fill.background()

        accent = stat.get("color") or colors["accent"]
        bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.6), Inches(card_w), Inches(0.06)
        )
        set_shape_fill(bar, accent)
        bar.line.fill.background()

        # Big number.
        box_val = slide.shapes.add_textbox(
            Inches(x + 0.2), Inches(2.0), Inches(card_w - 0.4), Inches(1.2)
        )
        add_text(box_val, str(stat.get("value", "")), heading_font, 40, True, accent, PP_ALIGN.CENTER)

        # Label.
        box_label = slide.shapes.add_textbox(
            Inches(x + 0.2), Inches(3.2), Inches(card_w - 0.4), Inches(0.8)
        )
        add_text(box_label, stat.get("label", ""), body_font, 14, False, "6B7280", PP_ALIGN.CENTER)

    return f"Stats: {title[:50]} ({num} KPI{'s' if num != 1 else ''})"


def build_table_slide(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_background(slide, colors["light_bg"])

    font = settings["font"]
    heading_font = font["heading"]
    body_font = font["body"]
    heading_size = int(font.get("heading_size", 28))

    title = data.get("title", "")
    box_title = slide.shapes.add_textbox(Inches(0.7), Inches(0.4), Inches(8.6), Inches(0.7))
    add_text(box_title, title, heading_font, heading_size, True, colors["text_dark"])

    headers = data.get("headers") or []
    rows = data.get("rows") or []
    if not headers and not rows:
        return f"Table: {title[:50]} (empty)"

    num_cols = len(headers) if headers else len(rows[0])
    num_rows = (1 if headers else 0) + len(rows)
    height = Inches(min(num_rows * 0.45, 3.8))

    table_shape = slide.shapes.add_table(
        num_rows, num_cols,
        Inches(0.5), Inches(1.3), Inches(9.0), height,
    )
    table = table_shape.table

    row_offset = 0
    if headers:
        for j, h in enumerate(headers):
            cell = table.cell(0, j)
            cell.text = str(h)
            cell.fill.solid()
            cell.fill.fore_color.rgb = hex_to_rgb(colors["primary"])
            for p in cell.text_frame.paragraphs:
                p.alignment = PP_ALIGN.CENTER
                for r in p.runs:
                    r.font.name = body_font
                    r.font.size = Pt(13)
                    r.font.bold = True
                    r.font.color.rgb = hex_to_rgb(colors["text_light"])
        row_offset = 1

    for i, row_data in enumerate(rows):
        for j, val in enumerate(row_data):
            if j >= num_cols:
                break
            cell = table.cell(row_offset + i, j)
            cell.text = str(val)
            cell.fill.solid()
            cell.fill.fore_color.rgb = hex_to_rgb(
                "FFFFFF" if i % 2 == 0 else colors["light_bg"]
            )
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.name = body_font
                    r.font.size = Pt(12)
                    r.font.color.rgb = hex_to_rgb(colors["text_dark"])

    return f"Table: {title[:50]} ({num_cols} cols × {len(rows)} rows)"


def build_chart_slide(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_background(slide, colors["light_bg"])

    font = settings["font"]
    heading_font = font["heading"]
    heading_size = int(font.get("heading_size", 28))

    title = data.get("title", "")
    box_title = slide.shapes.add_textbox(Inches(0.7), Inches(0.4), Inches(8.6), Inches(0.7))
    add_text(box_title, title, heading_font, heading_size, True, colors["text_dark"])

    chart_data_raw = data.get("data") or {}
    categories = chart_data_raw.get("categories") or []
    series_list = chart_data_raw.get("series") or []
    if not categories or not series_list:
        return f"Chart: {title[:50]} (no data)"

    chart_type_map = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "bar_horizontal": XL_CHART_TYPE.BAR_CLUSTERED,
        "line": XL_CHART_TYPE.LINE,
        "line_smooth": XL_CHART_TYPE.LINE,
        "pie": XL_CHART_TYPE.PIE,
        "doughnut": XL_CHART_TYPE.DOUGHNUT,
        "area": XL_CHART_TYPE.AREA,
    }
    chart_type = (data.get("chart_type") or "bar").lower()
    xl_type = chart_type_map.get(chart_type, XL_CHART_TYPE.COLUMN_CLUSTERED)

    chart_data = CategoryChartData()
    chart_data.categories = categories
    for series in series_list:
        chart_data.add_series(series.get("name", "Series"), series.get("values") or [])

    chart_shape = slide.shapes.add_chart(
        xl_type, Inches(0.5), Inches(1.2), Inches(9.0), Inches(4.0), chart_data
    )
    chart = chart_shape.chart

    chart.has_legend = len(series_list) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False

    try:
        plot = chart.plots[0]
        for i, series in enumerate(plot.series):
            fill = series.format.fill
            fill.solid()
            fill.fore_color.rgb = hex_to_rgb(CHART_PALETTE[i % len(CHART_PALETTE)])
    except Exception:
        pass

    return f"Chart: {title[:50]} ({chart_type}, {len(categories)} cats × {len(series_list)} series)"


def build_closing_slide(prs, data: dict, settings: dict, colors: dict) -> str:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_background(slide, colors["primary"])

    heading_font = settings["font"]["heading"]
    body_font = settings["font"]["body"]

    title = data.get("title", "Thank You")
    box = slide.shapes.add_textbox(Inches(1), Inches(2.0), Inches(8), Inches(1.2))
    add_text(box, title, heading_font, 40, True, colors["text_light"], PP_ALIGN.CENTER)

    subtitle = data.get("subtitle", "")
    if subtitle:
        box2 = slide.shapes.add_textbox(Inches(1), Inches(3.4), Inches(8), Inches(0.6))
        add_text(box2, subtitle, body_font, 18, False, "B0C4DE", PP_ALIGN.CENTER)

    return f"Closing: {title[:50]}"


SLIDE_BUILDERS = {
    "title": build_title_slide,
    "section_divider": build_section_divider,
    "content": build_content_slide,
    "two_column": build_two_column_slide,
    "stats": build_stats_slide,
    "table": build_table_slide,
    "chart": build_chart_slide,
    # content_with_image renders identically to content for now;
    # the placeholder description goes in the speaker notes so the
    # presenter can swap in the real image manually post-export.
    "content_with_image": build_content_slide,
    "closing": build_closing_slide,
}


# ─── Plan execution ────────────────────────────────────────────
def _resolve_settings(plan: dict) -> dict:
    settings = dict(plan.get("presentation_settings") or {})
    font = dict(settings.get("font") or {})
    language = (settings.get("language") or "en").lower()
    if language == "th":
        # Sarabun is bundled in the chat-edge image (see Dockerfile +
        # scripts/fonts/). python-pptx writes the font name verbatim;
        # PowerPoint will substitute the OS-installed Sarabun
        # automatically. The bundled TTFs are only loaded by the PDF
        # generator's reportlab path.
        font.setdefault("heading", "Sarabun")
        font.setdefault("body", "Sarabun")
        font.setdefault("heading_size", 32)
        font.setdefault("body_size", 18)
    else:
        font.setdefault("heading", "Calibri")
        font.setdefault("body", "Calibri")
        font.setdefault("heading_size", 28)
        font.setdefault("body_size", 16)
    settings["font"] = font
    return settings


def generate_pptx(plan: dict, output_path: str, source_path: str | None = None) -> dict:
    settings = _resolve_settings(plan)
    slides_plan = plan.get("slides") or []

    if source_path and os.path.exists(source_path):
        shutil.copy2(source_path, output_path)
        prs = Presentation(output_path)
        mode = "edit"
    else:
        prs = Presentation()
        prs.slide_width = Inches(10)
        prs.slide_height = Inches(5.625)
        mode = "create"

    layout = settings.get("layout", "16:9")
    if layout == "4:3":
        prs.slide_width = Inches(10)
        prs.slide_height = Inches(7.5)
    elif layout == "16:10":
        prs.slide_width = Inches(10)
        prs.slide_height = Inches(6.25)

    colors = dict(DEFAULT_COLORS)
    colors.update(settings.get("color_scheme") or {})

    if settings.get("title"):
        prs.core_properties.title = settings["title"]
    if settings.get("author"):
        prs.core_properties.author = settings["author"]

    change_log: list[dict[str, Any]] = []
    for i, slide_data in enumerate(slides_plan):
        slide_type = (slide_data.get("type") or "content").lower()
        builder = SLIDE_BUILDERS.get(slide_type, build_content_slide)
        try:
            detail = builder(prs, slide_data, settings, colors)
            change_log.append(
                {"slide": i + 1, "type": slide_type, "status": "ok", "detail": detail}
            )
        except Exception as exc:  # noqa: BLE001 — per-slide isolation
            change_log.append(
                {"slide": i + 1, "type": slide_type, "status": "error", "detail": str(exc)}
            )

    prs.save(output_path)
    file_size = os.path.getsize(output_path)
    return {
        "success": True,
        "mode": mode,
        "output_path": output_path,
        "file_size_bytes": file_size,
        "slide_count": len(prs.slides),
        "slides_created": sum(1 for c in change_log if c["status"] == "ok"),
        "slides_failed": sum(1 for c in change_log if c["status"] == "error"),
        "change_log": change_log,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {
                    "error": (
                        "Usage: pptx_generator.py <plan.json> <output.pptx> [source.pptx]"
                    )
                }
            )
        )
        return 1
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            plan = json.load(f)
        source = sys.argv[3] if len(sys.argv) > 3 else None
        result = generate_pptx(plan, sys.argv[2], source)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc), "success": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
