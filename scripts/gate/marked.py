"""AM15 — the backend tests no lane ran, held by name.

    python scripts/gate/marked.py <pytest output of `-m integration`> [--write]

661 backend tests carry the marker `integration` (backend/tests/conftest.py: a list of modules, and
tests/integration/), and every lane deselected it — so nothing ran them, for months. Run in the lane
on 12 Oct 2026, 556 passed, 91 failed and 17 were skipped; the 26 that AM13b had happened to see were
eight of those files. A test is in the gate or it is deleted: the gate now runs the marked tests too.

The ones that still fail are not green and are not hidden. They are on a list, by name, with why
(scripts/gate/backend_marked_failing.txt), and this script holds the lane's output to it:

  * a test that fails and is NOT on the list        → red  (something broke, or a new test is stale)
  * a test that is on the list and does NOT fail    → red  (it was fixed or deleted: take it off —
                                                            the list can only get shorter)
  * the lane did not finish (no pytest summary)     → red  (an unfinished run is not a green run)

and it says, on every run, how many are on the list and in which files, and what was skipped and why:
the exclusion is in the gate's own output, never silent. Exit 0 means only "nothing moved".

--write rewrites the list from this output (the names; reasons are kept, new names get "?") — for
taking names OFF. It refuses to add one.
"""
from __future__ import annotations

import collections
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
# (GATE_MARKED_LIST: another list — the script's own test holds it to one it wrote)
LIST = pathlib.Path(os.environ.get("GATE_MARKED_LIST") or ROOT / "scripts" / "gate" / "backend_marked_failing.txt")
FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(tests/\S+)")
SKIPPED = re.compile(r"^SKIPPED\s+\[(\d+)\]\s+(\S+?):\d+:\s*(.*)$")
SUMMARY = re.compile(r"^(?:=+ )?(?:\d+ (?:passed|failed|error|errors|skipped)\b.*|no tests ran\b.*) in \d")


def read_list(path: pathlib.Path) -> dict[str, str]:
    """{test id: reason} — a reason is the nearest `## …` heading above the name, or its own `# …`."""
    out: dict[str, str] = {}
    heading = ""
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            heading = line[3:].strip()
            continue
        if not line or line.startswith("#"):
            continue
        name, _, own = line.partition("#")
        out[name.strip()] = own.strip() or heading
    return out


def read_lane(text: str) -> tuple[set[str], dict[str, int], str | None]:
    failed: set[str] = set()
    skipped: dict[str, int] = collections.Counter()
    summary = None
    for raw in text.splitlines():
        line = raw.strip()
        m = FAILED.match(line)
        if m:
            failed.add(m.group(1).split(" - ")[0].strip())
            continue
        m = SKIPPED.match(line)
        if m:
            skipped[f"{m.group(2)} — {m.group(3)[:110]}"] += int(m.group(1))
            continue
        if SUMMARY.match(line):
            summary = line.strip("= ")
    return failed, dict(skipped), summary


def by_file(names) -> str:
    c = collections.Counter(n.split("::")[0].replace("tests/", "") for n in names)
    return ", ".join(f"{f} ×{n}" for f, n in sorted(c.items()))


def main(argv: list[str]) -> int:
    # This host's console is not UTF-8 (cp874), and the gate sends this output to a file: without this the
    # first line with a dash in it raised, and the gate read the crash as its verdict.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = [a for a in argv[1:] if not a.startswith("--")]
    write = "--write" in argv
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        text = pathlib.Path(args[0]).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"RED — cannot read {args[0]}: {exc}")
        return 2
    failed, skipped, summary = read_lane(text)
    if summary is None:
        print("RED — the lane's output has no pytest summary line: it was interrupted or never started. Nothing can be concluded from it.")
        return 2
    listed = read_list(LIST)
    new = sorted(failed - set(listed))
    healed = sorted(set(listed) - failed)

    if write:
        if new:
            print(f"REFUSED — {len(new)} failing test(s) are not on the list, and a name is never added to turn the gate green:")
            for n in new:
                print(f"  + {n}")
            return 1
        keep = [raw for raw in LIST.read_text(encoding="utf-8").splitlines()
                if not raw.strip() or raw.strip().startswith("#") or raw.partition("#")[0].strip() in failed]
        LIST.write_text("\n".join(keep).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
        print(f"list rewritten: {len(healed)} name(s) taken off, {len(failed)} left")
        listed = read_list(LIST)
        healed = []

    print(f"pytest: {summary}")
    print(f"marked tests that fail: {len(failed)} · on the list: {len(listed)} · not on the list: {len(new)} · on the list and passing now: {len(healed)}")
    if listed:
        print(f"STILL FAILING, BY NAME ({LIST.name}) — not green, not in any other lane: {by_file(listed)}")
        reasons = collections.Counter(listed.values())
        for why, n in reasons.most_common():
            print(f"  {n:3d} · {why or 'no reason written'}")
    if skipped:
        print(f"SKIPPED IN THIS LANE ({sum(skipped.values())}) — these run nowhere:")
        for why, n in sorted(skipped.items()):
            print(f"  {n:3d} · {why}")
    rc = 0
    if new:
        rc = 1
        print(f"\nRED — {len(new)} failing test(s) are not on the list:")
        for n in new:
            print(f"  + {n}")
        print("Fix what broke them, or the test. A name is never added to the list to turn the gate green.")
    if healed:
        rc = 1
        print(f"\nRED — {len(healed)} test(s) on the list do not fail any more (fixed, deleted or renamed). Take them off — the list only gets shorter:")
        for n in healed:
            print(f"  - {n}")
        print("  python scripts/gate/marked.py <this output> --write")
    if rc == 0:
        print("\nnothing moved: every marked test that fails is on the list, and every name on the list still fails")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
