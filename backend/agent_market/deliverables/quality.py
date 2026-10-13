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

from ..engine import words
from ..engine.words import SHARED as S
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
# … and as an English one does: 12.48 M · +0.95 pp
# … and as an English one does: 12.48 M · +0.95 pp. The units are the documents' own words, in both languages
# (AM18), never written here.
UNITS = [v for k in ("docs.unit.m", "docs.unit.pp") for v in S.both(k).values()]
_UNIT = "|".join(r"\s?" + re.escape(u) + (r"\b" if u.isascii() else "") for u in UNITS)
NUMBER = re.compile(r"[-+]?\(?\d[\d,]*(?:\.\d+)?\)?(?:\s?%|" + _UNIT + ")?")
_NUMBER_AT_END = re.compile(r"([-+]?\(?\d[\d,]*(?:\.\d+)?\)?(?:\s?%|" + _UNIT + r")?)\s*$")
_STRIP = "+-()% " + "".join(sorted(set("".join(UNITS))))


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


def _compare_table(tally: _Tally, table: str, got: list[list[str]] | None, want: dict, skip_cols=()) -> None:
    """``table``: the table as a sentence names it (gate.table.*). The rows and columns a difference is at
    are named by the words the file itself prints."""
    if got is None:
        tally.n += 1
        tally.bad.append(S("gate.table.missing", table=table))
        return
    body = got[1:]
    if len(body) != len(want["rows"]):
        tally.n += 1
        tally.bad.append(words.cap(S("gate.table.rows", table=table, n=len(body), want=len(want["rows"]))))
    for i, (g, w) in enumerate(zip(body, want["rows"])):
        for j, (gc, wc) in enumerate(zip(g, w)):
            if j in skip_cols:
                continue
            tally.text(words.cap(S("gate.table.cell", table=table, row=w[0], column=want["columns"][j])), gc, "" if wc is None else wc)


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
                m = _NUMBER_AT_END.search(t)
                n += 1
                if not m or _squash(_balanced(m.group(1))) not in by_cite.get(cite, set()):
                    bad.append(S("gate.narrative.wrong_cite", text=t.strip()[-30:], cite=cite))
                else:
                    body = t[:m.start()]
            plain += body + " "
        for tok in NUMBER.findall(_blank_ids(plain, ids)):
            tok = _balanced(tok)
            digits = tok.strip(_STRIP).replace(",", "")
            if "." not in digits and digits.isdigit() and (int(digits) <= 31 or 1990 <= int(digits) <= 2100
                                                          or 2400 <= int(digits) <= 2700):
                continue
            n += 1
            bad.append(S("gate.narrative.no_source", figure=tok.strip()))
        if "{" in plain and content_mod.FIGURE.search(plain):
            n += 1
            bad.append(S("gate.narrative.unknown", name=content_mod.FIGURE.search(plain).group(0)))
    if bad:
        return _check("docx.narrative", False, "; ".join(bad[:4]), S("gate.narrative.label"), n)
    return _check("docx.narrative", True, S("gate.narrative.ok", n=n),
                  S("gate.narrative.label"), n)


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
            # (the word before a figure's id — account, code — in either language)
            before = "|".join(re.escape(w) for k in ("docs.col.account", "docs.col.code") for w in S.both(k).values())
            text = re.sub(rf"({before}|[Aa]ccount|[Cc]ode)\s*{re.escape(i)}(?![0-9.,])", r"\1 ", text)
    return text


