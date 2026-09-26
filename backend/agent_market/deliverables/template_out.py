"""A run's numbers in the customer's own template (AM2 Phase E).

The install keeps one template (install default ``template``): the file, the
places it takes a value (``slots``) and the saved ``mapping`` — slot key →
one of the agent's template fields (the manifest's ``templates`` block:
single values, and lists whose rows fill a repeating table row or a
multi-cell named range). The mapping screen is asked once; a new version of
the template is diffed against the saved one, so only what changed is asked.

The filling itself is template_fill.fill (the shared scripts). Values are
handed to it keyed by the template's own slot keys and nothing else, so an
unmapped marker is never filled by a lucky name match. The gate: something
was filled; every mapped slot was filled or says why not; no formula changed.
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
    return out


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
    """A first mapping by name (English and Thai labels, aliases); the person confirms or changes it."""
    fields, lists = _fields(spec), spec.get("lists") or {}
    out: dict[str, str] = {}
    for s in sl:
        if not s["fillable"]:
            continue
        names = {_norm(s["key"]), _norm(s["label"])}
        if s["kind"] in LIST_KINDS:
            for lk, l in lists.items():
                if names & {_norm(lk), _norm(l.get("label", lk)), *(_norm(a) for a in l.get("aliases") or [])}:
                    out[s["key"]] = lk
                    cols = l.get("columns") or {}
                    for c in s.get("columns") or []:
                        hit = next((ck for ck, cl in cols.items() if _norm(c) in {_norm(ck), _norm(cl)}), None)
                        if hit:
                            out[f"{s['key']}.{c}"] = f"{lk}.{hit}"
                    break
            if s["key"] not in out and len(lists) == 1:
                out[s["key"]] = next(iter(lists))
            continue
        hit = next((fk for fk, f in fields.items() if names & f["names"]), None)
        if hit:
            out[s["key"]] = hit
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
    """Fill the saved template with a run's values; returns the filler's report plus the gate's checks."""
    sl = saved.get("slots") or slots(tf.inspect(path))
    mapping = saved.get("mapping") or {}
    tvals = template_values(sl, mapping, values)
    targets = {s["key"]: s["key"] for s in sl if s["kind"] == "target" and s["key"] in tvals}
    report = tf.fill(path, out_path, tvals, targets)
    if report["kind"] == "xlsx":
        _recalc_on_open(out_path)
    mapped = {s["key"] for s in sl if mapping.get(s["key"]) and s["fillable"]}
    filled = set(report.get("filled") or [])
    explained = {e.get("marker") or e.get("tag") or e.get("name") or e.get("target") for e in report.get("not_filled_explained") or []}
    silent = sorted(k for k in mapped if k not in filled and k not in explained
                    and not any(str(f).startswith(k + ".") or str(f).startswith(k + "[") for f in filled))

    def ck(name, ok, detail, label):
        return {"name": name, "ok": bool(ok), "detail": detail, "label": label}
    checks = [
        ck("template.filled", bool(filled), f"เติมแล้ว {len(filled)} ช่อง", "เติมค่าลงแบบฟอร์มได้"),
        ck("template.no_silent_gap", not silent, ("ไม่ได้เติม: " + ", ".join(silent)) if silent else "ทุกช่องที่จับคู่ไว้ได้ค่า",
           "ไม่มีช่องที่หายไปเงียบ ๆ"),
        ck("template.formulas_kept", not report.get("formulas_changed"),
           f"สูตรคงเดิม {len(report.get('formulas_kept') or [])} สูตร", "ไม่เขียนทับสูตร"),
        ck("template.parts_kept", not report.get("parts_dropped"), ", ".join(report.get("parts_dropped") or []) or "ครบ",
           "ส่วนประกอบของไฟล์ครบ"),
    ]
    report["checks"] = checks
    report["passed"] = all(c["ok"] for c in checks)
    return report


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
