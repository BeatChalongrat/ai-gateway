"""A run's numbers in the customer's own template (AM2 Phase E).

The install keeps one template (install default ``template``): the file, the
places it takes a value (``slots``) and the saved ``mapping`` — slot key →
one of the agent's template fields (the manifest's ``templates`` block:
single values, and lists whose rows fill a repeating table row or a
multi-cell named range). The mapping screen is asked once; a new version of
the template is diffed against the saved one, so only what changed is asked.

The filling itself is template_fill.fill (the shared scripts). Values are
handed to it keyed by the template's own slot keys and nothing else, so an
unmapped marker is never filled by a lucky name match.

AM8 — who decides what:
* Which slot takes which field is a guess about the customer's form: the
  model proposes it in the runner (it reads each slot's label, tag, name and
  the text around it) and the person confirms it on the mapping screen. Here
  the only proposal is identity (a slot named exactly like a field).
* Whether the fill is right is not a guess: ``verify`` opens the filled file
  again and asserts it — every mapped slot holds its value, no {{marker}} is
  left anywhere, no filled text is cut off by a fixed-height row or box, the
  template has no chart of its own still showing its old numbers, no formula
  changed, no part was dropped. Any of those failing is "not passed", and the
  slots not filled are listed by name for the download card.
* What cannot be filled is refused before work starts (``slots`` marks it
  ``blocking``): a marker where the filler does not write, a Word or
  PowerPoint chart. What can — markers (also on PowerPoint masters and
  layouts), plain-text Word controls, Excel names and cells, PowerPoint
  shapes a person named, repeating table rows — is supported.

An .xlsx comes back needing recalculation (openpyxl writes no computed
values): the runner has the doc-converter recalculate it, and the workbook
also asks Excel to recalculate on open.
"""

from __future__ import annotations

import re
from typing import Any

from . import template_fill as tf

LIST_KINDS = ("list", "table")


def _norm(s: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(s))
    return re.sub(r"[^0-9a-zก-๙]+", "", s.lower())


def slots(inspection: dict) -> list[dict]:
    """Every place the template can take a value, for the mapping screen (and why a place cannot)."""
    out: list[dict] = []
    lists: dict[str, list[str]] = {}
    for m in inspection.get("markers") or []:
        if "." in m:
            name, sub = m.split(".", 1)
            lists.setdefault(name, [])
            if sub not in lists[name]:
                lists[name].append(sub)
        else:
            out.append({"key": m, "kind": "marker", "label": m, "fillable": True, "reason": None})
    for name, cols in lists.items():
        out.append({"key": name, "kind": "list", "label": name, "fillable": True, "reason": None, "columns": cols})
    for c in inspection.get("controls") or []:
        key = c.get("tag") or c.get("alias")
        if key:
            out.append({"key": key, "kind": "control", "label": c.get("alias") or key, "fillable": bool(c.get("fillable")),
                        "reason": c.get("reason")})
    for n in inspection.get("names") or []:
        table = (n.get("cells") or 1) > 1
        out.append({"key": n["key"], "kind": "table" if table else "name", "label": n["name"], "where": n.get("ref"),
                    "fillable": bool(n.get("fillable")), "reason": n.get("reason"),
                    **({"columns": _range_columns(n.get("ref") or "")} if table else {})})
    for t in inspection.get("targets") or []:
        out.append({"key": t["cell"], "kind": "target", "label": t.get("label") or t["cell"], "where": t["cell"],
                    "fillable": True, "reason": None})
    for sh in inspection.get("shapes") or []:
        out.append({"key": sh["key"], "kind": "shape", "label": sh["name"], "where": f"สไลด์ {sh['slide']}",
                    "fillable": True, "reason": None, "context": sh.get("text") or ""})
    ctx = inspection.get("context") or {}
    for slot in out:
        if not slot.get("context"):
            slot["context"] = ctx.get(slot["key"]) or ctx.get(slot["label"]) or ""
    # AM8: what the filler cannot do is refused here, before any work, with its reason
    for r in inspection.get("refused") or []:
        out.append({"key": r["key"], "kind": "refused", "label": r["label"], "where": r.get("where"),
                    "fillable": False, "reason": r["reason"], "blocking": True})
    return out


