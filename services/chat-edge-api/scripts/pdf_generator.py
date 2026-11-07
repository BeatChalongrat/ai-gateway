#!/usr/bin/env python3
"""PDF generator — applies a JSON plan to produce a professional .pdf.

Sibling of docx_generator.py / excel_executor.py. Uses reportlab's
Platypus high-level API. Thai support comes from a bundled Sarabun
TTF (scripts/fonts/Sarabun-Regular.ttf + Sarabun-Bold.ttf) — see the
Dockerfile / scripts/fonts directory. Helvetica is the latin
fallback when the bundled font is missing.

Usage:  python3 pdf_generator.py <plan.json> <output.pdf>
Output: JSON result on stdout
"""

import json
import os
import sys
from typing import Any

from pypdf import PdfReader
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ─── Corporate palette ─────────────────────────────────────────
NAVY = HexColor("#0A1230")
DARK_BLUE = HexColor("#0F1835")
LIGHT_BG = HexColor("#F6F7F9")
BORDER = HexColor("#E0E0E0")
TEXT_DARK = HexColor("#1A1A2E")
TEXT_MUTED = HexColor("#6B7280")
CODE_BG = HexColor("#F5F5F5")


# ─── Font registration ─────────────────────────────────────────
# When the Sarabun TTFs are bundled (scripts/fonts/), Thai prompts
# render correctly. Without them we fall back to Helvetica, which
# does NOT support Thai — the generator still produces a valid PDF
# but Thai text comes out as boxes/empty glyphs. The Go service
# logs a warning if it sees the fallback path.
THAI_FONT = "ThaiFont"
THAI_FONT_BOLD = "ThaiFont-Bold"


def register_thai_font() -> str:
    """Returns the registered font name to use (THAI_FONT or
    'Helvetica' if no Thai TTF is available)."""
    candidates = [
        # Bundled in the chat-edge image — primary path.
        os.path.join(os.path.dirname(__file__), "fonts", "Sarabun-Regular.ttf"),
        # Common Linux paths (when running outside the container).
        "/usr/share/fonts/truetype/sarabun/Sarabun-Regular.ttf",
        "/usr/share/fonts/truetype/thai/THSarabunNew.ttf",
        "/usr/share/fonts/THSarabunNew.ttf",
        "/tmp/fonts/Sarabun-Regular.ttf",
    ]
    bold_candidates = [
        os.path.join(os.path.dirname(__file__), "fonts", "Sarabun-Bold.ttf"),
        "/usr/share/fonts/truetype/sarabun/Sarabun-Bold.ttf",
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(THAI_FONT, path))
        except Exception:
            continue
        for bpath in bold_candidates:
            if not os.path.exists(bpath):
                continue
            try:
                pdfmetrics.registerFont(TTFont(THAI_FONT_BOLD, bpath))
            except Exception:
                pass
            break
        return THAI_FONT
    return "Helvetica"


def _bold_variant(font_name: str) -> str:
    """Return the bold variant if registered, else the base name."""
    if font_name == THAI_FONT and THAI_FONT_BOLD in pdfmetrics.getRegisteredFontNames():
        return THAI_FONT_BOLD
    if font_name == "Helvetica":
        return "Helvetica-Bold"
    return font_name


