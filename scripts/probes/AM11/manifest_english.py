"""AM11 (A) — the English of what a package's manifest says to a person.

A package's manifest (backend/agent_market/packages/<package>/agent.json) holds the words a person
reads before a run exists: what the agent is, what it needs and gives back, what it cannot do, its
settings, its guide. Their English is kept in the manifest itself, under "en": a flat map from each
Thai string, exactly as the manifest has it, to its English — the same shape as the server's own
catalogue (found by the Thai sentence, so nothing that reads the manifest in Thai changes).

    python scripts/probes/AM11/manifest_english.py todo    # results/catalogue/manifest_todo_<package>.json: every Thai string, with where it is
    python scripts/probes/AM11/manifest_english.py check   # results/catalogue/manifest_en_<package>.json against the manifests
    python scripts/probes/AM11/manifest_english.py apply   # the checked maps written into each agent.json as its "en"
    python scripts/probes/AM11/manifest_english.py lint    # the manifests as they stand: every Thai string has its English

check / lint fail on: a Thai string with no English; an English entry for a string the manifest does
not have; Thai letters in an English entry; placeholders ({x}, {{x}}, %s) that differ; a number in the
Thai that the English lost; an English sentence shaped like an error (the server withholds those).
"""
import glob
import io
import json
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
PACKAGES = os.path.join(ROOT, "backend", "agent_market", "packages")
OUT = os.path.join(ROOT, "scripts", "probes", "AM11", "results", "catalogue")
THAI = re.compile(r"[฀-๿]")
PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}|\{[A-Za-z_][A-Za-z0-9_:.]*\}|%[sdvq]|%\[\d\][sdvq]")
ERROR_SHAPE = re.compile(
    r"\bnot found\b|\btimed out\b|\bpermission denied\b|\bundefined\b|\bconstraint\b|\bviolates\b|\b(?:error|warning|fatal|exception):"
    r"|\bexception\b|\btraceback\b|\bline \d+\b|https?://|\b\d{1,3}(?:\.\d{1,3}){3}\b", re.I)


def manifests():
    for path in sorted(glob.glob(os.path.join(PACKAGES, "*", "agent.json"))):
        yield os.path.basename(os.path.dirname(path)), path, json.load(io.open(path, encoding="utf-8"))


def thai_strings(manifest):
    """Every string of the manifest that has Thai in it, with where it is; the "en" map itself is not read."""
    found = []

    def walk(v, path):
        if isinstance(v, str):
            if THAI.search(v):
                found.append((path, v))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, "%s[%d]" % (path, i))
        elif isinstance(v, dict):
            for k, x in v.items():
                if not path and k == "en":
                    continue
                walk(x, "%s.%s" % (path, k) if path else k)

    walk(manifest, "")
    return found


def problems(package, manifest, english):
    out = []
    strings = thai_strings(manifest)
    have = {s for _, s in strings}
    for path, s in strings:
        e = english.get(s)
        if not isinstance(e, str) or not e.strip():
            out.append("%s %s: no English for %r" % (package, path, s[:60]))
            continue
        if THAI.search(e):
            out.append("%s %s: Thai in the English: %r" % (package, path, e[:80]))
        if sorted(PLACEHOLDER.findall(s)) != sorted(PLACEHOLDER.findall(e)):
            out.append("%s %s: placeholders differ: %s / %s" % (package, path, PLACEHOLDER.findall(s), PLACEHOLDER.findall(e)))
        if ERROR_SHAPE.search(e) and not ERROR_SHAPE.search(s):
            out.append("%s %s: reads like an error: %r" % (package, path, ERROR_SHAPE.search(e).group(0)))
        if e != e.strip() and s == s.strip():
            out.append("%s %s: space at an end of the English" % (package, path))
    for s in english:
        if s not in have:
            out.append("%s: an English entry for a string the manifest does not have: %r" % (package, s[:60]))
    name_en = manifest.get("name_en")
    if name_en and english.get(manifest.get("name", "")) not in (None, name_en):
        out.append("%s: the English of \"name\" is not the manifest's name_en (%r)" % (package, name_en))
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "lint"
    os.makedirs(OUT, exist_ok=True)
    bad, total = [], 0
    for package, path, manifest in manifests():
        strings = thai_strings(manifest)
        total += len(strings)
        if cmd == "todo":
            with io.open(os.path.join(OUT, "manifest_todo_%s.json" % package), "w", encoding="utf-8") as f:
                json.dump([{"where": p, "thai": s} for p, s in strings], f, ensure_ascii=False, indent=1)
            print("%s: %d strings" % (package, len(strings)))
            continue
        if cmd in ("check", "apply"):
            src = os.path.join(OUT, "manifest_en_%s.json" % package)
            if not os.path.exists(src):
                bad.append("%s: %s is missing" % (package, os.path.basename(src)))
                continue
            english = json.load(io.open(src, encoding="utf-8"))
        else:
            english = manifest.get("en") or {}
        found = problems(package, manifest, english)
        bad += found
        if cmd == "apply" and not found:
            # written as the manifest's last key, leaving every other line of the file as it is
            raw = io.open(path, encoding="utf-8").read().rstrip()
            at = raw.find('\n  "en": {')
            body = (raw[:at].rstrip().rstrip(",") if at >= 0 else raw[:raw.rindex("}")].rstrip())
            block = ",\n".join("    %s: %s" % (json.dumps(s, ensure_ascii=False), json.dumps(english[s], ensure_ascii=False)) for s in sorted(english))
            text = body + ',\n  "en": {\n' + block + "\n  }\n}\n"
            again = json.loads(text)
            assert {k: v for k, v in again.items() if k != "en"} == {k: v for k, v in manifest.items() if k != "en"}, "the manifest changed"
            io.open(path, "w", encoding="utf-8", newline="\n").write(text)
        print("%s: %d strings, %d problems" % (package, len(strings), len(found)))
    for line in bad[:60]:
        print("PROBLEM", line)
    print("%d strings in %s manifests · %d problems" % (total, cmd, len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
