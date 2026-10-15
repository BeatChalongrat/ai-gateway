"""The PowerPoint template: a master and the layout catalogue as real layouts with real placeholders (AM21).

Built from look.json: every layout of the pptx catalogue is a slide layout of the file, its placeholders
positioned and sized here (the one place geometry is decided), each carrying its text style — the role's
size and weight, the palette's colour, the one typeface — so that a slide made from it holds text in
placeholders and nothing else, with no size, colour or font of its own. There is no "Blank" layout: the
stock ones are rebuilt or removed. The theme part names the family for every script; the family is
embedded in the file (EOT, as PowerPoint keeps it) so a reader without Sarabun sees Sarabun.

``presentation(theme)`` is the template loaded and ready for slides; ``save_template`` writes it as a
.potx a person can open; ``embed_fonts`` is run on a finished deck.
"""

from __future__ import annotations

import copy
import os
import re
import struct
import zipfile

from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt

from .tokens import LOOK, color

FMT = "pptx"
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
A, P = "{%s}" % NS["a"], "{%s}" % NS["p"]
TABLE_STYLE_ID = "{7A21D7F0-A121-4C2E-9E3B-5F3B1C0D0001}"
# the slide, in inches (look.json: layouts.pptx.page)
PAGE = LOOK.page(FMT)
W, H, M = PAGE["width_in"], PAGE["height_in"], PAGE["margin_in"]
CW = W - 2 * M                    # the content width
TITLE_H = PAGE["title_in"]
FOOT_Y = H - PAGE["footer_in"] - 0.1
# the frame a chart picture fills, in inches (the chart is drawn at this aspect so nothing is cropped)
CHART_BOX = (CW, 4.9)
# a list's hanging indent, in inches: a wrapped line returns under the first word, not under the bullet
HANG_IN = 0.35
# placeholder indexes a renderer fills, by layout: {layout: {field: idx}} (0 is the title)
FIELDS = {
    "title": {"title": 0, "subtitle": 1, "company": 10, "lines": 11},
    "section": {"title": 0},
    "figure": {"title": 0, "label": 10, "value": 11, "sentence": 12},
    "figures": {"title": 0, "label": (10, 13, 16), "value": (11, 14, 17), "delta": (12, 15, 18)},
    "table": {"title": 0, "table": 10, "caption": 11},
    "chart": {"title": 0, "picture": 10, "caption": 11},
    "bullets": {"title": 0, "lead": 10, "items": 11},
    "statement": {"title": 0, "text": 10},
    "closing": {"title": 0, "lines": 10},
}
FOOTER_IDX, SLIDENUM_IDX = 20, 21
# which text role each text placeholder is set at: {layout: {idx: (role, bold or None, colour token, align, anchor)}}
# (None = the role's own weight)


def _roles(theme: dict) -> dict:
    cover_text = "on_primary"
    return {
        "title": {0: ("display", None, cover_text, "l", "b"), 1: ("heading", False, cover_text, "l", "t"),
                  10: ("body", True, "gold", "l", "b"), 11: ("body", None, cover_text, "l", "t")},
        "section": {0: ("heading", None, cover_text, "l", "ctr")},
        "figure": {0: ("heading", None, "primary", "l", "ctr"), 10: ("subheading", False, "muted", "ctr", "b"),
                   11: ("display", None, "primary", "ctr", "ctr"), 12: ("body", None, "ink", "ctr", "t")},
        "figures": {0: ("heading", None, "primary", "l", "ctr"), **{i: ("small", None, "muted", "l", "b") for i in (10, 13, 16)},
                    **{i: ("heading", None, "primary", "l", "ctr") for i in (11, 14, 17)},
                    **{i: ("body", None, "ink", "l", "t") for i in (12, 15, 18)}},
        "table": {0: ("heading", None, "primary", "l", "ctr"), 11: ("small", None, "muted", "l", "t")},
        "chart": {0: ("heading", None, "primary", "l", "ctr"), 11: ("small", None, "muted", "l", "t")},
        "bullets": {0: ("heading", None, "primary", "l", "ctr"), 10: ("body", None, "ink", "l", "t"), 11: ("body", None, "ink", "l", "t")},
        "statement": {0: ("heading", None, "primary", "l", "ctr"), 10: ("body", None, "ink", "l", "ctr")},
        "closing": {0: ("heading", None, "primary", "l", "ctr"), 10: ("small", None, "ink", "l", "t")},
    }