def check_docx(path: str, model: dict, theme: dict, req: dict) -> list[dict]:
    from docx import Document

    computed = model["computed"]
    out = []
    v = doc_validator.validate(path)
    out.append(_check("docx.valid", v.get("valid") and not v.get("errors"), "; ".join(v.get("errors") or []),
                      S("gate.valid.label")))
    doc = Document(path)
    allowed = set(theme["fonts"].values())
    bad_fonts, thai_no_cs = _docx_fonts(path, allowed)
    normal = doc.styles["Normal"].element.rPr.find(W_NS + "rFonts")
    base_ok = normal is not None and all(normal.get(W_NS + a) in allowed for a in ("ascii", "hAnsi", "cs"))
    with zipfile.ZipFile(path) as z:
        embedded = [n for n in z.namelist() if n.startswith("word/fonts/")]
    problems = []
    if not base_ok:
        problems.append(S("gate.docx.fonts.base"))
    if bad_fonts:
        problems.append(S("gate.docx.fonts.other", fonts=", ".join(sorted(bad_fonts))))
    if thai_no_cs:
        problems.append(S("gate.docx.fonts.thai", n=thai_no_cs))
    if not embedded:
        problems.append(S("gate.docx.fonts.not_embedded"))
    out.append(_check("docx.fonts", not problems,
                      " · ".join(problems) if problems else
                      S("gate.docx.fonts.ok", font=theme["fonts"]["thai"], n=len(embedded)),
                      S("gate.docx.fonts.label")))

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
    out.append(_check("docx.sections", not missing, S("gate.docx.sections.missing", sections=", ".join(missing)) if missing else
                      S("gate.docx.sections.ok", n=len(REQUIRED_SECTIONS)), S("gate.docx.sections.label")))

    # The figures, read back out of the file's tables.
    tally = _Tally()
    _compare_table(tally, S("gate.table.pl"), _find_table(doc.tables, content_mod.pl_table(computed)["columns"]),
                   content_mod.pl_table(computed))
    tiles = next((t for t in doc.tables if len(t.rows) == 1 and len(t.columns) == len(model["kpis"])
                  and all(k["label"] in _squash(c.text) or _squash(k["label"]) in _squash(c.text)
                          for k, c in zip(model["kpis"], t.rows[0].cells))), None)
    for i, k in enumerate(content_mod.kpis(computed)):
        tally.n += 1
        cell = _squash(tiles.rows[0].cells[i].text) if tiles is not None else ""
        if _squash(k["value"]) not in cell or f"[{k['cite']}]" not in cell:
            tally.bad.append(S("gate.kpi.docx", label=k["label"], value=k["value"], cite=k["cite"]))
    # AM11b: a report the person asked to have without charts (or short) is held to that — no chart, no
    # table of a chart's numbers, no picture — and not to the charts of a report nobody asked to change
    applied = list((model.get("asked") or {}).get("applied") or [])
    chartless = "no_charts" in applied or "brief" in applied
    specs = [] if chartless else expected_charts(computed)
    for spec in specs:
        _compare_table(tally, S("gate.table.under_chart", title=spec["title"]), _find_table(doc.tables, content_mod.chart_table(spec)["columns"]),
                       content_mod.chart_table(spec))
    out.append(tally.result("docx.figures", S("gate.docx.figures.label"), S("gate.docx.figures.what")))
    out.append(_chart_pictures(path, specs, theme, has_logo=bool(req.get("logo")), asked_none=chartless))
    out.append(_narrative_check(doc, model, computed))
    if applied:
        out.append(_asked_docx(doc, headings, model, computed, applied))
    return out


