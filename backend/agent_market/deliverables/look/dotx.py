"""The Word template: every style of the report, built from look.json (AM21).

A report applies named styles and never direct formatting: a paragraph is "AM Body" or "AM Cell Number",
a table is "AM Table", and the size, the weight, the colour and the typeface of each live here, read
from the scale and the theme's palette. ``document(theme)`` is a python-docx Document seeded with those
styles, the page set up, the typeface as the document's default and in its theme part, and the
complex-script language set to Thai; ``save_template`` writes the same as a .dotx a person can open.

The gate (quality.check_docx, ``docx.styles``) reads the finished file and fails a run that sets a size,
a colour or a font on a run instead of using a style.
"""

from __future__ import annotations

import os
import re
import zipfile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .tokens import LOOK, color, rgb

FMT = "docx"
# the style ids the gate accepts on a table (python-docx derives the id from the name by dropping spaces)
TABLE_STYLES = ("AM Table", "AM Tiles", "AM Band", "AM Plain")
CELL_KINDS = ("", "Number", "Negative", "Strong", "Strong Number", "Head", "Head Number", "Cite")


def cell_style(kind: str = "", small: bool = False) -> str:
    """The name of the paragraph style of a table cell: its kind (CELL_KINDS) at the body or the small size."""
    if kind not in CELL_KINDS:
        raise ValueError(f"no cell kind {kind!r}: {CELL_KINDS}")
    return "AM Cell" + (" Small" if small else "") + (" " + kind if kind else "")


def _rgb(hex6: str) -> RGBColor:
    return RGBColor(*rgb(hex6))


def _set_fonts(rPr, family: str) -> None:
    rf = rPr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rPr.insert(0, rf)
    for attr in list(rf.attrib):
        if attr.endswith("Theme"):
            del rf.attrib[attr]
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rf.set(qn(attr), family)