def _boxes() -> dict:
    """Every placeholder's frame, in inches: {layout: {idx: (x, y, w, h)}}."""
    title = {0: (M, 0.45, CW, TITLE_H)}
    cols = 3
    gap = 0.3
    cw = (CW - gap * (cols - 1)) / cols
    figs = {}
    for i in range(cols):
        x = M + i * (cw + gap)
        figs[10 + 3 * i] = (x, 2.3, cw, 0.6)
        figs[11 + 3 * i] = (x, 2.9, cw, 1.1)
        figs[12 + 3 * i] = (x, 4.05, cw, 0.9)
    return {
        "title": {0: (0.9, 1.9, W - 1.8, 1.7), 1: (0.9, 3.65, W - 1.8, 0.8), 10: (0.9, 1.1, W - 1.8, 0.6), 11: (0.9, 5.1, W - 1.8, 1.4)},
        "section": {0: (0.9, 2.8, W - 1.8, 1.6)},
        "figure": {**title, 10: (1.6, 1.9, W - 3.2, 0.7), 11: (1.6, 2.55, W - 3.2, 1.7), 12: (1.6, 4.4, W - 3.2, 1.3)},
        "figures": {**title, **figs},
        "table": {**title, 10: (M, 1.55, CW, 4.9), 11: (M, 6.45, CW, 0.4)},
        "chart": {**title, 10: (M, 1.45, CW, CHART_BOX[1]), 11: (M, 6.4, CW, 0.4)},
        "bullets": {**title, 10: (M, 1.55, CW, 0.9), 11: (M, 2.5, CW, 4.1)},
        "statement": {**title, 10: (0.9, 1.9, W - 1.8, 4.5)},
        "closing": {**title, 10: (M, 1.55, CW, 5.0)},
    }


BAND_LAYOUTS = ("title", "section")     # a filled background (the primary colour)
CHROME_LAYOUTS = ("figure", "figures", "table", "chart", "bullets", "statement", "closing")


def _emu(inches: float) -> int:
    return int(round(inches * 914400))


