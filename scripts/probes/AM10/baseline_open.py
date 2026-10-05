"""AM10 F — the hard-coded-string baseline, opened.

Every entry of apps/web-chat/i18n-baseline.json quotes the source of the node the lint flagged. Here
the file each entry names is opened and that exact source is found in it (whitespace apart): the
line it starts on, and the words a person reads there. An entry whose source is not in its file any
more is said so — a baseline that lists what is no longer there is a wrong baseline. Nothing here
reads the linter's summary.

    python scripts/probes/AM10/baseline_open.py            (prints a TSV; exit 1 when an entry is not found)
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
APP = os.path.join(ROOT, "apps", "web-chat")
MARK = "see src/lib/i18n/dict.ts: "


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def find(source: str, snippet: str) -> int | None:
    """The 1-based line the snippet starts on in the file, compared without whitespace."""
    want = squash(snippet)
    if not want:
        return None
    pos, flat, starts = 0, [], []
    for i, ch in enumerate(source):
        if not ch.isspace():
            flat.append(ch)
            starts.append(i)
    at = "".join(flat).find(want)
    if at < 0:
        return None
    return source.count("\n", 0, starts[at]) + 1


def reads(snippet: str) -> str:
    """What a person reads in the node: attribute values and JSX text, expressions left as {…}."""
    attrs = re.findall(r'\b(?:alt|title|placeholder|aria-label)="([^"]*)"', snippet)
    if attrs and not snippet.lstrip().startswith("<"):
        return " | ".join(attrs)
    text = re.sub(r"<[^<>]*>", "\n", snippet)
    text = re.sub(r"\{t\([^)]*\)\}", "{t(…)}", text)
    parts = [re.sub(r"\s+", " ", p).strip() for p in text.split("\n")]
    parts = [p for p in parts if p and p not in ("{t(…)}",) and not re.fullmatch(r"\{[^{}]*\}", p)]
    return " | ".join(parts) or (" | ".join(attrs) if attrs else snippet.strip())


def main() -> int:
    with io.open(os.path.join(APP, "i18n-baseline.json"), encoding="utf-8") as f:
        base = json.load(f)
    bad = 0
    print("n\tfile:line\twhat a person reads there\tthe line as written")
    for n, key in enumerate(base["keys"], 1):
        rel, _, rest = key.partition("::")
        snippet = rest.split(MARK, 1)[-1]
        path = os.path.join(APP, rel)
        if not os.path.exists(path):
            print(f"{n}\t{rel}:?\tFILE MISSING\t")
            bad += 1
            continue
        with io.open(path, encoding="utf-8") as f:
            source = f.read()
        line = find(source, snippet)
        if line is None:
            print(f"{n}\t{rel}:?\tSOURCE NOT IN THE FILE\t{snippet[:70]!r}")
            bad += 1
            continue
        written = source.split("\n")[line - 1].strip()
        print(f"{n}\t{rel}:{line}\t{reads(snippet)}\t{written[:120]}")
    print(f"entries {len(base['keys'])} · declared count {base['count']} · not found {bad}")
    return 1 if bad or base["count"] != len(base["keys"]) else 0


if __name__ == "__main__":
    sys.exit(main())
