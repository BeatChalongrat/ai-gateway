#!/usr/bin/env python3
"""The writing half of scripts/ops/envsafe.sh (`envsafe.sh set NAME VALUE` calls this).

Writes one NAME=VALUE line into the env file — in place, the same file, every other line byte for byte —
for a NAME the caller has already checked is on the list of settings that are not secrets. A secret is
never typed on a command line, so there is no way to write one here: the shell script refuses the name
before this runs, and this refuses a value that is not plain (letters, digits, and . , : @ / + _ -).

    python scripts/ops/envsafe_set.py <env file> NAME VALUE
"""
from __future__ import annotations

import re
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("envsafe_set.py <env file> NAME VALUE", file=sys.stderr)
        return 2
    path, name, value = argv[1], argv[2], argv[3]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or not re.fullmatch(r"[A-Za-z0-9_.,:@/+-]*", value):
        print("a NAME is letters, digits and underscores; a value of this kind is plain (what was given is not shown)", file=sys.stderr)
        return 2
    with open(path, "rb") as f:
        raw = f.read().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(eol)
    line = f"{name}={value}"
    at = [i for i, existing in enumerate(lines) if existing.startswith(name + "=")]
    if at:
        lines[at[0]] = line
    elif lines and lines[-1] == "":
        lines.insert(len(lines) - 1, line)
    else:
        lines.append(line)
    with open(path, "r+b") as f:  # in place: a container that has the file mounted keeps seeing it
        f.seek(0)
        f.write(eol.join(lines).encode("utf-8"))
        f.truncate()
    print(f"{name} {'changed' if at else 'added'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
