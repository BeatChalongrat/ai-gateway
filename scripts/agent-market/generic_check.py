"""Is an agent generic? The fresh-install test's last step: look inside the package and inside what
its self-test delivers for anything that belongs to one organisation.

    bash scripts/agent-market/generic-check.sh [package ...]          (in the sandbox image, where the agents run)
    bash scripts/agent-market/generic-check.sh --out <dir> ...        (also look inside the files in <dir>: a run's deliverables)

An agent whose manifest says ``"scope": "generic"`` must install at another company with nobody
touching it, so nothing about a company may be in its files: a name, an id, an account code, a host
name. This script opens every file of the package the way a person could — a workbook, a document
or a deck is unzipped, a PDF's text and its metadata are read — and reports every line that holds
one of the patterns below. It also runs the package's self-test on its own sample data (the way
tests/samplekit.py does) and looks inside what comes out.

The publisher's namespace at the start of a package id ("sran.bank-reconciliation") names who
published the package, the same at every company that installs it. It is reported apart, as
``namespace``, and is the one thing that does not fail the check.

Exit code: 0 when every package named is clean, 1 otherwise. The patterns are this repository's
owner's; another publisher's check lists its own.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.environ.get("AM_BACKEND") or os.path.join(ROOT, "backend")
SANDBOX_SCRIPTS = os.environ.get("AM_SANDBOX_SCRIPTS") or os.path.join(ROOT, "services", "chat-edge-api", "scripts")
PACKAGES = os.path.join(BACKEND, "agent_market", "packages")
# what belongs to the organisation this repository was written in
PATTERNS = (
    ("name", re.compile(r"sran|สราน|ส\s*ร\s*า\s*น\s*เ\s*ท\s*ค", re.I)),
    ("host", re.compile(r"gbtech|sran\.ai|\.internal\b|localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+", re.I)),
    ("tax_id", re.compile(r"0105563012343")),          # the organisation the AM2 sample documents were made out to
    ("mail", re.compile(r"[\w.+-]+@(?:gbtech|sran)[\w.-]*", re.I)),
)
NAMESPACE = re.compile(r'"(?:id|package)"\s*:\s*"sran\.[a-z0-9-]+"')
ZIPPED = (".xlsx", ".xlsm", ".docx", ".pptx", ".dotx", ".potx")
SKIP_DIRS = ("__pycache__",)
# what the runtime zip leaves out (runner/engine.go BuildZip): these directories anywhere, dot files and
# .pyc — everything else of backend/agent_market ships, an eval set's cases.json included. What does
# not ship is looked at all the same and counts the same; it is only reported apart.
NOT_SHIPPED_DIRS = {"__pycache__", "tests", "fixtures", "nasty", "tools", ".pytest_cache"}


def shipped(rel: str) -> bool:
    parts = rel.split("/")
    return not (set(parts[:-1]) & NOT_SHIPPED_DIRS) and not parts[-1].startswith(".") and not parts[-1].endswith(".pyc")


def _texts(path: str) -> list:
    """(where inside the file, text) for everything a person could read in it."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        raw = f.read()
    if ext in ZIPPED:
        out = []
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return [("", raw.decode("utf-8", "replace"))]
        for n in z.namelist():
            if n.endswith((".xml", ".rels", ".txt", ".json")):
                out.append((n, z.read(n).decode("utf-8", "replace")))
        return out
    if ext == ".pdf":
        out = [("bytes", raw.decode("latin-1"))]
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                out.append(("metadata", json.dumps({k: str(v) for k, v in (pdf.metadata or {}).items()}, ensure_ascii=False)))
                for i, page in enumerate(pdf.pages, start=1):
                    out.append((f"page {i}", page.extract_text() or ""))
        except Exception as exc:  # noqa: BLE001 — a PDF that cannot be opened is still looked at as bytes
            out.append(("unreadable", f"{type(exc).__name__}"))
        try:
            from agent_market.engine.e2 import etax

            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(raw)
            tmp.close()
            emb = etax.embedded_xml(tmp.name)
            os.unlink(tmp.name)
            if emb:
                out.append((f"attachment {emb[0]}", emb[1].decode("utf-8", "replace")))
        except Exception:  # noqa: BLE001
            pass
        return out
    if ext in (".jpg", ".jpeg", ".png", ".ttf", ".otf", ".pyc"):
        return [("bytes", raw.decode("latin-1"))]
    return [("", raw.decode("utf-8", "replace"))]


def look(path: str, shown: str) -> list:
    hits = []
    for inside, text in _texts(path):
        for n, line in enumerate(text.splitlines() or [text], start=1):
            for kind, rx in PATTERNS:
                for m in rx.finditer(line):
                    around = line[max(0, m.start() - 40): m.end() + 40].strip()
                    ns = kind == "name" and NAMESPACE.search(line) is not None and \
                        all(NAMESPACE.search(line[max(0, x.start() - 30): x.end() + 40]) for x in [m])
                    hits.append({"file": shown, "inside": inside, "line": n, "kind": "namespace" if ns else kind,
                                 "match": m.group(0), "text": around[:160]})
    return hits


def walk(base: str, label: str) -> list:
    hits = []
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            p = os.path.join(root, n)
            rel = os.path.relpath(p, base).replace("\\", "/")
            for h in look(p, f"{label}/{rel}"):
                h["shipped"] = shipped(rel)
                hits.append(h)
    return hits


