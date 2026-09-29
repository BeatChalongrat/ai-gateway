"""The report (.docx) — cover, executive summary, tables, charts,
exceptions, assumptions and the source/citation appendices.

Reuses docx_generator.py (Thai font wiring, page setup, the paged footer)
by import; nothing in that shared script is changed for this path.
Sarabun is embedded in the file so the report looks the same on a machine
that does not have the font installed.
"""

from __future__ import annotations

import os
import uuid
import zipfile

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

import docx_generator as dg  # /sandbox/scripts — shared, imported, not modified

from . import thai_break
from .fit import SAFETY, text_width_pt
from .theme import FONT_FILES, rgb

BODY_PT = 10.5
SMALL_PT = 8.5
# A table cell's left + right margins (_cell_margins: 90 twips each), in points.
CELL_PAD_PT = 9.0
MIN_CELL_PT = 6.5
EMU_PER_PT = 12700


def _rgb(hex6: str) -> RGBColor:
    return RGBColor(*rgb(hex6))


def _shade(cell, hex6: str) -> None:
    tcPr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex6)
    tcPr.append(shd)


def _cell_margins(table, top=60, bottom=60, left=90, right=90) -> None:
    tblPr = table._element.tblPr
    mar = OxmlElement("w:tblCellMar")
    for side, v in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(v))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.append(mar)


def _borders(table, color: str, inside_h=True, outer=False) -> None:
    tblPr = table._element.tblPr
    b = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        on = (side == "insideH" and inside_h) or (outer and side in ("top", "bottom"))
        el.set(qn("w:val"), "single" if on else "nil")
        if on:
            el.set(qn("w:sz"), "4")
            el.set(qn("w:color"), color)
        b.append(el)
    tblPr.append(b)


