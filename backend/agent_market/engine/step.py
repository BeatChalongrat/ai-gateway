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
    # AM5: who answered each card ({answer key: {name, via}}), for a package that records it
    if with_today and isinstance(req.get("answered_by"), dict) and _accepts(fn, "answered_by"):
        out["answered_by"] = req["answered_by"]
    # AM12: a package that speaks the run's language is told it; one that reads stored snapshots (E4: the
    # pages the runner captured, handed over as files) is told where the step's files are
    if _accepts(fn, "lang"):
        out["lang"] = req.get("lang") or "th"
    if with_today and _accepts(fn, "in_dir"):
        out["in_dir"] = JOB_IN
    if with_today and _accepts(fn, "out_dir"):
        out["out_dir"] = JOB_OUT
    return out


def _write_json(name: str, obj) -> str:
    path = os.path.join(JOB_OUT, os.path.basename(name))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    return path


def _preview(path: str, name: str, pf) -> dict:
    """Headers, the proposed mapping and five raw rows — the install wizard's
    column-mapping screen (no numbers are computed here)."""
    from .headers import FIELD_LABEL, candidates_for
    from .tabular import read_workbook

    wb = read_workbook(path, name)
    sheet = next((s for s in wb.sheets if s.name == pf.sheet), None)
    if sheet is None or pf.header is None:
        return {"headers": [], "rows": [], "proposal": {}, "fields": dict(FIELD_LABEL)}
    hr = pf.header.header_row
    rows = [["" if v is None else str(v) for v in r] for r in sheet.rows[hr + 1: hr + 6]]
    proposal = {f: {"header": pf.header.headers[c], "source": pf.header.source.get(f)} for f, c in pf.header.fields.items()}
    need = ["date", "account_code", "debit", "credit", "amount", "account_name", "description", "cost_center", "dc_indicator"]
    candidates = {f: candidates_for(f, sheet, pf.header) for f in need if f not in pf.header.fields}
    return {"sheet": pf.sheet, "header_row": hr + 1, "headers": pf.header.headers, "rows": rows,
            "proposal": proposal, "candidates": candidates, "fields": dict(FIELD_LABEL), "doc_type": pf.doc_type}


def _as_original(d, read_as: str, name: str, sha256: str, converted_from: str):
    """A parsed file read from a converted copy, given back its own name wherever the copy's name was written, its own
    SHA-256 as the file's, and a note of the conversion (AM23 §1.6)."""
    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str) and read_as in v:
            return v.replace(read_as, name)
        return v

    out = walk(d)
    if isinstance(out, dict):
        if sha256 and "sha256" in out:
            out["sha256_copy"] = out["sha256"]
            out["sha256"] = sha256
        out["converted_from"] = converted_from
    return out


