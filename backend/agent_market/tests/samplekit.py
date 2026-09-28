"""A package's self-test, run the way agent-runner runs it (AM3).

    render_sample("invoice_processing")  →  the files the self-test delivers, on disk

Every step goes through engine/step.py with files on disk (parse → parsed.json → preflight →
compute → render), on the package's own sample-data (self-test.json "files"), with the
install defaults the runner gives a self-test: the manifest's install defaults, then the
install's own settings, then the self-test's "settings" (the identity the sample data was
made for — the runner lays them over the install's so a person's real company does not turn
the sample invoices into "not addressed to your company" questions). A finding left over is
answered with its default, as a skipped card is.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import tempfile

from agent_market.engine import step

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TODAY = _dt.date(2026, 9, 30)


def package_dir(package: str) -> str:
    return os.path.join(ROOT, "packages", package)


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def manifest(package: str) -> dict:
    return _load(os.path.join(package_dir(package), "agent.json"))


def self_test(package: str) -> dict:
    return _load(os.path.join(package_dir(package), "self-test.json"))


def self_test_defaults(package: str, install: dict | None = None, *, overlay: bool = True) -> dict:
    """What a self-test run's engine defaults are: manifest defaults < the install's < the self-test's settings."""
    d = dict(manifest(package)["install"].get("defaults") or {})
    d.update(install or {})
    if overlay:
        d.update(self_test(package).get("settings") or {})
    return d


def render_sample(package: str, *, out_dir: str | None = None, defaults: dict | None = None,
                  formats: list[str] | None = None) -> dict:
    st = self_test(package)
    defaults = self_test_defaults(package) if defaults is None else defaults
    formats = formats or st.get("formats") or ["xlsx"]
    files = [(os.path.join(package_dir(package), rel), os.path.basename(rel)) for rel in st["files"]]
    return run_files(package, files, defaults=defaults, formats=formats, message=st.get("message", ""),
                     out_dir=out_dir)


def run_files(package: str, files: list[tuple[str, str]], *, defaults: dict, formats: list[str],
              message: str = "", out_dir: str | None = None, answers: dict | None = None) -> dict:
    """The whole package on (path, the name the person gave the file) pairs, in upload order (AM4).
    answers are the ones the person has already given (AM5); a card they do not answer is skipped."""
    out_dir = out_dir or tempfile.mkdtemp(prefix=f"am_sample_{package}_")
    base = tempfile.mkdtemp(prefix="am_sample_job_")
    inp, out = os.path.join(base, "in"), os.path.join(base, "out")
    os.makedirs(inp)
    os.makedirs(out)
    step.JOB_IN, step.JOB_OUT = inp, out

    def run(req):
        res = step.run(req)
        assert res.get("ok"), res
        return res

    try:
        parsed = []
        for i, (src, display) in enumerate(files):
            name = f"in_{i:02d}{os.path.splitext(src)[1]}"
            shutil.copy(src, os.path.join(inp, name))
            res = run({"op": "parse", "package": package, "file": {"name": display, "input": name},
                       "mapping": defaults.get("mapping") or {}})
            shutil.move(os.path.join(out, "parsed.json"), os.path.join(inp, f"parsed_{i:02d}.json"))
            parsed.append(f"parsed_{i:02d}.json")
            for n in res["summary"].get("vision_pages", []) or []:
                p = os.path.join(out, n)
                if os.path.exists(p):
                    os.remove(p)
        req = {"package": package, "parsed": parsed, "defaults": defaults, "today": TODAY.isoformat()}
        answers = dict(answers or {})
        first = None
        for _ in range(8):
            pre = run({**req, "op": "preflight", "answers": answers, "message": message})
            if first is None:
                first = pre
            new = [f for f in pre["findings"] if f["key"] not in answers]
            if not new:
                break
            for f in new:
                answers[f["key"]] = f["default"]
        comp = run({**req, "op": "compute", "roles": pre["roles"], "period": pre["period"], "answers": answers,
                    "today": pre.get("today") or TODAY.isoformat()})
        shutil.move(os.path.join(out, "computed.json"), os.path.join(inp, "computed.json"))
        computed = _load(os.path.join(inp, "computed.json"))
        names = {f: f"sample.{f}" for f in formats}
        rend = run({"op": "render", "package": package, "computed": "computed.json", "formats": formats, "names": names,
                    "theme": {"id": "navy", "company": defaults.get("company") or ""}, "assumptions": [],
                    "run": {"id": "sample", "date": TODAY.isoformat(), "agent_version": manifest(package)["version"]},
                    "model_info": {"model": None, "self_hosted": None}})
        paths = {}
        for a in rend["artifacts"]:
            dst = os.path.join(out_dir, a["name"])
            shutil.copy(os.path.join(out, a["name"]), dst)
            paths[a["format"]] = dst
        return {"paths": paths, "render": rend, "first_findings": first["findings"], "answers": answers,
                "computed": computed, "summary": comp.get("summary"), "out_dir": out_dir,
                "roles": pre["roles"], "period": pre["period"], "assumptions": pre.get("assumptions") or []}
    finally:
        shutil.rmtree(base, ignore_errors=True)
