"""The quality gate of the financial report: the finished files, opened again and read back.

A run is not "succeeded" until its files pass. Every figure check below opens the
delivered file, reads the figure out of it, and compares it with computed.json —
never with what the writer said it wrote (AM8: a copy with the revenue cell
deleted, a VAT of 999,999 and a chart redrawn at 99.99 million with matching
metadata all passed the earlier gate, which trusted the writer's own record).

What each check asserts, and nothing more:

* รายงาน (.docx)
  - ``docx.valid``: the shared validator opens it without errors.
  - ``docx.fonts``: every font named anywhere in the package (body, headers,
    footers, footnotes, styles) is the theme's; every Thai run names a
    complex-script font; the theme font is embedded. Not checked: how a
    reader's Word substitutes or shapes it.
  - ``docx.sections``: every required section has a heading and content.
  - ``docx.figures``: the P&L table, the KPI tiles and the table under every
    chart, read back cell by cell, equal the computed figures; an empty cell
    where a figure belongs fails.
  - ``docx.charts``: each chart picture in the file is, byte for byte, the
    picture of the computed numbers drawn again here (the gate does not read
    pixels; it re-draws and compares).
  - ``docx.narrative``: every figure in the written sentences is a computed
    figure followed by its own citation; no other figure appears in them.
  - ``citations``: every citation the report prints resolves to a source
    (file · sheet · cells, or the citations it is computed from).
  - ``assumptions``: the assumptions list is not empty, and lists a defaulted
    answer when the run had one.
* สไลด์ (.pptx)
  - ``pptx.valid``; ``pptx.slide_count`` (8–12); ``pptx.overflow`` (an
    estimate from font metrics, not a render); ``pptx.fonts``.
  - ``pptx.figures``: every chart's categories and plotted values (read from
    the chart in the file), the P&L table and the KPI values equal the
    computed ones; the summary slide has no figure that is not computed.
* ตาราง (.xlsx)
  - ``xlsx.sheets``; ``xlsx.figures``: every P&L line and ratio on สรุป and
    every account on รายบัญชี, cell by cell; every citation on อ้างอิง with
    its value; every ledger row on รายการที่ใช้ and its totals.

Each check's detail says how many values it compared. Not checked anywhere:
page rendering, Thai line breaking on the reader's machine, layout beyond the
overflow estimate, or whether the wording is good.

Reuses doc_validator.py and pptx_inspector.py by import.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from decimal import Decimal

import doc_validator  # /sandbox/scripts — shared, imported, not modified
import pptx_inspector  # /sandbox/scripts — shared, imported, not modified

from . import content as content_mod
from .content import REQUIRED_SECTIONS, money0, pct_or_dash
from .fit import frame_overflow
from .reread import CENT
from .reread import Tally as _Tally
from .reread import check as _check
from .reread import dec as _dec
from .reread import squash as _squash

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# A figure as a sentence prints it: 12,480,300 · (41,730) · 10.5% · 12.48 ล้าน · +0.95 จุด
NUMBER = re.compile(r"[-+]?\(?\d[\d,]*(?:\.\d+)?\)?(?:\s?%|\s?ล้าน|\s?จุด)?")


# ── expected figures, from computed.json itself ───────────────────────────

def resolve(computed: dict, path: str, codes: list[str] | None = None) -> list[str]:
    """A chart series' values, looked up in computed.json by its path (not taken from the chart spec)."""
    if path.startswith("trend."):
        return list(computed["trend"][path.split(".", 1)[1]])
    if path == "accounts[].var_budget":
        by = {a["code"]: a for a in computed["accounts"]}
        return [by[c]["var_budget"] for c in codes or []]
    if path == "composition[].amount":
        return [c["amount"] for c in computed["composition"]]
    raise KeyError(path)


def expected_charts(computed: dict) -> list[dict]:
    """The charts the report must draw, each series' values resolved in computed.json."""
    specs = content_mod.charts(computed)
    for spec in specs:
        for s in spec["series"]:
            want = resolve(computed, s["path"], s.get("codes"))
            if [str(_dec(x)) for x in want] != [str(_dec(x)) for x in s["values"]]:
                raise AssertionError(f"chart {spec['id']} series {s['name']} is not computed.json's {s['path']}")
    return specs


