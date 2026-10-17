"""AM24 — a green that means green: the gate's ledger, and the three ways a check passes without having looked.

A check that says it passed must have looked at something and been able to fail. This is the one rule every lane
of the gate, every chunk of the integration lane and the smoke apply, in one place:

1. **matched nothing** — a selector that selected no test is red, never green: a lane whose output names no check
   that ran, a chunk of ``-run '^(A|B)$'`` in which A or B ran as no test (renamed, deleted, misspelt);
2. **ran over nothing** — a check whose input was empty is a skip with its reason, never a pass: a lane in which
   every check was skipped is ``skip``, not green;
3. **disappeared** — the count of checks that ran is recorded on every run and compared with the lane's last: a
   count that falls on its own is red, naming what went missing when the names are known. A check removed on
   purpose is removed together with a line in ``scripts/gate/removed_checks.txt`` saying what replaced it, and
   that line is what lets the count fall.

And the ledger (AM24 Part 4): every lane, when it finishes, writes the commit it ran at, when, its count and its
result to ``scripts/gate/results/ledger.jsonl`` (GATE_LEDGER). The gate says green only when every lane has a
green result at the same commit, with nothing uncommitted under it; a lane missing, or carrying another commit,
is named on the last line with its commit. A run in pieces is then a verdict, not a caveat.

    python scripts/gate/ledger.py record <lane> <output file> <exit code>     a lane's run (gate.sh one())
    python scripts/gate/ledger.py chunk <regex> <go test -v output>           every name of a chunk ran (rule 1)
    python scripts/gate/ledger.py record-chunk <regex> <output> <exit code>   a chunk's run (chunk.sh), as it:<first test>+n
    python scripts/gate/ledger.py it-lane <it_lane.txt>                       a whole integration run, chunk by chunk
    python scripts/gate/ledger.py chunk-id <regex>                            a chunk's name in the ledger
    python scripts/gate/ledger.py names <lane> <names file>                   a run's check names against the last (rule 3)
    python scripts/gate/ledger.py verdict <lane…>                             green only when every lane is green at HEAD
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = Path(os.environ.get("GATE_LEDGER") or ROOT / "scripts" / "gate" / "results" / "ledger.jsonl")
REMOVED = Path(os.environ.get("GATE_REMOVED") or ROOT / "scripts" / "gate" / "removed_checks.txt")
# the backend tests that still fail by name (AM15, scripts/gate/marked.py): while one is on it, no verdict says green
STANDING = Path(os.environ.get("GATE_STANDING") or ROOT / "scripts" / "gate" / "backend_marked_failing.txt")

# Lanes that check the whole tree at once and print nothing when it is right: one check, when it exited 0.
WHOLE = {"go_vet", "gofmt", "web_tsc", "web_lints", "removal_drill", "gitleaks", "build_images", "build_runner",
         "build_fetcher", "stack_up", "stack_web", "clients_build", "clients_up", "backup"}


# ── counting ─────────────────────────────────────────────────────────────────────────────────────────────

def count(lane: str, text: str, rc: int) -> dict:
    """{ran, skipped, how}: the checks a lane's output says ran (skipped among them), read from the runner's own
    summary; ``ran`` None when the output names none."""
    def total(pat, flags=0):
        return sum(int(m) for m in re.findall(pat, text, flags))
    if lane in WHOLE:
        return {"ran": 1 if rc == 0 else 0, "skipped": 0, "how": "a whole-tree check"}
    m = re.findall(r"^(?:GREEN|RED) \w+: (\d+) pass(?: \(\d+ slow\))?, (\d+) fail, (\d+) skip", text, re.M)
    if m:
        a, b, c = m[-1]
        return {"ran": int(a) + int(b) + int(c), "skipped": int(c), "how": "smoke"}
    m = re.findall(r"dead-primary (?:PASS|FAIL) ", text)
    if m:
        return {"ran": len(m), "skipped": 0, "how": "dead-primary turns"}
    m = re.findall(r"^compared (\d+) pictures", text, re.M)
    if m:
        return {"ran": int(m[-1]), "skipped": 0, "how": "pictures compared"}
    if re.search(r"^tracks: ", text, re.M):
        return {"ran": len(re.findall(r"^tracks: ", text, re.M)), "skipped": 0, "how": "tracks checks"}
    m = re.findall(r"^Ran (\d+) tests?\b", text, re.M)
    if m:
        skipped = total(r"^OK \(.*?skipped=(\d+)", re.M) + total(r"^FAILED \(.*?skipped=(\d+)", re.M)
        return {"ran": sum(int(x) for x in m), "skipped": skipped, "how": "unittest"}
    m = re.findall(r"^test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", text, re.M)
    if m:
        return {"ran": sum(int(a) + int(b) + int(c) for a, b, c in m), "skipped": sum(int(c) for _, _, c in m),
                "how": "cargo test"}
    summary = [l for l in text.splitlines() if re.search(r"\b\d+ (passed|failed)\b", l) and re.search(r"\bin [\d.]+s\b", l)]
    if summary:
        l = summary[-1]
        n = lambda w: sum(int(x) for x in re.findall(r"(\d+) " + w + r"\b", l))
        ran = n("passed") + n("failed") + n("errors?") + n("skipped") + n("xfailed") + n("xpassed")
        return {"ran": ran, "skipped": n("skipped"), "how": "pytest"}
    m = re.findall(r"^ℹ tests (\d+)", text, re.M)
    if m:
        return {"ran": int(m[-1]), "skipped": total(r"^ℹ skipped (\d+)", re.M), "how": "node --test"}
    m = re.findall(r"^\s*Tests\s+.*?\((\d+)\)", text, re.M)
    if m:
        return {"ran": int(m[-1]), "skipped": total(r"^\s*Tests\s+.*?(\d+) skipped", re.M), "how": "vitest"}
    top = re.findall(r"^--- (PASS|FAIL|SKIP): ", text, re.M)
    if top:
        return {"ran": len(top), "skipped": top.count("SKIP"), "how": "go test -v"}
    pkgs = re.findall(r"^(ok|FAIL)\s+\S+\s+(?:\(cached\)|[\d.]+s)", text, re.M)
    if pkgs:
        return {"ran": len(pkgs), "skipped": 0, "how": "go test packages"}
    if re.search(r"^\s*SUCCESS\s*$", text, re.M):
        return {"ran": len(re.findall(r"^Unit Testing: ", text, re.M)) or 1, "skipped": 0, "how": "promtool"}
    return {"ran": None, "skipped": 0, "how": "no summary a runner prints"}


# ── the ledger ───────────────────────────────────────────────────────────────────────────────────────────

def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def head() -> dict:
    """The commit a run is at (7 characters), and whether anything tracked is changed under it."""
    sha = os.environ.get("GATE_COMMIT") or _git("rev-parse", "--short=7", "HEAD")
    dirty = os.environ.get("GATE_DIRTY")
    if dirty is None:
        dirty = "1" if _git("status", "--porcelain", "--untracked-files=no") else "0"
    return {"commit": sha[:7], "dirty": dirty == "1"}


def entries() -> list[dict]:
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out


def last(lane: str, before: list[dict] | None = None) -> dict | None:
    """The lane's last green run: what a count is held to. (Held to the last run, a check gone would be red once and
    then the new normal.)"""
    for e in reversed(entries() if before is None else before):
        if e.get("lane") == lane and e.get("ran") is not None and e.get("result") == "green":
            return e
    return None


def _shown(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def removals(lane: str) -> list[str]:
    """The lines of removed_checks.txt that are this lane's: ``<lane> | <count> | <what> | <what replaced it>``."""
    try:
        text = REMOVED.read_text(encoding="utf-8")
    except OSError:
        return []
    return [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")
            and l.split("|", 1)[0].strip() == lane]