class Potx:
    def __init__(self, theme: dict):
        self.t = theme
        self.p = theme["palette"]
        self.family = LOOK.family
        self.roles = _roles(theme)
        self.boxes = _boxes()

    def c(self, token: str) -> str:
        return color(self.p, token)

    # ── XML pieces ───────────────────────────────────────────────────
    def _rpr(self, role: str, bold: bool | None, token: str) -> str:
        size = int(round(LOOK.pt(FMT, role) * 100))
        b = LOOK.bold(role) if bold is None else bold
        return (f'<a:defRPr sz="{size}" b="{1 if b else 0}"><a:solidFill><a:srgbClr val="{self.c(token)}"/></a:solidFill>'
                f'<a:latin typeface="{self.family}"/><a:ea typeface="{self.family}"/><a:cs typeface="{self.family}"/></a:defRPr>')

    def _ph(self, idx: int, kind: str, name: str, box: tuple, style: tuple | None, bullets: bool = False) -> str:
        x, y, w, h = (_emu(v) for v in box)
        ph = '<p:ph type="ctrTitle"/>' if (idx == 0 and kind == "ctrTitle") else (
            '<p:ph type="title"/>' if idx == 0 else f'<p:ph type="{kind}" idx="{idx}"/>')
        if kind in ("tbl", "pic"):
            return (f'<p:sp><p:nvSpPr><p:cNvPr id="{idx + 2}" name="{name}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                    f'<p:nvPr>{ph}</p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'
                    f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr/></a:p></p:txBody></p:sp>')
        role, bold, token, align, anchor = style
        bu = ('<a:buChar char="•"/>' if bullets else "<a:buNone/>")
        hang = _emu(HANG_IN)
        mar = f'marL="{hang}" indent="-{hang}"' if bullets else 'marL="0" indent="0"'
        spc = '<a:spcBef><a:spcPts val="600"/></a:spcBef>' if bullets else ""
        line = f'<a:lnSpc><a:spcPct val="{int(LOOK.line(FMT) * 100000)}"/></a:lnSpc>'
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{idx + 2}" name="{name}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                f'<p:nvPr>{ph}</p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'
                f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
                f'<p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="{anchor}"><a:noAutofit/></a:bodyPr>'
                f'<a:lstStyle><a:lvl1pPr {mar} algn="{align}">{line}{spc}{bu}{self._rpr(role, bold, token)}</a:lvl1pPr></a:lstStyle>'
                f'<a:p><a:endParaRPr/></a:p></p:txBody></p:sp>')

    def _rect(self, cid: int, name: str, box: tuple, token: str) -> str:
        x, y, w, h = (_emu(v) for v in box)
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{cid}" name="{name}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
                f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                f'<a:solidFill><a:srgbClr val="{self.c(token)}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr></p:sp>')

    def _footer(self) -> str:
        small = self._rpr("small", False, "muted")
        f_y = FOOT_Y
        fx, fw = _emu(M), _emu(CW - 1.4)
        nx, nw = _emu(W - M - 1.2), _emu(1.2)
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="30" name="Footer"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                f'<p:nvPr><p:ph type="ftr" sz="quarter" idx="{FOOTER_IDX}"/></p:nvPr></p:nvSpPr>'
                f'<p:spPr><a:xfrm><a:off x="{fx}" y="{_emu(f_y)}"/><a:ext cx="{fw}" cy="{_emu(0.35)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
                f'<p:txBody><a:bodyPr lIns="0" tIns="0" rIns="0" bIns="0" anchor="ctr"/><a:lstStyle><a:lvl1pPr algn="l"><a:buNone/>{small}</a:lvl1pPr></a:lstStyle><a:p><a:endParaRPr/></a:p></p:txBody></p:sp>'
                f'<p:sp><p:nvSpPr><p:cNvPr id="31" name="Slide Number"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                f'<p:nvPr><p:ph type="sldNum" sz="quarter" idx="{SLIDENUM_IDX}"/></p:nvPr></p:nvSpPr>'
                f'<p:spPr><a:xfrm><a:off x="{nx}" y="{_emu(f_y)}"/><a:ext cx="{nw}" cy="{_emu(0.35)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
                f'<p:txBody><a:bodyPr lIns="0" tIns="0" rIns="0" bIns="0" anchor="ctr"/><a:lstStyle><a:lvl1pPr algn="r"><a:buNone/>{small}</a:lvl1pPr></a:lstStyle>'
                f'<a:p><a:fld id="{{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}}" type="slidenum"><a:t>1</a:t></a:fld></a:p></p:txBody></p:sp>')

    def _layout_xml(self, name: str) -> tuple[str, str | None]:
        """The shapes of one layout of the catalogue, and its background colour token."""
        boxes, roles = self.boxes[name], self.roles[name]
        parts = []
        if name in CHROME_LAYOUTS:
            parts.append(self._rect(40, "Accent mark", (M, 0.36, 0.5, 0.06), "accent"))
            parts.append(self._rect(41, "Rule", (M, FOOT_Y - 0.12, CW, 0.012), "rule"))
        kinds = {"table": {10: "tbl"}, "chart": {10: "pic"}}.get(name, {})
        for idx, box in boxes.items():
            kind = kinds.get(idx, "ctrTitle" if (idx == 0 and name in BAND_LAYOUTS) else ("subTitle" if idx == 1 else "body"))
            style = roles.get(idx)
            bullets = name == "bullets" and idx == 11
            parts.append(self._ph(idx, kind, f"{name} {idx}", box, style, bullets))
        if name in CHROME_LAYOUTS:
            parts.append(self._footer())
        return "".join(parts), ("primary" if name in BAND_LAYOUTS else None)

    # ── the template ─────────────────────────────────────────────────
    def presentation(self) -> Presentation:
        prs = Presentation()
        # PowerPoint's own 16:9 is 12192000 × 6858000 EMU (13.333… in): the size is rounded to its thousand
        prs.slide_width, prs.slide_height = int(round(W * 914400 / 1000)) * 1000, int(round(H * 914400 / 1000)) * 1000
        master = prs.slide_master
        layouts = list(master.slide_layouts)
        names = list(LOOK.catalogue(FMT))
        if len(names) > len(layouts):
            raise ValueError("the catalogue has more layouts than the stock template can hold")
        for lay, name in zip(layouts, names):
            self._rebuild(lay, name)
        for lay in layouts[len(names):]:
            master.slide_layouts.remove(lay)
        self._master(master)
        self._theme_fonts(prs)
        self._table_style(prs)
        return prs

    def _rebuild(self, layout, name: str) -> None:
        el = layout._element
        csld = el.find(P + "cSld")
        csld.set("name", name)
        el.set("type", "obj" if name not in BAND_LAYOUTS else ("title" if name == "title" else "secHead"))
        tree = csld.find(P + "spTree")
        for child in list(tree):
            if child.tag not in (P + "nvGrpSpPr", P + "grpSpPr"):
                tree.remove(child)
        xml, bg = self._layout_xml(name)
        wrapper = etree.fromstring(f'<w xmlns:a="{NS["a"]}" xmlns:p="{NS["p"]}" xmlns:r="{NS["r"]}">{xml}</w>')
        for child in list(wrapper):
            tree.append(child)
        old_bg = csld.find(P + "bg")
        if old_bg is not None:
            csld.remove(old_bg)
        if bg:
            bg_el = etree.fromstring(f'<p:bg xmlns:a="{NS["a"]}" xmlns:p="{NS["p"]}"><p:bgPr><a:solidFill><a:srgbClr val="{self.c(bg)}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>')
            csld.insert(0, bg_el)

    def _master(self, master) -> None:
        """The master keeps its text styles but in the family, at the scale, in the palette — no stock size."""
        el = master._element
        tree = el.find(P + "cSld").find(P + "spTree")
        for child in list(tree):
            if child.tag not in (P + "nvGrpSpPr", P + "grpSpPr"):
                tree.remove(child)
        styles = el.find(P + "txStyles")
        if styles is not None:
            for st in styles:
                for lvl in st:
                    for d in lvl.findall(A + "defRPr"):
                        for sub in list(d):
                            d.remove(sub)
                        d.set("sz", str(int(LOOK.pt(FMT, "body") * 100)))
                        d.append(etree.fromstring(f'<a:solidFill xmlns:a="{NS["a"]}"><a:srgbClr val="{self.c("ink")}"/></a:solidFill>'))
                        for tag in ("latin", "ea", "cs"):
                            d.append(etree.fromstring(f'<a:{tag} xmlns:a="{NS["a"]}" typeface="{self.family}"/>'))
                    for bu in lvl.findall(A + "buFont"):
                        lvl.remove(bu)

    def _theme_fonts(self, prs) -> None:
        for part in prs.part.package.iter_parts():
            if str(part.partname).endswith("theme/theme1.xml") and hasattr(part, "_blob"):
                xml = part._blob.decode("utf-8")
                xml = re.sub(r'(<a:(?:latin|ea|cs) typeface=")[^"]*(")', r"\g<1>" + self.family + r"\2", xml)
                xml = re.sub(r'<a:font script="[^"]*" typeface="[^"]*"/>', "", xml)
                part._blob = xml.encode("utf-8")

    def _table_style(self, prs) -> None:
        """The deck's one table style, from the theme: a header row in the header colour, bands when the
        theme has them, rules in the rule colour."""
        t = self.t["table"]
        band = (f'<a:band1H><a:tcStyle><a:fill><a:solidFill><a:srgbClr val="{self.c("surface")}"/></a:solidFill></a:fill></a:tcStyle></a:band1H>'
                if t.get("zebra") else "")
        ln = f'<a:ln w="6350"><a:solidFill><a:srgbClr val="{self.c("rule")}"/></a:solidFill></a:ln>'
        xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               f'<a:tblStyleLst xmlns:a="{NS["a"]}" def="{TABLE_STYLE_ID}">'
               f'<a:tblStyle styleId="{TABLE_STYLE_ID}" styleName="AM Table">'
               f'<a:wholeTbl><a:tcTxStyle><a:fontRef idx="minor"><a:srgbClr val="{self.c("ink")}"/></a:fontRef><a:srgbClr val="{self.c("ink")}"/></a:tcTxStyle>'
               f'<a:tcStyle><a:tcBdr><a:left><a:ln><a:noFill/></a:ln></a:left><a:right><a:ln><a:noFill/></a:ln></a:right>'
               f'<a:top><a:ln><a:noFill/></a:ln></a:top><a:bottom><a:ln><a:noFill/></a:ln></a:bottom>'
               f'<a:insideH>{ln}</a:insideH><a:insideV><a:ln><a:noFill/></a:ln></a:insideV></a:tcBdr>'
               f'<a:fill><a:noFill/></a:fill></a:tcStyle></a:wholeTbl>{band}'
               f'<a:firstRow><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:srgbClr val="{self.c(t["header_text"])}"/></a:fontRef><a:srgbClr val="{self.c(t["header_text"])}"/></a:tcTxStyle>'
               f'<a:tcStyle><a:fill><a:solidFill><a:srgbClr val="{self.c(t["header"])}"/></a:solidFill></a:fill></a:tcStyle></a:firstRow>'
               f'</a:tblStyle></a:tblStyleLst>')
        for part in prs.part.package.iter_parts():
            if str(part.partname).endswith("tableStyles.xml") and hasattr(part, "_blob"):
                part._blob = xml.encode("utf-8")


