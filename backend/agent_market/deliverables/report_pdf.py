"""The report as PDF (reportlab), from the same content model.

reportlab wraps on spaces only and Thai has none between words, so lines
are broken here by measured width — at a space when one fits, otherwise
between grapheme clusters (a base letter never loses its vowel or tone mark).
reportlab does no OpenType shaping; stacked Thai marks are positioned by
the font's defaults. The .docx is the reference document; the PDF is for
reading and printing.
"""

from __future__ import annotations

import unicodedata
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .theme import FONT_FILES

REG, BOLD = "AMSarabun", "AMSarabun-Bold"


def _fonts(face: str) -> None:
    files = FONT_FILES[face]
    if REG not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(REG, files["regular"]))
        pdfmetrics.registerFont(TTFont(BOLD, files["bold"]))
        from reportlab.lib.fonts import addMapping

        addMapping(REG, 0, 0, REG)
        addMapping(REG, 1, 0, BOLD)


def _clusters(text: str) -> list[str]:
    out: list[str] = []
    for ch in text:
        if out and unicodedata.category(ch) in ("Mn", "Me"):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    lines = []
    for para in text.split("\n"):
        cur = ""
        last_space = -1
        for cl in _clusters(para):
            trial = cur + cl
            if pdfmetrics.stringWidth(trial, font, size) <= width or not cur:
                cur = trial
                if cl == " ":
                    last_space = len(cur)
                continue
            if last_space > len(cur) * 0.5:
                lines.append(cur[:last_space].rstrip())
                cur = cur[last_space:] + cl
            else:
                lines.append(cur)
                cur = cl
            last_space = -1
        lines.append(cur)
    return lines


class PdfWriter:
    def __init__(self, model: dict, theme: dict, charts: dict[str, str]):
        self.m, self.t, self.charts = model, theme, charts
        self.p = theme["palette"]
        _fonts(theme["fonts"]["thai"])
        self.width = A4[0] - 3.6 * cm

    def _c(self, key):
        return HexColor("#" + self.p[key])

    def para(self, text, size=10.5, bold=False, color="ink", width=None, space_after=4):
        font = BOLD if bold else REG
        lines = wrap(text, font, size, (width or self.width) - 2)
        style = ParagraphStyle("p", fontName=font, fontSize=size, leading=size * 1.45, textColor=self._c(color),
                               spaceAfter=space_after)
        return Paragraph("<br/>".join(escape(l) for l in lines), style)

    def table(self, block, size=8.5):
        cols = block["columns"]
        n = len(cols)
        cite_col = block.get("cite_col")
        num_from = block.get("numeric_from")
        widths = []
        for j in range(n):
            if j == cite_col:
                widths.append(1.2 * cm)
            elif num_from is not None and j >= num_from:
                widths.append(2.3 * cm)
            else:
                widths.append(None)
        fixed = sum(w for w in widths if w)
        free = [j for j, w in enumerate(widths) if w is None]
        for j in free:
            widths[j] = max(2.0 * cm, (self.width - fixed) / max(1, len(free)))
        data = [[self.para(h, size, True, "on_primary", widths[j], 0) for j, h in enumerate(cols)]]
        for row in block["rows"]:
            data.append([self.para("" if v is None else str(v), size - (1 if j == cite_col else 0), False,
                                   "muted" if j == cite_col else "ink", widths[j], 0) for j, v in enumerate(row)])
        t = Table(data, colWidths=widths, repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), self._c("primary")),
                 ("LINEBELOW", (0, 1), (-1, -1), 0.4, self._c("rule")),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for i in range(1, len(data)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), self._c("surface")))
        for i in block.get("strong_rows") or []:
            style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), self._c("accent_soft")))
        t.setStyle(TableStyle(style))
        return t

    def write(self, path: str) -> dict:
        m = self.m
        story = []

        def cover(canvas, doc):
            canvas.saveState()
            canvas.setFillColor(self._c("primary"))
            canvas.rect(0, A4[1] - 11 * cm, A4[0], 11 * cm, stroke=0, fill=1)
            canvas.setFillColor(self._c("gold"))
            canvas.setFont(BOLD, 12)
            canvas.drawString(1.8 * cm, A4[1] - 2.4 * cm, m["company"] or "")
            canvas.setFillColor(self._c("on_primary"))
            canvas.setFont(BOLD, 30)
            canvas.drawString(1.8 * cm, A4[1] - 5.6 * cm, m["title"])
            canvas.setFont(REG, 16)
            canvas.drawString(1.8 * cm, A4[1] - 6.8 * cm, m["subtitle"])
            canvas.setFont(REG, 10.5)
            y = A4[1] - 8.2 * cm
            for line in m["cover_lines"] + [m["prepared"]]:
                canvas.drawString(1.8 * cm, y, line)
                y -= 0.55 * cm
            canvas.restoreState()

        def later(canvas, doc):
            canvas.saveState()
            canvas.setFont(REG, 7.5)
            canvas.setFillColor(self._c("muted"))
            canvas.drawString(1.8 * cm, 1.1 * cm, m["source_note"][:120])
            canvas.drawRightString(A4[0] - 1.8 * cm, 1.1 * cm, f"หน้า {doc.page}")
            canvas.restoreState()

        story.append(Spacer(1, 10.5 * cm))
        kp = m["kpis"]
        cell_w = self.width / len(kp)
        tiles = [[[self.para(k["label"], 8.5, False, "muted", cell_w, 1),
                   self.para(k["value"], 14, True, "primary", cell_w, 1),
                   self.para((k["delta"] or "") + f" [{k['cite']}]", 8, False, "muted", cell_w, 0)] for k in kp]]
        tt = Table(tiles, colWidths=[cell_w] * len(kp))
        tt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), self._c("surface")),
                                ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        story += [tt, PageBreak()]
        for sec in m["sections"]:
            story.append(self.para(sec["title"], 16, True, "primary", space_after=8))
            for b in sec["blocks"]:
                if b["type"] == "paragraph":
                    story.append(self.para("".join(t + (f" [{c}]" if c else "") for t, c in b["runs"]),
                                           color="muted" if b.get("muted") else "ink"))
                elif b["type"] == "kpis":
                    continue
                elif b["type"] == "table":
                    if b["rows"]:
                        story += [self.table(b), Spacer(1, 4)]
                        if b.get("caption"):
                            story.append(self.para(b["caption"], 8, color="muted"))
                    else:
                        story.append(self.para(b.get("empty_text") or "ไม่มีรายการ", color="muted"))
                elif b["type"] == "chart" and self.charts.get(b["chart"]):
                    story += [Image(self.charts[b["chart"]], width=self.width, height=self.width * 2.9 / 6.3), Spacer(1, 6)]
                elif b["type"] == "bullets":
                    for runs in b["items"]:
                        story.append(self.para("• " + "".join(t + (f" [{c}]" if c else "") for t, c in runs)))
                elif b["type"] == "subheading":
                    story.append(self.para(b["text"], 12, True, "primary"))
            story.append(Spacer(1, 10))
        doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.8 * cm,
                                bottomMargin=1.8 * cm, title=f"{m['title']} {m['subtitle']}", author=m["company"] or "SRAN Agent")
        doc.build(story, onFirstPage=cover, onLaterPages=later)
        return {"path": path}