def _fill_template(pkg, computed: dict, t: dict, req: dict | None = None) -> dict:
    """The customer's own template filled with this run's values (AM2 Phase E): one more artifact."""
    import hashlib

    from ..deliverables import template_out

    name = os.path.basename(t["name"])
    path = os.path.join(JOB_OUT, name)
    values = dict(pkg.template_values(computed, req) if _accepts(pkg.template_values, "req") else pkg.template_values(computed))
    if "company" in values and not str(values.get("company") or "").strip():
        # AM8: the company a form names is a real one — the install's (the theme carries it), else the
        # organisation's name — or the slot is listed as having no value; never a stand-in
        req = req or {}
        values["company"] = (str((req.get("theme") or {}).get("company") or "").strip()
                             or str((req.get("run") or {}).get("organisation") or "").strip())
    # AM22 §2.2: the fields this round has no concept of, in the package's own words (taken off the form's face)
    early = pkg.template_gaps(computed) if hasattr(pkg, "template_gaps") else {}
    rep = template_out.fill(_in(t["input"]), path, t.get("saved") or {}, values, (early or {}).get("not_applicable"))
    if hasattr(pkg, "template_gaps"):
        # AM12: a draft says on its own face what it lacks — the form's blanks that are still empty (read
        # from the filled file, not from the mapping) and what the package knows cannot be known — and the
        # file is opened once more to see that every one of them is there
        from ..deliverables import form_regions

        gaps = pkg.template_gaps(computed)
        whys = gaps.get("form_why") or {}
        rows = list(gaps.get("rows") or []) + [[f"{gaps.get('form_blank') or ''}: {u.get('label') or u.get('slot')}".lstrip(": "),
                                                 whys.get(u.get("reason") or "", "") or whys.get("other") or u.get("why") or ""]
                                                for u in rep.get("unfilled") or []]
        printed = {**gaps, "rows": rows}
        if rows:
            printed["first_page"] = form_regions.append_gaps(path, rep["kind"], printed)
        missing = form_regions.gaps_printed(path, rep["kind"], printed) if rows else []
        words = gaps.get("check") or {}
        rep["checks"].append({"name": "template.gaps_printed", "ok": not missing, "label": words.get("label") or "",
                              "detail": (words.get("missing") or "{list}").replace("{list}", ", ".join(missing)[:240]) if missing
                              else (words.get("ok") or "{n}").replace("{n}", str(len(rows))), "values": len(rows)})
        rep["passed"] = all(c["ok"] for c in rep["checks"])
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    from ..deliverables import agree

    # AM23 Part 2: the figures the filled form holds — every slot its gate found holding its value, a form with blanks
    # nothing can fill included — for the check that every file says the same number (taken off the artifact once compared)
    printed = agree.form_printed(pkg, values, t.get("saved") or {}, rep, computed.get("figures") or {})
    # AM8: what the filled file was found to lack travels with its quality, which the download card shows —
    # every slot without its value, by name (a reason kept only in the run's JSON reached nobody)
    return {"name": name, "format": "template", "kind": "template", "size": os.path.getsize(path), "sha256": sha,
            "quality": {"passed": rep["passed"], "checks": rep["checks"], "unfilled": rep.get("unfilled") or []},
            "chart_bindings": [],
            "meta": {"ext": rep["kind"], "needs_recalc": bool(rep.get("needs_recalc")), "filled": len(rep.get("filled") or []),
                     "not_filled": rep.get("not_filled_explained") or [], "refused": rep.get("refused") or {},
                     "unfilled": rep.get("unfilled") or [],
                     # AM22: the places this round had no concept of, taken off the face — recorded, not printed
                     "not_applicable": [{k: o.get(k) for k in ("key", "label", "field", "why", "done")}
                                        for o in rep.get("not_applicable") or []]},
            "_printed": printed}


