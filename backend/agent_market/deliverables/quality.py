"""The quality gate. A run is not "succeeded" until its files pass.

Fails the step on: a file the shared validator rejects · text overflowing
a slide · a font outside the theme (or a Thai run with no complex-script
font) · a chart with no data binding, or plotted values that differ from the
computed table · an empty required section · a blank citation slot · an
empty assumptions section on a run that had defaulted findings.

Reuses doc_validator.py and pptx_inspector.py by import.
"""

from __future__ import annotations

import zipfile
from decimal import Decimal

import doc_validator  # /sandbox/scripts — shared, imported, not modified
import pptx_inspector  # /sandbox/scripts — shared, imported, not modified

from .content import REQUIRED_SECTIONS
from .fit import frame_overflow

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _check(name: str, ok: bool, detail: str = "", label: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "label": label or name}


def check_docx(path: str, model: dict, theme: dict, req: dict) -> list[dict]:
    from docx import Document

    out = []
    v = doc_validator.validate(path)
    out.append(_check("docx.valid", v.get("valid") and not v.get("errors"), "; ".join(v.get("errors") or []),
                      "ไฟล์เปิดได้และโครงสร้างถูกต้อง"))
    doc = Document(path)
    allowed = set(theme["fonts"].values())
    bad_fonts = set()
    thai_no_cs = 0

    def scan_runs(paragraphs):
        nonlocal thai_no_cs
        for p in paragraphs:
            for r in p.runs:
                rpr = r._element.rPr
                fonts = rpr.find(W_NS + "rFonts") if rpr is not None else None
                if fonts is not None:
                    for attr in ("ascii", "hAnsi", "cs"):
                        f = fonts.get(W_NS + attr)
                        if f and f not in allowed:
                            bad_fonts.add(f)
                    if any("฀" <= ch <= "๿" for ch in r.text) and not fonts.get(W_NS + "cs"):
                        thai_no_cs += 1

    scan_runs(doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                scan_runs(cell.paragraphs)
    normal = doc.styles["Normal"].element.rPr.find(W_NS + "rFonts")
    base_ok = normal is not None and all(normal.get(W_NS + a) in allowed for a in ("ascii", "hAnsi", "cs"))
    with zipfile.ZipFile(path) as z:
        embedded = [n for n in z.namelist() if n.startswith("word/fonts/")]
    # The detail is shown to people: say what is wrong, or plainly that it is fine.
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
                      " · ".join(problems) if problems else f"{theme['fonts']['thai']} ฝังในไฟล์ {len(embedded)} ไฟล์",
                      "ฟอนต์ไทยฝังครบ"))

    # Required sections present and non-empty (heading followed by content).
    headings = []
    body = doc.element.body
    for el in body.iterchildren():
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
    missing = []
    for key in REQUIRED_SECTIONS:
        title = titles.get(key)
        h = next((h for h in headings if h[0] == title), None)
        if title is None or h is None or h[1] == 0:
            missing.append(key)
    out.append(_check("docx.sections", not missing, "ส่วนที่ว่างหรือหายไป: " + ", ".join(missing) if missing else
                      f"{len(REQUIRED_SECTIONS)} ส่วนที่ต้องมีครบ", "ทุกส่วนที่ต้องมีไม่ว่าง"))
    return out


def check_citations(model: dict, computed: dict) -> dict:
    """Every numeric table row carries a citation that resolves to a source."""
    ids = {c["id"]: c for c in computed["citations"]}
    slots = filled = 0
    bad = []
    for sec in model["sections"]:
        for b in sec["blocks"]:
            if b["type"] != "table" or b.get("cite_col") is None or sec.get("appendix"):
                continue
            for row in b["rows"]:
                has_number = any(isinstance(v, str) and any(ch.isdigit() for ch in v)
                                 for j, v in enumerate(row) if j != b["cite_col"] and j != 0)
                if not has_number:
                    continue
                slots += 1
                cid = row[b["cite_col"]]
                c = ids.get(cid)
                if c and (c["sources"] or c.get("derived_from")) and c["script"] and c["formula"]:
                    filled += 1
                else:
                    bad.append(f"{sec['key']}:{row[0]}")
    for k in model["kpis"]:
        slots += 1
        filled += 1 if k["cite"] in ids else 0
    return _check("citations", filled == slots and slots > 0,
                  f"{filled} จาก {slots}" + (" · ว่าง: " + ", ".join(bad[:5]) if bad else ""), f"ช่องอ้างอิงครบ {filled} จาก {slots}")


