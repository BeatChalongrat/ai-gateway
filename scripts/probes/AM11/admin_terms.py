"""AM11 (C) — where the admin app's Thai dictionary keeps an English term.

Prints every Thai entry that holds one of the terms given on the command line (or a default
list), with its key, so each English string the walk read on a Thai screen can be judged where
it is written: a name that stays, or a translation that was missed.

    python scripts/probes/AM11/admin_terms.py ["term" ...]
"""
import glob
import io
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
ENTRY = re.compile(r'^\s*"([^"]+)":\s*"(.*)",?\s*$', re.M)


def thai_entries():
    for path in sorted(glob.glob(os.path.join(ROOT, "admin-ui", "lib", "i18n", "*.ts"))):
        if path.endswith(".test.ts"):
            continue
        text = io.open(path, encoding="utf-8").read()
        at = text.find("export const th")
        if at < 0:
            continue
        for m in ENTRY.finditer(text[at:]):
            yield os.path.basename(path), m.group(1), m.group(2)


def main(terms):
    entries = list(thai_entries())
    for term in terms:
        print("== " + term)
        for name, key, value in entries:
            if term in value or term == key:
                print("   %s  %s => %s" % (name, key, value[:200]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:] or ["API Key", "audit log", "manual override", "discovery sync", "production"])
