"""Sandbox entry point: one step of one run.

agent-runner launches the stock sandbox image in code mode with this
engine as a read-only zip on /job/in and runs:

    import sys; sys.path[0:0] = ['/job/in/am_engine.zip', '/sandbox/scripts']
    from agent_market.engine.step import main; sys.exit(main())

The request is /job/in/request.json; large outputs (parsed.json,
computed.json, deliverables) are written to /job/out and come back to the
runner as artifacts; the small result is printed as one JSON line on stdout.
A step either returns {"ok": true, ...} or {"ok": false, "error": ...} —
never a traceback in place of a result.
"""

from __future__ import annotations

import datetime as _dt
import importlib
import json
import os
import sys
import time
import traceback

JOB_IN = os.environ.get("AM_JOB_IN", "/job/in")
JOB_OUT = os.environ.get("AM_JOB_OUT", "/job/out")


def _load_package(name: str):
    if not name.replace("_", "").isalnum():
        raise ValueError(f"bad package name {name!r}")
    return importlib.import_module(f"agent_market.packages.{name}")


def _in(name: str) -> str:
    base = os.path.basename(name)
    if not base or base.startswith("."):
        raise ValueError(f"bad input name {name!r}")
    return os.path.join(JOB_IN, base)


def _read_json(name: str):
    with open(_in(name), encoding="utf-8") as f:
        return json.load(f)


def _accepts(fn, name: str) -> bool:
    import inspect

    return name in inspect.signature(fn).parameters


def _extra(req: dict, fn, with_today: bool = False) -> dict:
    """Optional run context for packages that take it (AM2): the duplicate index (``history``), a
    model's readings (``proposals``), and the run's date for compute. A package whose function does
    not name a parameter never receives it."""
    out = {}
    for k in ("history", "proposals"):
        if req.get(k) and _accepts(fn, k):
            out[k] = _read_json(req[k])
    if with_today and req.get("today") and _accepts(fn, "today"):
        out["today"] = _dt.date.fromisoformat(req["today"])
    return out


def _write_json(name: str, obj) -> str:
    path = os.path.join(JOB_OUT, os.path.basename(name))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    return path


def _preview(path: str, name: str, pf) -> dict:
    """Headers, the proposed mapping and five raw rows — the install wizard's
    column-mapping screen (no numbers are computed here)."""
    from .headers import FIELD_LABEL_TH, candidates_for
    from .tabular import read_workbook

    wb = read_workbook(path, name)
    sheet = next((s for s in wb.sheets if s.name == pf.sheet), None)
    if sheet is None or pf.header is None:
        return {"headers": [], "rows": [], "proposal": {}, "fields": FIELD_LABEL_TH}
    hr = pf.header.header_row
    rows = [["" if v is None else str(v) for v in r] for r in sheet.rows[hr + 1: hr + 6]]
    proposal = {f: {"header": pf.header.headers[c], "source": pf.header.source.get(f)} for f, c in pf.header.fields.items()}
    need = ["date", "account_code", "debit", "credit", "amount", "account_name", "description", "cost_center", "dc_indicator"]
    candidates = {f: candidates_for(f, sheet, pf.header) for f in need if f not in pf.header.fields}
    return {"sheet": pf.sheet, "header_row": hr + 1, "headers": pf.header.headers, "rows": rows,
            "proposal": proposal, "candidates": candidates, "fields": FIELD_LABEL_TH, "doc_type": pf.doc_type}


def _fill_template(pkg, computed: dict, t: dict) -> dict:
    """The customer's own template filled with this run's values (AM2 Phase E): one more artifact."""
    import hashlib

    from ..deliverables import template_out

    name = os.path.basename(t["name"])
    path = os.path.join(JOB_OUT, name)
    rep = template_out.fill(_in(t["input"]), path, t.get("saved") or {}, pkg.template_values(computed))
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    return {"name": name, "format": "template", "kind": "template", "size": os.path.getsize(path), "sha256": sha,
            "quality": {"passed": rep["passed"], "checks": rep["checks"]}, "chart_bindings": [],
            "meta": {"ext": rep["kind"], "needs_recalc": bool(rep.get("needs_recalc")), "filled": len(rep.get("filled") or []),
                     "not_filled": rep.get("not_filled_explained") or [], "refused": rep.get("refused") or {}}}


