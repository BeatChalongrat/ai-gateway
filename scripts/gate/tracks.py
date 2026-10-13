"""AM19 — only what the code needs is tracked: a tracked file that .gitignore says is a track fails.

    python scripts/gate/tracks.py            # the whole tracked tree (the gate's `tracks` lane, CI)
    python scripts/gate/tracks.py <range>    # only the files added in a commit range (e.g. gitlab/main..main)

The rule is .gitignore's: working notes, probe folders, result folders, reports, plans, scratch. A file
that matches a rule there and is tracked anyway — added with `git add -f`, or tracked since before the
rule — is exactly what a person cloning the repository would be puzzled to find. `git ls-files -ci
--exclude-standard` names every such file; this script turns that list into a verdict with the file
names, so the gate fails saying which file, not someone reading CARD-RULES.md.

The second half is the owner's rule from the same card: nothing is untracked while something still
points at it. A person who clones this and finds a comment citing `docs/SOMETHING.md` that is not
there is worse off than one who finds a file they did not need. So every tracked text file is read
for a path under `docs/` or `scripts/` with a file extension, and each such path must be tracked;
`.gitignore` and this check's own files are not read (they name the paths on purpose). A pointer at
a record that stays on the author's machine is reworded to say what the record was.

Exit 0 = nothing tracked that the rules exclude and nothing points at an untracked file; 1 = the
files listed; 2 = git could not be asked.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# a path under docs/ or scripts/ with an extension, not glued to a longer path on its left
POINTER = re.compile(r"(?<![A-Za-z0-9_./-])((?:docs|scripts)/[A-Za-z0-9_./-]+\.(?:md|sh|py|yml|yaml|js|mjs|ts|tsx|go|json|txt|csv|sql))\b")
NOT_READ = (".gitignore", "scripts/gate/tracks.py", "scripts/tests/test_am19_tracks.py")


def dangling_pointers(root: pathlib.Path) -> list[str]:
    """Every tracked line that names a docs/ or scripts/ file that is not tracked: 'file:line  path'."""
    ls = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if ls.returncode != 0:
        raise RuntimeError(ls.stderr.strip() or "git ls-files failed")
    # a pointer may be relative to the pointing file's own project (apps/web-chat/package.json says
    # scripts/dist.js and means apps/web-chat/scripts/dist.js; a package's test names the package's
    # scripts/compare.py): a path that ends some tracked file's path is found
    tracked: set[str] = set()
    folders: set[str] = set()  # every tracked folder, by full path
    for p in ls.stdout.split("\0"):
        parts = p.split("/")
        for i in range(len(parts)):
            tracked.add("/".join(parts[i:]))
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))
    grep = subprocess.run(
        ["git", "-C", str(root), "grep", "-I", "-n", "-E", r"(docs|scripts)/[A-Za-z0-9_./-]+\.[a-z]+", "--", ".", *(f":!{n}" for n in NOT_READ)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if grep.returncode not in (0, 1):
        raise RuntimeError(grep.stderr.strip() or "git grep failed")
    found: list[str] = []
    for line in grep.stdout.splitlines():
        file, _, rest = line.partition(":")
        lineno, _, text = rest.partition(":")
        for m in POINTER.finditer(text):
            p = m.group(1).rstrip(".")
            if "/results/" in p and p.split("/results/", 1)[0] in folders:
                continue  # an output of a tracked tool (the repo ignores scripts/**/results/), not something to find
            if p not in tracked:
                found.append(f"{file}:{lineno}  {p}")
    return sorted(set(found))


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
    red = False
    if names:
        red = True
        print(f"RED — {len(names)} file(s) {where} that .gitignore says are not part of the product:")
        for n in names:
            print(f"  {n}")
        print("Untrack them (git rm --cached; they stay on disk), or change the rule in .gitignore with a reason.")
    else:
        print(f"tracks: nothing {where} that .gitignore excludes")
    if not rng:
        try:
            pointers = dangling_pointers(root)
        except RuntimeError as exc:
            print(f"tracks: cannot ask git — {exc}")
            return 2
        if pointers:
            red = True
            print(f"RED — {len(pointers)} tracked line(s) point at a docs/ or scripts/ file that is not tracked:")
            for p in pointers:
                print(f"  {p}")
            print("Nothing is untracked while something still points at it: say what the record was, or track the file.")
        else:
            print("tracks: nothing tracked points at an untracked docs/ or scripts/ file")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
