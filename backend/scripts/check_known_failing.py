"""Release gate: compare FAILING TEST NAMES against the known list.

Usage:
    python -m pytest -m 'not integration' -q > lane.out 2>&1 ; true
    python backend/scripts/check_known_failing.py lane.out

Exit status:
    0  no failing test outside backend/tests/known_failing.txt
    1  at least one name is new  (the gate is red)
    2  the input could not be read, or it has no pytest summary line
       (the lane was interrupted — an unfinished run is not a green run)

Why names and not a count: this suite does not produce a stable number
of failures. Three consecutive runs of one unchanged tree gave 21, 22
and 23, with a different extra member each time and every drifting
member passing when run alone. A count cannot distinguish "the same
known breakage" from "something new broke"; a name can.

The script also reports entries in the list that did NOT fail. Those
are not an error — the list is allowed to be pessimistic — but they
are printed so the list can be trimmed, because a ledger of known
breakage is supposed to shrink.
"""

from __future__ import annotations

import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve()
_KNOWN = _HERE.parent.parent / "tests" / "known_failing.txt"

_FAILED_RE = re.compile(r"^FAILED\s+(\S+)")
# pytest's closing line: "21 failed, 3995 passed, 27 skipped ... in 265.70s"
# or "no tests ran in 0.01s". FIX61: a lane that was interrupted has no
# such line and no FAILED lines either — it used to read as GREEN.
_SUMMARY_RE = re.compile(r"^(?:=+ )?(?:\d+ (?:passed|failed|error|errors)\b.*|no tests ran\b.*) in \d")


def lane_finished(text: str) -> bool:
    """True only if pytest printed its final summary line."""
    return any(_SUMMARY_RE.match(line.strip()) for line in text.splitlines())


def load_known(path: pathlib.Path) -> dict[str, str]:
    """Return {test_id: reason} from the ledger."""
    known: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        test_id, _, comment = line.partition("#")
        known[test_id.strip()] = comment.strip()
    return known


def parse_failures(text: str) -> set[str]:
    """Test ids from pytest's `FAILED <id> - <message>` summary lines."""
    out: set[str] = set()
    for line in text.splitlines():
        m = _FAILED_RE.match(line.strip())
        if m:
            out.add(m.group(1).split(" - ")[0].strip())
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        text = pathlib.Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"cannot read {argv[1]}: {exc}")
        return 2

    if not lane_finished(text):
        print(
            "GATE NOT RUN — the lane output has no pytest summary line, so the "
            "run was interrupted or never started. Nothing can be concluded "
            "from it; re-run the lane."
        )
        return 2

    known = load_known(_KNOWN)
    failed = parse_failures(text)

    new = sorted(failed - set(known))
    stale = sorted(set(known) - failed)

    print(f"failing now: {len(failed)}   known: {len(known)}   new: {len(new)}")

    if stale:
        print(
            f"\n{len(stale)} known entr{'y' if len(stale) == 1 else 'ies'} did not "
            "fail this run — trim the ledger when they stay green:"
        )
        for t in stale:
            print(f"  - {t}")

    if new:
        print(f"\nGATE RED — {len(new)} failing test(s) not in the ledger:")
        for t in new:
            print(f"  + {t}")
        print(
            "\nFix the change that broke them. Adding a name here to turn the "
            "gate green is not allowed."
        )
        return 1

    print("\nGATE GREEN — every failing test is a known, documented entry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