def _asked_docx(doc, headings: list, model: dict, computed: dict, applied: list[str]) -> dict:
    """AM11b: what the person asked of the report, read back out of the finished file — the sections
    that were to be left out are not in it, and the table that was to be ordered is in that order by the
    numbers in its own cells."""
    in_file = {h[0] for h in headings}
    tally = _Tally()
    said = []
    gone = []
    if "no_charts" in applied or "brief" in applied:
        gone.append("charts")
    if "brief" in applied:
        gone += [k for k in content_mod.BRIEF_LEAVES_OUT if k != "charts"]
    for key in gone:
        tally.n += 1
        if content_mod.SECTION_TITLES[key] in in_file:
            tally.bad.append(S("gate.asked.section_left", title=content_mod.SECTION_TITLES[key]))
    if gone:
        tally.n += 1
        chart_tables = [t for t in doc.tables if t.rows and _squash(t.rows[0].cells[0].text) in
                        {_squash(h) for h in content_mod.CHART_TABLE_HEADS}]
        if chart_tables:
            tally.bad.append(S("gate.asked.chart_tables", n=len(chart_tables)))
        said.append(S("gate.asked.gone", sections=" ".join(f"“{content_mod.SECTION_TITLES[k]}”" for k in gone)))
    if "sort_by_variance" in applied:
        block = next((b for s in model["sections"] if s["key"] == "accounts" for b in s["blocks"]), None)
        if block is not None:  # (a short report has no account table to order)
            got = _find_table(doc.tables, block["columns"])
            cols = block["columns"]
            sizes = []
            for row in (got or [])[1:]:
                if block.get("ordered_by") == "var_budget":
                    v = _cell_money(row[cols.index(S("docs.col.var_budget"))])
                else:
                    cur, pri = _cell_money(row[cols.index(S("docs.col.current"))]), _cell_money(row[cols.index(S("docs.col.prior"))])
                    v = None if cur is None or pri is None else cur - pri
                sizes.append(abs(v) if v is not None else Decimal(-1))
            tally.n += 1
            # (cells are whole baht: two differences less than a baht apart may read as equal or one apart)
            if not sizes or any(b - a > 1 for a, b in zip(sizes, sizes[1:])):
                tally.bad.append(S("gate.asked.not_sorted"))
            # … and it is the table of this round's accounts, figure for figure
            _compare_table(tally, S("gate.table.section", title=content_mod.SECTION_TITLES["accounts"]), got, block)
            said.append((S("gate.asked.sorted.budget", n=len(sizes)) if block.get("ordered_by") == "var_budget"
                         else S("gate.asked.sorted.prior", n=len(sizes))))
    label = S("asked.label")
    if tally.bad:
        return _check("docx.asked", False, "; ".join(tally.bad[:4]), label, tally.n)
    return _check("docx.asked", True, " · ".join(said), label, tally.n)


def _cell_money(text) -> Decimal | None:
    """A money cell as the report prints it: 1,284,500 · (12,400) · — (no figure)."""
    t = _squash(text)
    neg = t.startswith("(") and t.endswith(")")
    d = _dec(t.strip("()"))
    return None if d is None else (-d if neg else d)


def _chart_pictures(path: str, specs: list[dict], theme: dict, has_logo: bool, asked_none: bool = False) -> dict:
    """Each chart picture in the file is the picture of the computed numbers: drawn again, compared.
    ``asked_none`` (AM11b): the person asked for no charts — any picture beside the logo fails."""
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
    detail = (S("gate.docx.charts.not_computed", charts=", ".join(missing)) if missing else
              (S("gate.docx.charts.extra", extra=extra) if extra > 0 else
               S("gate.docx.charts.none_asked") if asked_none else
               S("gate.docx.charts.ok", n=len(specs))))
    return _check("docx.charts", ok, detail, S("gate.docx.charts.label"), len(specs))


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
                  (S("gate.citations.no_source", ids=", ".join(bad[:6])) if bad else
                   S("gate.citations.blank", figures=", ".join(blank[:6])) if blank else
                   S("gate.citations.ok", n=len(set(printed)))),
                  S("gate.citations.label"), len(set(printed)))


def check_assumptions(model: dict, req: dict) -> dict:
    defaulted = ("default", "reprompt_default", "skip")
    had_defaults = any(a.get("source") in defaulted for a in req.get("assumptions") or [])
    listed = list(model["assumptions"])
    ok = bool(listed) and (not had_defaults or any(a.get("source") in defaulted for a in listed))
    return _check("assumptions", ok, (S("gate.assumptions.n.defaults", n=len(listed)) if had_defaults else S("gate.assumptions.n", n=len(listed))),
                  S("gate.assumptions.label"))


# ── .pptx ─────────────────────────────────────────────────────────────────

def _slide_text(slide) -> str:
    parts = []
    for sh in slide.shapes:
        if sh.has_text_frame:
            parts.append(sh.text_frame.text)
    return "\n".join(parts)