def _scaled(v, scale) -> Decimal:
    return (Decimal(str(v)) / Decimal(str(scale))).quantize(CENT)


# ── .docx ─────────────────────────────────────────────────────────────────

def _docx_fonts(path: str, allowed: set[str]) -> tuple[set[str], int]:
    """Font names in every part of the package, and Thai runs without a complex-script font."""
    bad: set[str] = set()
    thai_no_cs = 0
    with zipfile.ZipFile(path) as z:
        # Every part that carries text or its styles. numbering.xml is left out: its fonts draw the
        # list markers (Symbol's bullet), not text.
        parts = [n for n in z.namelist() if n.startswith("word/") and n.endswith(".xml")
                 and not n.startswith(("word/fonts/", "word/theme/", "word/_rels/", "word/numbering"))]
        for name in parts:
            xml = z.read(name).decode("utf-8", "replace")
            for attrs in re.findall(r"<w:rFonts\b([^>]*)/?>", xml):
                for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
                    m = re.search(rf'\bw:{attr}="([^"]*)"', attrs)
                    if m and m.group(1) not in allowed:
                        bad.add(m.group(1))
            if name.startswith(("word/document", "word/header", "word/footer", "word/footnotes", "word/endnotes")):
                for run in re.findall(r"<w:r\b.*?</w:r>", xml, re.S):
                    text = "".join(re.findall(r"<w:t\b[^>]*>(.*?)</w:t>", run, re.S))
                    if any("฀" <= ch <= "๿" for ch in text):
                        fonts = re.search(r"<w:rFonts\b([^>]*)/?>", run)
                        if not fonts or 'w:cs="' not in fonts.group(1):
                            thai_no_cs += 1
    return bad, thai_no_cs


def _table_text(t) -> list[list[str]]:
    return [[c.text for c in row.cells] for row in t.rows]


def _find_table(tables, header: list[str]):
    want = [_squash(h) for h in header]
    for t in tables:
        rows = _table_text(t)
        if rows and [_squash(x) for x in rows[0]] == want:
            return rows
    return None


def _compare_table(tally: _Tally, where: str, got: list[list[str]] | None, want: dict, skip_cols=()) -> None:
    if got is None:
        tally.n += 1
        tally.bad.append(f"ไม่พบตาราง{where}ในไฟล์")
        return
    body = got[1:]
    if len(body) != len(want["rows"]):
        tally.n += 1
        tally.bad.append(f"ตาราง{where}: ในไฟล์ {len(body)} แถว ควรมี {len(want['rows'])} แถว")
    for i, (g, w) in enumerate(zip(body, want["rows"])):
        for j, (gc, wc) in enumerate(zip(g, w)):
            if j in skip_cols:
                continue
            tally.text(f"ตาราง{where} แถว “{w[0]}” คอลัมน์ “{want['columns'][j]}”", gc, "" if wc is None else wc)


