"""The purchase request as a plain document, for an organisation that has no form of its own yet.

One page a person can complete and hand in: who asks, for what, from whom,
at what price as seen on the seller's page — and, on its face, under its own
heading, everything the draft could not fill in and why. A draft never says
more than was found: an amount that could not be worked out is written as
unknown with its reason, never as zero and never left looking finished.

AM21: the draft is laid out by the look's Word renderer — the template's
named styles, tables sized from their content, the one typeface embedded —
and this package sets no size, colour, font or width of its own.

``check`` opens the finished file again: the lines and their amounts are the
computed ones, every gap of the request is on the page under the gaps'
heading, and no figure stands in the document that is not one of the
computed figures.
"""

from __future__ import annotations

import os
import re

from docx import Document

from ....deliverables.report_docx import ReportWriter, embed_fonts
from ....deliverables.reread import check as _check
from ....deliverables.reread import squash
from ..words import t
from . import wording

LINE_COLS = ("no", "description", "quantity", "unit", "unit_price", "amount")


class RequestDraft:
    def __init__(self, computed: dict, theme: dict, run: dict | None = None):
        self.c, self.theme, self.run = computed, theme, run or {}
        self.lang = computed.get("lang") or "th"
        self.r = ReportWriter({}, theme, {})   # the renderer: its styles, its measured tables
        self.doc = self.r.doc

    def w(self, key: str, **kw) -> str:
        return t(self.lang, key, **kw)

    def para(self, text: str, style: str = "AM Body"):
        return self.r._para(style, text)

    def heading(self, text: str):
        self.r.heading(text, 2)

    def table(self, heads: list[str] | None, rows: list[list[str]], right: tuple = (), section: str = ""):
        block = {"columns": heads or [""] * (len(rows[0]) if rows else 2), "rows": rows, "numeric_from": (min(right) if right else None),
                 "cite_col": None, "strong_rows": [], "small": True, "no_header": heads is None}
        self.r.table(block, section or self.w("pr.title"))

    def money(self, v, why_key: str = "unknown") -> str:
        return wording.amount(v) if v not in (None, "") else self.w(why_key)

    def build(self, out_dir: str | None = None) -> None:
        c, lang = self.c, self.lang
        req = c.get("request") or {}
        need = c.get("need") or {}
        self.r.heading(self.w("pr.title"), 1)
        self.para(self.w("pr.sub"), "AM Small Muted")
        place = need.get("delivery_place") or (self.w("pr.pickup") if need.get("delivery_place_from") == "pickup" else self.w("pr.blank"))
        rows = [[self.w("pr.date"), wording.date(lang, c.get("today"))],
                [self.w("pr.requester"), self.run.get("preparer") or self.w("pr.blank")],
                [self.w("pr.organisation"), self.run.get("organisation") or self.w("pr.blank")],
                [self.w("pr.delivery_place"), place],
                [self.w("pr.needed_by"), need.get("needed_by") or self.w("pr.blank")],
                [self.w("pr.seller"), (req.get("seller") or self.w("pr.sellers.several")) if req.get("lines") else self.w("pr.blank")]]
        self.table(None, rows)
        self.heading(self.w("pr.lines.title"))
        lines = []
        for ln in req.get("lines") or []:
            lines.append([str(ln["no"]), ln["description"], ln["quantity"] or self.w("unknown"), ln["unit"] or self.w("item.unit"),
                          self.money(ln["unit_price"]), self.money(ln["amount"])])
        self.table([self.w(f"pr.col.{k}") for k in LINE_COLS], lines, right=(2, 4, 5), section=self.w("pr.lines.title"))
        tot = req.get("totals")
        cur = (tot or {}).get("currency") or ""
        sums = [[self.w("pr.subtotal", currency=cur), self.money((tot or {}).get("subtotal"))],
                [self.w("pr.vat", rate=str((tot or {}).get("vat_rate") or c.get("vat_rate") or "")), self.money((tot or {}).get("vat"), "pr.vat.unknown")],
                [self.w("pr.total", currency=cur), self.money((tot or {}).get("total"), "pr.total.unknown")]]
        self.table(None, sums, right=(1,))
        # on the face of the draft: what could not be filled in, and why
        self.heading(self.w("gaps.title"))
        self.para(self.w("gaps.lead"), "AM Small Muted")
        many = len(req.get("lines") or []) > 1
        gaps = []
        for g in req.get("gaps") or []:
            what, why = wording.gap(lang, g)
            gaps.append([(self.w("gaps.line", n=str(g["line"])) + " · " if many and g.get("line") else "") + what, why])
        self.table([self.w("gaps.col.what"), self.w("gaps.col.why")], gaps, section=self.w("gaps.title"))
        notes = [a["text"] for a in c.get("assumptions") or []] + [self.w("pr.passed", item=n) for n in req.get("passed") or []]
        if notes:
            self.heading(self.w("assumptions.title"))
            for line in notes:
                self.para(line, "AM Bullet")
        self.heading(self.w("pr.evidence.title"))
        self.para(self.w("pr.evidence.lead"), "AM Small Muted")
        items = {it["key"]: it for it in c["items"]}
        for ln in req.get("lines") or []:
            cand = next((x for x in items[ln["item"]]["candidates"] if x["key"] == ln["candidate"]), {})
            price = cand.get("price") or {}
            ev = [[self.w("pr.ev.page"), ln["url"] or ""], [self.w("pr.ev.seen"), wording.moment(lang, ln["observed_at"])],
                  [self.w("pr.ev.shown"), ln["price_shown"] or self.w("unknown")],
                  [self.w("pr.ev.beside"), " | ".join(price.get("beside") or [])],
                  [self.w("pr.ev.snapshot"), wording.snapshot_of(c, cand.get("source") or "")]]
            if many:
                self.para(self.w("gaps.line", n=str(ln["no"])), "AM Body Strong")
            self.table(None, ev)
            pic = (cand.get("picture") or {}).get("name")
            if pic and out_dir and os.path.exists(os.path.join(out_dir, pic)):
                self.r.picture(os.path.join(out_dir, pic))
        self.heading(self.w("pr.sign.title"))
        self.table(None, [[self.w("pr.sign.requester"), ""], [self.w("pr.sign.approver"), ""]])
        self.para(self.w("pr.footer"), "AM Small Muted")

    def write(self, path: str, out_dir: str | None = None) -> dict:
        self.build(out_dir)
        props = self.doc.core_properties
        props.title = self.w("pr.title")
        props.author = str(self.run.get("organisation") or "")
        self.doc.save(path)
        try:
            embed_fonts(path, self.r.doc.styles["Normal"].font.name)
        except Exception:  # noqa: BLE001 — a document without its font embedded still reads; the check below is what matters
            pass
        return {"lines": len((self.c.get("request") or {}).get("lines") or []), "gaps": len((self.c.get("request") or {}).get("gaps") or []),
                "layouts": self.r.layouts}


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
    # (the renderer breaks a Thai header between words with a line break where the column is narrow —
    # a layout decision, not content — so a header is matched with its whitespace taken out)
    flat = lambda cells: [squash(c) for c in cells]  # noqa: E731
    grid = next((tb for tb in tables if tb and flat(tb[0]) == flat(heads)), None)
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
    gap_table = next((tb for tb in tables if tb and flat(tb[0]) == flat([w("gaps.col.what"), w("gaps.col.why")])), None)
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