def presentation(theme: dict) -> Presentation:
    return Potx(theme).presentation()


def layout_named(prs, name: str):
    for lay in prs.slide_master.slide_layouts:
        if lay.name == name:
            return lay
    raise KeyError(name)


def save_template(theme: dict, path: str) -> str:
    """The template as a .potx a person can open."""
    prs = Potx(theme).presentation()
    tmp = path + ".pptx"
    prs.save(tmp)
    with zipfile.ZipFile(tmp) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "[Content_Types].xml":
                data = data.replace(b"presentationml.presentation.main+xml", b"presentationml.template.main+xml")
            dst.writestr(name, data)
    os.remove(tmp)
    return path


# ── font embedding (EOT, as PowerPoint keeps an embedded font) ────────────

def _eot(ttf: bytes) -> bytes:
    """An Embedded OpenType wrapper (version 2.1, uncompressed, not encrypted) around a TrueType file."""
    from io import BytesIO

    from fontTools.ttLib import TTFont

    f = TTFont(BytesIO(ttf))
    os2 = f["OS/2"]
    head = f["head"]
    panose = bytes(getattr(os2.panose, a) for a in ("bFamilyType", "bSerifStyle", "bWeight", "bProportion", "bContrast",
                                                    "bStrokeVariation", "bArmStyle", "bLetterForm", "bMidline", "bXHeight"))
    names = {n.nameID: n.toUnicode() for n in f["name"].names if n.platformID == 3 and n.nameID in (1, 2, 4, 5)}

    def u16(s: str) -> bytes:
        b = s.encode("utf-16-le")
        return struct.pack("<H", len(b)) + b

    italic = 1 if (head.macStyle & 2) else 0
    body = (panose + struct.pack("<BBIH", 1 if os2.sCapHeight else 1, italic, os2.usWeightClass, os2.fsType)
            + struct.pack("<H", 0x504C)
            + struct.pack("<IIIIII", os2.ulUnicodeRange1, os2.ulUnicodeRange2, os2.ulUnicodeRange3, os2.ulUnicodeRange4,
                          os2.ulCodePageRange1, os2.ulCodePageRange2)
            + struct.pack("<IIIII", head.checkSumAdjustment, 0, 0, 0, 0)
            + struct.pack("<H", 0) + u16(names.get(1, "")) + struct.pack("<H", 0) + u16(names.get(2, ""))
            + struct.pack("<H", 0) + u16(names.get(5, "")) + struct.pack("<H", 0) + u16(names.get(4, ""))
            + struct.pack("<H", 0) + struct.pack("<H", 0))   # Padding5, RootStringSize (2.1: empty)
    # EOTSize(4) FontDataSize(4) Version(4) Flags(4) + body + FontData
    total = 16 + len(body) + len(ttf)
    return struct.pack("<IIII", total, len(ttf), 0x00020001, 0) + body + ttf