def _narrative_check(doc, model: dict, computed: dict) -> dict:
    """Every figure in the written sentences is a computed figure, and the citation right after it is that
    figure's own. Read from the file's runs: a figure is a run's trailing number, its cite the next
    superscript run."""
    figures = computed.get("figures") or {}
    by_cite: dict[str, set[str]] = {}
    for f in figures.values():
        for form in (f["text"], content_mod.figure_text(f, "abs")):
            by_cite.setdefault(f.get("cite") or "", set()).add(_squash(form))
    ids = set(computed.get("quotable_ids") or [])
    titles = {s["key"]: s["title"] for s in model["sections"]}
    narrative_titles = {titles.get(k) for k in ("summary", "movements", "insights")} - {None}
    n, bad = 0, []
    section = None
    for el in doc.element.body.iterchildren():
        tag = el.tag.split("}")[-1]
        if tag != "p":
            continue
        style = el.find(f"{W_NS}pPr/{W_NS}pStyle")
        sval = style.get(W_NS + "val", "") if style is not None else ""
        text_all = "".join(t.text or "" for t in el.iter(W_NS + "t")).strip()
        if sval.startswith("Heading1"):
            section = text_all
            continue
        if section not in narrative_titles or sval.startswith("Heading"):
            continue
        runs = []
        for r in el.iter(W_NS + "r"):
            t = "".join(x.text or "" for x in r.iter(W_NS + "t"))
            sup = r.find(f"{W_NS}rPr/{W_NS}vertAlign")
            runs.append((t, sup is not None and sup.get(W_NS + "val") == "superscript"))
        plain = ""
        for i, (t, sup) in enumerate(runs):
            if sup:
                continue
            nxt = runs[i + 1] if i + 1 < len(runs) else ("", False)
            cite = nxt[0].strip() if nxt[1] else ""
            body = t
            if cite:
                m = re.search(r"([-+]?\(?\d[\d,]*(?:\.\d+)?\)?(?:\s?%|\s?ล้าน|\s?จุด)?)\s*$", t)
                n += 1
                if not m or _squash(_balanced(m.group(1))) not in by_cite.get(cite, set()):
                    bad.append(f"“{t.strip()[-30:]}” อ้าง {cite} แต่ตัวเลขนี้ไม่ใช่ของ {cite}")
                else:
                    body = t[:m.start()]
            plain += body + " "
        for tok in NUMBER.findall(_blank_ids(plain, ids)):
            tok = _balanced(tok)
            digits = tok.strip("+-()% ล้านจุด").replace(",", "")
            if "." not in digits and digits.isdigit() and (int(digits) <= 31 or 1990 <= int(digits) <= 2100
                                                          or 2400 <= int(digits) <= 2700):
                continue
            n += 1
            bad.append(f"ตัวเลข “{tok.strip()}” ในคำอธิบายไม่มีที่มา")
        if "{" in plain and content_mod.FIGURE.search(plain):
            n += 1
            bad.append("มีตัวเลขที่อ้างชื่อซึ่งไม่มีในตารางที่คำนวณ: " + content_mod.FIGURE.search(plain).group(0))
    if bad:
        return _check("docx.narrative", False, "; ".join(bad[:4]), "ตัวเลขในคำอธิบายมาจากตารางที่คำนวณพร้อมที่มาของตัวมันเอง", n)
    return _check("docx.narrative", True, f"ตัวเลขในคำอธิบาย {n} ตัว แต่ละตัวมีที่มาของตัวเลขนั้นเอง",
                  "ตัวเลขในคำอธิบายมาจากตารางที่คำนวณพร้อมที่มาของตัวมันเอง", n)


def _balanced(tok: str) -> str:
    """Only a balanced "(1,234)" is an accounting negative; "(12.48 ล้าน" is a figure inside ordinary
    parentheses (the runner's guard reads it the same way)."""
    tok = tok.strip()
    if tok.startswith("(") and not tok.endswith(")"):
        return tok[1:]
    if tok.endswith(")") and not tok.startswith("("):
        return tok[:-1]
    return tok


def _blank_ids(text: str, ids: set[str]) -> str:
    for i in sorted(ids, key=len, reverse=True):
        if i and any(ch in i for ch in "-./"):
            text = text.replace(i, " ")
        elif i:
            text = re.sub(rf"(บัญชี|รหัส)\s*{re.escape(i)}(?![0-9.,])", r"\1 ", text)
    return text