def check_assumptions(model: dict, req: dict) -> dict:
    defaulted = ("default", "reprompt_default", "skip")
    had_defaults = any(a.get("source") in defaulted for a in req.get("assumptions") or [])
    listed = [a for a in model["assumptions"]]
    ok = bool(listed) and (not had_defaults or any(a.get("source") in defaulted for a in listed))
    return _check("assumptions", ok, f"{len(listed)} ข้อ" + (" · มีค่าตั้งต้นที่ต้องแสดง" if had_defaults else ""),
                  "ข้อสมมติครบ")


def check_pptx(path: str, deck: dict, computed: dict, theme: dict) -> list[dict]:
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
    out.append(_check("pptx.overflow", not overflows, "; ".join(overflows[:4]) or "ไม่มี", "ไม่มีข้อความล้นสไลด์"))
    fonts_used = set(info.get("fonts_used") or []) - allowed
    out.append(_check("pptx.fonts", not bad_fonts and not fonts_used,
                      f"นอกธีม: {sorted(bad_fonts | fonts_used)}" if (bad_fonts or fonts_used) else "Sarabun ทุกข้อความ",
                      "ฟอนต์ตามธีมทุกข้อความ"))
    # Charts: each chart on a slide has a binding, and its values are the computed ones.
    problems = []
    bindings = {b["slide"]: b for b in deck.get("chart_bindings", [])}
    for i, slide in enumerate(prs.slides, start=1):
        for sh in slide.shapes:
            if not (getattr(sh, "has_chart", False) and sh.has_chart):
                continue
            b = bindings.get(i)
            if b is None:
                problems.append(f"สไลด์ {i}: กราฟไม่มีที่มา")
                continue
            plotted = [list(s.values) for s in sh.chart.plots[0].series]
            scale = Decimal(b["scale"])
            for s_i, ser in enumerate(b["series"]):
                src = [float((Decimal(x) / scale).quantize(Decimal("0.01"))) for x in ser["source_values"]]
                if ser.get("reversed"):
                    src = list(reversed(src))
                if s_i >= len(plotted) or [round(x, 2) for x in plotted[s_i]] != [round(x, 2) for x in src]:
                    problems.append(f"สไลด์ {i}: ค่าในกราฟไม่ตรงตาราง ({ser['name']})")
                if not ser.get("path"):
                    problems.append(f"สไลด์ {i}: ชุดข้อมูล {ser['name']} ไม่ระบุที่มา")
    n_charts = sum(1 for s in prs.slides for sh in s.shapes if getattr(sh, "has_chart", False) and sh.has_chart)
    out.append(_check("pptx.charts", not problems and n_charts > 0, "; ".join(problems[:4]) or f"{n_charts} กราฟ",
                      "กราฟผูกกับตัวเลขที่คำนวณจริง"))
    return out


def check_docx_charts(bindings: list[dict], computed: dict, expected: list[dict]) -> dict:
    want = {c["id"] for c in expected}
    got = {b["chart"] for b in bindings}
    bad = []
    for b in bindings:
        spec = next((c for c in expected if c["id"] == b["chart"]), None)
        if spec is None:
            bad.append(b["chart"])
            continue
        for s_spec, s_b in zip(spec["series"], b["series"]):
            if [str(x) for x in s_spec["values"]] != s_b["values"] or not s_b.get("path"):
                bad.append(b["chart"])
    return _check("docx.charts", want == got and not bad, f"{len(got)} กราฟ" + (f" · ไม่ตรง: {bad}" if bad else ""),
                  "กราฟในรายงานผูกกับตาราง")


def check_xlsx(path: str, computed: dict) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    need = {"สรุป", "รายบัญชี", "รายการที่ใช้", "ตรวจสอบ", "อ้างอิง"}
    ok = need <= set(wb.sheetnames)
    ws = wb["สรุป"]
    lines = {l["label"]: l["current"] for l in computed["lines"]}
    mism = []
    for row in ws.iter_rows(min_row=5, values_only=True):
        if row[0] in lines and lines[row[0]] is not None and row[1] is not None:
            if Decimal(str(row[1])).quantize(Decimal("0.01")) != Decimal(lines[row[0]]):
                mism.append(row[0])
    return [_check("xlsx.sheets", ok, ", ".join(wb.sheetnames), "ชีตครบ"),
            _check("xlsx.numbers", not mism, "ไม่ตรง: " + ", ".join(mism) if mism else "ตรงกับตารางที่คำนวณ", "ตัวเลขตรงกับรายงาน")]