def run(req: dict) -> dict:
    from . import dataset

    op = req.get("op")
    t0 = time.monotonic()
    if op == "stamp_review":
        # AM3: a reviewer's sign-off on the face of a run's files — a platform op
        # (no package): the runner runs it with its current engine.
        from ..deliverables import signoff

        out = signoff.stamp_files(req.get("files") or [], req.get("review") or {}, JOB_IN, JOB_OUT)
        out.update(ok=True, op=op, ms=int((time.monotonic() - t0) * 1000),
                   engine=__import__("agent_market").ENGINE_VERSION)
        return out
    pkg = _load_package(req.get("package", ""))
    dump = getattr(pkg, "dump", dataset.dump)
    load = getattr(pkg, "load", dataset.load)
    if op == "parse":
        f = req["file"]
        pf = pkg.parse(_in(f["input"]), f["name"], req.get("mapping") or {})
        _write_json("parsed.json", dump(pf))
        out = {"summary": pf.summary()}
        if hasattr(pkg, "parse_extras"):
            out["summary"].update(pkg.parse_extras(pf, _in(f["input"]), JOB_OUT))
        if req.get("preview"):
            out["preview"] = _preview(_in(f["input"]), f["name"], pf)
    elif op in ("preflight", "compute", "postcompute"):
        files = [load(_read_json(n)) for n in req.get("parsed", [])]
        today = _dt.date.fromisoformat(req["today"]) if req.get("today") else None
        if op == "preflight":
            out = pkg.preflight(files, message=req.get("message", ""), defaults=req.get("defaults") or {},
                                answers=req.get("answers") or {}, today=today, **_extra(req, pkg.preflight))
        elif op == "compute":
            computed = pkg.compute(files, req["roles"], req["period"], answers=req.get("answers") or {},
                                   defaults=req.get("defaults") or {}, **_extra(req, pkg.compute, with_today=True))
            _write_json("computed.json", computed)
            out = {"summary": pkg.summarize(computed)}
        else:
            computed = _read_json(req["computed"])
            out = pkg.postcompute(files, req["roles"], req["period"], computed,
                                  answers=req.get("answers") or {}, defaults=req.get("defaults") or {})
    elif op == "render":
        computed = _read_json(req["computed"])
        out = pkg.render(computed, req, in_dir=JOB_IN, out_dir=JOB_OUT)
        if req.get("template") and hasattr(pkg, "template_values"):
            art = _fill_template(pkg, computed, req["template"])
            out["artifacts"] = list(out.get("artifacts") or []) + [art]
            out["passed"] = bool(out.get("passed", True)) and art["quality"]["passed"]
    elif op == "inspect_template":
        from ..deliverables import template_fill, template_out

        f = req["file"]
        inspection = template_fill.inspect(_in(f["input"]))
        sl = template_out.slots(inspection)
        saved = req.get("saved") or None
        spec = req.get("spec") or {}
        proposal = template_out.propose(sl, spec)
        out = {"inspection": inspection, "slots": sl, "proposal": template_out.carry_over(saved, sl, proposal),
               "diff": template_out.diff(saved, sl)}
    else:
        raise ValueError(f"unknown op {op!r}")
    out["ok"] = True
    out["op"] = op
    out["ms"] = int((time.monotonic() - t0) * 1000)
    out["engine"] = __import__("agent_market").ENGINE_VERSION
    out["package_version"] = getattr(pkg, "VERSION", None)
    return out


def main(request_path: str | None = None) -> int:
    try:
        with open(request_path or os.path.join(JOB_IN, "request.json"), encoding="utf-8") as f:
            req = json.load(f)
        result = run(req)
    except Exception as exc:  # noqa: BLE001 — the runner reads a result, never a bare traceback
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000],
                  "trace": traceback.format_exc()[-4000:]}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