def check_docx(path: str, model: dict, theme: dict, req: dict) -> list[dict]:
    from docx import Document

    computed = model["computed"]
    out = []
    v = doc_validator.validate(path)
    out.append(_check("docx.valid", v.get("valid") and not v.get("errors"), "; ".join(v.get("errors") or []),
                      "ไฟล์เปิดได้และโครงสร้างถูกต้อง"))
    doc = Document(path)
    allowed = set(theme["fonts"].values())
    bad_fonts, thai_no_cs = _docx_fonts(path, allowed)
    normal = doc.styles["Normal"].element.rPr.find(W_NS + "rFonts")
    base_ok = normal is not None and all(normal.get(W_NS + a) in allowed for a in ("ascii", "hAnsi", "cs"))
    with zipfile.ZipFile(path) as z:
        embedded = [n for n in z.namelist() if n.startswith("word/fonts/")]
    problems = []
    if not base_ok:
        problems.append("ฟอนต์หลักของเอกสารไม่ใช่ฟอนต์ของธีม")
    if bad_fonts:
        problems.append("มีฟอนต์นอกธีม: " + ", ".join(sorted(bad_fonts)))
    if thai_no_cs:
        problems.append(f"ข้อความไทย {thai_no_cs} ช่วงไม่ได้กำหนดฟอนต์ไทย")
    if not embedded:
        problems.append("ไม่ได้ฝังฟอนต์ในไฟล์")
    out.append(_check("docx.fonts", not problems,
                      " · ".join(problems) if problems else
                      f"ทุกส่วนของไฟล์ (เนื้อหา หัว/ท้ายกระดาษ สไตล์) ใช้ฟอนต์ของธีม · {theme['fonts']['thai']} ฝังในไฟล์ {len(embedded)} ไฟล์",
                      "ฟอนต์ตามธีมทุกส่วนของไฟล์และฝังฟอนต์ไทย"))

    # Required sections present and non-empty (heading followed by content).
    headings = []
    for el in doc.element.body.iterchildren():
        tag = el.tag.split("}")[-1]
        if tag == "p":
            style = el.find(f"{W_NS}pPr/{W_NS}pStyle")
            text = "".join(t.text or "" for t in el.iter(W_NS + "t")).strip()
            if style is not None and style.get(W_NS + "val", "").startswith("Heading1"):
                headings.append([text, 0])
            elif text and headings:
                headings[-1][1] += 1
        elif tag == "tbl" and headings:
            headings[-1][1] += 1
    titles = {s["key"]: s["title"] for s in model["sections"]}
    missing = [k for k in REQUIRED_SECTIONS
               if titles.get(k) is None or not any(h[0] == titles[k] and h[1] for h in headings)]
    out.append(_check("docx.sections", not missing, "ส่วนที่ว่างหรือหายไป: " + ", ".join(missing) if missing else
                      f"{len(REQUIRED_SECTIONS)} ส่วนที่ต้องมีครบ", "ทุกส่วนที่ต้องมีไม่ว่าง"))

    # The figures, read back out of the file's tables.
    tally = _Tally()
    _compare_table(tally, "งบกำไรขาดทุน", _find_table(doc.tables, content_mod.pl_table(computed)["columns"]),
                   content_mod.pl_table(computed))
    tiles = next((t for t in doc.tables if len(t.rows) == 1 and len(t.columns) == len(model["kpis"])
                  and all(k["label"] in _squash(c.text) or _squash(k["label"]) in _squash(c.text)
                          for k, c in zip(model["kpis"], t.rows[0].cells))), None)
    for i, k in enumerate(content_mod.kpis(computed)):
        tally.n += 1
        cell = _squash(tiles.rows[0].cells[i].text) if tiles is not None else ""
        if _squash(k["value"]) not in cell or f"[{k['cite']}]" not in cell:
            tally.bad.append(f"ตัวเลขสำคัญ “{k['label']}”: ควรเป็น {k['value']} [{k['cite']}]")
    specs = expected_charts(computed)
    for spec in specs:
        _compare_table(tally, f"ใต้กราฟ “{spec['title']}”", _find_table(doc.tables, content_mod.chart_table(spec)["columns"]),
                       content_mod.chart_table(spec))
    out.append(tally.result("docx.figures", "ตัวเลขในรายงาน (อ่านจากไฟล์) ตรงกับที่คำนวณ", "ตัวเลขในตาราง"))
    out.append(_chart_pictures(path, specs, theme, has_logo=bool(req.get("logo"))))
    out.append(_narrative_check(doc, model, computed))
    return out


def _chart_pictures(path: str, specs: list[dict], theme: dict, has_logo: bool) -> dict:
    """Each chart picture in the file is the picture of the computed numbers: drawn again, compared."""
    from .charts import render_png

    with zipfile.ZipFile(path) as z:
        media = [z.read(n) for n in z.namelist() if n.startswith("word/media/")]
    missing = []
    with tempfile.TemporaryDirectory() as tmp:
        for spec in specs:
            p = os.path.join(tmp, f"{spec['id']}.png")
            render_png(spec, theme, p)
            with open(p, "rb") as f:
                if f.read() not in media:
                    missing.append(spec["title"])
    extra = len(media) - len(specs) - (1 if has_logo else 0)
    ok = not missing and extra <= 0
    detail = (("กราฟในไฟล์ไม่ใช่กราฟของตัวเลขที่คำนวณ: " + ", ".join(missing)) if missing else
              (f"มีภาพในไฟล์เกินมา {extra} ภาพ" if extra > 0 else
               f"วาดกราฟ {len(specs)} ภาพใหม่จากตัวเลขที่คำนวณ ภาพในไฟล์ตรงทุกภาพ"))
    return _check("docx.charts", ok, detail, "กราฟในรายงานคือกราฟของตัวเลขที่คำนวณ", len(specs))