def allowed_fall(lane: str, prev: dict | None) -> int:
    seen = set((prev or {}).get("removals") or [])
    n = 0
    for l in removals(lane):
        if l not in seen:
            parts = [p.strip() for p in l.split("|")]
            if len(parts) >= 4 and parts[1].isdigit() and parts[3]:
                n += int(parts[1])
    return n


def judge(lane: str, c: dict, rc: int, prev: dict | None, names: list[str] | None = None) -> tuple[str, str]:
    """(result, why): green, red or skip, by the exit code and the three rules."""
    ran, skipped = c["ran"], c.get("skipped") or 0
    if rc != 0:
        return "red", f"exit {rc}"
    if ran is None or ran == 0:
        return "red", f"matched nothing: the output names no check that ran ({c['how']})"
    if prev is not None and ran < prev["ran"]:
        fall, ok = prev["ran"] - ran, allowed_fall(lane, prev)
        if fall > ok:
            gone = ""
            if names is not None and prev.get("names"):
                missing = [n for n in prev["names"] if n not in set(names)]
                gone = f" — gone: {', '.join(missing[:12])}" if missing else ""
            return "red", (f"disappeared: {ran} checks ran, {prev['ran']} at its last green run ({prev['commit']}), and "
                           f"{'no line' if not ok else f'lines for {ok} only'} in {_shown(REMOVED)} "
                           f"say{'s' if ok == 1 else ''} what replaced them{gone}")
    if skipped >= ran:
        return "skip", f"ran over nothing: all {ran} checks skipped"
    return "green", ""