def _size(rPr, pt: float) -> None:
    for tag in ("w:sz", "w:szCs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            rPr.append(el)
        el.set(qn("w:val"), str(int(round(pt * 2))))


def _bold(rPr, on: bool) -> None:
    for tag in ("w:b", "w:bCs"):
        el = rPr.find(qn(tag))
        if el is None and on:
            rPr.append(OxmlElement(tag))
        elif el is not None and not on:
            rPr.remove(el)


def _color(rPr, hex6: str) -> None:
    el = rPr.find(qn("w:color"))
    if el is None:
        el = OxmlElement("w:color")
        rPr.append(el)
    el.set(qn("w:val"), hex6)


def _shd(parent, fill: str):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    parent.append(shd)
    return shd


class Dotx:
    """The styles of one theme, built once into a Document."""

    def __init__(self, theme: dict):
        self.t = theme
        self.p = theme["palette"]
        self.family = LOOK.family
        self.table = theme["table"]
        self.cover = theme.get("cover", "band")

    def c(self, token: str) -> str:
        return color(self.p, token)

    def pt(self, role: str) -> float:
        return LOOK.pt(FMT, role)

    # ── the document ─────────────────────────────────────────────────
    def document(self) -> Document:
        doc = Document()
        self._defaults(doc)
        self._page(doc)
        self._paragraph_styles(doc)
        self._table_styles(doc)
        self._theme_part(doc)
        return doc

    def _defaults(self, doc) -> None:
        defaults = doc.styles.element.find(qn("w:docDefaults"))
        rpr = defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}")
        _set_fonts(rpr, self.family)
        _size(rpr, self.pt("body"))
        lang = rpr.find(qn("w:lang"))
        if lang is None:
            lang = OxmlElement("w:lang")
            rpr.append(lang)
        lang.set(qn("w:bidi"), "th-TH")   # Word breaks Thai lines between words, not anywhere
        # every stock style names the family, with no theme font left to win over it (Word prefers a theme
        # font attribute to an explicit one; python-docx's headings carry one)
        for rf in doc.styles.element.iter(qn("w:rFonts")):
            for attr in list(rf.attrib):
                if attr.endswith("Theme"):
                    del rf.attrib[attr]
            for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
                if rf.get(qn(attr)) not in (None, self.family):
                    rf.set(qn(attr), self.family)
        for part in doc.part.package.iter_parts():
            if str(part.partname).endswith("stylesWithEffects.xml") and hasattr(part, "_blob"):
                xml = part._blob.decode("utf-8")
                xml = re.sub(r'\sw:(?:ascii|hAnsi|cs|eastAsia)Theme="[^"]*"', "", xml)
                part._blob = re.sub(r'(w:(?:ascii|hAnsi|cs|eastAsia)=")(?!' + re.escape(self.family) + r'")[^"]*"',
                                    r"\g<1>" + self.family + '"', xml).encode("utf-8")

    def _page(self, doc) -> None:
        page = LOOK.page(FMT)
        sec = doc.sections[0]
        from docx.shared import Cm

        sec.page_width, sec.page_height = Cm(21), Cm(29.7)   # A4 (look.json: page.size)
        m = page["margins_in"]
        sec.top_margin, sec.bottom_margin = Inches(m["top"]), Inches(m["bottom"])
        sec.left_margin, sec.right_margin = Inches(m["left"]), Inches(m["right"])
        sec.different_first_page_header_footer = True
        sec.header_distance, sec.footer_distance = Inches(0.35), Inches(0.3)

    # ── paragraph and character styles ───────────────────────────────
    def _para(self, doc, name: str, role: str, token: str, *, bold: bool | None = None, base: str = "Normal",
              align=None, before: float = 0, after: float = 4, keep_next: bool = False, line: float | None = None,
              kind=WD_STYLE_TYPE.PARAGRAPH, border_token: str | None = None, indent_cm: float | None = None):
        st = doc.styles.add_style(name, kind)
        if kind == WD_STYLE_TYPE.PARAGRAPH:
            st.base_style = doc.styles[base]
            pf = st.paragraph_format
            pf.space_before, pf.space_after = Pt(before), Pt(after)
            pf.line_spacing = line if line is not None else LOOK.line(FMT)
            pf.keep_with_next = keep_next
            if align is not None:
                pf.alignment = align
            if indent_cm is not None:
                from docx.shared import Cm

                pf.left_indent = Cm(indent_cm)
        rPr = st.element.get_or_add_rPr()
        _set_fonts(rPr, self.family)
        _size(rPr, self.pt(role))
        _bold(rPr, LOOK.bold(role) if bold is None else bold)
        _color(rPr, self.c(token))
        if border_token:
            pPr = st.element.get_or_add_pPr()
            bdr = OxmlElement("w:pBdr")
            b = OxmlElement("w:bottom")
            b.set(qn("w:val"), "single")
            b.set(qn("w:sz"), "8")
            b.set(qn("w:space"), "4")
            b.set(qn("w:color"), self.c(border_token))
            bdr.append(b)
            pPr.append(bdr)
        st.quick_style = True
        return st

    def _restyle(self, doc, name: str, role: str, token: str, *, before: float, after: float, border_token: str | None = None):
        """A stock style (Normal, Heading 1, Heading 2) set from the scale: the gate reads these ids."""
        st = doc.styles[name]
        rPr = st.element.get_or_add_rPr()
        _set_fonts(rPr, self.family)
        _size(rPr, self.pt(role))
        _bold(rPr, LOOK.bold(role))
        _color(rPr, self.c(token))
        pf = st.paragraph_format
        pf.space_before, pf.space_after = Pt(before), Pt(after)
        pf.line_spacing = LOOK.line(FMT)
        pf.keep_with_next = name != "Normal"
        pPr = st.element.get_or_add_pPr()
        for old in pPr.findall(qn("w:pBdr")):
            pPr.remove(old)
        if border_token:
            bdr = OxmlElement("w:pBdr")
            b = OxmlElement("w:bottom")
            b.set(qn("w:val"), "single")
            b.set(qn("w:sz"), "8")
            b.set(qn("w:space"), "4")
            b.set(qn("w:color"), self.c(border_token))
            bdr.append(b)
            pPr.append(bdr)

    def _paragraph_styles(self, doc) -> None:
        on_band = self.cover == "band"
        self._restyle(doc, "Normal", "body", "ink", before=0, after=4)
        self._restyle(doc, "Heading 1", "heading", "primary", before=16, after=6, border_token="accent")
        self._restyle(doc, "Heading 2", "subheading", "primary", before=10, after=3)
        P = self._para
        R, C = WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.CENTER
        # the cover
        P(doc, "AM Cover Company", "body", "gold" if on_band else "accent", bold=True, before=0, after=2)
        P(doc, "AM Display", "display", "on_primary" if on_band else "primary", before=30, after=2, line=1.05)
        P(doc, "AM Cover Subtitle", "heading", "on_primary" if on_band else "ink", bold=False, before=0, after=24)
        P(doc, "AM Cover Line", "body", "on_primary" if on_band else "muted", before=0, after=2)
        P(doc, "AM Cover Rule", "small", "accent", before=0, after=0, border_token="accent")
        # the body
        P(doc, "AM Body", "body", "ink")
        P(doc, "AM Body Strong", "body", "ink", bold=True)
        P(doc, "AM Body Muted", "body", "muted")
        P(doc, "AM Small", "small", "ink", after=2)
        P(doc, "AM Small Muted", "small", "muted", after=2)
        P(doc, "AM Caption", "small", "muted", before=3, after=10)
        P(doc, "AM References", "small", "muted", before=1, after=8)
        P(doc, "AM Chart Title", "body", "ink", bold=True, before=6, after=2, keep_next=True)
        P(doc, "AM Picture", "body", "ink", after=2, align=C, keep_next=True)
        P(doc, "AM Bullet", "body", "ink", base="List Bullet", after=3)
        P(doc, "AM Bullet Small", "small", "ink", base="List Bullet", after=2)
        P(doc, "AM Appendix Entry", "small", "ink", after=3, line=1.0)
        # the headline figures (tiles)
        P(doc, "AM Tile Label", "small", "muted", after=2)
        P(doc, "AM Tile Value", "heading", "primary", after=1)
        P(doc, "AM Tile Up", "small", "positive", after=0)
        P(doc, "AM Tile Down", "small", "negative", after=0)
        P(doc, "AM Tile Flat", "small", "muted", after=0)
        # table cells, at the body size and at the small size
        head_text = self.table["header_text"]
        for small in (False, True):
            role = "small" if small else "body"
            P(doc, cell_style("", small), role, "ink", after=0)
            P(doc, cell_style("Number", small), role, "ink", after=0, align=R)
            P(doc, cell_style("Negative", small), role, "negative", after=0, align=R)
            P(doc, cell_style("Strong", small), role, "ink", bold=True, after=0)
            P(doc, cell_style("Strong Number", small), role, "ink", bold=True, after=0, align=R)
            P(doc, cell_style("Head", small), role, head_text, bold=True, after=0)
            P(doc, cell_style("Head Number", small), role, head_text, bold=True, after=0, align=R)
            P(doc, cell_style("Cite", small), "small", "muted", after=0)
        # page furniture
        P(doc, "AM Header", "small", "muted", after=0, align=R)
        P(doc, "AM Footer", "small", "muted", after=0)
        P(doc, "AM Footer Right", "small", "muted", after=0, align=R)
        # pieces inside a paragraph: a unit after a chart's title, a muted aside
        for name, role, token, bold in (("AM Char Muted", "small", "muted", False), ("AM Char Cite", "small", "muted", False),
                                        ("AM Char Strong", "small", "primary", True)):
            st = doc.styles.add_style(name, WD_STYLE_TYPE.CHARACTER)
            rPr = st.element.get_or_add_rPr()
            _set_fonts(rPr, self.family)
            _size(rPr, self.pt(role))
            _bold(rPr, bold)
            _color(rPr, self.c(token))

    # ── table styles ─────────────────────────────────────────────────
    def _table(self, doc, name: str, *, borders: dict, fill: str | None = None, first_row: dict | None = None,
               band: str | None = None, margins=(60, 60, 90, 90)):
        st = doc.styles.add_style(name, WD_STYLE_TYPE.TABLE)
        st.base_style = doc.styles["Normal Table"]
        el = st.element
        tblPr = el.find(qn("w:tblPr"))
        if tblPr is None:
            tblPr = OxmlElement("w:tblPr")
            el.append(tblPr)
        b = OxmlElement("w:tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{side}")
            tok = borders.get(side)
            e.set(qn("w:val"), "single" if tok else "nil")
            if tok:
                e.set(qn("w:sz"), "4")
                e.set(qn("w:space"), "0")
                e.set(qn("w:color"), self.c(tok))
            b.append(e)
        tblPr.append(b)
        mar = OxmlElement("w:tblCellMar")
        for side, v in zip(("top", "bottom", "left", "right"), margins):
            e = OxmlElement(f"w:{side}")
            e.set(qn("w:w"), str(v))
            e.set(qn("w:type"), "dxa")
            mar.append(e)
        tblPr.append(mar)
        if fill:
            tcPr = OxmlElement("w:tcPr")
            _shd(tcPr, self.c(fill))
            el.append(tcPr)
        if first_row:
            sp = OxmlElement("w:tblStylePr")
            sp.set(qn("w:type"), "firstRow")
            tcPr = OxmlElement("w:tcPr")
            _shd(tcPr, self.c(first_row["fill"]))
            sp.append(tcPr)
            el.append(sp)
        if band:
            sp = OxmlElement("w:tblStylePr")
            sp.set(qn("w:type"), "band1Horz")
            tcPr = OxmlElement("w:tcPr")
            _shd(tcPr, self.c(band))
            sp.append(tcPr)
            el.append(sp)
        return st

    def _table_styles(self, doc) -> None:
        self._table(doc, "AM Table", borders={"insideH": "rule"}, first_row={"fill": self.table["header"]},
                    band="surface" if self.table.get("zebra") else None)
        self._table(doc, "AM Tiles", borders={}, fill="surface", margins=(110, 110, 120, 120))
        self._table(doc, "AM Band", borders={}, fill="primary", margins=(60, 60, 90, 90))
        self._table(doc, "AM Plain", borders={}, margins=(0, 0, 0, 0))

    # ── the theme part: Word's own notion of the document's fonts ────
    def _theme_part(self, doc) -> None:
        for part in doc.part.package.iter_parts():
            if str(part.partname).endswith("theme/theme1.xml") and hasattr(part, "_blob"):
                xml = part._blob.decode("utf-8")
                xml = re.sub(r'(<a:(?:latin|ea|cs) typeface=")[^"]*(")', r"\g<1>" + self.family + r"\2", xml)
                part._blob = xml.encode("utf-8")