def check_citations(model: dict, computed: dict) -> dict:
    """Every citation the report prints resolves to a source (file · sheet · cells, or the ones it is computed from)."""
    ids = {c["id"]: c for c in computed["citations"]}
    printed: list[str] = []
    blank: list[str] = []
    for sec in model["sections"]:
        for b in sec["blocks"]:
            if b["type"] == "table" and b.get("cite_col") is not None and not sec.get("appendix"):
                for row in b["rows"]:
                    cell = [c.strip() for c in str(row[b["cite_col"]] or "").split(",") if c.strip()]
                    has_number = any(isinstance(v, str) and any(ch.isdigit() for ch in v)
                                     for j, v in enumerate(row) if j not in (b["cite_col"], 0))
                    if has_number and not cell:
                        blank.append(f"{sec['title']}: {row[0]}")  # a figure with no citation at all
                    printed += cell
            elif b["type"] == "paragraph":
                printed += [c for _, c in b["runs"] if c]
            elif b["type"] == "bullets":
                printed += [c for runs in b["items"] for _, c in runs if c]
    printed += [k["cite"] for k in model["kpis"]]
    bad = sorted({c for c in printed if not (c in ids and (ids[c]["sources"] or ids[c].get("derived_from"))
                                             and ids[c]["script"] and ids[c]["formula"])})
    return _check("citations", not bad and not blank and bool(printed),
                  (f"เลขอ้างอิงที่ไม่มีที่มา: {', '.join(bad[:6])}" if bad else
                   f"ตัวเลขที่ไม่มีเลขอ้างอิง: {', '.join(blank[:6])}" if blank else
                   f"เลขอ้างอิง {len(set(printed))} รายการที่พิมพ์ในรายงาน มีที่มาทุกรายการ"),
                  "ทุกเลขอ้างอิงในรายงานบอกที่มาได้", len(set(printed)))


def check_assumptions(model: dict, req: dict) -> dict:
    defaulted = ("default", "reprompt_default", "skip")
    had_defaults = any(a.get("source") in defaulted for a in req.get("assumptions") or [])
    listed = list(model["assumptions"])
    ok = bool(listed) and (not had_defaults or any(a.get("source") in defaulted for a in listed))
    return _check("assumptions", ok, f"{len(listed)} ข้อ" + (" · รวมค่าตั้งต้นที่ใช้ในรอบนี้" if had_defaults else ""),
                  "มีรายการข้อสมมติ และบอกค่าตั้งต้นที่ใช้")


# ── .pptx ─────────────────────────────────────────────────────────────────

def _slide_text(slide) -> str:
    parts = []
    for sh in slide.shapes:
        if sh.has_text_frame:
            parts.append(sh.text_frame.text)
    return "\n".join(parts)


