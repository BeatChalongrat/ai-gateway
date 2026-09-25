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


def run(req: dict) -> dict:
    from . import dataset

    op = req.get("op")
    pkg = _load_package(req.get("package", ""))
    t0 = time.monotonic()
    if op == "parse":
        f = req["file"]
        pf = pkg.parse(_in(f["input"]), f["name"], req.get("mapping") or {})
        _write_json("parsed.json", dataset.dump(pf))
        out = {"summary": pf.summary()}
        if req.get("preview"):
            out["preview"] = _preview(_in(f["input"]), f["name"], pf)
    elif op in ("preflight", "compute", "postcompute"):
        files = [dataset.load(_read_json(n)) for n in req.get("parsed", [])]
        today = _dt.date.fromisoformat(req["today"]) if req.get("today") else None
        if op == "preflight":
            out = pkg.preflight(files, message=req.get("message", ""), defaults=req.get("defaults") or {},
                                answers=req.get("answers") or {}, today=today)
        elif op == "compute":
            computed = pkg.compute(files, req["roles"], req["period"], answers=req.get("answers") or {},
                                   defaults=req.get("defaults") or {})
            _write_json("computed.json", computed)
            out = {"summary": pkg.summarize(computed)}
        else:
            computed = _read_json(req["computed"])
            out = pkg.postcompute(files, req["roles"], req["period"], computed,
                                  answers=req.get("answers") or {}, defaults=req.get("defaults") or {})
    elif op == "render":
        computed = _read_json(req["computed"])
        out = pkg.render(computed, req, in_dir=JOB_IN, out_dir=JOB_OUT)
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
