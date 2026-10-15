"""The executive deck (.pptx): one idea per slide, each on a layout of the catalogue (AM21).

Every slide is made from a layout of the template look/potx.py builds out of look.json, and holds its
content in that layout's placeholders — nothing is drawn on a slide by hand, no shape sits outside a
placeholder, and no run carries a font of its own. The slide is 13.333 × 7.5 in. The text of every
placeholder is measured in the real typeface before the file is saved: too long for the layout, the
next size down the scale is used; too long for that, the catalogue breaks it across slides where the
layout counts items, rows or lines; and when nothing holds it the run says so and names the section
(``DoesNotFit``), and no file is written.

The deck stops being the report retyped: the headline figure on a slide of its own, the other figures
in a row, the summary as one statement, at most four points of at most two lines with a hanging indent,
a table of at most ten rows, a chart large enough to read across a room. The footer carries the source
file's name only; the sha256, the references and the model are on the closing slide. The family is
embedded in the file.
"""

from __future__ import annotations

import copy
import os
from decimal import Decimal

from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from ..engine.words import SHARED as S
from .content import EXC_LABEL, money0, pct_or_dash
from .look import catalogue, measure, potx
from .look.tokens import LOOK

FMT = "pptx"
EMU_PER_IN = 914400
TABLE_ROLE_ROWS = 8          # a table of up to this many rows is set at the body size, longer ones at small
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _chunks(text: str, limit: int) -> list[str]:
    """A long line of comma-separated ids broken into lines of at most ``limit`` characters."""
    out, cur = [], ""
    for piece in text.split(", "):
        cand = (cur + ", " + piece) if cur else piece
        if cur and len(cand) > limit:
            out.append(cur)
            cur = piece
        else:
            cur = cand
    return out + ([cur] if cur else [])


