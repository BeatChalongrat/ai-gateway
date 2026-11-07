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
  again and asserts it — every mapped slot holds its value and sits beside
  the label it was mapped under; every blank an unmarked form expects filled
  holds something, mapped or not (AM11: a form with one field of ten filled
  is not "every slot filled"); no {{marker}} is left anywhere, no filled text is cut off by a fixed-height row or box, the
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

from ..engine.words import SHARED as S
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
    info = inspection.get("target_info") or {}
    for t in inspection.get("targets") or []:
        slot = {"key": t["cell"], "kind": "target", "label": t.get("label") or t["cell"], "where": t["cell"],
                "fillable": True, "reason": None}
        # AM11: a blank the form expects filled is a gap when the delivered file leaves it empty (verify)
        i = info.get(t["cell"])
        if i:
            slot.update({"required": bool(i.get("required")), "side": i.get("side"), "label_at": i.get("label_at")})
            # AM22: a blank that is a merged range says so; one inside a range is refused by name
            if i.get("merged"):
                slot["merged"] = i["merged"]
            if i.get("fillable") is False:
                slot.update(fillable=False, reason=i.get("reason"))
        out.append(slot)
    # AM12: the rows of a table of items in a form with no marker — one place that takes a list, its
    # columns the headings over the rows; and a blank drawn as a line after its label
    for g in inspection.get("regions") or []:
        out.append({"key": g["key"], "kind": "table", "label": g["label"], "where": g.get("where"), "fillable": True,
                    "reason": None, "columns": g["columns"], "column_labels": g["column_labels"], "rows": g["rows"],
                    "context": " | ".join(f"{c} = {g['column_labels'][c]}" for c in g["columns"])})
    for b in inspection.get("lines") or []:
        out.append({"key": b["key"], "kind": "target", "label": b["label"], "where": b["key"], "fillable": True, "reason": None,
                    "required": bool(b["required"]), "side": "inline", "label_at": b["key"], "context": b.get("context") or ""})
    for sh in inspection.get("shapes") or []:
        out.append({"key": sh["key"], "kind": "shape", "label": sh["name"], "where": S("form.where.slide", slide=sh["slide"]),
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


# AM22 §2.4: what a slot is, besides its key — a new version whose key is the same and whose label, kind, place
# or columns are not is the same address holding something else ("moved"), never "kept"
IDENTITY = ("kind", "label", "label_at", "side", "where", "columns", "column_labels", "merged", "fillable")


def _same_slot(a: dict, b: dict) -> bool:
    return all(_norm(str(a.get(k) or "")) == _norm(str(b.get(k) or "")) if k in ("label",) else a.get(k) == b.get(k)
               for k in IDENTITY)


def diff(saved: dict | None, sl: list[dict]) -> dict:
    """A new version against the saved template: the slots kept (their mapping kept), added, removed — and
    moved (AM22): the same key whose label, kind or place changed. Anvil's field detection ran only on the first
    upload, and a PDF swapped under an existing template kept its old fields; a key compared by name alone
    would say "kept" here and carry the old mapping into the wrong cell."""
    if not saved:
        return {"first": True, "kept": [], "added": [s["key"] for s in sl], "removed": [], "moved": []}
    old = {s["key"]: s for s in saved.get("slots") or []}
    new = {s["key"]: s for s in sl}
    both = sorted(set(old) & set(new))
    moved = [k for k in both if not _same_slot(old[k], new[k])]
    return {"first": False, "kept": [k for k in both if k not in moved], "added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)), "moved": moved}


def slot_of(key: str, keys) -> str:
    """The slot a mapping key belongs to: the key itself, or — for a column of a table of items, "<slot>.<column>" —
    the slot before its last dot. (AM22: a Word blank's own key has a dot in it, "table0.r1c1"; split at its first
    dot it was "table0", and a saved mapping of a Word form's blanks was never carried to its next version.)"""
    if key in keys:
        return key
    return key.rsplit(".", 1)[0] if "." in key else key


def carry_over(saved: dict | None, sl: list[dict], proposal: dict) -> dict:
    """The mapping to show for a new version: the saved choice for every kept slot, the proposal for the rest.
    A moved slot (AM22) is not kept: the person confirms it again."""
    mapping = dict(proposal)
    if saved:
        keep = set(diff(saved, sl)["kept"])
        for k, v in (saved.get("mapping") or {}).items():
            if slot_of(k, keep) in keep:
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


def fill(path: str, out_path: str, saved: dict, values: dict, not_applicable: dict | None = None) -> dict:
    """Fill the saved template with a run's values; then open the filled file again and judge it (``verify``).
    Returns the filler's report plus the checks and ``unfilled`` — every slot that does not hold its value,
    by name, for the person.

    AM22 §2.2: ``not_applicable`` is the package's word on the fields this round has no concept of ({field:
    why}). A slot mapped to one is not a gap: it is taken off the form's face (template_fill.drop_not_applicable)
    and recorded in ``report["not_applicable"]`` — a reader cannot act on it, and it would make a complete form
    look incomplete. Only the package decides which fields those are; the filler never guesses from emptiness
    (a blank from no data, from a name that does not match and from a permission look the same)."""
    sl = saved.get("slots") or slots(tf.inspect(path))
    mapping = saved.get("mapping") or {}
    na = not_applicable or {}
    off = [{"key": s["key"], "label": s.get("label") or s["key"], "label_at": s.get("label_at"), "field": mapping[s["key"]].split(".", 1)[0],
            "why": na[mapping[s["key"]].split(".", 1)[0]]}
           for s in sl if s.get("fillable") and mapping.get(s["key"]) and mapping[s["key"]].split(".", 1)[0] in na]
    off_keys = {o["key"] for o in off}
    mapping = {k: v for k, v in mapping.items() if slot_of(k, off_keys) not in off_keys}
    tvals = template_values(sl, mapping, values)
    targets = {s["key"]: s["key"] for s in sl if s["kind"] == "target" and s["key"] in tvals}
    report = tf.fill(path, out_path, tvals, targets)
    report["regions"] = ([g for g in (tf.inspect(path, reads=("rows",)).get("regions") or []) if g["key"] in tvals]
                         if any(tf.fr.ROWS_RE.match(str(k)) for k in tvals) else [])
    report["not_applicable"], removed = tf.drop_not_applicable(out_path, report["kind"], off)
    report["moved"] = list(report.get("moved") or []) + removed
    if report["kind"] == "xlsx":
        _recalc_on_open(out_path)
    checks, unfilled = verify(path, out_path, sl, mapping, tvals, report)
    report["checks"] = checks
    report["unfilled"] = unfilled
    report["passed"] = all(c["ok"] for c in checks)
    return report


# why a field of the form was not filled, said when it is read
WHY = S.table({"no_value": "form.why.no_value", "not_in_file": "form.why.not_in_file",
          "not_mapped": "form.why.not_mapped",
          "wrong_label": "form.why.wrong_label",
          "marker_left": "form.why.marker_left", "cut_off": "form.why.cut_off",
          "unsupported_location": "form.why.unsupported_location",
          "chart_not_updated": "form.why.chart_not_updated",
          "merged_cell": "form.why.merged_cell", "formula_cell": "form.why.formula_cell", "protected": "form.why.protected",
          "not_plain_text": "form.why.not_plain_text", "content_locked": "form.why.content_locked",
          "rows_short": "form.why.rows_short"})


def _why(reason) -> str:
    code = str(reason or "")
    return WHY[code] if code in WHY else (code or S("form.why.cannot"))


_NO_VALUE = object()


def verify(src: str, out: str, sl: list[dict], mapping: dict, tvals: dict, report: dict) -> tuple[list[dict], list[dict]]:
    """The filled file, opened again (AM8): the gate's checks, and every slot left without its value."""
    kind = report["kind"]
    by_key = {s["key"]: s for s in sl}
    unfilled: dict[str, dict] = {}

    def miss(key: str, why: str) -> None:
        s = by_key.get(key) or {"label": key}
        unfilled.setdefault(key, {"slot": key, "label": s.get("label") or key, "why": _why(why),
                                  "reason": str(why or "")})

    reasons: dict = {}
    for e in report.get("not_filled_explained") or []:
        k = e.get("marker") or e.get("tag") or e.get("name") or e.get("target")
        if k:
            reasons.setdefault(k, e.get("reason"))
    for k, why in (report.get("refused") or {}).items():
        reasons.setdefault(k, why)
    mapped = [s for s in sl if mapping.get(s["key"]) and s["fillable"]]
    regions = {g["key"]: g for g in report.get("regions") or []}
    moves = report.get("moved") or []

    def there(s: dict) -> dict:
        """AM12: the slot as the filled file has it — rows added to a table of items move what is under them."""
        if not moves:
            return s
        return {**s, "key": tf.fr.moved_key(s["key"], moves), "label_at": tf.fr.moved_key(s.get("label_at") or "", moves) or s.get("label_at")}

    misplaced: list[str] = []
    for s in mapped:
        k = s["key"]
        if s["kind"] in LIST_KINDS:
            region = regions.get(k)
            if not tvals.get(k):
                miss(k, reasons.get(k) or "no_value")
            elif region is not None:
                # AM12: the rows of an unmarked form, opened again — every line under the headings it was
                # mapped by; a form with fewer rows than lines says so
                written = tvals[k][: int((report.get("rows") or {}).get(k) or 0)]
                if kind == "xlsx":
                    ok, why = tf.fr.xlsx_rows_hold(tf.openpyxl.load_workbook(out), region, written, tf._same_value)
                else:
                    ok, why = tf.fr.docx_rows_hold(tf.Document(out), region, written)
                if (report.get("rows_short") or {}).get(k):
                    miss(k, "rows_short")
                elif not ok or not written:
                    miss(k, why or reasons.get(k) or "not_in_file")
            elif kind != "xlsx" and int((report.get("rows") or {}).get(k) or 0) < len(tvals[k]):
                miss(k, reasons.get(k) or "not_in_file")
            continue
        value = tvals.get(k, _NO_VALUE)
        s = there(s)
        if value is _NO_VALUE or not (tf.dg.as_text(value) or "").strip():
            miss(k, reasons.get(k) or "no_value")
        elif not tf.holds(out, kind, s, value):
            miss(k, reasons.get(k) or "not_in_file")
        elif s["kind"] == "target" and not tf.label_beside(out, kind, s):
            misplaced.append(s.get("label") or k)
            miss(k, "wrong_label")
    # AM11: the gap list is the file's, not the mapping's. A blank the form expects filled, that the
    # delivered file still holds empty, is named — whether nobody mapped it or a person left it "not used".
    # AM22: not a place the package said this round has no concept of (taken off the face, recorded).
    off = {o["key"]: o for o in report.get("not_applicable") or []}
    expected = [s for s in sl if s.get("kind") == "target" and s.get("required") and s["fillable"]
                and not mapping.get(s["key"]) and s["key"] not in off]
    for s in expected:
        if tf.still_blank(out, kind, there(s)):
            miss(s["key"], "not_mapped")
    # AM22 §2.4: a blank the filler cannot write (inside a merged range) is named with its reason, never skipped
    refused_blanks = [s for s in sl if s.get("kind") == "target" and s.get("required") and not s["fillable"]]
    for s in refused_blanks:
        miss(s["key"], s.get("reason") or "cannot")
    # AM22: a blank that is a merged range is still one range in the filled file
    for s in mapped:
        if s.get("merged") and kind == "xlsx" and not tf.still_merged(out, s["key"], s["merged"]):
            miss(s["key"], "merged_cell")
    asked = len(mapped) + len(expected) + len(refused_blanks)
    off_face = [o for o in off.values() if not tf.not_applicable_off(out, kind, {**o, "key": there(o)["key"]})]
    left = tf.markers_left(out)
    for k in left:
        miss(k, "marker_left")
    cut = tf.cut_off(src, out)
    if kind == "pdf":  # AM12: a value longer than its field, as the filler measured it
        cut = [(by_key.get(k) or {}).get("label") or k for k in report.get("cut") or []]
    charts = tf.chart_parts(out) if kind in ("docx", "pptx") else []
    filled = report.get("filled") or []

    def ck(name, ok, detail, label, values=None):
        c = {"name": name, "ok": bool(ok), "detail": detail, "label": label}
        if values is not None:
            c["values"] = values
        return c
    gaps = [u for u in unfilled.values() if u["why"] != _why("marker_left")]
    checks = [
        ck("template.filled", bool(filled), S("form.filled.detail", n=len(filled)), S("form.filled.label")),
        # the slots by name are in ``unfilled`` (the form's own names, shown on the file's row); a check's
        # detail says how many, in the package's words
        ck("template.every_slot", not gaps,
           S("form.every.gaps", n=len(gaps), asked=asked) if gaps
           else S("form.every.ok", asked=asked),
           S("form.every.label"), asked),
        # AM11: a value written beside another label than the one it was mapped for is in the wrong slot
        ck("template.beside_label", not misplaced,
           S("form.beside.bad", fields=", ".join(misplaced))[:300] if misplaced
           else S("form.beside.ok"),
           S("form.beside.label")),
        ck("template.no_marker_left", not left,
           S("form.marker.left", n=len(left)) if left else S("form.marker.none"),
           S("form.marker.none")),
        ck("template.fits", not cut, S("form.fits.cut", places=", ".join(cut))[:300] if cut else S("form.fits.ok"),
           S("form.fits.label")),
        ck("template.no_static_chart", not charts,
           S("form.chart.static", n=len(charts)) if charts else S("form.chart.none"),
           S("form.chart.label")),
        ck("template.formulas_kept", not report.get("formulas_changed"),
           S("form.formulas.kept", n=len(report.get("formulas_kept") or [])), S("form.formulas.label")),
        ck("template.parts_kept", not report.get("parts_dropped"), ", ".join(report.get("parts_dropped") or []) or S("form.parts.ok"),
           S("form.parts.label")),
    ]
    if off:
        # AM22 §2.2: what the round has no concept of is off the form's face, read from the file
        checks.append(ck("template.not_applicable", not off_face,
                         S("form.na.left", fields=", ".join(o["label"] for o in off_face))[:300] if off_face
                         else S("form.na.ok", n=len(off)), S("form.na.label"), len(off)))
    for where in cut:
        unfilled.setdefault(f"cut:{where}", {"slot": f"cut:{where}", "label": where, "why": _why("cut_off")})
    for c in charts:
        unfilled.setdefault(c, {"slot": c, "label": S("form.chart.name"), "why": _why("chart_not_updated")})
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