def blocking(sl: list[dict]) -> list[dict]:
    """The slots that stop this template being used at all (a chart it would leave showing old numbers,
    a marker it would leave in place)."""
    return [s for s in sl if s.get("blocking")]


def _range_columns(ref: str) -> list[str]:
    """ "สรุป!A10:C13" -> ["A", "B", "C"]: a multi-cell name's columns, each mapped on the screen."""
    from openpyxl.utils import column_index_from_string, get_column_letter

    m = re.search(r"\$?([A-Z]{1,3})\$?\d+:\$?([A-Z]{1,3})\$?\d+$", ref.split("!")[-1])
    if not m:
        return []
    a, b = column_index_from_string(m.group(1)), column_index_from_string(m.group(2))
    return [get_column_letter(i) for i in range(min(a, b), max(a, b) + 1)]


def _fields(spec: dict) -> dict[str, dict]:
    """The agent's template fields, with every name they may be recognised by."""
    out = {}
    for key, f in (spec.get("fields") or {}).items():
        f = f if isinstance(f, dict) else {"label": f}
        out[key] = {"label": f.get("label", key), "names": {_norm(key), _norm(f.get("label", key)),
                                                            *(_norm(a) for a in f.get("aliases") or [])}}
    return out


def propose(sl: list[dict], spec: dict) -> dict[str, str]:
    """The proposal code may make: a slot whose key is exactly a field's key ({{revenue}} → revenue).

    AM8: anything more is a guess about the customer's form — a label in Thai beside a cell, a control
    named "TotalAmt", a column headed "มูลค่า" — and that guess is the model's (the runner asks it, with
    each slot's label and context), confirmed by the person. No list of synonyms is matched here."""
    fields, lists = spec.get("fields") or {}, spec.get("lists") or {}
    out: dict[str, str] = {}
    for s in sl:
        if not s["fillable"]:
            continue
        if s["kind"] in LIST_KINDS:
            if s["key"] in lists:
                out[s["key"]] = s["key"]
                cols = (lists[s["key"]] or {}).get("columns") or {}
                for c in s.get("columns") or []:
                    if c in cols:
                        out[f"{s['key']}.{c}"] = f"{s['key']}.{c}"
            continue
        if s["key"] in fields:
            out[s["key"]] = s["key"]
    return out


def diff(saved: dict | None, sl: list[dict]) -> dict:
    """A new version against the saved template: the slots kept (their mapping kept), added, removed."""
    if not saved:
        return {"first": True, "kept": [], "added": [s["key"] for s in sl], "removed": []}
    old = {s["key"] for s in saved.get("slots") or []}
    new = {s["key"] for s in sl}
    return {"first": False, "kept": sorted(old & new), "added": sorted(new - old), "removed": sorted(old - new)}


def carry_over(saved: dict | None, sl: list[dict], proposal: dict) -> dict:
    """The mapping to show for a new version: the saved choice for every kept slot, the proposal for the rest."""
    mapping = dict(proposal)
    if saved:
        keep = {s["key"] for s in sl}
        for k, v in (saved.get("mapping") or {}).items():
            if k.split(".", 1)[0] in keep:
                mapping[k] = v
    return mapping


def template_values(sl: list[dict], mapping: dict, values: dict) -> dict[str, Any]:
    """The agent's values keyed by the template's own slot keys — only what the mapping names."""
    out: dict[str, Any] = {}
    for s in sl:
        src = mapping.get(s["key"])
        if not src or not s["fillable"]:
            continue
        if s["kind"] in LIST_KINDS:
            rows = values.get(src) or []
            cols = s.get("columns") or []
            # a column the person left unmapped stays empty — never filled from a same-named column
            pick = {c: (mapping.get(f"{s['key']}.{c}") or "").split(".", 1)[-1] for c in cols}
            out[s["key"]] = [{c: (r.get(pick[c], "") if pick[c] else "") for c in cols} for r in rows] if cols else rows
        elif src in values:
            out[s["key"]] = values[src]
    return out


