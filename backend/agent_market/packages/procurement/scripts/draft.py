"""The purchase request as a plain document, for an organisation that has no form of its own yet.

One page a person can complete and hand in: who asks, for what, from whom,
at what price as seen on the seller's page — and, on its face, under its own
heading, everything the draft could not fill in and why. A draft never says
more than was found: an amount that could not be worked out is written as
unknown with its reason, never as zero and never left looking finished.

``check`` opens the finished file again: the lines and their amounts are the
computed ones, every gap of the request is on the page under the gaps'
heading, and no figure stands in the document that is not one of the
computed figures.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ....deliverables.reread import check as _check
from ....deliverables.reread import squash
from ..words import t
from . import wording

LINE_COLS = ("no", "description", "quantity", "unit", "unit_price", "amount")


def _font(run, face: str, size: float, bold: bool = False, color: str | None = None):
    run.font.name = face
    run.font.size = Pt(size)
    run.font.bold = bold
    rpr = run._r.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), face)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


class RequestDraft:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None):
        self.c, self.theme, self.run = computed, theme, run or {}
        self.lang = computed.get("lang") or "th"
        self.font = theme["fonts"]["thai"]
        self.p = theme["palette"]
        self.doc = Document()
        for section in self.doc.sections:
            section.page_width, section.page_height = Cm(21.0), Cm(29.7)
            section.left_margin = section.right_margin = Cm(2.0)
            section.top_margin = section.bottom_margin = Cm(1.8)
        normal = self.doc.styles["Normal"]
        normal.font.name = self.font
        normal.font.size = Pt(11)

    def w(self, key: str, **kw) -> str:
        return t(self.lang, key, **kw)

    def para(self, text: str, size: float = 11, bold: bool = False, color: str | None = None, align=None, after: float = 4):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(after)
        p.paragraph_format.space_before = Pt(0)
        if align:
            p.alignment = align
        _font(p.add_run(text), self.font, size, bold, color)
        return p

    def heading(self, text: str):
        p = self.para(text, 13, True, self.p["primary"], after=3)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.keep_with_next = True

    def table(self, heads: list[str] | None, rows: list[list[str]], widths: list[float], right: tuple = ()):
        tb = self.doc.add_table(rows=(1 if heads else 0) + len(rows), cols=len(widths))
        tb.style = "Table Grid"
        tb.autofit = False
        grid = ([heads] if heads else []) + rows
        for i, row in enumerate(grid):
            for j, v in enumerate(row):
                cell = tb.cell(i, j)
                cell.width = Cm(widths[j])
                p = cell.paragraphs[0]
                p.paragraph_format.space_after = Pt(1)
                if j in right and not (heads and i == 0):
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                _font(p.add_run("" if v is None else str(v)), self.font, 10, bold=bool(heads) and i == 0)
        return tb

    def money(self, v, why_key: str = "unknown") -> str:
        return wording.amount(v) if v not in (None, "") else self.w(why_key)

    def build(self, out_dir: str | None = None) -> None:
        c, lang = self.c, self.lang
        req = c.get("request") or {}
        need = c.get("need") or {}
        self.para(self.w("pr.title"), 18, True, self.p["primary"], after=0)
        self.para(self.w("pr.sub"), 9, False, self.p["muted"], after=8)
        place = need.get("delivery_place") or (self.w("pr.pickup") if need.get("delivery_place_from") == "pickup" else self.w("pr.blank"))
        rows = [[self.w("pr.date"), wording.date(lang, c.get("today"))],
                [self.w("pr.requester"), self.run.get("preparer") or self.w("pr.blank")],
                [self.w("pr.organisation"), self.run.get("organisation") or self.w("pr.blank")],
                [self.w("pr.delivery_place"), place],
                [self.w("pr.needed_by"), need.get("needed_by") or self.w("pr.blank")],
                [self.w("pr.seller"), (req.get("seller") or self.w("pr.sellers.several")) if req.get("lines") else self.w("pr.blank")]]
        self.table(None, rows, [5.0, 12.0])
        self.heading(self.w("pr.lines.title"))
        lines = []
        for ln in req.get("lines") or []:
            lines.append([str(ln["no"]), ln["description"], ln["quantity"] or self.w("unknown"), ln["unit"] or self.w("item.unit"),
                          self.money(ln["unit_price"]), self.money(ln["amount"])])
        self.table([self.w(f"pr.col.{k}") for k in LINE_COLS], lines, [1.0, 7.4, 1.6, 1.6, 2.7, 2.7], right=(2, 4, 5))
        tot = req.get("totals")
        cur = (tot or {}).get("currency") or ""
        sums = [[self.w("pr.subtotal", currency=cur), self.money((tot or {}).get("subtotal"))],
                [self.w("pr.vat", rate=str((tot or {}).get("vat_rate") or c.get("vat_rate") or "")), self.money((tot or {}).get("vat"), "pr.vat.unknown")],
                [self.w("pr.total", currency=cur), self.money((tot or {}).get("total"), "pr.total.unknown")]]
        self.para("", 4, after=0)
        self.table(None, sums, [12.0, 5.0], right=(1,))
        # on the face of the draft: what could not be filled in, and why
        self.heading(self.w("gaps.title"))
        self.para(self.w("gaps.lead"), 9, False, self.p["muted"])
        many = len(req.get("lines") or []) > 1
        gaps = []
        for g in req.get("gaps") or []:
            what, why = wording.gap(lang, g)
            gaps.append([(self.w("gaps.line", n=str(g["line"])) + " · " if many and g.get("line") else "") + what, why])
        self.table([self.w("gaps.col.what"), self.w("gaps.col.why")], gaps, [6.0, 11.0])
        notes = [a["text"] for a in c.get("assumptions") or []] + [self.w("pr.passed", item=n) for n in req.get("passed") or []]
        if notes:
            self.heading(self.w("assumptions.title"))
            for line in notes:
                self.para("• " + line, 10)
        self.heading(self.w("pr.evidence.title"))
        self.para(self.w("pr.evidence.lead"), 9, False, self.p["muted"])
        items = {it["key"]: it for it in c["items"]}
        for ln in req.get("lines") or []:
            cand = next((x for x in items[ln["item"]]["candidates"] if x["key"] == ln["candidate"]), {})
            price = cand.get("price") or {}
            ev = [[self.w("pr.ev.page"), ln["url"] or ""], [self.w("pr.ev.seen"), wording.moment(lang, ln["observed_at"])],
                  [self.w("pr.ev.shown"), ln["price_shown"] or self.w("unknown")],
                  [self.w("pr.ev.beside"), " | ".join(price.get("beside") or [])],
                  [self.w("pr.ev.snapshot"), wording.snapshot_of(c, cand.get("source") or "")]]
            if many:
                self.para(self.w("gaps.line", n=str(ln["no"])), 10, True)
            self.table(None, ev, [4.0, 13.0])
            pic = (cand.get("picture") or {}).get("name")
            if pic and out_dir and os.path.exists(os.path.join(out_dir, pic)):
                self.doc.add_picture(os.path.join(out_dir, pic), width=Cm(15.5))
        self.heading(self.w("pr.sign.title"))
        self.table(None, [[self.w("pr.sign.requester"), ""], [self.w("pr.sign.approver"), ""]], [5.0, 12.0])
        self.para(self.w("pr.footer"), 8, False, self.p["muted"], after=0)

    def write(self, path: str, out_dir: str | None = None) -> dict:
        self.build(out_dir)
        self.doc.save(path)
        try:
            from ....deliverables.report_docx import embed_fonts

            embed_fonts(path, self.font)
        except Exception:  # noqa: BLE001 — a document without its font embedded still reads; the check below is what matters
            pass
        return {"lines": len((self.c.get("request") or {}).get("lines") or []), "gaps": len((self.c.get("request") or {}).get("gaps") or [])}


_NUMBER = re.compile(r"\d[\d,]*\.\d{2}")


def check(path: str, computed: dict) -> list[dict]:
    """The finished draft, opened again."""
    lang = computed.get("lang") or "th"
    w = lambda key, **kw: t(lang, key, **kw)  # noqa: E731
    doc = Document(path)
    req = computed.get("request") or {}
    lines = req.get("lines") or []
    tables = [[[c.text for c in r.cells] for r in tb.rows] for tb in doc.tables]
    text = "\n".join(p.text for p in doc.paragraphs)
    bad: list[str] = []
    n = 0
    heads = [w(f"pr.col.{k}") for k in LINE_COLS]
    grid = next((tb for tb in tables if tb and tb[0] == heads), None)
    if grid is None:
        bad.append(w("check.draft.no_lines"))
    else:
        rows = grid[1:]
        if len(rows) != len(lines):
            bad.append(w("check.draft.rows", file=str(len(rows)), computed=str(len(lines))))
        for ln, row in zip(lines, rows):
            for col, value in ((4, ln["unit_price"]), (5, ln["amount"])):
                n += 1
                want = wording.amount(value) if value else w("unknown")
                if squash(row[col]) != squash(want):
                    bad.append(w("check.draft.differs", line=str(ln["no"]), column=heads[col], file=row[col] or w("pr.blank"), computed=want))
            n += 1
            if squash(row[1]) != squash(ln["description"]):
                bad.append(w("check.draft.differs", line=str(ln["no"]), column=heads[1], file=row[1], computed=ln["description"]))
    # every gap is on the face, under the gaps' own heading
    n += 1
    if w("gaps.title") not in text:
        bad.append(w("check.draft.no_gaps_heading"))
    gap_table = next((tb for tb in tables if tb and tb[0] == [w("gaps.col.what"), w("gaps.col.why")]), None)
    listed = "\n".join(squash(" ".join(r)) for r in (gap_table or [])[1:])
    missing = []
    for g in req.get("gaps") or []:
        n += 1
        what, why = wording.gap(lang, g)
        if squash(what) not in listed or squash(why) not in listed:
            missing.append(what)
    if missing:
        bad.append(w("check.draft.gaps_missing", list=" · ".join(dict.fromkeys(missing))))
    # no figure in the document that is not one of the computed figures
    allowed = set()
    for ln in lines:
        for v in (ln["unit_price"], ln["amount"]):
            if v:
                allowed.add(wording.amount(v))
    for v in ((req.get("totals") or {}).get(k) for k in ("subtotal", "vat", "total", "net")):
        if v:
            allowed.add(wording.amount(v))
    stray = []
    for tb in tables:
        if tb is grid or (tb and len(tb[0]) == 2 and any(squash(r[0]).startswith(squash(w("pr.subtotal", currency=""))[:6]) for r in tb)):
            for r in tb[1:] if tb is grid else tb:
                for cell in (r[4:] if tb is grid else r[1:]):
                    for m in _NUMBER.findall(cell):
                        n += 1
                        if m not in allowed:
                            stray.append(m)
    if stray:
        bad.append(w("check.draft.stray", list=", ".join(stray[:5])))
    more = " " + w("check.more", n=str(len(bad) - 4)) if len(bad) > 4 else ""
    return [_check("docx.request", not bad, ("; ".join(bad[:4]) + more) if bad else w("check.draft.ok", n=str(n), gaps=str(len(req.get("gaps") or []))),
                   w("check.draft.label"), n)]