def check_pptx(path: str, deck: dict, computed: dict, theme: dict) -> list[dict]:
    """``deck`` (what the writer returned) is not read for any figure: the figures come out of the file."""
    from pptx import Presentation

    out = []
    v = doc_validator.validate(path)
    out.append(_check("pptx.valid", v.get("valid") and not v.get("errors"), "; ".join(v.get("errors") or []),
                      "ไฟล์เปิดได้และโครงสร้างถูกต้อง"))
    info = pptx_inspector.inspect_pptx(path)
    n = info.get("slide_count", 0)
    out.append(_check("pptx.slide_count", 8 <= n <= 12, f"{n} สไลด์", "จำนวนสไลด์ 8–12"))
    prs = Presentation(path)
    allowed = set(theme["fonts"].values())
    overflows = []
    bad_fonts = set()
    for i, slide in enumerate(prs.slides, start=1):
        for sh in slide.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                r = frame_overflow(sh.text_frame, sh.width, sh.height, theme["fonts"]["thai"])
                if r > 1.02:
                    overflows.append(f"สไลด์ {i}: “{sh.text_frame.text[:30]}” ({r:.2f})")
                for p in sh.text_frame.paragraphs:
                    for run in p.runs:
                        if run.font.name and run.font.name not in allowed:
                            bad_fonts.add(run.font.name)
                        if not run.font.name:
                            bad_fonts.add("(inherit)")
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    for cell in row.cells:
                        for p in cell.text_frame.paragraphs:
                            for run in p.runs:
                                if run.font.name not in allowed:
                                    bad_fonts.add(run.font.name or "(inherit)")
            if getattr(sh, "has_chart", False) and sh.has_chart:
                if sh.chart.font.name not in allowed:
                    bad_fonts.add(sh.chart.font.name or "(chart default)")
    out.append(_check("pptx.overflow", not overflows, "; ".join(overflows[:4]) or "ไม่มี (ประมาณจากขนาดตัวอักษร ไม่ได้เรนเดอร์)",
                      "ไม่มีข้อความล้นสไลด์ (ประมาณการ)"))
    fonts_used = set(info.get("fonts_used") or []) - allowed
    out.append(_check("pptx.fonts", not bad_fonts and not fonts_used,
                      f"นอกธีม: {sorted(bad_fonts | fonts_used)}" if (bad_fonts or fonts_used) else
                      f"{theme['fonts']['thai']} ทุกข้อความ", "ฟอนต์ตามธีมทุกข้อความ"))

    tally = _Tally()
    # Charts: every chart in the file is one of the computed charts, every computed chart is there once.
    want = expected_charts(computed)
    found: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        for sh in slide.shapes:
            if not (getattr(sh, "has_chart", False) and sh.has_chart):
                continue
            plot = sh.chart.plots[0]
            cats = [str(c.label if hasattr(c, "label") else c) for c in plot.categories]
            series = [[_dec(x) for x in s.values] for s in plot.series]
            match = None
            for spec in want:
                rev = spec["kind"] == "barh"
                w_cats = list(reversed(spec["categories"])) if rev else list(spec["categories"])
                w_series = [[_scaled(x, spec["scale"]) for x in (reversed(s["values"]) if rev else s["values"])]
                            for s in spec["series"]]
                if [_squash(c) for c in cats] == [_squash(c) for c in w_cats] and series == w_series:
                    match = spec
                    break
            tally.n += sum(len(s) for s in series)
            if match is None:
                tally.bad.append(f"สไลด์ {i}: กราฟในไฟล์ไม่ตรงกับตัวเลขที่คำนวณ")
            else:
                found.append(match["id"])
    for spec in want:
        if found.count(spec["id"]) != 1:
            tally.n += 1
            tally.bad.append(f"กราฟ “{spec['title']}” {'ไม่มีในไฟล์' if not found.count(spec['id']) else 'ซ้ำในไฟล์'}")
    # The summary P&L table.
    c = computed
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
    tables = [[[cell.text for cell in row.cells] for row in sh.table.rows] for s in prs.slides for sh in s.shapes
              if getattr(sh, "has_table", False) and sh.has_table]
    pl = next((t for t in tables if t and [_squash(x) for x in t[0]] == [_squash(h) for h in heads]), None)
    _compare_table(tally, "งบกำไรขาดทุนโดยย่อ", pl, {"columns": heads, "rows": rows})
    # KPI values on the key-figures slide.
    kslide = next((s for s in prs.slides if "ตัวเลขสำคัญ" in _slide_text(s)), None)
    ktext = _squash(_slide_text(kslide)) if kslide is not None else ""
    for k in content_mod.kpis(computed):
        tally.n += 1
        if _squash(k["value_short"].replace(" ล้าน", "M")) not in ktext:
            tally.bad.append(f"ตัวเลขสำคัญ “{k['label']}”: ควรเป็น {k['value_short']}")
    # The summary slide: no figure that is not a computed one.
    allowed_forms = set()
    for f in (computed.get("figures") or {}).values():
        allowed_forms |= {_squash(f["text"]), _squash(content_mod.figure_text(f, "abs"))}
    sslide = next((s for s in prs.slides if "สรุปผู้บริหาร" in _slide_text(s)), None)
    if sslide is not None:
        body = _blank_ids(_slide_text(sslide), set(computed.get("quotable_ids") or []))
        for tok in NUMBER.findall(body.split("ที่มา:")[0]):
            tok = _balanced(tok)
            digits = tok.strip("+-()% ล้านจุด").replace(",", "")
            if "." not in digits and digits.isdigit() and (int(digits) <= 31 or 1990 <= int(digits) <= 2100
                                                          or 2400 <= int(digits) <= 2700):
                continue
            tally.n += 1
            if _squash(tok) not in allowed_forms:
                tally.bad.append(f"สไลด์สรุป: ตัวเลข “{tok.strip()}” ไม่มีในตารางที่คำนวณ")
    out.append(tally.result("pptx.figures", "ตัวเลขและกราฟในสไลด์ (อ่านจากไฟล์) ตรงกับที่คำนวณ", "ตัวเลขและค่าในกราฟ"))
    return out


