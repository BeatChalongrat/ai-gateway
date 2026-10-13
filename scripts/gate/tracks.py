"""AM19 — only what the code needs is tracked: a tracked file that .gitignore says is a track fails.

    python scripts/gate/tracks.py            # the whole tracked tree (the gate's `tracks` lane, CI)
    python scripts/gate/tracks.py <range>    # only the files added in a commit range (e.g. gitlab/main..main)

The rule is .gitignore's: working notes, probe folders, result folders, reports, plans, scratch. A file
that matches a rule there and is tracked anyway — added with `git add -f`, or tracked since before the
rule — is exactly what a person cloning the repository would be puzzled to find. `git ls-files -ci
--exclude-standard` names every such file; this script turns that list into a verdict with the file
names, so the gate fails saying which file, not someone reading CARD-RULES.md. Exit 0 = nothing
tracked that the rules exclude; 1 = the files listed; 2 = git could not be asked.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def tracked_but_excluded(root: pathlib.Path, rng: str | None) -> list[str]:
    cmd = ["git", "-C", str(root), "ls-files", "-c", "-i", "--exclude-standard"]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "git ls-files failed")
    names = sorted(line.strip() for line in out.stdout.splitlines() if line.strip())
    if rng:
        added = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "--diff-filter=A", rng],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or "git diff failed")
        new = {line.strip() for line in added.stdout.splitlines() if line.strip()}
        names = [n for n in names if n in new]
    return names


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    rng = argv[1] if len(argv) > 1 else None
    root = pathlib.Path(argv[2]) if len(argv) > 2 else ROOT
    try:
        names = tracked_but_excluded(root, rng)
    except RuntimeError as exc:
        print(f"tracks: cannot ask git — {exc}")
        return 2
    where = f"added in {rng}" if rng else "tracked"
    if names:
        print(f"RED — {len(names)} file(s) {where} that .gitignore says are not part of the product:")
        for n in names:
            print(f"  {n}")
        print("Untrack them (git rm --cached; they stay on disk), or change the rule in .gitignore with a reason.")
        return 1
    print(f"tracks: nothing {where} that .gitignore excludes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
