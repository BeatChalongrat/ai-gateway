"""The report (.docx): the content model laid out by the layout catalogue, in the template's styles (AM21).

The writer applies named styles — "AM Body", "AM Cell Number", "AM Table" — from the Word template that
look/dotx.py builds out of look.json, and never direct formatting: no run of the file carries a size, a
colour or a font of its own (the gate reads that back, ``docx.styles``). Every text whose frame is fixed
(the cover band, the headline tiles, a table column) is measured in the real typeface before it is
placed (look/measure.py), and broken between words where a reader's Word would break a Thai word
anywhere. A section is laid out by the layout the catalogue chose for it (look/catalogue.py); content
too long for every layout is refused naming the section, and no file is written.

The headline figures are printed once, on the cover. A figure in a sentence is followed by nothing: the
references of a paragraph or a list are listed under it ("References: c61, c101"), and a table keeps its
reference column. Sarabun is embedded so the report looks the same on a machine without the font.
"""

from __future__ import annotations

import os
import uuid
import zipfile

from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Emu

from ..engine.words import SHARED as S
from .look import catalogue, dotx, measure
from .look.tokens import LOOK

FMT = "docx"
EMU_PER_PT = 12700
# a table cell's left + right margins ("AM Table": 90 twips each), in points; the tiles' (120 each)
CELL_PAD_PT = 9.0
TILE_PAD_PT = 12.0
BAND_PAD_PT = 9.0


def _no_split(row) -> None:
    row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))


def _repeat_header(row) -> None:
    row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))


def _shade(cell, hex6: str) -> None:
    tcPr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex6)
    tcPr.append(shd)


def _borders(table, color: str, inside_h=True, outer=False) -> None:
    """(kept for the review stamp, signoff.py, which is laid over a finished file)"""
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


def _cell_margins(table, top=60, bottom=60, left=90, right=90) -> None:
    """(kept for the review stamp, signoff.py)"""
    tblPr = table._element.tblPr
    mar = OxmlElement("w:tblCellMar")
    for side, v in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(v))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.append(mar)


def _cell_text(p, text: str, font: str, size: float, width_emu: int, bold=False, color=None, pad_pt=CELL_PAD_PT):
    """(kept for the review stamp, signoff.py: a stamp laid over a finished file sets its own look) Text in a
    cell that Word never has to wrap: Thai broken between words here, at the size given."""
    from docx.shared import Pt, RGBColor

    from .theme import rgb

    def run(t):
        r = p.add_run(t)
        r.font.size = Pt(size)
        r.font.bold = bold
        if color:
            r.font.color.rgb = RGBColor(*rgb(color))
        return r

    if not measure.is_thai(text):
        return run(text)
    avail = width_emu / EMU_PER_PT - pad_pt
    lines = measure.break_lines(text, size, avail, bold)
    r = None
    for i, line in enumerate(lines):
        if i:
            r.add_break()
        r = run(line)
    return r


def _field(p, instr: str) -> None:
    """A Word field (PAGE, NUMPAGES) as runs with no formatting of their own: the paragraph's style is theirs."""
    for kind, text in (("begin", None), (None, instr), ("end", None)):
        r = p.add_run()
        if kind:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), kind)
            r._element.append(fc)
        else:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = f" {text} "
            r._element.append(it)


def _page_line(p, text: str) -> None:
    """'หน้า {page} / {total}' with live fields where the placeholders are."""
    pos = 0
    for token, instr in (("{page}", "PAGE"), ("{total}", "NUMPAGES")):
        i = text.find(token, pos)
        if i < 0:
            continue
        if text[pos:i]:
            p.add_run(text[pos:i])
        _field(p, instr)
        pos = i + len(token)
    if text[pos:]:
        p.add_run(text[pos:])