def fill(path: str, out_path: str, saved: dict, values: dict) -> dict:
    """Fill the saved template with a run's values; then open the filled file again and judge it (``verify``).
    Returns the filler's report plus the checks and ``unfilled`` — every slot that does not hold its value,
    by name, for the person."""
    sl = saved.get("slots") or slots(tf.inspect(path))
    mapping = saved.get("mapping") or {}
    tvals = template_values(sl, mapping, values)
    targets = {s["key"]: s["key"] for s in sl if s["kind"] == "target" and s["key"] in tvals}
    report = tf.fill(path, out_path, tvals, targets)
    if report["kind"] == "xlsx":
        _recalc_on_open(out_path)
    checks, unfilled = verify(path, out_path, sl, mapping, tvals, report)
    report["checks"] = checks
    report["unfilled"] = unfilled
    report["passed"] = all(c["ok"] for c in checks)
    return report


WHY_TH = {"no_value": "รอบนี้ไม่มีค่าให้ช่องนี้", "not_in_file": "เปิดไฟล์ที่เติมแล้วไม่พบค่าในช่องนี้",
          "marker_left": "ยังมีเครื่องหมายช่องว่างค้างอยู่", "cut_off": "ข้อความยาวเกินช่องที่สูงคงที่ จะถูกตัด",
          "unsupported_location": "อยู่ในส่วนของไฟล์ที่ระบบเติมไม่ได้",
          "chart_not_updated": "กราฟเดิมในแบบฟอร์มจะยังแสดงตัวเลขเดิม",
          "merged_cell": "เป็นช่องที่ถูกผสานและไม่ใช่ช่องแรก", "formula_cell": "เป็นช่องสูตร", "protected": "ชีตถูกล็อกไว้",
          "not_plain_text": "เป็นช่องที่ไม่ใช่ข้อความธรรมดา", "content_locked": "ช่องถูกล็อกไม่ให้แก้"}


def _why(reason) -> str:
    return WHY_TH.get(str(reason or ""), str(reason or "เติมไม่ได้"))


_NO_VALUE = object()


