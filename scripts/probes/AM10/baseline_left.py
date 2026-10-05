"""AM10 F — the entries that LEFT the hard-coded-string baseline, opened.

The baseline before the card (git show <base>:apps/web-chat/i18n-baseline.json) is read entry by
entry. For each one that is not in today's baseline, the file it names is opened as it is now and
the words the entry quoted are looked for in it:

    gone        the quoted source is not in the file and neither are its words — the string went
                through the dictionary (or the element was removed)
    words stay  the quoted source is gone but its words are still written in the file — printed with
                every line they are on, to be read: a string moved out of the JSX is out of the
                lint's sight, not translated
    as written  the quoted source is still in the file exactly — the rule stopped counting it

    python scripts/probes/AM10/baseline_left.py [base-commit]     (default 39b5aed2)
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from baseline_open import APP, MARK, ROOT, find, reads  # noqa: E402


def words_of(snippet: str) -> list[str]:
    """The pieces of the node a person reads, each long enough to look for."""
    out = []
    for part in reads(snippet).split(" | "):
        part = part.strip()
        if len(part) >= 2 and re.search(r"[A-Za-z฀-๿]", part) and not part.startswith("{"):
            out.append(part)
    return out


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "39b5aed2"
    old = json.loads(subprocess.run(["git", "-C", ROOT, "show", f"{base}:apps/web-chat/i18n-baseline.json"],
                                    capture_output=True, check=True).stdout.decode("utf-8"))
    with io.open(os.path.join(APP, "i18n-baseline.json"), encoding="utf-8") as f:
        now = set(json.load(f)["keys"])
    tally = {"gone": 0, "words stay": 0, "as written": 0, "file gone": 0, "still in the baseline": 0}
    print("n\tfile\twhat the entry quoted\twhat is there now")
    for n, key in enumerate(old["keys"], 1):
        rel, _, rest = key.partition("::")
        snippet = rest.split(MARK, 1)[-1]
        said = reads(snippet)[:70]
        if key in now:
            tally["still in the baseline"] += 1
            continue
        path = os.path.join(APP, rel)
        if not os.path.exists(path):
            tally["file gone"] += 1
            print(f"{n}\t{rel}\t{said}\tFILE GONE")
            continue
        with io.open(path, encoding="utf-8") as f:
            source = f.read()
        line = find(source, snippet)
        if line is not None:
            tally["as written"] += 1
            print(f"{n}\t{rel}:{line}\t{said}\tAS WRITTEN: {source.splitlines()[line - 1].strip()[:110]}")
            continue
        lines = source.splitlines()
        hits = []
        for w in words_of(snippet):
            for i, text in enumerate(lines, 1):
                if w in text:
                    hits.append(f"{i}: {text.strip()[:110]}")
        if hits:
            tally["words stay"] += 1
            print(f"{n}\t{rel}\t{said}\tWORDS STAY → " + "  ‖  ".join(dict.fromkeys(hits)))
        else:
            tally["gone"] += 1
            print(f"{n}\t{rel}\t{said}\tgone")
    print(f"before {len(old['keys'])} · " + " · ".join(f"{k} {v}" for k, v in tally.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