def check_pptx(path: str, deck: dict, computed: dict, theme: dict, asked=()) -> list[dict]:
    """``deck`` (what the writer returned) is not read for any figure: the figures come out of the file.
    ``asked`` (AM11b): the directives applied — a deck asked to have no charts has none, and that many
    fewer slides."""
    from pptx import Presentation

    out = []
    v = doc_validator.validate(path)
    out.append(_check("pptx.valid", v.get("valid") and not v.get("errors"), "; ".join(v.get("errors") or []),
                      S("gate.valid.label")))
    info = pptx_inspector.inspect_pptx(path)
    n = info.get("slide_count", 0)
    no_charts = "no_charts" in asked
    least = 8 - (len(content_mod.charts(computed)) if no_charts else 0)
    out.append(_check("pptx.slide_count", least <= n <= 12, (S("gate.pptx.slides.no_charts", n=n) if no_charts else S("gate.pptx.slides", n=n)),
                      S("gate.pptx.slides.label", least=least)))
    prs = Presentation(path)
    allowed = set(theme["fonts"].values())
    overflows = []
    bad_fonts = set()
    for i, slide in enumerate(prs.slides, start=1):
        for sh in slide.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                r = frame_overflow(sh.text_frame, sh.width, sh.height, theme["fonts"]["thai"])
                if r > 1.02:
                    overflows.append(S("gate.pptx.overflow.at", i=i, text=sh.text_frame.text[:30], ratio=f"{r:.2f}"))
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
    out.append(_check("pptx.overflow", not overflows, "; ".join(overflows[:4]) or S("gate.pptx.overflow.none"),
                      S("gate.pptx.overflow.label")))
    fonts_used = set(info.get("fonts_used") or []) - allowed
    out.append(_check("pptx.fonts", not bad_fonts and not fonts_used,
                      S("gate.pptx.fonts.other", fonts=sorted(bad_fonts | fonts_used)) if (bad_fonts or fonts_used) else
                      S("gate.pptx.fonts.ok", font=theme["fonts"]["thai"]), S("gate.pptx.fonts.label")))

    tally = _Tally()
    # Charts: every chart in the file is one of the computed charts, every computed chart is there once.
    want = [] if no_charts else expected_charts(computed)
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
            if match is None and no_charts:
                tally.bad.append(S("gate.pptx.chart.unasked", i=i))
            elif match is None:
                tally.bad.append(S("gate.pptx.chart.differs", i=i))
            else:
                found.append(match["id"])
    for spec in want:
        if found.count(spec["id"]) != 1:
            tally.n += 1
            tally.bad.append((S("gate.pptx.chart.missing", title=spec["title"]) if not found.count(spec["id"])
                            else S("gate.pptx.chart.twice", title=spec["title"])))
    # The summary P&L table.
    c = computed
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
    tables = [[[cell.text for cell in row.cells] for row in sh.table.rows] for s in prs.slides for sh in s.shapes
              if getattr(sh, "has_table", False) and sh.has_table]
    pl = next((t for t in tables if t and [_squash(x) for x in t[0]] == [_squash(h) for h in heads]), None)
    _compare_table(tally, S("gate.table.deck_pl"), pl, {"columns": heads, "rows": rows})
    # KPI values on the key-figures slide.
    kslide = next((s for s in prs.slides if S("docs.deck.stats") in _slide_text(s)), None)
    ktext = _squash(_slide_text(kslide)) if kslide is not None else ""
    for k in content_mod.kpis(computed):
        tally.n += 1
        if _squash(k["value_short"].replace(" " + S("docs.unit.m"), "M")) not in ktext:
            tally.bad.append(S("gate.kpi.pptx", label=k["label"], value=k["value_short"]))
    # The summary slide: no figure that is not a computed one.
    allowed_forms = set()
    for f in (computed.get("figures") or {}).values():
        allowed_forms |= {_squash(f["text"]), _squash(content_mod.figure_text(f, "abs"))}
    sslide = next((s for s in prs.slides if S("docs.section.summary") in _slide_text(s)), None)
    if sslide is not None:
        body = _blank_ids(_slide_text(sslide), set(computed.get("quotable_ids") or []))
        for tok in NUMBER.findall(body.split(S("docs.source_prefix"))[0]):
            tok = _balanced(tok)
            digits = tok.strip(_STRIP).replace(",", "")
            if "." not in digits and digits.isdigit() and (int(digits) <= 31 or 1990 <= int(digits) <= 2100
                                                          or 2400 <= int(digits) <= 2700):
                continue
            tally.n += 1
            if _squash(tok) not in allowed_forms:
                tally.bad.append(S("gate.pptx.summary.unknown", figure=tok.strip()))
    out.append(tally.result("pptx.figures", S("gate.pptx.figures.label"), S("gate.pptx.figures.what")))
    return out