def _no_split(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:cantSplit")
    trPr.append(el)


def _repeat_header(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    trPr.append(el)


def _run(p, text, font, size=None, bold=False, color=None, superscript=False, italic=False):
    r = p.add_run(text)
    dg._set_run_cs_font(r, font)
    if size:
        r.font.size = Pt(size)
        rPr = r._element.get_or_add_rPr()
        szcs = OxmlElement("w:szCs")
        szcs.set(qn("w:val"), str(int(size * 2)))
        rPr.append(szcs)
    if bold:
        r.bold = True
        rPr = r._element.get_or_add_rPr()
        rPr.append(OxmlElement("w:bCs"))
    r.italic = italic
    if color:
        r.font.color.rgb = _rgb(color)
    if superscript:
        r.font.superscript = True
    return r


# ── Thai in table cells (AM-1b → AM-2) ────────────────────────────────────
# Word without its Thai proofing tools treats every boundary between two Thai
# characters as a place to break a line, so any Thai text Word has to wrap
# in a table cell can break mid-word ("อ้างอิ|ง", "บริ|หาร"). The report
# therefore never lets Word wrap Thai: each column is at least as wide as its
# widest word, and a cell whose Thai text is wider than its column is broken
# here — between words, found by libthai's dictionary (deliverables/
# thai_break.py), or at spaces — into lines ending in an explicit line
# break. A single word wider than its column gets a smaller size (down to
# MIN_CELL_PT).


def _is_thai(text: str) -> bool:
    return any(0x0E00 <= ord(ch) <= 0x0E7F for ch in text)


def _widest_word_pt(text: str, font: str, size: float, bold: bool) -> float:
    return max((text_width_pt(w, font, size, bold) for w, _ in thai_break.words(text)), default=0.0) * SAFETY


def _thai_lines(text: str, font: str, size: float, bold: bool, avail_pt: float) -> list[str]:
    """Lines broken between words only, each no wider than avail_pt (a longer word stays whole)."""
    lines, cur = [], ""
    for word, spaced in thai_break.words(text):
        cand = (cur + (" " if spaced else "") + word) if cur else word
        if cur and text_width_pt(cand, font, size, bold) * SAFETY > avail_pt:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    return lines + ([cur] if cur else [])


def _cell_text(p, text: str, font: str, size: float, width_emu: int, bold=False, color=None, pad_pt=CELL_PAD_PT):
    """Text in a table cell that Word never has to wrap (see above)."""
    if not _is_thai(text):
        return _run(p, text, font, size, bold=bold, color=color)
    avail = width_emu / EMU_PER_PT - pad_pt
    while size > MIN_CELL_PT and _widest_word_pt(text, font, size, bold) > avail:
        size = round(size - 0.5, 1)
    lines = _thai_lines(text, font, size, bold, avail)
    r = None
    for i, line in enumerate(lines):
        if i:
            r.add_break()
        r = _run(p, line, font, size, bold=bold, color=color)
    r._am_last_line_pt = text_width_pt(lines[-1], font, size, bold) * SAFETY if lines else 0.0
    return r


def _para(doc_or_cell, space_before=0, space_after=4, align=None, line=1.15):
    p = doc_or_cell.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line
    if align is not None:
        p.alignment = align
    return p


class ReportWriter:
    def __init__(self, model: dict, theme: dict, charts: dict[str, str]):
        self.m = model
        self.t = theme
        self.p = theme["palette"]
        self.font = theme["fonts"]["thai"]
        self.charts = charts  # chart id → png path
        self.doc = Document()
        self.bindings: list[dict] = []

    # ── page furniture ────────────────────────────────────────────────
    def setup(self):
        doc = self.doc
        dg.setup_thai_font(doc, self.font, 11)
        # The document's complex-script language is Thai (python-docx's
        # template says ar-SA): Word then breaks Thai lines between words
        # instead of anywhere ("ค่าจ้า|ง") in narrow table cells.
        defaults = doc.styles.element.find(qn("w:docDefaults"))
        lang = defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}/{qn('w:lang')}") if defaults is not None else None
        if lang is not None:
            lang.set(qn("w:bidi"), "th-TH")
        st = doc.styles["Normal"]
        st.font.size = Pt(BODY_PT)
        st.font.color.rgb = _rgb(self.p["ink"])
        rpr = st.element.get_or_add_rPr()
        szcs = OxmlElement("w:szCs")
        szcs.set(qn("w:val"), str(int(BODY_PT * 2)))
        rpr.append(szcs)
        st.paragraph_format.space_after = Pt(4)
        st.paragraph_format.line_spacing = 1.2
        for lvl, size in ((1, 17), (2, 12.5)):
            hs = doc.styles[f"Heading {lvl}"]
            hs.font.size = Pt(size)
            hs.font.bold = True
            hs.font.color.rgb = _rgb(self.p["primary"])
            hs.paragraph_format.space_before = Pt(16 if lvl == 1 else 10)
            hs.paragraph_format.space_after = Pt(6 if lvl == 1 else 3)
            hs.paragraph_format.keep_with_next = True
            hr = hs.element.get_or_add_rPr()
            for tag in ("w:szCs",):
                el = OxmlElement(tag)
                el.set(qn("w:val"), str(int(size * 2)))
                hr.append(el)
        dg.setup_page(doc, {"page_size": "A4", "margins": {"top": 0.85, "bottom": 0.8, "left": 0.9, "right": 0.9}})
        sec = doc.sections[0]
        sec.different_first_page_header_footer = True
        sec.header_distance = Cm(0.9)
        sec.footer_distance = Cm(0.8)
        # Running header: company · report title (not on the cover).
        hp = sec.header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _run(hp, (self.m["company"] + " · " if self.m["company"] else "") + self.m["title"] + " " + self.m["subtitle"],
             self.font, 8, color=self.p["muted"])
        # Footer: source note left, page x / y right — a borderless two-cell table,
        # which every Word/LibreOffice version lays out the same way.
        text_w = sec.page_width - sec.left_margin - sec.right_margin
        ft = sec.footer.add_table(rows=1, cols=2, width=text_w)
        _borders(ft, self.p["rule"], inside_h=False)
        left, right = ft.rows[0].cells
        left.width, right.width = int(text_w * 0.8), int(text_w * 0.2)
        lp = left.paragraphs[0]
        _run(lp, self.m["source_note"], self.font, 7.5, color=self.p["muted"])
        rp = right.paragraphs[0]
        rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        dg._build_paged_footer(rp, "หน้า {page} / {total}", self.font)
        for run in rp.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = _rgb(self.p["muted"])
        empty = sec.footer.paragraphs[0]
        empty.paragraph_format.space_after = Pt(0)

    # ── cover ─────────────────────────────────────────────────────────
    def cover(self, logo_path: str | None):
        doc, p = self.doc, self.p
        band = doc.add_table(rows=1, cols=1)
        band.alignment = WD_TABLE_ALIGNMENT.CENTER
        _borders(band, p["primary"], inside_h=False)
        cell = band.rows[0].cells[0]
        _shade(cell, p["primary"])
        cell.width = doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin
        cp = cell.paragraphs[0]
        cp.paragraph_format.space_before = Pt(26)
        if logo_path and os.path.exists(logo_path):
            cp.add_run().add_picture(logo_path, height=Cm(1.4))
            cp = cell.add_paragraph()
        _run(cp, (self.m["company"] or "").upper() if self.m["company"].isascii() else self.m["company"],
             self.font, 11, bold=True, color=p["gold"])
        tp = cell.add_paragraph()
        tp.paragraph_format.space_before = Pt(40)
        tp.paragraph_format.space_after = Pt(2)
        _run(tp, self.m["title"], self.font, 30, bold=True, color=p["on_primary"])
        sp = cell.add_paragraph()
        sp.paragraph_format.space_after = Pt(30)
        _run(sp, self.m["subtitle"], self.font, 16, color=p["on_primary"])
        for line in self.m["cover_lines"]:
            lp = cell.add_paragraph()
            lp.paragraph_format.space_after = Pt(2)
            _run(lp, line, self.font, 10.5, color="DCE3EE")
        endp = cell.add_paragraph()
        endp.paragraph_format.space_after = Pt(28)
        _run(endp, self.m["prepared"], self.font, 10.5, color="DCE3EE")

        # Key figures on the cover, straight from the computed table.
        _para(doc, space_before=18, space_after=6)
        self.kpi_tiles()
        note = _para(doc, space_before=14)
        _run(note, "ทุกตัวเลขในรายงานนี้มีรหัสอ้างอิง (เช่น c12) ที่บอกได้ว่ามาจากไฟล์ไหน ชีตไหน ช่องไหน "
                   "และคำนวณด้วยสคริปต์ใด ดูภาคผนวก ข", self.font, 9, color=p["muted"])
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    def kpi_tiles(self):
        doc, p = self.doc, self.p
        k = self.m["kpis"]
        t = doc.add_table(rows=1, cols=len(k))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        _borders(t, p["rule"], inside_h=False)
        _cell_margins(t, 110, 110, 120, 120)
        sec = doc.sections[0]
        tile = int((sec.page_width - sec.left_margin - sec.right_margin) / len(k))
        t.autofit = False
        pad = 12.0  # 120 twips each side
        for i, kp in enumerate(k):
            c = t.rows[0].cells[i]
            c.width = tile
            _shade(c, p["surface"])
            lp = c.paragraphs[0]
            lp.paragraph_format.space_after = Pt(2)
            _cell_text(lp, kp["label"], self.font, 8.5, tile, color=p["muted"], pad_pt=pad)
            vp = c.add_paragraph()
            vp.paragraph_format.space_after = Pt(1)
            _run(vp, kp["value"], self.font, 15, bold=True, color=p["primary"])
            dp = c.add_paragraph()
            dp.paragraph_format.space_after = Pt(0)
            if kp["delta"]:
                color = p["muted"] if kp["good"] is None else (p["positive"] if kp["good"] else p["negative"])
                arrow = {"up": "▲ ", "down": "▼ ", "flat": ""}[kp["tone"]]
                last = _cell_text(dp, arrow + kp["delta"], self.font, 8, tile, color=color, pad_pt=pad)
            else:
                last = _cell_text(dp, "ไม่มีข้อมูลเปรียบเทียบ", self.font, 8, tile, color=p["muted"], pad_pt=pad)
            cite = f"  [{kp['cite']}]"
            used = getattr(last, "_am_last_line_pt", None)
            if used is not None and used + text_width_pt(cite, self.font, 7) * SAFETY > tile / EMU_PER_PT - pad:
                last.add_break()      # the cite goes on its own line rather than making Word wrap Thai
                cite = cite.strip()
            _run(dp, cite, self.font, 7, color=p["muted"])

    # ── blocks ────────────────────────────────────────────────────────
    def heading(self, text, level=1):
        h = self.doc.add_heading(level=level)
        _run(h, text, self.font, 17 if level == 1 else 12.5, bold=True, color=self.p["primary"])
        if level == 1:
            pPr = h._element.get_or_add_pPr()
            bdr = OxmlElement("w:pBdr")
            b = OxmlElement("w:bottom")
            b.set(qn("w:val"), "single")
            b.set(qn("w:sz"), "8")
            b.set(qn("w:space"), "4")
            b.set(qn("w:color"), self.p["accent"])
            bdr.append(b)
            pPr.append(bdr)
        return h

    def paragraph(self, runs, muted=False):
        p = _para(self.doc, space_after=6)
        for text, cite in runs:
            _run(p, text, self.font, BODY_PT, color=self.p["muted"] if muted else None)
            if cite:
                _run(p, cite, self.font, 6.5, color=self.p["muted"], superscript=True)

    def bullets(self, items):
        for runs in items:
            p = self.doc.add_paragraph(style="List Bullet")
            p.paragraph_format.space_after = Pt(3)
            for text, cite in runs:
                _run(p, text, self.font, BODY_PT)
                if cite:
                    _run(p, cite, self.font, 6.5, color=self.p["muted"], superscript=True)

    def table(self, block):
        doc, p = self.doc, self.p
        cols = block["columns"]
        rows = block["rows"]
        if not rows:
            para = _para(doc, space_after=8)
            _run(para, block.get("empty_text") or "ไม่มีรายการ", self.font, BODY_PT, color=p["muted"])
            return
        size = SMALL_PT if block.get("small") else 9.5
        t = doc.add_table(rows=1 + len(rows), cols=len(cols))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        _borders(t, p["rule"], inside_h=True)
        _cell_margins(t)
        num_from = block.get("numeric_from")
        cite_col = block.get("cite_col")
        widths = self._widths(t, block, size)
        hdr = t.rows[0]
        _repeat_header(hdr)
        for j, name in enumerate(cols):
            c = hdr.cells[j]
            _shade(c, p["primary"])
            cp = c.paragraphs[0]
            cp.paragraph_format.space_after = Pt(0)
            if num_from is not None and j >= num_from and j != cite_col:
                cp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _cell_text(cp, name, self.font, size, widths[j], bold=True, color=p["on_primary"])
        strong = set(block.get("strong_rows") or [])
        for i, row in enumerate(rows):
            tr = t.rows[i + 1]
            _no_split(tr)
            for j, val in enumerate(row):
                c = tr.cells[j]
                if self.t["table"].get("zebra") and i % 2 == 1:
                    _shade(c, p["surface"])
                if i in strong:
                    _shade(c, p["accent_soft"])
                cp = c.paragraphs[0]
                cp.paragraph_format.space_after = Pt(0)
                is_cite = j == cite_col
                if (num_from is not None and j >= num_from and not is_cite):
                    cp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                text = "" if val is None else str(val)
                color = p["muted"] if is_cite else (p["negative"] if text.startswith("(") and not is_cite else None)
                _cell_text(cp, text, self.font, 7 if is_cite else size, widths[j], bold=i in strong and not is_cite,
                           color=color)
        if block.get("caption"):
            cap = _para(doc, space_before=3, space_after=10)
            _run(cap, block["caption"], self.font, 8, color=p["muted"])
        else:
            _para(doc, space_after=6)

    def _widths(self, t, block, size) -> list[int]:
        """Label columns get the room; numbers get a fixed width; the cite id a narrow one — and no
        column is narrower than its widest word (header included), so Word never breaks a Thai word."""
        sec = self.doc.sections[0]
        total = int(sec.page_width - sec.left_margin - sec.right_margin)
        cols = block["columns"]
        num_from = block.get("numeric_from")
        cite_col = block.get("cite_col")

        def need(j) -> int:
            words = [(str(cols[j]), size, True)] + [
                ("" if r[j] is None else str(r[j]), 7 if j == cite_col else size, False) for r in block["rows"]]
            pt = max(_widest_word_pt(w, self.font, s, b) for w, s, b in words) + CELL_PAD_PT
            return int(pt * EMU_PER_PT)
        widths = [None] * len(cols)
        for j, name in enumerate(cols):
            if j == cite_col:
                widths[j] = max(int(Cm(1.15)), need(j))
            elif num_from is not None and j >= num_from:
                widths[j] = max(int(Cm(1.45) if name in ("%",) else Cm(2.15)), need(j))
        fixed = sum(w for w in widths if w is not None)
        free = [j for j, w in enumerate(widths) if w is None]
        if free:
            share = max(int(Cm(2.2)), int((total - fixed) / len(free)))
            for j in free:
                widths[j] = share
        t.autofit = False
        for row in t.rows:
            for j, cell in enumerate(row.cells):
                cell.width = widths[j]
        return widths

    def chart(self, chart_id):
        spec = next((c for c in self.m["charts"] if c["id"] == chart_id), None)
        path = self.charts.get(chart_id)
        if not spec or not path:
            return
        tp = _para(self.doc, space_before=6, space_after=2)
        tp.paragraph_format.keep_with_next = True
        _run(tp, spec["title"], self.font, 11, bold=True, color=self.p["ink"])
        _run(tp, "  " + spec.get("subtitle", ""), self.font, 8.5, color=self.p["muted"])
        pp = _para(self.doc, space_after=2, align=WD_ALIGN_PARAGRAPH.CENTER)
        pp.add_run().add_picture(path, width=Cm(16.2))
        cap = _para(self.doc, space_after=10)
        _run(cap, f"ที่มา: ตารางที่คำนวณแล้ว [{spec.get('cite') or '—'}] · กราฟวาดจากตัวเลขชุดเดียวกับตาราง",
             self.font, 8, color=self.p["muted"])

    # ── assemble ──────────────────────────────────────────────────────
    def write(self, path: str, logo_path: str | None = None) -> dict:
        self.setup()
        self.cover(logo_path)
        appendix_started = False
        for sec in self.m["sections"]:
            if sec.get("appendix") and not appendix_started:
                self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
                appendix_started = True
            if sec["key"] == "assumptions":
                self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            self.heading(sec["title"])
            for b in sec["blocks"]:
                kind = b["type"]
                if kind == "paragraph":
                    self.paragraph(b["runs"], b.get("muted", False))
                elif kind == "kpis":
                    self.kpi_tiles()
                    _para(self.doc, space_after=4)
                elif kind == "table":
                    self.table(b)
                elif kind == "chart":
                    self.chart(b["chart"])
                elif kind == "bullets":
                    self.bullets(b["items"])
                elif kind == "subheading":
                    self.heading(b["text"], 2)
        props = self.doc.core_properties
        props.title = f"{self.m['title']} {self.m['subtitle']}"
        props.author = self.m["company"] or "Agent Market"
        props.subject = self.m["source_note"]
        self.doc.save(path)
        embedded = embed_fonts(path, self.font)
        return {"path": path, "embedded_fonts": embedded}


# ── font embedding (ECMA-376 Part 1 §17.8.1, obfuscated TrueType) ─────────

def _obfuscate(data: bytes, key_guid: str) -> bytes:
    key = bytes.fromhex(key_guid.strip("{}").replace("-", ""))
    head = bytearray(data[:32])
    for i in range(32):
        head[i] ^= key[15 - (i % 16)]
    return bytes(head) + data[32:]


def embed_fonts(docx_path: str, face: str) -> list[str]:
    files = FONT_FILES.get(face) or {}
    todo = [(kind, path) for kind, path in (("Regular", files.get("regular")), ("Bold", files.get("bold")))
            if path and os.path.exists(path)]
    if not todo:
        return []
    with zipfile.ZipFile(docx_path) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    font_rels = []
    embeds = []
    for i, (kind, path) in enumerate(todo, start=1):
        # Deterministic key: the same face always embeds to the same bytes.
        key = "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"agent-market/font/{face}/{kind}")).upper() + "}"
        with open(path, "rb") as f:
            entries[f"word/fonts/font{i}.odttf"] = _obfuscate(f.read(), key)
        rid = f"rIdAmFont{i}"
        font_rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/font" Target="fonts/font{i}.odttf"/>')
        embeds.append(f'<w:embed{kind} r:id="{rid}" w:fontKey="{key}"/>')
    ft = entries["word/fontTable.xml"].decode("utf-8")
    if 'xmlns:r=' not in ft.split(">", 2)[1]:
        ft = ft.replace("<w:fonts ", '<w:fonts xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ', 1)
    font_el = (f'<w:font w:name="{face}"><w:charset w:val="DE"/><w:family w:val="swiss"/>'
               f'<w:pitch w:val="variable"/>{"".join(embeds)}</w:font>')
    ft = ft.replace("</w:fonts>", font_el + "</w:fonts>")
    entries["word/fontTable.xml"] = ft.encode("utf-8")
    entries["word/_rels/fontTable.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(font_rels) + "</Relationships>").encode("utf-8")
    ct = entries["[Content_Types].xml"].decode("utf-8")
    if 'Extension="odttf"' not in ct:
        ct = ct.replace("<Types ", "<Types ", 1).replace(
            "</Types>", '<Default Extension="odttf" ContentType="application/vnd.openxmlformats-officedocument.obfuscatedFont"/></Types>')
    entries["[Content_Types].xml"] = ct.encode("utf-8")
    st = entries["word/settings.xml"].decode("utf-8")
    if "embedTrueTypeFonts" not in st:
        # Must come early in w:settings (after w:writeProtection/w:view/w:zoom…); Word accepts it after w:zoom.
        if "<w:zoom" in st:
            i = st.index("<w:zoom")
            j = st.index("/>", i) + 2
            st = st[:j] + "<w:embedTrueTypeFonts/>" + st[j:]
        else:
            st = st.replace(">", "><w:embedTrueTypeFonts/>", 2).replace("<w:embedTrueTypeFonts/>", "", 1)
    entries["word/settings.xml"] = st.encode("utf-8")
    tmp = docx_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name in (["[Content_Types].xml"] + [n for n in entries if n != "[Content_Types].xml"]):
            out.writestr(name, entries[name])
    os.replace(tmp, docx_path)
    return [f"{face} {kind}" for kind, _ in todo]
