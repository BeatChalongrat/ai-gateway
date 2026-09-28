"""The executive deck (.pptx), 8–12 slides from the same content model.

Slides are built with pptx_generator.py's own builders (title, stats,
content, two-column, table, chart) — imported, not modified — and then a
theme layer restyles them: palette, fonts on every run including chart
text, an accent mark, a footer with the source note, the citation ids used
on the slide and the page number, then a measured fit pass.
"""

from __future__ import annotations

from decimal import Decimal

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

import pptx_generator as pg  # /sandbox/scripts — shared, imported, not modified

from .content import money0, pct_or_dash
from .fit import shrink_to_fit

W, H = 10.0, 5.625


def _colors(theme: dict) -> dict:
    p = theme["palette"]
    return {"primary": p["primary"], "secondary": p["primary_dark"], "accent": p["accent"],
            "light_bg": p["surface"], "text_dark": p["ink"], "text_light": p["on_primary"],
            "success": p["positive"], "warning": p["gold"], "border": p["rule"]}


class DeckWriter:
    def __init__(self, model: dict, computed: dict, theme: dict):
        self.m = model
        self.c = computed
        self.t = theme
        self.p = theme["palette"]
        self.font = theme["fonts"]["thai"]
        self.prs = Presentation()
        self.prs.slide_width = Inches(W)
        self.prs.slide_height = Inches(H)
        self.settings = {"font": {"heading": self.font, "body": self.font, "heading_size": 22, "body_size": 14},
                         "language": "th"}
        self.colors = _colors(theme)
        self.cites: list[list[str]] = []  # per slide
        self.bindings: list[dict] = []

    # ── theme layer ───────────────────────────────────────────────────
    def _text(self, slide, x, y, w, h, text, size, color, bold=False, align=PP_ALIGN.LEFT):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        pg.add_text(box, text, self.font, size, bold, color, align)
        return box

    def _chrome(self, slide, n: int, cites: list[str]):
        p = self.p
        mark = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.7), Inches(0.28), Inches(0.42), Inches(0.05))
        pg.set_shape_fill(mark, p["accent"])
        mark.line.fill.background()
        rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.5), Inches(5.18), Inches(9.0), Inches(0.01))
        pg.set_shape_fill(rule, p["rule"])
        rule.line.fill.background()
        note = self.m["source_note"]
        if cites:
            note += "  ·  อ้างอิง " + ", ".join(cites[:8]) + (" …" if len(cites) > 8 else "")
        self._text(slide, 0.5, 5.2, 8.2, 0.3, note, 7.5, p["muted"])
        self._text(slide, 8.7, 5.2, 0.8, 0.3, str(n), 8, p["muted"], align=PP_ALIGN.RIGHT)

    def _restyle_fonts(self, slide):
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for r in para.runs:
                        r.font.name = self.font
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        for para in cell.text_frame.paragraphs:
                            for r in para.runs:
                                r.font.name = self.font
            if getattr(shape, "has_chart", False) and shape.has_chart:
                ch = shape.chart
                ch.font.name = self.font
                ch.font.size = Pt(10)
                ch.font.color.rgb = RGBColor.from_string(self.p["ink"])

    def _fit(self, slide):
        worst = 0.0
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                worst = max(worst, shrink_to_fit(shape, face=self.font))
        return worst

    def _finish(self, slide, cites: list[str], chrome=True):
        n = len(self.prs.slides)
        self._restyle_fonts(slide)
        if chrome:
            self._chrome(slide, n, cites)
            self._restyle_fonts(slide)
        self._fit(slide)
        self.cites.append(cites)

    # ── slides ────────────────────────────────────────────────────────
    def title(self):
        p = self.p
        pg.build_title_slide(self.prs, {"title": "ผลประกอบการ", "subtitle": self.m["subtitle"].replace("ประจำเดือน", ""),
                                        "author": " · ".join(x for x in (self.m["company"], "ประชุมผู้บริหาร") if x)},
                             self.settings, self.colors)
        s = self.prs.slides[-1]
        if self.m["company"]:
            self._text(s, 1.0, 0.7, 8.0, 0.4, self.m["company"], 12, p["gold"], bold=True)
        self._text(s, 1.0, 4.55, 8.0, 0.35, self.m["prepared"] + " · " + " · ".join(self.m["cover_lines"]),
                   10, "C9D3E3")
        self._finish(s, [], chrome=False)

    def stats(self):
        k = self.m["kpis"]
        pg.build_stats_slide(self.prs, {"title": "ตัวเลขสำคัญ",
                                        "stats": [{"value": x["value_short"].replace(" ล้าน", "M"), "label": x["label"],
                                                   "color": self.p["primary"]} for x in k]},
                             self.settings, self.colors)
        s = self.prs.slides[-1]
        # The stock builder sets the big number at 40 pt; four KPIs across need 30.
        for sh in s.shapes:
            if sh.has_text_frame:
                for para in sh.text_frame.paragraphs:
                    for r in para.runs:
                        if r.font.size and r.font.size.pt >= 40:
                            r.font.size = Pt(30)
        n = len(k)
        card_w = (9.0 - 0.3 * (n - 1)) / n
        for i, kp in enumerate(k):
            x = 0.5 + i * (card_w + 0.3)
            if kp["delta"]:
                color = self.p["muted"] if kp["good"] is None else (self.p["positive"] if kp["good"] else self.p["negative"])
                arrow = {"up": "▲ ", "down": "▼ ", "flat": ""}[kp["tone"]]
                self._text(s, x + 0.15, 3.85, card_w - 0.3, 0.5, arrow + kp["delta"], 11, color, align=PP_ALIGN.CENTER)
            self._text(s, x + 0.15, 4.3, card_w - 0.3, 0.3, kp["value"] if kp["key"] != "net_margin" else "", 9,
                       self.p["muted"], align=PP_ALIGN.CENTER)
        self._finish(s, [kp["cite"] for kp in k])

    def summary(self):
        nar = self.m["narrative"]
        watch = (nar.get("watch", [])[:2] + nar.get("movements", [])[:2])[:4]
        pg.build_content_slide(self.prs, {"title": "สรุปผู้บริหาร", "body": nar["summary"], "bullets": watch},
                               {**self.settings, "font": {**self.settings["font"], "body_size": 13}}, self.colors)
        s = self.prs.slides[-1]
        # The stock body box is one inch tall; give the summary the room it needs.
        shapes = [sh for sh in s.shapes if sh.has_text_frame]
        if len(shapes) >= 2:
            body = shapes[1]
            body.height = Inches(1.55)
            if len(shapes) >= 3:
                shapes[2].top = Inches(3.0)
                shapes[2].height = Inches(2.0)
        self._finish(s, sorted({c for c in self.c["quotable_cites"].values()} & self._cites_in(nar["summary"]))[:6])

    def _cites_in(self, text: str) -> set[str]:
        from .content import mark_citations

        return {c for _, c in mark_citations(text, self.c) if c}

    def chart(self, spec: dict):
        scale = Decimal(str(spec.get("scale", 1)))
        values = [[float((Decimal(v) / scale).quantize(Decimal("0.01"))) for v in s["values"]] for s in spec["series"]]
        kind = {"line": "line", "barh": "bar_horizontal"}.get(spec["kind"], "bar")
        cats = spec["categories"]
        if kind == "line" and len(cats) <= 3:
            kind = "bar"  # two or three months read better as columns (same rule as the report's chart)
        if kind == "bar_horizontal":
            cats = list(reversed(cats))
            values = [list(reversed(v)) for v in values]
        pg.build_chart_slide(self.prs, {"title": spec["title"], "chart_type": kind,
                                        "data": {"categories": cats,
                                                 "series": [{"name": s["name"], "values": v}
                                                            for s, v in zip(spec["series"], values)]}},
                             self.settings, self.colors)
        s = self.prs.slides[-1]
        ch = next(sh for sh in s.shapes if getattr(sh, "has_chart", False) and sh.has_chart)
        ch.top = Inches(1.25)
        ch.height = Inches(3.75)
        palette = [self.p["accent"], self.p["gold"], self.p["positive"], self.p["negative"]]
        plot = ch.chart.plots[0]
        for i, ser in enumerate(plot.series):
            color = RGBColor.from_string(palette[i % len(palette)])
            if kind == "line":
                ser.format.line.color.rgb = color
                ser.format.line.width = Pt(2.5)
                ser.smooth = False
            else:
                ser.format.fill.solid()
                ser.format.fill.fore_color.rgb = color
                # PowerPoint draws a negative bar hollow unless told not to
                # (the under-budget bars came out white in the walkthrough).
                ser.invert_if_negative = False
        if kind != "line":
            plot.has_data_labels = True
            dl = plot.data_labels
            dl.font.size = Pt(9)
            dl.font.name = self.font
            dl.number_format = "#,##0.00" if scale != 1 else "#,##0"
            dl.number_format_is_linked = False
        from pptx.enum.chart import XL_TICK_LABEL_POSITION

        chart = ch.chart
        chart.has_title = False  # the slide title names the chart; a one-series chart would repeat the series name
        try:
            # Category labels at the low edge: a negative bar never runs into its own label.
            chart.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
            chart.value_axis.tick_labels.number_format = "#,##0.0" if scale != 1 else "#,##0"
            chart.value_axis.tick_labels.number_format_is_linked = False
            chart.value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string(self.p["rule"])
        except (AttributeError, ValueError):
            pass
        if spec.get("signed") and kind == "bar_horizontal":
            ser = plot.series[0]
            for idx, v in enumerate(values[0]):
                pt = ser.points[idx]
                pt.format.fill.solid()
                pt.format.fill.fore_color.rgb = RGBColor.from_string(self.p["negative"] if v > 0 else self.p["positive"])
                # a point with its own colour (<c:dPt>) inverts on its own when
                # negative: say no right after its <c:idx>, as the schema orders it
                dpt = ser._element.get_or_add_dPt_for_point(idx)
                if dpt.find(qn("c:invertIfNegative")) is None:
                    inv = OxmlElement("c:invertIfNegative")
                    inv.set("val", "0")
                    dpt.find(qn("c:idx")).addnext(inv)
        if spec.get("subtitle"):
            self._text(s, 0.7, 0.95, 8.6, 0.3, spec["subtitle"], 10, self.p["muted"])
        self.bindings.append({"slide": len(self.prs.slides), "chart": spec["id"], "categories": cats,
                              "series": [{"name": sv["name"], "values": v, "path": sv.get("path"),
                                          "source_values": sv["values"], "reversed": kind == "bar_horizontal"}
                                         for sv, v in zip(spec["series"], values)],
                              "scale": str(scale)})
        self._finish(s, [spec["cite"]] if spec.get("cite") else [])

    def table(self, title: str, headers: list[str], rows: list[list[str]], cites: list[str], numeric_from=1,
              widths: list[float] | None = None):
        pg.build_table_slide(self.prs, {"title": title, "headers": headers, "rows": rows}, self.settings, self.colors)
        s = self.prs.slides[-1]
        tbl_shape = next(sh for sh in s.shapes if getattr(sh, "has_table", False) and sh.has_table)
        tbl = tbl_shape.table
        tbl_shape.top = Inches(1.15)
        if widths:
            total = sum(widths)
            for j, w in enumerate(widths):
                tbl.columns[j].width = int(Inches(9.0) * w / total)
        n_rows = len(tbl.rows)
        size = 11 if n_rows <= 8 else 10 if n_rows <= 11 else 9
        for i, row in enumerate(tbl.rows):
            row.height = Inches(0.34 if n_rows <= 10 else 0.3)
            for j, cell in enumerate(row.cells):
                cell.margin_top = cell.margin_bottom = Inches(0.03)
                for para in cell.text_frame.paragraphs:
                    if j >= numeric_from:
                        para.alignment = PP_ALIGN.RIGHT
                    for r in para.runs:
                        r.font.size = Pt(size)
                        if i > 0 and r.text.startswith("("):
                            r.font.color.rgb = RGBColor.from_string(self.p["negative"])
        self._finish(s, cites)

    def bullets(self, title: str, items: list[str], cites: list[str], body: str = ""):
        pg.build_content_slide(self.prs, {"title": title, "body": body, "bullets": items[:6]},
                               {**self.settings, "font": {**self.settings["font"], "body_size": 14}}, self.colors)
        s = self.prs.slides[-1]
        self._finish(s, cites)

    def two_column(self, title: str, left: tuple[str, list[str]], right: tuple[str, list[str]], cites):
        pg.build_two_column_slide(self.prs, {"title": title, "left": {"heading": left[0], "bullets": left[1]},
                                             "right": {"heading": right[0], "bullets": right[1]}},
                                  {**self.settings, "font": {**self.settings["font"], "body_size": 11}}, self.colors)
        s = self.prs.slides[-1]
        self._finish(s, cites)

    # ── assemble ──────────────────────────────────────────────────────
    def write(self, path: str) -> dict:
        c, m = self.c, self.m
        self.title()
        self.stats()
        self.summary()
        charts = {ch["id"]: ch for ch in m["charts"]}
        if "trend" in charts:
            self.chart(charts["trend"])
        lines = {l["key"]: l for l in c["lines"]}
        heads = ["รายการ", "งวดนี้"] + (["งวดก่อน", "%"] if c["has_prior"] else []) + (["งบประมาณ"] if c["has_budget"] else [])
        rows = []
        for key in ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "total_expenses", "net_profit"):
            l = lines[key]
            r = [l["label"], money0(l["current"])]
            if c["has_prior"]:
                r += [money0(l["prior"]), pct_or_dash(l["var_prior_pct"])]
            if c["has_budget"]:
                r += [money0(l["budget"])]
            rows.append(r)
        self.table("งบกำไรขาดทุนโดยย่อ", heads, rows, [lines[k]["cite"] for k in ("revenue", "total_expenses", "net_profit")],
                   widths=[2.6] + [1.3] * (len(heads) - 1))
        if "budget_variance" in charts:
            self.chart(charts["budget_variance"])
        if "composition" in charts:
            self.chart(charts["composition"])
        if c["top_movements"]:
            heads = ["บัญชี", "งวดนี้"] + (["งวดก่อน", "เปลี่ยนแปลง", "%"] if c["has_prior"] else [])
            rows = []
            for mv in c["top_movements"][:6]:
                r = [f"{mv['code']} {mv['name']}".strip(), money0(mv["current"])]
                if c["has_prior"]:
                    r += [money0(mv["prior"]), money0(mv["change"]), pct_or_dash(mv["change_pct"])]
                rows.append(r)
            self.table("รายการเคลื่อนไหวสำคัญ", heads, rows, [mv["cite"] for mv in c["top_movements"][:6]],
                       widths=[3.2] + [1.2] * (len(heads) - 1))
        exc = c["exceptions"]
        from .content import EXC_LABEL

        # what kind of item, in Thai (the code is in the full report's รหัสเหตุผล column, for whoever maintains it)
        items = [f"{EXC_LABEL.get(e['code'], 'ต้องตรวจ')}: {e['title']} — {e['detail']}" for e in exc[:5]] or [
            "ไม่พบรายการผิดปกติตามกฎที่ตั้งไว้"]
        self.bullets(f"รายการที่ต้องตรวจ ({len(exc)})", items, [e["cite"] for e in exc[:5] if e.get("cite")],
                     body="ระบบไม่แก้ตัวเลขเอง ทุกข้อมีแถวต้นทางในรายงานฉบับเต็ม")
        nar = m["narrative"]
        self.bullets("สิ่งที่ควรทำต่อ", nar.get("next_steps", [])[:5], [])
        assumptions = [a["text"] for a in m["assumptions"]][:6]
        model = m.get("model") or {}
        about = [f"ไฟล์หลัก: {c['source']['primary']['file']} ({c['source']['primary']['rows_used']:,} รายการ)",
                 f"คำนวณด้วย {c['script']} (Decimal)",
                 "โมเดลที่เขียนคำอธิบาย: " + (model.get("model") or "ไม่มี (ข้อความจากกฎ)")
                 + (" · รันในองค์กร" if model.get("self_hosted") else ""),
                 "อ่านอย่างเดียว: ไม่เขียนกลับระบบบัญชี ไม่ส่งอีเมล ไม่โอนเงิน"]
        self.two_column("ข้อสมมติและที่มาของข้อมูล", ("ข้อสมมติในรอบนี้", assumptions), ("ที่มาและขอบเขต", about), [])
        props = self.prs.core_properties
        props.title = f"{m['title']} {m['subtitle']}"
        props.author = m["company"] or "SRAN Agent"
        self.prs.save(path)
        return {"path": path, "slides": len(self.prs.slides), "chart_bindings": self.bindings, "slide_cites": self.cites}