def write(lane: str, c: dict, result: str, why: str, at: dict, names: list[str] | None = None) -> dict:
    e = {"lane": lane, "commit": at["commit"], "dirty": at["dirty"],
         "at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "result": result, "ran": c["ran"], "skipped": c.get("skipped") or 0, "how": c["how"], "why": why,
         "removals": removals(lane)}
    if names is not None:
        e["names"] = names
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def record(lane: str, out_file: str, rc: int) -> int:
    try:
        text = Path(out_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    text = re.sub(r"^exit=\d+\s*$", "", text, flags=re.M)
    at = head()
    c = count(lane, text, rc)
    prev = last(lane)
    result, why = judge(lane, c, rc, prev)
    write(lane, c, result, why, at)
    tail = f" (last green: {prev['ran']} at {prev['commit']})" if prev else " (its first count)"
    print(f"ledger: {lane} {result.upper()} at {at['commit']}{'+uncommitted' if at['dirty'] else ''} · "
          f"{c['ran'] if c['ran'] is not None else 'no'} checks{tail}{' — ' + why if why else ''}")
    return 0 if result == "green" else 1


# ── a chunk of the integration lane ──────────────────────────────────────────────────────────────────────

def chunk_names(regex: str) -> list[str]:
    m = re.fullmatch(r"\^\(?(.*?)\)?\$", regex.strip())
    body = m.group(1) if m else regex
    return [n for n in body.split("|") if n]


def chunk(regex: str, out_file: str) -> int:
    """Every test a chunk names ran (rule 1): a name that ran as no test is red, by name."""
    try:
        text = Path(out_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    ran = set(re.findall(r"^--- (?:PASS|FAIL|SKIP): (\S+)", text, re.M))
    want = chunk_names(regex)
    missing = [n for n in want if n not in ran]
    if not ran:
        print(f"FAIL chunk ran no test: {regex}")
        return 1
    if missing:
        print(f"FAIL chunk matched no test for {len(missing)} of its {len(want)} names: {', '.join(missing)}")
        return 1
    return 0


def chunk_id(regex: str) -> str:
    """A chunk's name in the ledger: its first test, and how many more it names (it:TestA+2)."""
    n = chunk_names(regex)
    return "it:" + (n[0] if n else regex[:40]) + (f"+{len(n) - 1}" if len(n) > 1 else "")


def record_chunk(regex: str, out_file: str, rc: int) -> int:
    """One chunk's run in the ledger, under its chunk_id: red when it failed, ran no test, or a name it lists ran as
    no test; its count compared with its last run like any lane's."""
    try:
        text = Path(out_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    at = head()
    lane = chunk_id(regex)
    top = re.findall(r"^--- (PASS|FAIL|SKIP): (\S+)", text, re.M)
    c = {"ran": len(top), "skipped": sum(1 for v, _ in top if v == "SKIP"), "how": "go test -v"}
    prev = last(lane)
    if re.search(r"^(--- FAIL|FAIL|panic)", text, re.M):
        rc = rc or 1
    result, why = judge(lane, c, rc, prev, names=[n for _, n in top])
    missing = [n for n in chunk_names(regex) if n not in {x for _, x in top}]
    if result == "green" and missing:
        result, why = "red", f"matched nothing for {', '.join(missing)}"
    write(lane, c, result, why, at, names=[n for _, n in top])
    print(f"ledger: {lane} {result.upper()} at {at['commit']}{'+uncommitted' if at['dirty'] else ''} · {c['ran']} tests"
          f"{' — ' + why if why else ''}")
    return 0 if result == "green" else 1


def it_lane(lane_file: str) -> int:
    """A whole integration run (gate.sh it): each chunk of its file recorded as chunk.sh records one."""
    try:
        text = Path(lane_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    blocks = re.split(r"^== chunk (\S+)\s*$", text, flags=re.M)
    rc = 0
    if len(blocks) < 3:
        print("ledger: it RED — the lane's file holds no chunk (matched nothing)")
        return 1
    import tempfile
    for i in range(1, len(blocks), 2):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(blocks[i + 1])
        try:
            rc |= record_chunk(blocks[i], f.name, 0)
        finally:
            os.unlink(f.name)
    return rc


# ── the smoke's check names ──────────────────────────────────────────────────────────────────────────────

def names(lane: str, names_file: str) -> int:
    """The smoke's checks by name against the lane's last run (rule 3), recorded; prints one JSON line."""
    try:
        got = json.loads(Path(names_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "detail": f"the check names could not be read: {e}"}))
        return 1
    at = head()
    prev = last(lane)
    c = {"ran": len(got), "skipped": 0, "how": "smoke checks"}
    result, why = judge(lane, c, 0, prev, names=list(got))
    write(lane, c, result, why, at, names=list(got))
    detail = (f"{len(got)} checks; {prev['ran']} at the last green run ({prev['at']})" if prev else f"{len(got)} checks (the first count)")
    print(json.dumps({"ok": result == "green", "detail": detail + (f" — {why}" if why else "")}, ensure_ascii=False))
    return 0 if result == "green" else 1


# ── the verdict ──────────────────────────────────────────────────────────────────────────────────────────

def verdict(lanes: list[str]) -> int:
    """Green only when every lane's latest result is green at HEAD, with nothing uncommitted. The last line names
    the lanes that are not, each with the commit its result carries."""
    at = head()
    es = entries()
    bad = []
    for lane in lanes:
        mine = [e for e in es if e.get("lane") == lane]
        here = [e for e in mine if e.get("commit") == at["commit"] and not e.get("dirty")]
        if not here:
            old = mine[-1] if mine else None
            bad.append(f"{lane} (no result at {at['commit']}" +
                       (f"; its last: {old['result']} at {old['commit']}{'+uncommitted' if old.get('dirty') else ''})" if old else "; never run)"))
        elif here[-1]["result"] != "green":
            bad.append(f"{lane} ({here[-1]['result']} at {at['commit']}: {here[-1].get('why') or 'see its file'})")
    if at["dirty"]:
        print(f"gate: NOT GREEN — the checkout has uncommitted changes under {at['commit']}: a result is for a commit")
        return 1
    if bad:
        print(f"gate: NOT GREEN at {at['commit']} — {len(bad)} of {len(lanes)} lanes: " + " · ".join(bad))
        return 1
    standing = standing_names()
    if standing:
        # (as the gate's full run says it since AM15: every lane passed, and the list of tests that still fail is
        # not empty — that is not green)
        print(f"gate: every one of the {len(lanes)} lanes passed at {at['commit']}, with {standing} backend test(s) still "
              f"failing by name ({_shown(STANDING)}) — not ALL GREEN until that list is empty")
        return 0
    print(f"gate: ALL GREEN at {at['commit']} — every one of the {len(lanes)} lanes has a green result at {at['commit']}")
    return 0


def standing_names() -> int:
    try:
        lines = STANDING.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    return sum(1 for l in lines if l.strip() and not l.lstrip().startswith("#"))


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "record":
        return record(argv[1], argv[2], int(argv[3]) if argv[3].lstrip("-").isdigit() else 1)
    if len(argv) == 3 and argv[0] == "chunk":
        return chunk(argv[1], argv[2])
    if len(argv) == 4 and argv[0] == "record-chunk":
        return record_chunk(argv[1], argv[2], int(argv[3]) if argv[3].lstrip("-").isdigit() else 1)
    if len(argv) == 2 and argv[0] == "chunk-id":
        print(chunk_id(argv[1]))
        return 0
    if len(argv) == 2 and argv[0] == "it-lane":
        return it_lane(argv[1])
    if len(argv) == 3 and argv[0] == "names":
        return names(argv[1], argv[2])
    if len(argv) >= 2 and argv[0] == "verdict":
        return verdict([l for a in argv[1:] for l in a.split()])
    print(__doc__)
    return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