class DeckWriter:
    def __init__(self, model: dict, computed: dict, theme: dict, charts: dict[str, str] | None = None):
        self.m = model
        self.c = computed
        self.t = theme
        self.p = theme["palette"]
        self.charts = charts or {}   # chart id → png path, drawn at the chart layout's frame
        self.prs = potx.presentation(theme)
        self.cites: list[list[str]] = []        # per slide
        self.bindings: list[dict] = []
        self.layouts: list[dict] = []           # what the catalogue chose, slide by slide (recorded)
        self.footer = self.c["source"]["primary"]["file"]   # the source file's name, and nothing else

    # ── the placeholders ─────────────────────────────────────────────
    def _slide(self, layout: str):
        return self.prs.slides.add_slide(potx.layout_named(self.prs, layout))

    def _ph(self, slide, idx: int):
        for ph in slide.placeholders:
            if ph.placeholder_format.idx == idx:
                return ph
        raise KeyError(idx)

    @staticmethod
    def _text(ph, text: str) -> None:
        tf = ph.text_frame
        lines = str(text).split("\n")
        tf.paragraphs[0].text = lines[0]
        for line in lines[1:]:
            tf.add_paragraph().text = line

    @staticmethod
    def _lines(ph, items: list[str]) -> None:
        tf = ph.text_frame
        tf.paragraphs[0].text = items[0] if items else ""
        for item in items[1:]:
            tf.add_paragraph().text = item

    def _fit(self, slide, layout: str, section: str) -> None:
        """Every text placeholder measured against its frame: a text that would overflow is set at the
        next size down the scale (a run size from the scale, by the renderer); one that still overflows
        is refused naming the section."""
        roles = potx._roles(self.t)[layout]
        boxes = potx._boxes()[layout]
        for ph in list(slide.placeholders):
            idx = ph.placeholder_format.idx
            if idx not in roles or not ph.has_text_frame:
                continue
            role, bold, _tok, _al, _an = roles[idx]
            b = LOOK.bold(role) if bold is None else bold
            x, y, w, h = boxes[idx]
            paras = [p.text for p in ph.text_frame.paragraphs]
            bullets = layout == "bullets" and idx == 11
            indent = 0.35 if bullets else 0.0
            r: str | None = role
            while r is not None:
                size = LOOK.pt(FMT, r)
                need = sum(measure.lines_in(t, size, (w - indent) * 72, b) for t in paras) * size * LOOK.line(FMT)
                need += (len(paras) - 1) * 6 if bullets else 0
                if need <= h * 72 + 0.5 and all(measure.widest_word_pt(t, size, b) <= (w - indent) * 72 for t in paras):
                    break
                r = LOOK.smaller(r)
            if r is None:
                raise catalogue.DoesNotFit(section, S("deck.fit.why", text=paras[0][:40] if paras else ""))
            if r != role:
                for p in ph.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(LOOK.pt(FMT, r))
                self.layouts[-1]["role"] = r

    def _finish(self, slide, layout: str, cites: list[str], section: str) -> None:
        self._fit(slide, layout, section)
        # a placeholder left empty is taken off the slide (no prompt text, nothing outside the content)
        for ph in list(slide.placeholders):
            if ph.has_text_frame and not ph.text_frame.text.strip():
                ph._element.getparent().remove(ph._element)
        if layout in potx.CHROME_LAYOUTS:
            self._footer(slide, layout)
        self.cites.append(list(dict.fromkeys(c for c in cites if c)))

    def _footer(self, slide, layout: str) -> None:
        """The layout's footer and slide-number placeholders, on the slide with their text."""
        lay = potx.layout_named(self.prs, layout)
        for ph in lay.placeholders:
            idx = ph.placeholder_format.idx
            if idx not in (potx.FOOTER_IDX, potx.SLIDENUM_IDX):
                continue
            el = copy.deepcopy(ph._element)
            if idx == potx.FOOTER_IDX:
                p = el.find(".//{%s}p" % A_NS)
                for child in list(p):
                    p.remove(child)
                r = p.makeelement("{%s}r" % A_NS)
                t = r.makeelement("{%s}t" % A_NS)
                t.text = self.footer
                r.append(t)
                p.append(r)
            slide.shapes._spTree.append(el)

    def _choose(self, layout: str, content: dict, section: str) -> catalogue.Choice:
        ch = catalogue.choose(FMT, content, section, kinds=(layout,))
        self.layouts.append({"section": section, "layout": ch.layout, "role": ch.role, "reason": ch.reason, "parts": len(ch.parts)})
        return ch

    # ── the slides ───────────────────────────────────────────────────
    def title(self):
        m = self.m
        content = {"title": S("docs.deck.title"), "subtitle": self.c["period_label"],
                   "lines": [m["prepared"]] + list(m["cover_lines"]) + [" · ".join(x for x in (m["company"], S("docs.deck.audience")) if x)]}
        self._choose("title", content, S("docs.deck.title"))
        s = self._slide("title")
        self._text(self._ph(s, 0), content["title"])
        self._text(self._ph(s, 1), content["subtitle"])
        if m["company"]:
            self._text(self._ph(s, 10), m["company"])
        self._lines(self._ph(s, 11), content["lines"])
        self._finish(s, "title", [], S("docs.deck.title"))

    @staticmethod
    def _delta(kp: dict) -> str:
        if not kp["delta"]:
            return S("docs.kpi.no_compare")
        return {"up": "▲ ", "down": "▼ ", "flat": ""}[kp["tone"]] + kp["delta"]

    def figure(self, kp: dict):
        sentence = self._delta(kp)
        content = {"title": S("docs.deck.stats"), "label": kp["label"], "value": kp["value_short"], "sentence": sentence}
        self._choose("figure", content, S("docs.deck.stats"))
        s = self._slide("figure")
        self._text(self._ph(s, 0), content["title"])
        self._text(self._ph(s, 10), kp["label"])
        self._text(self._ph(s, 11), kp["value_short"])
        self._text(self._ph(s, 12), sentence)
        self._finish(s, "figure", [kp["cite"]], S("docs.deck.stats"))

    def figures(self, kpis: list[dict]):
        deltas = [self._delta(k) for k in kpis]
        content = {"title": S("docs.deck.stats"), "items": [k["label"] for k in kpis], "label": max((k["label"] for k in kpis), key=len),
                   "value": max((k["value_short"] for k in kpis), key=len), "delta": max(deltas, key=len)}
        self._choose("figures", content, S("docs.deck.stats"))
        s = self._slide("figures")
        self._text(self._ph(s, 0), content["title"])
        f = potx.FIELDS["figures"]
        for i, k in enumerate(kpis):
            self._text(self._ph(s, f["label"][i]), k["label"])
            self._text(self._ph(s, f["value"][i]), k["value_short"])
            self._text(self._ph(s, f["delta"][i]), deltas[i])
        self._finish(s, "figures", [k["cite"] for k in kpis], S("docs.deck.stats"))

    def statement(self, title: str, text: str, cites: list[str]):
        self._choose("statement", {"title": title, "text": text}, title)
        s = self._slide("statement")
        self._text(self._ph(s, 0), title)
        self._text(self._ph(s, 10), text)
        self._finish(s, "statement", cites, title)

    def bullets(self, title: str, items: list[str], cites: list[str], lead: str = ""):
        if not items:
            return
        if len(items) == 1 and not lead:
            # one point is a statement, not a list of one: the shorter layout fills the slide honestly
            self.statement(title, items[0], cites)
            return
        content = {"title": title, "items": items}
        if lead:
            content["lead"] = lead
        ch = self._choose("bullets", content, title)
        for n, part in enumerate(ch.parts, start=1):
            s = self._slide("bullets")
            self._text(self._ph(s, 0), title if len(ch.parts) == 1 else S("docs.deck.part", title=title, n=n, of=len(ch.parts)))
            if lead and n == 1:
                self._text(self._ph(s, 10), lead)
            self._lines(self._ph(s, 11), part["items"])
            self._finish(s, "bullets", cites if n == 1 else [], title)

    def chart(self, spec: dict):
        path = self.charts.get(spec["id"])
        if not path or not os.path.exists(path):
            return
        caption = (spec["subtitle"] + " · " if spec.get("subtitle") else "") + S("docs.chart.note")
        self._choose("chart", {"title": spec["title"], "series": spec["series"], "categories": spec["categories"], "caption": caption}, spec["title"])
        s = self._slide("chart")
        self._text(self._ph(s, 0), spec["title"])
        self._ph(s, 10).insert_picture(path)
        self._text(self._ph(s, 11), caption)
        scale = Decimal(str(spec.get("scale", 1)))
        self.bindings.append({"slide": len(self.prs.slides), "chart": spec["id"], "file": os.path.basename(path), "categories": spec["categories"],
                              "series": [{"name": sv["name"], "values": [str(v) for v in sv["values"]], "path": sv.get("path")} for sv in spec["series"]],
                              "scale": str(scale)})
        self._finish(s, "chart", list(spec.get("cites") or []), spec["title"])

    def table(self, title: str, headers: list[str], rows: list[list[str]], cites: list[str], numeric_from: int = 1, caption: str = ""):
        content = {"title": title, "rows": rows, "columns": len(headers), "caption": caption}
        ch = self._choose("table", content, title)
        for n, part in enumerate(ch.parts, start=1):
            body = part["rows"]
            s = self._slide("table")
            self._text(self._ph(s, 0), title if len(ch.parts) == 1 else S("docs.deck.part", title=title, n=n, of=len(ch.parts)))
            gf = self._ph(s, 10).insert_table(1 + len(body), len(headers))
            tbl = gf.table
            tbl._tbl.tblPr.find(qn("a:tableStyleId")).text = potx.TABLE_STYLE_ID
            role = "body" if len(body) <= TABLE_ROLE_ROWS else "small"
            size = Pt(LOOK.pt(FMT, role))
            widths = self._widths(headers, body, role, numeric_from)
            for j, w in enumerate(widths):
                tbl.columns[j].width = Emu(int(w * EMU_PER_IN))
            for i, row in enumerate([list(headers)] + body):
                for j, val in enumerate(row):
                    cell = tbl.cell(i, j)
                    cell.text = "" if val is None else str(val)
                    for para in cell.text_frame.paragraphs:
                        if j >= numeric_from:
                            para.alignment = PP_ALIGN.RIGHT
                        for run in para.runs:
                            run.font.size = size
                            if i and str(val).startswith("("):
                                run.font.color.rgb = RGBColor.from_string(LOOK.color(self.p, "negative"))
            if caption:
                self._text(self._ph(s, 11), caption)
            self._finish(s, "table", cites if n == 1 else [], title)

    def _widths(self, headers: list[str], rows: list[list[str]], role: str, numeric_from: int) -> list[float]:
        """Column widths in inches from the content: numbers what their widest value needs, the label
        columns the rest in proportion to their longest text."""
        size = LOOK.pt(FMT, role)
        pad = 0.2
        need = []
        for j in range(len(headers)):
            texts = [str(headers[j])] + ["" if r[j] is None else str(r[j]) for r in rows]
            need.append(max(measure.width_pt(t, size, False) for t in texts) / 72 + pad)
        total = potx.CW
        fixed = [j for j in range(len(headers)) if j >= numeric_from]
        free = [j for j in range(len(headers)) if j < numeric_from]
        widths = [0.0] * len(headers)
        for j in fixed:
            widths[j] = need[j]
        rest = total - sum(widths[j] for j in fixed)
        if free:
            share = sum(need[j] for j in free) or 1.0
            for j in free:
                widths[j] = max(1.2, rest * need[j] / share)
        over = sum(widths) - total
        if over > 0:
            for j in free or fixed:
                widths[j] -= over / len(free or fixed)
        return widths

    def closing(self, lines: list[str]):
        title = S("docs.deck.assumptions")
        ch = self._choose("closing", {"title": title, "lines": lines}, title)
        for n, part in enumerate(ch.parts, start=1):
            s = self._slide("closing")
            self._text(self._ph(s, 0), title if len(ch.parts) == 1 else S("docs.deck.part", title=title, n=n, of=len(ch.parts)))
            self._lines(self._ph(s, 10), part["lines"])
            self._finish(s, "closing", [], title)

    # ── assemble ─────────────────────────────────────────────────────
    def write(self, path: str, organisation: str = "") -> dict:
        c, m = self.c, self.m
        nar, nc = m["narrative"], (m.get("narrative_cites") or {})
        self.title()
        kpis = {k["key"]: k for k in m["kpis"]}
        self.figure(kpis["net_profit"])
        self.figures([k for k in m["kpis"] if k["key"] != "net_profit"])
        self.statement(S("docs.section.summary"), nar["summary"], list(nc.get("summary") or []))
        self.bullets(S("docs.sub.watch"), list(nar.get("watch") or []), list(nc.get("watch") or []))
        charts = {ch["id"]: ch for ch in m["charts"]}
        if "trend" in charts:
            self.chart(charts["trend"])
        lines = {l["key"]: l for l in c["lines"]}
        heads = [S("docs.col.item"), S("docs.col.current")] + ([S("docs.col.prior"), "%"] if c["has_prior"] else []) + ([S("docs.col.budget")] if c["has_budget"] else [])
        rows = []
        for key in ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "total_expenses", "net_profit"):
            l = lines[key]
            r = [l["label"], money0(l["current"])]
            if c["has_prior"]:
                r += [money0(l["prior"]), pct_or_dash(l["var_prior_pct"])]
            if c["has_budget"]:
                r += [money0(l["budget"])]
            rows.append(r)
        self.table(S("docs.deck.pl"), heads, rows, [lines[k]["cite"] for k in ("revenue", "total_expenses", "net_profit")],
                   caption=S("docs.caption.unit_baht"))
        if "budget_variance" in charts:
            self.chart(charts["budget_variance"])
        if "composition" in charts:
            self.chart(charts["composition"])
        if c["top_movements"]:
            heads = [S("docs.col.account"), S("docs.col.current")] + ([S("docs.col.prior"), S("docs.col.change"), "%"] if c["has_prior"] else [])
            rows = []
            for mv in c["top_movements"][:6]:
                r = [f"{mv['code']} {mv['name']}".strip(), money0(mv["current"])]
                if c["has_prior"]:
                    r += [money0(mv["prior"]), money0(mv["change"]), pct_or_dash(mv["change_pct"])]
                rows.append(r)
            self.table(S("docs.section.movements"), heads, rows, [mv["cite"] for mv in c["top_movements"][:6]], caption=S("docs.caption.unit_baht"))
            self.bullets(S("docs.section.movements"), list(nar.get("movements") or []), list(nc.get("movements") or []))
        exc = c["exceptions"]
        items = [f"{EXC_LABEL.get(e['code'], S('docs.exc.to_check'))}: {e['title']} — {e['detail']}" for e in exc] or [S("docs.exc.none")]
        self.bullets(S("docs.deck.exceptions", n=len(exc)), items, [e["cite"] for e in exc if e.get("cite")], lead=S("docs.deck.exceptions_note"))
        self.bullets(S("docs.sub.next"), list(nar.get("next_steps") or []), list(nc.get("next_steps") or []))
        model = m.get("model") or {}
        refs = sorted(set(c for per in self.cites for c in per), key=lambda x: (len(x), x))
        closing = [S("docs.deck.primary", file=c["source"]["primary"]["file"], rows_used=f"{c['source']['primary']['rows_used']:,}"),
                   m["source_note"],
                   S("docs.deck.computed", script=c["script"]),
                   S("docs.deck.model") + " " + (model.get("model") or S("docs.deck.no_model")) + (" " + S("docs.deck.inside") if model.get("self_hosted") else ""),
                   S("docs.deck.readonly"), S("docs.deck.scope")]
        closing += [a["text"] for a in m["assumptions"]]
        closing += [(S("docs.deck.cites") + " " if i == 0 else "    ") + line for i, line in enumerate(_chunks(", ".join(refs), 110))]
        self.closing(closing)
        props = self.prs.core_properties
        props.title = f"{m['title']} {m['subtitle']}"
        props.author = m["company"] or organisation or ""
        self.prs.save(path)
        embedded = potx.embed_fonts(path, LOOK.family)
        return {"path": path, "slides": len(self.prs.slides), "chart_bindings": self.bindings, "slide_cites": self.cites,
                "layouts": self.layouts, "embedded_fonts": embedded}