# ── .xlsx ─────────────────────────────────────────────────────────────────

def check_xlsx(path: str, computed: dict) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    need = {S("docs.sheet.summary"), S("docs.sheet.accounts"), S("docs.sheet.ledger"), S("docs.sheet.verify"), S("docs.sheet.citations")}
    out = [_check("xlsx.sheets", need <= set(wb.sheetnames), ", ".join(wb.sheetnames), S("gate.xlsx.sheets.label"))]
    tally = _Tally()
    if need <= set(wb.sheetnames):
        ws = wb[S("docs.sheet.summary")]
        by_label = {}
        for row in ws.iter_rows(min_row=5, values_only=True):
            if row and row[0] not in (None, "") and row[0] not in by_label:
                by_label[row[0]] = row
        cols = ("current", "prior", "var_prior", "var_prior_pct", "budget", "var_budget")
        heads = (S("docs.col.current"), S("docs.col.prior"), S("docs.col.change"), "%", S("docs.col.budget"), S("docs.col.var_budget"))
        for l in computed["lines"]:
            row = by_label.get(l["label"])
            if row is None:
                tally.n += 1
                tally.bad.append(S("gate.xlsx.summary.no_row", label=l["label"]))
                continue
            for j, (k, h) in enumerate(zip(cols, heads), start=1):
                tally.number(S("gate.xlsx.summary.cell", label=l["label"], column=h), row[j], l[k])
            tally.text(S("gate.xlsx.summary.cite", label=l["label"]), row[7], l["cite"])
        for rt in computed["ratios"]:
            row = by_label.get(rt["label"])
            if row is None:
                tally.n += 1
                tally.bad.append(S("gate.xlsx.summary.no_row", label=rt["label"]))
                continue
            tally.number(S("gate.xlsx.summary.now", label=rt["label"]), row[1], rt["current"])
            tally.number(S("gate.xlsx.summary.prior", label=rt["label"]), row[2], rt["prior"])
        ws = wb[S("docs.sheet.accounts")]
        by_code = {str(r[0]): r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[0] not in (None, "")}
        for a in computed["accounts"]:
            row = by_code.get(str(a["code"]))
            if row is None:
                tally.n += 1
                tally.bad.append(S("gate.xlsx.accounts.no_row", code=a["code"]))
                continue
            for j, k in ((3, "current"), (4, "prior"), (5, "var_prior"), (6, "var_prior_pct"), (7, "budget"), (8, "var_budget")):
                tally.number(S("gate.xlsx.accounts.cell", code=a["code"], column=j + 1), row[j], a[k])
        ws = wb[S("docs.sheet.citations")]
        by_id = {r[0]: r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[0]}
        for cit in computed["citations"]:
            row = by_id.get(cit["id"])
            if row is None:
                tally.n += 1
                tally.bad.append(S("gate.xlsx.cites.no_row", id=cit["id"]))
                continue
            if _dec(cit["value"]) is not None:
                tally.number(S("gate.xlsx.cites.cell", id=cit["id"]), row[2], cit["value"])
            tally.n += 1
            if not (row[3] and row[6] and row[7]):
                tally.bad.append(S("gate.xlsx.cites.no_source", id=cit["id"]))
        ws = wb[S("docs.sheet.ledger")]
        led = [r for r in ws.iter_rows(min_row=5, values_only=True) if r and r[1] not in (None, "")]
        tally.number(S("gate.xlsx.ledger.rows"), len(led), len(computed["ledger"]))
        dr = sum((Decimal(str(r[5] or 0)) for r in led), Decimal(0))
        cr = sum((Decimal(str(r[6] or 0)) for r in led), Decimal(0))
        tally.number(S("gate.xlsx.ledger.dr"), dr, computed["balance"]["debit"])
        tally.number(S("gate.xlsx.ledger.cr"), cr, computed["balance"]["credit"])
    out.append(tally.result("xlsx.figures", S("gate.xlsx.figures.label"), S("gate.xlsx.figures.what")))
    from . import workpaper

    out.extend(workpaper.checks(path, words.lang()))
    return out