# ─── Styles ────────────────────────────────────────────────────
def create_styles(language: str, font_name: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    bold_font = _bold_variant(font_name)
    base_size = 14 if language == "th" else 10

    return {
        "body": ParagraphStyle(
            "CustomBody", parent=base["Normal"],
            fontName=font_name, fontSize=base_size,
            leading=base_size * 1.6, spaceBefore=4, spaceAfter=8,
            textColor=TEXT_DARK,
        ),
        "h1": ParagraphStyle(
            "CustomH1", parent=base["Heading1"],
            fontName=bold_font, fontSize=base_size * 2,
            leading=base_size * 2.4, spaceBefore=24, spaceAfter=12,
            textColor=NAVY,
        ),
        "h2": ParagraphStyle(
            "CustomH2", parent=base["Heading2"],
            fontName=bold_font, fontSize=base_size * 1.5,
            leading=base_size * 1.8, spaceBefore=18, spaceAfter=8,
            textColor=DARK_BLUE,
        ),
        "h3": ParagraphStyle(
            "CustomH3", parent=base["Heading3"],
            fontName=font_name, fontSize=base_size * 1.2,
            leading=base_size * 1.5, spaceBefore=12, spaceAfter=6,
            textColor=DARK_BLUE,
        ),
        "bullet": ParagraphStyle(
            "CustomBullet",
            parent=ParagraphStyle("_p", fontName=font_name, fontSize=base_size,
                                  leading=base_size * 1.6, textColor=TEXT_DARK),
            leftIndent=30, bulletIndent=15, spaceBefore=2, spaceAfter=4,
        ),
        "blockquote": ParagraphStyle(
            "Blockquote",
            parent=ParagraphStyle("_p", fontName=font_name, fontSize=base_size,
                                  leading=base_size * 1.5, textColor=TEXT_MUTED),
            leftIndent=30, rightIndent=20,
            borderPadding=10, backColor=LIGHT_BG,
        ),
        "code": ParagraphStyle(
            "Code", parent=base["Code"],
            fontName="Courier", fontSize=base_size * 0.85,
            leading=base_size * 1.3, leftIndent=20,
            backColor=CODE_BG, borderPadding=8, textColor=TEXT_DARK,
        ),
        "title_main": ParagraphStyle(
            "TitleMain", parent=base["Title"],
            fontName=bold_font, fontSize=base_size * 3,
            leading=base_size * 3.5, alignment=TA_CENTER, textColor=white,
        ),
        "title_sub": ParagraphStyle(
            "TitleSub", parent=base["Normal"],
            fontName=font_name, fontSize=base_size * 1.5,
            leading=base_size * 2, alignment=TA_CENTER,
            textColor=HexColor("#B0C4DE"),
        ),
        "title_meta": ParagraphStyle(
            "TitleMeta", parent=base["Normal"],
            fontName=font_name, fontSize=base_size,
            alignment=TA_CENTER, textColor=TEXT_MUTED,
        ),
    }


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ─── Plan execution ────────────────────────────────────────────
def generate_pdf(plan: dict, output_path: str) -> dict:
    settings = plan.get("document_settings") or {}
    actions = plan.get("actions") or []
    language = (settings.get("language") or "en").lower()

    font_name = register_thai_font() if language == "th" else "Helvetica"
    styles = create_styles(language, font_name)

    page_size = A4 if (settings.get("page_size") or "A4").upper() == "A4" else letter
    if (settings.get("orientation") or "portrait").lower() == "landscape":
        page_size = landscape(page_size)

    margins = settings.get("margins") or {}
    left_m = margins.get("left", 72)
    right_m = margins.get("right", 72)
    top_m = margins.get("top", 72)
    bottom_m = margins.get("bottom", 72)

    header_text = settings.get("header_text", "")
    footer_text = settings.get("footer_text", "")
    title_page_settings = settings.get("title_page") or None

    doc = SimpleDocTemplate(
        output_path,
        pagesize=page_size,
        rightMargin=right_m, leftMargin=left_m,
        topMargin=top_m, bottomMargin=bottom_m,
        title=(title_page_settings or {}).get("title") or "Report",
    )

    story: list[Any] = []
    change_log: list[dict[str, Any]] = []

    def _decorate(canvas, _doc) -> None:
        canvas.saveState()
        w, h = page_size
        small = 8 if language != "th" else 10
        if header_text:
            canvas.setFont(font_name, small)
            canvas.setFillColor(TEXT_MUTED)
            canvas.drawString(left_m, h - 40, header_text)
            canvas.setStrokeColor(BORDER)
            canvas.line(left_m, h - 45, w - right_m, h - 45)
        if footer_text:
            canvas.setFont(font_name, small)
            canvas.setFillColor(TEXT_MUTED)
            page_num = canvas.getPageNumber()
            ft = footer_text.replace("{page}", str(page_num))
            # {total} is resolved on a second pass below; for now
            # show the current page number as a placeholder.
            ft = ft.replace("{total}", "?")
            canvas.drawCentredString(w / 2, 30, ft)
        canvas.restoreState()

    for i, action in enumerate(actions):
        action_type = action.get("type", "")
        try:
            if action_type == "add_title_page" and title_page_settings:
                story.append(Spacer(1, page_size[1] * 0.25))
                story.append(Paragraph(_escape_html(title_page_settings.get("title", "Report")), styles["title_main"]))
                story.append(Spacer(1, 20))
                subtitle = title_page_settings.get("subtitle") or ""
                if subtitle:
                    story.append(Paragraph(_escape_html(subtitle), styles["title_sub"]))
                    story.append(Spacer(1, 40))
                author = title_page_settings.get("author") or ""
                date_str = title_page_settings.get("date") or ""
                if author or date_str:
                    sep = "  •  " if author and date_str else ""
                    story.append(Paragraph(_escape_html(f"{author}{sep}{date_str}"), styles["title_meta"]))
                story.append(PageBreak())
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"Title page: {title_page_settings.get('title', '')[:60]}"})

            elif action_type == "add_heading":
                level = int(action.get("level", 1))
                text = _escape_html(action.get("text", ""))
                key = f"h{min(max(level, 1), 3)}"
                story.append(Paragraph(text, styles[key]))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"H{level}: {action.get('text', '')[:60]}"})

            elif action_type == "add_paragraph":
                raw = action.get("text", "")
                text = _escape_html(raw)
                if action.get("bold"):
                    text = f"<b>{text}</b>"
                if action.get("italic"):
                    text = f"<i>{text}</i>"
                story.append(Paragraph(text, styles["body"]))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"Paragraph ({len(raw)} chars)"})

            elif action_type == "add_bullet_list":
                items = action.get("items") or []
                for item in items:
                    story.append(Paragraph(f"• {_escape_html(str(item))}", styles["bullet"]))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"{len(items)} bullets"})

            elif action_type == "add_numbered_list":
                items = action.get("items") or []
                for idx, item in enumerate(items, 1):
                    story.append(Paragraph(f"{idx}. {_escape_html(str(item))}", styles["bullet"]))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"{len(items)} numbered items"})

            elif action_type == "add_table":
                headers = action.get("headers") or []
                rows = action.get("rows") or []
                style_name = (action.get("style") or "professional").lower()
                data: list[list[Any]] = []
                if headers:
                    data.append([_escape_html(str(h)) for h in headers])
                for row in rows:
                    data.append([_escape_html(str(c)) for c in row])
                if not data:
                    change_log.append({"action": i + 1, "type": action_type, "status": "skipped", "detail": "Empty table"})
                    continue
                col_count = max(len(r) for r in data)
                # Wrap every cell in a Paragraph so long Thai text
                # wraps inside its cell instead of overflowing.
                cell_style = ParagraphStyle(
                    "Cell", parent=styles["body"],
                    fontSize=9 if language != "th" else 12,
                    leading=(9 if language != "th" else 12) * 1.3,
                    spaceBefore=0, spaceAfter=0,
                )
                wrapped = [[Paragraph(c, cell_style) for c in row] for row in data]
                content_width = page_size[0] - left_m - right_m
                col_widths = [content_width / col_count] * col_count
                t = Table(wrapped, colWidths=col_widths, repeatRows=1 if headers else 0)
                cmds: list[tuple] = [
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9 if language != "th" else 12),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
                if headers and style_name == "professional":
                    cmds.extend([
                        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                        ("TEXTCOLOR", (0, 0), (-1, 0), white),
                        ("FONTNAME", (0, 0), (-1, 0), _bold_variant(font_name)),
                        ("FONTSIZE", (0, 0), (-1, 0), 10 if language != "th" else 13),
                        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                    ])
                    for r in range(1, len(data)):
                        if r % 2 == 0:
                            cmds.append(("BACKGROUND", (0, r), (-1, r), LIGHT_BG))
                t.setStyle(TableStyle(cmds))
                story.append(t)
                story.append(Spacer(1, 12))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"Table: {col_count} cols × {len(rows)} rows"})

            elif action_type == "add_page_break":
                story.append(PageBreak())
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": "Page break"})

            elif action_type == "add_spacer":
                height = float(action.get("height", 20))
                story.append(Spacer(1, height))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"Spacer {height}pt"})

            elif action_type == "add_horizontal_rule":
                story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceBefore=10, spaceAfter=10))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": "Horizontal rule"})

            elif action_type == "add_blockquote":
                text = _escape_html(action.get("text", ""))
                story.append(Paragraph(text, styles["blockquote"]))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"Blockquote ({len(action.get('text', ''))} chars)"})

            elif action_type == "add_code_block":
                code = _escape_html(action.get("code", "")).replace("\n", "<br/>")
                story.append(Paragraph(code, styles["code"]))
                change_log.append({"action": i + 1, "type": action_type, "status": "ok", "detail": f"Code ({len(action.get('code', ''))} chars)"})

            else:
                change_log.append({"action": i + 1, "type": action_type, "status": "skipped", "detail": f"Unknown action: {action_type}"})
        except Exception as exc:  # noqa: BLE001
            change_log.append({"action": i + 1, "type": action_type, "status": "error", "detail": str(exc)})

    doc.build(story, onFirstPage=_decorate, onLaterPages=_decorate)

    # Probe the rendered file for an accurate page count instead of
    # the "check_after_build" placeholder.
    page_count = 0
    try:
        page_count = len(PdfReader(output_path).pages)
    except Exception:
        pass

    file_size = os.path.getsize(output_path)
    return {
        "success": True,
        "output_path": output_path,
        "file_size_bytes": file_size,
        "page_count": page_count,
        "font_used": font_name,
        "actions_executed": sum(1 for c in change_log if c["status"] == "ok"),
        "actions_failed": sum(1 for c in change_log if c["status"] == "error"),
        "change_log": change_log,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: pdf_generator.py <plan.json> <output.pdf>"}))
        return 1
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            plan = json.load(f)
        result = generate_pdf(plan, sys.argv[2])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__, "success": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