def document(theme: dict):
    """A Document with the theme's styles, ready for content."""
    return Dotx(theme).document()


def save_template(theme: dict, path: str) -> str:
    """The same styles as a .dotx (a Word template) a person can open and look at."""
    doc = Dotx(theme).document()
    tmp = path + ".docx"
    doc.save(tmp)
    with zipfile.ZipFile(tmp) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "[Content_Types].xml":
                data = data.replace(b"wordprocessingml.document.main+xml", b"wordprocessingml.template.main+xml")
            dst.writestr(name, data)
    os.remove(tmp)
    return path


def run_formatting_in(path: str) -> list[str]:
    """Every run of the document's body, headers and footers that sets a size, a colour or a font of its own —
    what a report must not do (its look is its styles'). Each as 'part: text…'."""
    bad: list[str] = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not (name.startswith(("word/document", "word/header", "word/footer")) and name.endswith(".xml")):
                continue
            xml = z.read(name).decode("utf-8", "replace")
            for run in re.findall(r"<w:r\b[^>]*>.*?</w:r>", xml, re.S):
                rpr = re.search(r"<w:rPr>(.*?)</w:rPr>", run, re.S)
                if rpr and re.search(r"<w:(?:sz|szCs|color|rFonts|b|bCs)\b", rpr.group(1)):
                    text = "".join(re.findall(r"<w:t\b[^>]*>(.*?)</w:t>", run, re.S))
                    bad.append(f"{name.split('/')[-1]}: {text[:40]}")
    return bad