class ReportWriter:
    def __init__(self, model: dict, theme: dict, charts: dict[str, str]):
        self.m = model
        self.t = theme
        self.p = theme["palette"]
        self.charts = charts  # chart id → png path
        self.doc = dotx.document(theme)
        self.layouts: list[dict] = []   # what the catalogue chose, section by section (recorded)

    # ── helpers ───────────────────────────────────────────────────────
    def _color(self, token: str) -> str:
        return LOOK.color(self.p, token)

    @property
    def text_width(self) -> int:
        sec = self.doc.sections[0]
        return int(sec.page_width - sec.left_margin - sec.right_margin)

    def _choose(self, section: str, content: dict, kinds: tuple | None = None, block: str = "") -> catalogue.Choice:
        ch = catalogue.choose(FMT, content, section, kinds)
        self.layouts.append({"section": section, "block": block or ch.layout, "layout": ch.layout, "role": ch.role, "reason": ch.reason})
        return ch

    def _para(self, style: str, text: str = "", where=None):
        p = (where or self.doc).add_paragraph(style=style)
        if text:
            p.add_run(text)
        return p

    def _broken(self, p, text: str, role: str, avail_pt: float, bold: bool | None = None) -> str:
        """Text in a frame of ``avail_pt``: Thai is broken between words here (a reader's Word would break it
        anywhere); Latin is left to wrap. Returns the last line, for a caller that puts something after it."""
        if not measure.is_thai(text):
            p.add_run(text)
            return text
        size = LOOK.pt(FMT, role)
        b = LOOK.bold(role) if bold is None else bold
        lines = measure.break_lines(text, size, avail_pt, b)
        r = None
        for i, line in enumerate(lines):
            if i:
                r.add_break()
            r = p.add_run(line)
        return lines[-1] if lines else ""

    # ── page furniture ────────────────────────────────────────────────
    def setup(self):
        doc, m = self.doc, self.m
        sec = doc.sections[0]
        hp = sec.header.paragraphs[0]
        hp.style = doc.styles["AM Header"]
        hp.add_run((m["company"] + " · " if m["company"] else "") + m["title"] + " " + m["subtitle"])
        # Footer: source note left, page x / y right — a borderless two-cell table, which every Word and
        # LibreOffice version lays out the same way.
        ft = sec.footer.add_table(rows=1, cols=2, width=self.text_width)
        ft.style = doc.styles["AM Plain"]
        left, right = ft.rows[0].cells
        left.width, right.width = int(self.text_width * 0.8), int(self.text_width * 0.2)
        lp = left.paragraphs[0]
        lp.style = doc.styles["AM Footer"]
        lp.add_run(m["source_note"])
        rp = right.paragraphs[0]
        rp.style = doc.styles["AM Footer Right"]
        _page_line(rp, S("docs.page", page="{page}", total="{total}"))
        sec.footer.paragraphs[0].style = doc.styles["AM Footer"]

    # ── the cover (layout: cover) ─────────────────────────────────────
    def cover(self, logo_path: str | None):
        doc, m = self.doc, self.m
        self._choose(S("docs.cover.name"), {"title": m["title"], "subtitle": m["subtitle"], "figures": [k["value"] for k in m["kpis"]],
                                            "lines": m["cover_lines"] + [m["prepared"]]}, kinds=("cover",))
        width_pt = self.text_width / EMU_PER_PT
        if self.t.get("cover") == "band":
            band = doc.add_table(rows=1, cols=1)
            band.alignment = WD_TABLE_ALIGNMENT.CENTER
            band.style = doc.styles["AM Band"]
            sec = doc.sections[0]
            band.rows[0].height = Emu(int((sec.page_height - sec.top_margin - sec.bottom_margin) * 0.42))
            cell = band.rows[0].cells[0]
            cell.width = self.text_width
            cell.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
            where, avail = cell, width_pt - 2 * BAND_PAD_PT
            first = cell.paragraphs[0]
        else:
            where, avail = doc, width_pt
            first = None
        if logo_path and os.path.exists(logo_path):
            lp = first if first is not None else self._para("AM Cover Company", where=where)
            lp.style = doc.styles["AM Cover Company"]
            lp.add_run().add_picture(logo_path, height=Cm(1.4))
            first = None
        cp = first if first is not None else self._para("AM Cover Company", where=where)
        cp.style = doc.styles["AM Cover Company"]
        company = m["company"] or ""
        self._broken(cp, company.upper() if company.isascii() else company, "body", avail, bold=True)
        if self.t.get("cover") != "band":
            self._para("AM Cover Rule", where=where)
        # the title at the display size, or the next size down when it would take a third line
        role = measure.fit_role(FMT, m["title"], "display", avail, 2) or "heading"
        tp = self._para("AM Display" if role == "display" else "AM Cover Subtitle", where=where)
        self._broken(tp, m["title"], role, avail)
        sp = self._para("AM Cover Subtitle", where=where)
        self._broken(sp, m["subtitle"], "heading", avail, bold=False)
        for line in m["cover_lines"] + [m["prepared"]]:
            lp = self._para("AM Cover Line", where=where)
            self._broken(lp, line, "body", avail)
        if self.t.get("cover") == "band":
            self._para("AM Body")
        self.tiles()
        note = self._para("AM Small Muted")
        self._broken(note, S("docs.cover.cite_note"), "small", width_pt)
        self._para("AM Body").add_run().add_break(WD_BREAK.PAGE)

    def tiles(self):
        """The headline figures, once: a row of tiles on the cover."""
        doc, k = self.doc, self.m["kpis"]
        t = doc.add_table(rows=1, cols=len(k))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.style = doc.styles["AM Tiles"]
        t.autofit = False
        tile = int(self.text_width / len(k))
        avail = tile / EMU_PER_PT - 2 * TILE_PAD_PT
        for i, kp in enumerate(k):
            c = t.rows[0].cells[i]
            c.width = tile
            lp = c.paragraphs[0]
            lp.style = doc.styles["AM Tile Label"]
            self._broken(lp, kp["label"], "small", avail)
            vp = self._para("AM Tile Value", where=c)
            vrole = measure.fit_role(FMT, kp["value"], "heading", avail, 1) or "small"
            if vrole != "heading":
                vp.style = doc.styles["AM Body Strong" if vrole == "body" else "AM Small"]
            vp.add_run(kp["value"])
            style = "AM Tile Up" if kp["good"] is True else ("AM Tile Down" if kp["good"] is False else "AM Tile Flat")
            dp = self._para(style, where=c)
            arrow = {"up": "▲ ", "down": "▼ ", "flat": ""}[kp["tone"]]
            text = (arrow + kp["delta"]) if kp["delta"] else S("docs.kpi.no_compare")
            last = self._broken(dp, text, "small", avail)
            cite = f"  [{kp['cite']}]"
            if measure.width_pt(last + cite, LOOK.pt(FMT, "small")) > avail:
                dp.runs[-1].add_break()
                cite = cite.strip()
            dp.add_run(cite, style=doc.styles["AM Char Cite"])
        for row in t.rows:
            for j, cell in enumerate(row.cells):
                cell.width = tile

    # ── blocks ────────────────────────────────────────────────────────
    def heading(self, text: str, level: int = 1):
        return self._para("Heading 1" if level == 1 else "Heading 2", text)

    def references(self, cites: list[str], appendix: bool = False):
        ids = list(dict.fromkeys(c for c in cites if c))
        if ids:
            self._para("AM References", S("docs.refs", ids=", ".join(ids)))

    def paragraph(self, runs, muted=False, appendix=False):
        p = self._para("AM Body Muted" if muted else ("AM Small" if appendix else "AM Body"))
        p.add_run("".join(t for t, _ in runs))
        self.references([c for _, c in runs])

    def bullets(self, items, appendix=False):
        cites: list[str] = []
        for runs in items:
            p = self._para("AM Bullet Small" if appendix else "AM Bullet")
            p.add_run("".join(t for t, _ in runs))
            cites += [c for _, c in runs if c]
        self.references(cites)

    def table(self, block, section: str):
        doc = self.doc
        cols, rows = block["columns"], block["rows"]
        if not rows:
            self._para("AM Body Muted", block.get("empty_text") or S("docs.table.empty"))
            return
        self._choose(section, {"rows": rows, "columns": len(cols)}, kinds=("table",), block="table")
        small = bool(block.get("small"))
        no_header = bool(block.get("no_header"))   # a label-and-value table has no header row (the columns still size it)
        t = doc.add_table(rows=(0 if no_header else 1) + len(rows), cols=len(cols))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.style = doc.styles["AM Plain" if no_header else "AM Table"]
        num_from, cite_col = block.get("numeric_from"), block.get("cite_col")
        role = "small" if small else "body"
        widths = self._widths(t, block, role)
        if role == "body" and self._wraps(block, widths, role):
            # the next size down the scale, before any label is broken over lines
            role = "small"
            small = True
            widths = self._widths(t, block, role)
            self.layouts[-1]["role"] = role
        if not no_header:
            hdr = t.rows[0]
            _repeat_header(hdr)
            for j, name in enumerate(cols):
                cp = hdr.cells[j].paragraphs[0]
                numeric = num_from is not None and j >= num_from and j != cite_col
                cp.style = doc.styles[dotx.cell_style("Head Number" if numeric else "Head", small)]
                self._broken(cp, str(name), role, widths[j] / EMU_PER_PT - CELL_PAD_PT, bold=True)
        strong = set(block.get("strong_rows") or [])
        strong_fill = self._color(self.t["table"]["strong"])
        for i, row in enumerate(rows):
            tr = t.rows[i + (0 if no_header else 1)]
            _no_split(tr)
            for j, val in enumerate(row):
                c = tr.cells[j]
                if i in strong:
                    _shade(c, strong_fill)
                cp = c.paragraphs[0]
                is_cite = j == cite_col
                numeric = num_from is not None and j >= num_from and not is_cite
                text = "" if val is None else str(val)
                if is_cite:
                    kind = "Cite"
                elif numeric and text.startswith("("):
                    kind = "Negative"
                elif i in strong:
                    kind = "Strong Number" if numeric else "Strong"
                else:
                    kind = "Number" if numeric else ""
                cp.style = doc.styles[dotx.cell_style(kind, small)]
                self._broken(cp, text, "small" if is_cite else role, widths[j] / EMU_PER_PT - CELL_PAD_PT, bold=i in strong and not is_cite)
        if block.get("caption"):
            self._para("AM Caption", block["caption"])
        else:
            self._para("AM Body")

    def _wraps(self, block, widths: list[int], role: str) -> bool:
        """Whether a label column is narrower than its longest text on one line."""
        cols, num_from, cite_col = block["columns"], block.get("numeric_from"), block.get("cite_col")
        size = LOOK.pt(FMT, role)
        for j in range(len(cols)):
            if j == cite_col or (num_from is not None and j >= num_from):
                continue
            longest = max([measure.width_pt("" if r[j] is None else str(r[j]), size) for r in block["rows"]] or [0.0]) + CELL_PAD_PT
            if longest * EMU_PER_PT > widths[j] + 1:
                return True
        return False

    def _widths(self, t, block, role: str) -> list[int]:
        """Numbers and the reference id take what their widest value (or header word) needs; label columns
        get the rest — each its one-line width when the page has it, else a share in proportion to its
        longest text — and room left over goes back to the numbers. No column is narrower than its widest
        word (header included), so Word never breaks a Thai word (AM9)."""
        total = self.text_width
        cols, num_from, cite_col = block["columns"], block.get("numeric_from"), block.get("cite_col")
        size = LOOK.pt(FMT, role)
        small = LOOK.pt(FMT, "small")

        def texts(j):
            head = [] if block.get("no_header") else [(str(cols[j]), size, True)]
            return head + [("" if r[j] is None else str(r[j]), small if j == cite_col else size, False) for r in block["rows"]]

        def need(j) -> int:
            pt = max(measure.widest_word_pt(w, s, b) for w, s, b in texts(j)) + CELL_PAD_PT
            return int(pt * EMU_PER_PT) + 100   # (a hundred EMU over: never a point short by rounding)

        def one_line(j) -> int:
            body_texts = texts(j) if block.get("no_header") else texts(j)[1:]
            pt = max([measure.width_pt(w, s, b) for w, s, b in body_texts] or [0.0]) + CELL_PAD_PT
            return max(need(j), int(pt * EMU_PER_PT))

        widths: list = [None] * len(cols)
        numbers = []
        for j in range(len(cols)):
            if j == cite_col:
                widths[j] = max(int(Cm(1.15)), need(j))
                numbers.append(j)   # room left over reaches the reference column too: "c116, c118" on one line
            elif num_from is not None and j >= num_from:
                widths[j] = max(int(Cm(1.25)), need(j))
                numbers.append(j)
        fixed = sum(w for w in widths if w is not None)
        free = [j for j, w in enumerate(widths) if w is None]
        avail = total - fixed
        if free:
            full = {j: max(int(Cm(2.2)), one_line(j)) for j in free}
            if sum(full.values()) <= avail:
                for j in free:
                    widths[j] = full[j]
            else:
                floor = {j: max(int(Cm(2.2)), need(j)) for j in free}
                rest = max(0, avail - sum(floor.values()))
                extra = sum(full[j] - floor[j] for j in free) or 1
                for j in free:
                    widths[j] = floor[j] + int(rest * (full[j] - floor[j]) / extra)
        spare = total - sum(widths)
        receivers = numbers or free
        if spare > 0 and receivers:
            for j in receivers:
                widths[j] += int(spare / len(receivers))
        t.autofit = False
        for j, col in enumerate(t.columns):
            col.width = Emu(widths[j])
        for row in t.rows:
            for j, cell in enumerate(row.cells):
                cell.width = Emu(widths[j])
        return widths

    def picture(self, path: str, width_emu: int | None = None):
        """A picture across the text width (or narrower), on its own line."""
        pp = self._para("AM Picture")
        pp.add_run().add_picture(path, width=Emu(min(width_emu or self.text_width, self.text_width)))

    def chart(self, chart_id: str, section: str):
        spec = next((c for c in self.m["charts"] if c["id"] == chart_id), None)
        path = self.charts.get(chart_id)
        if not spec or not path:
            return
        self._choose(section, {"title": spec["title"], "series": spec["series"], "categories": spec["categories"],
                               "caption": S("docs.chart.note")}, kinds=("chart",), block="chart")
        tp = self._para("AM Chart Title", spec["title"])
        if spec.get("subtitle"):
            tp.add_run("  " + spec["subtitle"], style=self.doc.styles["AM Char Muted"])
        pp = self._para("AM Picture")
        pp.add_run().add_picture(path, width=Emu(self.text_width))
        self._para("AM Caption", S("docs.chart.note"))

    # ── appendix (AM9): its own section in two columns, each row an entry ──
    def appendix_section(self):
        new = self.doc.add_section(WD_SECTION.NEW_PAGE)
        new.different_first_page_header_footer = False
        cols = new._sectPr.find(qn("w:cols"))
        if cols is None:
            cols = OxmlElement("w:cols")
            new._sectPr.append(cols)
        cols.set(qn("w:num"), "2")
        cols.set(qn("w:space"), str(int(0.7 / 2.54 * 1440)))

    def entries(self, block, section: str):
        """An appendix table as entries: the first column strong, the next two on its line, every other
        column on a line of its own under its header's name."""
        cols = block["columns"]
        if not block["rows"]:
            self._para("AM Small Muted", block.get("empty_text") or S("docs.table.empty"))
            return
        self._choose(section, {"entry": max(("".join(str(v or "") for v in r) for r in block["rows"]), key=len), "title": section},
                     kinds=("appendix",), block="entries")
        strong, muted = self.doc.styles["AM Char Strong"], self.doc.styles["AM Char Muted"]
        for row in block["rows"]:
            para = self._para("AM Appendix Entry")
            para.paragraph_format.keep_together = True
            vals = ["" if v is None else str(v) for v in row]
            para.add_run(vals[0], style=strong)
            head = " · ".join(v for v in vals[1:3] if v)
            if head:
                para.add_run("  " + head)
            for name, v in zip(cols[3:], vals[3:]):
                if v:
                    para.add_run().add_break()
                    para.add_run(f"{name}: ", style=muted)
                    para.add_run(v, style=muted)

    # ── assemble ──────────────────────────────────────────────────────
    def write(self, path: str, logo_path: str | None = None, organisation: str = "") -> dict:
        self.setup()
        self.cover(logo_path)
        appendix_started = False
        for sec in self.m["sections"]:
            if sec.get("appendix") and not appendix_started:
                self.appendix_section()
                appendix_started = True
            # the sections flow (a heading keeps with what follows it; a chart keeps with its title and
            # a table's rows never split): a page ends early only where the next block would not fit whole
            self._choose(sec["title"], {"title": sec["title"]}, kinds=("section",), block="section")
            self.heading(sec["title"])
            for b in sec["blocks"]:
                kind = b["type"]
                if kind == "paragraph":
                    self._choose(sec["title"], {"paragraph": "".join(t for t, _ in b["runs"])}, kinds=("prose",), block="paragraph")
                    self.paragraph(b["runs"], b.get("muted", False), appendix_started)
                elif kind == "table":
                    self.entries(b, sec["title"]) if appendix_started else self.table(b, sec["title"])
                elif kind == "chart":
                    self.chart(b["chart"], sec["title"])
                elif kind == "bullets":
                    self._choose(sec["title"], {"items": ["".join(t for t, _ in runs) for runs in b["items"]]}, kinds=("prose",), block="bullets")
                    self.bullets(b["items"], appendix_started)
                elif kind == "subheading":
                    self.heading(b["text"], 2)
        props = self.doc.core_properties
        props.title = f"{self.m['title']} {self.m['subtitle']}"
        props.author = self.m["company"] or organisation or ""
        props.subject = self.m["source_note"]
        self.doc.save(path)
        embedded = embed_fonts(path, LOOK.family)
        return {"path": path, "embedded_fonts": embedded, "layouts": self.layouts}


# ── font embedding (ECMA-376 Part 1 §17.8.1, obfuscated TrueType) ─────────

def _obfuscate(data: bytes, key_guid: str) -> bytes:
    key = bytes.fromhex(key_guid.strip("{}").replace("-", ""))
    head = bytearray(data[:32])
    for i in range(32):
        head[i] ^= key[15 - (i % 16)]
    return bytes(head) + data[32:]


def embed_fonts(docx_path: str, face: str) -> list[str]:
    files = LOOK.font_files if face == LOOK.family else {}
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
        ct = ct.replace("</Types>", '<Default Extension="odttf" ContentType="application/vnd.openxmlformats-officedocument.obfuscatedFont"/></Types>')
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