# ── .xlsx ─────────────────────────────────────────────────────────────────

def check_xlsx(path: str, computed: dict) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    need = {"สรุป", "รายบัญชี", "รายการที่ใช้", "ตรวจสอบ", "อ้างอิง"}
    out = [_check("xlsx.sheets", need <= set(wb.sheetnames), ", ".join(wb.sheetnames), "ชีตครบ")]
    tally = _Tally()
    if need <= set(wb.sheetnames):
        ws = wb["สรุป"]
        by_label = {}
        for row in ws.iter_rows(min_row=5, values_only=True):
            if row and row[0] not in (None, "") and row[0] not in by_label:
                by_label[row[0]] = row
        cols = ("current", "prior", "var_prior", "var_prior_pct", "budget", "var_budget")
        heads = ("งวดนี้", "งวดก่อน", "เปลี่ยนแปลง", "%", "งบประมาณ", "ต่างจากงบ")
        for l in computed["lines"]:
            row = by_label.get(l["label"])
            if row is None:
                tally.n += 1
                tally.bad.append(f"สรุป: ไม่มีแถว “{l['label']}”")
                continue
            for j, (k, h) in enumerate(zip(cols, heads), start=1):
                tally.number(f"สรุป “{l['label']}” {h}", row[j], l[k])
            tally.text(f"สรุป “{l['label']}” อ้างอิง", row[7], l["cite"])
        for rt in computed["ratios"]:
            row = by_label.get(rt["label"])
            if row is None:
                tally.n += 1
                tally.bad.append(f"สรุป: ไม่มีแถว “{rt['label']}”")
                continue
            tally.number(f"สรุป “{rt['label']}” งวดนี้", row[1], rt["current"])
            tally.number(f"สรุป “{rt['label']}” งวดก่อน", row[2], rt["prior"])
        ws = wb["รายบัญชี"]
        by_code = {str(r[0]): r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[0] not in (None, "")}
        for a in computed["accounts"]:
            row = by_code.get(str(a["code"]))
            if row is None:
                tally.n += 1
                tally.bad.append(f"รายบัญชี: ไม่มีบัญชี {a['code']}")
                continue
            for j, k in ((3, "current"), (4, "prior"), (5, "var_prior"), (6, "var_prior_pct"), (7, "budget"), (8, "var_budget")):
                tally.number(f"รายบัญชี {a['code']} คอลัมน์ {j + 1}", row[j], a[k])
        ws = wb["อ้างอิง"]
        by_id = {r[0]: r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[0]}
        for cit in computed["citations"]:
            row = by_id.get(cit["id"])
            if row is None:
                tally.n += 1
                tally.bad.append(f"อ้างอิง: ไม่มี {cit['id']}")
                continue
            if _dec(cit["value"]) is not None:
                tally.number(f"อ้างอิง {cit['id']}", row[2], cit["value"])
            tally.n += 1
            if not (row[3] and row[6] and row[7]):
                tally.bad.append(f"อ้างอิง {cit['id']}: ไม่บอกที่มา")
        ws = wb["รายการที่ใช้"]
        led = [r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[1] not in (None, "")]
        tally.number("รายการที่ใช้: จำนวนแถว", len(led), len(computed["ledger"]))
        dr = sum((Decimal(str(r[5] or 0)) for r in led), Decimal(0))
        cr = sum((Decimal(str(r[6] or 0)) for r in led), Decimal(0))
        tally.number("รายการที่ใช้: ยอดรวมเดบิต", dr, computed["balance"]["debit"])
        tally.number("รายการที่ใช้: ยอดรวมเครดิต", cr, computed["balance"]["credit"])
    out.append(tally.result("xlsx.figures", "ตัวเลขในตาราง (อ่านจากไฟล์) ตรงกับที่คำนวณ", "ตัวเลข"))
    from . import workpaper

    out.append(workpaper.check(path))
    return out