def embed_fonts(pptx_path: str, face: str) -> list[str]:
    """The family's regular and bold embedded in a finished deck (ppt/fonts/*.fntdata, named in the
    presentation's embeddedFontLst)."""
    files = LOOK.font_files if face == LOOK.family else {}
    todo = [(kind, path) for kind, path in (("regular", files.get("regular")), ("bold", files.get("bold")))
            if path and os.path.exists(path)]
    if not todo:
        return []
    with zipfile.ZipFile(pptx_path) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    rels = entries["ppt/_rels/presentation.xml.rels"].decode("utf-8")
    fonts_xml = []
    for i, (kind, path) in enumerate(todo, start=1):
        with open(path, "rb") as f:
            entries[f"ppt/fonts/font{i}.fntdata"] = _eot(f.read())
        rid = f"rIdAmFont{i}"
        rels = rels.replace("</Relationships>",
                            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/font" Target="fonts/font{i}.fntdata"/></Relationships>')
        fonts_xml.append(f'<p:{kind} r:id="{rid}"/>')
    entries["ppt/_rels/presentation.xml.rels"] = rels.encode("utf-8")
    pres = entries["ppt/presentation.xml"].decode("utf-8")
    if "embeddedFontLst" not in pres:
        lst = (f'<p:embeddedFontLst><p:embeddedFont><p:font typeface="{face}" charset="-34" pitchFamily="34"/>'
               + "".join(fonts_xml) + "</p:embeddedFont></p:embeddedFontLst>")
        i = pres.index("<p:notesSz")
        j = pres.index("/>", i) + 2
        pres = pres[:j] + lst + pres[j:]
        if 'xmlns:r=' not in pres.split(">", 2)[1]:
            pres = pres.replace("<p:presentation ", '<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ', 1)
    entries["ppt/presentation.xml"] = pres.encode("utf-8")
    ct = entries["[Content_Types].xml"].decode("utf-8")
    if 'Extension="fntdata"' not in ct:
        ct = ct.replace("</Types>", '<Default Extension="fntdata" ContentType="application/x-fontdata"/></Types>')
    entries["[Content_Types].xml"] = ct.encode("utf-8")
    tmp = pptx_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name in (["[Content_Types].xml"] + [n for n in entries if n != "[Content_Types].xml"]):
            out.writestr(name, entries[name])
    os.replace(tmp, pptx_path)
    return [f"{face} {kind.capitalize()}" for kind, _ in todo]


def typefaces_in(path: str) -> dict[str, set]:
    """Every typeface named anywhere in the package, by part — what a reader could see."""
    out: dict[str, set] = {}
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.startswith("ppt/") and name.endswith(".xml"):
                faces = set(re.findall(r'typeface="([^"]*)"', z.read(name).decode("utf-8", "replace")))
                faces = {f for f in faces if f and not f.startswith("+")}
                if faces:
                    out[name] = faces
    return out


def embedded_in(path: str) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return sorted(n for n in z.namelist() if n.startswith("ppt/fonts/"))