def verify(src: str, out: str, sl: list[dict], mapping: dict, tvals: dict, report: dict) -> tuple[list[dict], list[dict]]:
    """The filled file, opened again (AM8): the gate's checks, and every slot left without its value."""
    kind = report["kind"]
    by_key = {s["key"]: s for s in sl}
    unfilled: dict[str, dict] = {}

    def miss(key: str, why: str) -> None:
        s = by_key.get(key) or {"label": key}
        unfilled.setdefault(key, {"slot": key, "label": s.get("label") or key, "why": _why(why)})

    reasons: dict = {}
    for e in report.get("not_filled_explained") or []:
        k = e.get("marker") or e.get("tag") or e.get("name") or e.get("target")
        if k:
            reasons.setdefault(k, e.get("reason"))
    for k, why in (report.get("refused") or {}).items():
        reasons.setdefault(k, why)
    mapped = [s for s in sl if mapping.get(s["key"]) and s["fillable"]]
    for s in mapped:
        k = s["key"]
        if s["kind"] in LIST_KINDS:
            if not tvals.get(k):
                miss(k, reasons.get(k) or "no_value")
            elif kind != "xlsx" and int((report.get("rows") or {}).get(k) or 0) < len(tvals[k]):
                miss(k, reasons.get(k) or "not_in_file")
            continue
        value = tvals.get(k, _NO_VALUE)
        if value is _NO_VALUE or not (tf.dg.as_text(value) or "").strip():
            miss(k, reasons.get(k) or "no_value")
        elif not tf.holds(out, kind, s, value):
            miss(k, reasons.get(k) or "not_in_file")
    left = tf.markers_left(out)
    for k in left:
        miss(k, "marker_left")
    cut = tf.cut_off(src, out)
    charts = tf.chart_parts(out) if kind in ("docx", "pptx") else []
    filled = report.get("filled") or []

    def ck(name, ok, detail, label, values=None):
        c = {"name": name, "ok": bool(ok), "detail": detail, "label": label}
        if values is not None:
            c["values"] = values
        return c
    gaps = [u for u in unfilled.values() if u["why"] != _why("marker_left")]
    checks = [
        ck("template.filled", bool(filled), f"เติมแล้ว {len(filled)} ช่อง", "เติมค่าลงแบบฟอร์มได้"),
        # the slots by name are in ``unfilled`` (the form's own names, shown on the file's row); a check's
        # detail says how many, in the package's words
        ck("template.every_slot", not gaps,
           f"ยังไม่ได้ค่า {len(gaps)} ช่อง จาก {len(mapped)} ช่องที่จับคู่ไว้ (ชื่อช่องอยู่บนแถวของแบบฟอร์ม)" if gaps
           else f"เปิดไฟล์อ่านกลับ ทุกช่องที่จับคู่ไว้ {len(mapped)} ช่องมีค่าของรอบนี้",
           "ทุกช่องที่จับคู่ไว้มีค่า (อ่านจากไฟล์ที่เติมแล้ว)", len(mapped)),
        ck("template.no_marker_left", not left,
           f"ยังมีช่องที่ไม่ได้เติมค้างในไฟล์ {len(left)} ช่อง (ชื่อช่องอยู่บนแถวของแบบฟอร์ม)" if left else "ไม่มีช่องว่างค้างในไฟล์",
           "ไม่มีช่องว่างค้างในไฟล์"),
        ck("template.fits", not cut, ("ข้อความถูกตัดใน: " + ", ".join(cut))[:300] if cut else "ข้อความที่เติมพอดีช่อง",
           "ข้อความที่เติมไม่ล้นช่องที่สูงคงที่"),
        ck("template.no_static_chart", not charts,
           f"แบบฟอร์มมีกราฟ {len(charts)} กราฟ ที่ระบบไม่ได้วาดใหม่ จะยังแสดงตัวเลขเดิม" if charts else "ไม่มีกราฟที่ค้างตัวเลขเดิม",
           "ไม่มีกราฟที่ยังแสดงตัวเลขเดิม"),
        ck("template.formulas_kept", not report.get("formulas_changed"),
           f"สูตรคงเดิม {len(report.get('formulas_kept') or [])} สูตร", "ไม่เขียนทับสูตร"),
        ck("template.parts_kept", not report.get("parts_dropped"), ", ".join(report.get("parts_dropped") or []) or "ครบ",
           "ส่วนประกอบของไฟล์ครบ"),
    ]
    for where in cut:
        unfilled.setdefault(f"cut:{where}", {"slot": f"cut:{where}", "label": where, "why": _why("cut_off")})
    for c in charts:
        unfilled.setdefault(c, {"slot": c, "label": "กราฟในแบบฟอร์ม", "why": _why("chart_not_updated")})
    return checks, list(unfilled.values())


def _recalc_on_open(path: str) -> None:
    """Ask Excel to recalculate every formula when it opens the file (the converter recalculates it too).
    The attribute is set in workbook.xml directly — a second openpyxl round trip could drop parts."""
    import os
    import shutil
    import tempfile
    import zipfile

    with zipfile.ZipFile(path) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
        if "<calcPr" not in wb or "fullCalcOnLoad" in wb:
            return
        wb = re.sub(r"<calcPr\b", '<calcPr fullCalcOnLoad="1"', wb, count=1)
        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for item in z.infolist():
                out.writestr(item, wb.encode("utf-8") if item.filename == "xl/workbook.xml" else z.read(item.filename))
    shutil.move(tmp, path)