def self_test_output(package: str) -> tuple[list, dict]:
    from agent_market.tests import samplekit as S

    out_dir = tempfile.mkdtemp(prefix=f"am_generic_{package}_")
    st = S.self_test(package)
    res = S.render_sample(package, out_dir=out_dir, defaults=S.self_test_defaults(package), formats=st.get("formats"))
    with open(os.path.join(out_dir, "computed.json"), "w", encoding="utf-8") as f:
        json.dump(res["computed"], f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(res["summary"], f, ensure_ascii=False, indent=1)
    hits = walk(out_dir, f"{package}:self-test output")
    for h in hits:
        h["shipped"] = True
    return hits, {"asked": [f["code"] for f in res["first_findings"]], "passed": res["render"]["passed"],
                  "files": sorted(os.listdir(out_dir)), "dir": out_dir}


def check(package: str, extra: list) -> dict:
    with open(os.path.join(PACKAGES, package, "agent.json"), encoding="utf-8") as f:
        m = json.load(f)
    hits = walk(os.path.join(PACKAGES, package), package)
    out_hits, run = self_test_output(package)
    hits += out_hits
    delivered = []
    for d in extra:
        # a directory that holds one folder per package is what the fresh-install test downloaded
        own = os.path.join(d, package)
        if not os.path.isdir(own) and any(os.path.isdir(os.path.join(d, n)) for n in os.listdir(d)):
            continue
        d = own if os.path.isdir(own) else d
        delivered += sorted(os.listdir(d))
        more = walk(d, f"{package}:delivered")
        for h in more:
            h["shipped"] = True
        hits += more
    real = [h for h in hits if h["kind"] != "namespace"]
    return {"package": package, "id": m["id"], "version": m["version"], "scope": m.get("scope"), "jurisdiction": m.get("jurisdiction"),
            "self_test": run, "delivered": delivered, "hits": len(real), "hits_shipped": sum(1 for h in real if h["shipped"]),
            "namespace": sum(1 for h in hits if h["kind"] == "namespace"), "generic": not real and not run["asked"] and run["passed"],
            "detail": real, "namespace_detail": [h for h in hits if h["kind"] == "namespace"]}


def engine() -> dict:
    """The rest of the runtime zip: the engine every agent runs on ships to every company as well."""
    base = os.path.join(BACKEND, "agent_market")
    hits = []
    for root, dirs, names in os.walk(base):
        rel_root = os.path.relpath(root, base).replace("\\", "/")
        if rel_root == ".":
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and d != "packages")
        else:
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            rel = n if rel_root == "." else f"{rel_root}/{n}"
            for h in look(os.path.join(root, n), f"engine/{rel}"):
                h["shipped"] = shipped(rel)
                hits.append(h)
    real = [h for h in hits if h["kind"] != "namespace"]
    return {"hits_shipped": sum(1 for h in real if h["shipped"]), "hits_not_shipped": sum(1 for h in real if not h["shipped"]),
            "detail": [h for h in real if h["shipped"]],
            "not_shipped_files": sorted({h["file"] for h in real if not h["shipped"]})}


def main(argv: list) -> int:
    sys.path[0:0] = [BACKEND, SANDBOX_SCRIPTS]
    extra, names = [], []
    it = iter(argv)
    for a in it:
        if a == "--out":
            extra.append(next(it))
        elif a == "--json":
            pass
        else:
            names.append(a)
    names = names or sorted(n for n in os.listdir(PACKAGES) if os.path.isfile(os.path.join(PACKAGES, n, "agent.json")))
    results = [check(n, extra) for n in names]
    if "--json" in argv:
        sys.stdout.buffer.write((json.dumps(results, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    else:
        for r in results:
            line = (f"{'GENERIC ' if r['generic'] else 'NOT     '} {r['package']} {r['version']} · scope {r['scope']} · jurisdiction "
                    f"{r['jurisdiction']} · hits {r['hits']} (shipped {r['hits_shipped']}) · namespace {r['namespace']} · self-test "
                    f"asked {len(r['self_test']['asked'])}, quality gate {'passed' if r['self_test']['passed'] else 'FAILED'}"
                    f" · files of a fresh install looked into {len(r['delivered'])}\n")
            sys.stdout.buffer.write(line.encode("utf-8"))
            seen = set()
            for h in r["detail"]:
                k = (h["file"], h["inside"], h["match"])
                if k in seen:
                    continue
                seen.add(k)
                sys.stdout.buffer.write(f"    {h['kind']:8} {h['file']} [{h['inside']}] {h['match']!r}: {h['text'][:100]}\n".encode("utf-8"))
    eng = engine()
    if "--json" not in argv:
        sys.stdout.buffer.write((f"{'CLEAN   ' if not eng['hits_shipped'] else 'NOT     '} engine (the rest of the runtime zip) · hits "
                                 f"shipped {eng['hits_shipped']} · not shipped {eng['hits_not_shipped']} in "
                                 f"{len(eng['not_shipped_files'])} file(s): {', '.join(eng['not_shipped_files'][:6])}\n").encode("utf-8"))
        for h in eng["detail"]:
            sys.stdout.buffer.write(f"    {h['kind']:8} {h['file']} [{h['inside']}] {h['match']!r}: {h['text'][:100]}\n".encode("utf-8"))
    ok = all(r["generic"] for r in results) and not eng["hits_shipped"]
    declared_wrong = [r["package"] for r in results if r["scope"] == "generic" and not r["generic"]]
    if declared_wrong:
        sys.stdout.buffer.write(("declared generic and is not: " + ", ".join(declared_wrong) + "\n").encode("utf-8"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