def run(req: dict) -> dict:
    from . import dataset, words

    op = req.get("op")
    t0 = time.monotonic()
    words.use(req.get("lang"))  # AM14: the run's language, for every sentence this step says
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
        # AM23 §1.6: a file converted on the way in (an .xls to .xlsx, a .doc to PDF) is read under the name of its
        # converted kind; what is kept is the original's — its name, and its SHA-256, which the run cites
        read_as = f.get("as") or f["name"]
        pf = pkg.parse(_in(f["input"]), read_as, req.get("mapping") or {})
        dumped = dump(pf)
        if f.get("as"):
            dumped = _as_original(dumped, read_as, f["name"], f.get("sha256") or "", f.get("converted_from") or "")
        _write_json("parsed.json", dumped)
        out = {"summary": pf.summary()}
        if hasattr(pkg, "parse_extras"):
            out["summary"].update(pkg.parse_extras(pf, _in(f["input"]), JOB_OUT))
        if req.get("preview"):
            out["preview"] = _preview(_in(f["input"]), f["name"], pf)
    elif op in ("preflight", "compute", "postcompute"):
        # AM12: a round that starts from a request has no file at all ("parsed" is then null, not a list)
        files = [load(_read_json(n)) for n in req.get("parsed") or []]
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
                                  answers=req.get("answers") or {}, defaults=req.get("defaults") or {},
                                  **({"lang": req.get("lang") or "th"} if _accepts(pkg.postcompute, "lang") else {}))
    elif op == "plan":
        # AM23 §1.5: what of the files asked for this run's content cannot be, before any is written
        computed = _read_json(req["computed"])
        out = {"refusals": pkg.plan(computed, req) if hasattr(pkg, "plan") else []}
    elif op == "render":
        computed = _read_json(req["computed"])
        out = pkg.render(computed, req, in_dir=JOB_IN, out_dir=JOB_OUT)
        # AM9: what the runner's print proof looks for on the rendered pages of every working paper
        from ..deliverables import workpaper

        for a in out.get("artifacts") or []:
            if a.get("format") == "xlsx" and a.get("kind") != "template" and os.path.exists(os.path.join(JOB_OUT, a["name"])):
                a["print_rows"] = workpaper.print_rows(os.path.join(JOB_OUT, a["name"]))
        # AM12: a package says when there is nothing of a round to put in a form (nothing was chosen)
        wanted = pkg.template_wanted(computed) if hasattr(pkg, "template_wanted") else True
        if req.get("template") and hasattr(pkg, "template_values") and wanted:
            art = _fill_template(pkg, computed, req["template"], req)
            out["artifacts"] = list(out.get("artifacts") or []) + [art]
            out["passed"] = bool(out.get("passed", True)) and art["quality"]["passed"]
        elif req.get("template") and hasattr(pkg, "template_values"):
            # AM22 §2.4: nothing of this round goes in a form — no file, and a line that says why (a form full of
            # blanks is worse than no form)
            why = pkg.template_why_not(computed) if hasattr(pkg, "template_why_not") else ""
            out["template_not_made"] = {"why": why}
        # AM23 Part 2: every file of the round says the same number — read back from the finished files, this render's
        # and the round's made before it; a figure printed two ways fails the two files that print it
        from ..deliverables import agree

        arts = out.get("artifacts") or []
        prior = [{**p, "path": _in(p["input"])} for p in req.get("prior") or [] if p.get("input")]
        res = agree.check(JOB_OUT, arts, computed, pkg, prior=prior)
        if res is not None:
            if not agree.attach(res, arts):
                out["passed"] = False
            out["agree"] = {k: res[k] for k in ("ok", "counts", "files", "bad") if k in res}
        for a in arts:
            a.pop("_printed", None)
        # AM23 §3.3: no hedge of the package's (its manifest's narrative_hedges, the list the runner holds a model's
        # answer to) in the commentary of any finished file that prints it — its sentences and its headings, as printed
        # (the file's commentary gate proves they are in the file as written); the round's own data is not the
        # commentary's to answer for (a claim's description may say "ตามความเหมาะสม")
        from ..deliverables.look import commentary as _commentary

        hedges = words.manifest(pkg.__name__).get("narrative_hedges") or []
        narrative = req.get("narrative") or computed.get("fallback_narrative") or {}
        said = _commentary.rows(narrative, computed) if hedges and narrative.get("summary") else []
        printed = [t for _, t, _ in said] + [_commentary.heading(narrative, p, computed) for p in dict.fromkeys(p for p, _, _ in said)]
        first = "".join(said[0][1].split()) if said else ""
        for a in arts if first else []:
            path = os.path.join(JOB_OUT, a.get("name") or "")
            if a.get("kind") == "template" or not os.path.exists(path) or first not in "".join("".join(_commentary.file_texts(path)).split()):
                continue
            chk = _commentary.no_hedge(printed, hedges)
            a["quality"]["checks"].append(chk)
            if not chk["ok"]:
                a["quality"]["passed"] = False
                out["passed"] = False
    elif op == "inspect_template":
        from ..deliverables import template_fill, template_out

        f = req["file"]
        spec = req.get("spec") or {}
        inspection = template_fill.inspect(_in(f["input"]), reads=tuple(spec.get("reads") or ()))
        sl = template_out.slots(inspection)
        saved = req.get("saved") or None
        proposal = template_out.propose(sl, spec)
        # AM8: identity is all the code proposes; the runner asks a model for the rest (the person confirms).
        # What the filler cannot do stops the template before any work, with its reasons.
        out = {"inspection": inspection, "slots": sl, "proposal": template_out.carry_over(saved, sl, proposal),
               "diff": template_out.diff(saved, sl), "blocking": template_out.blocking(sl)}
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
