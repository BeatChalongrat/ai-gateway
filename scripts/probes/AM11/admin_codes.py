"""AM11 (B) — the stored codes the admin app shows, and the word each now reads as, in both languages.

Read from the app's own files: the code → key maps (admin-ui/lib/codeWords.ts, and the outcome map
Agent Market's Problems section words a run's status with) and the two dictionaries. The thirteen
AM10 found shown as stored are marked; the others are their siblings (a code of the same family the
backend can store, worded at the same time so it never shows raw).

    python scripts/probes/AM11/admin_codes.py            # a table, as markdown
"""
import glob
import io
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
ENTRY = re.compile(r'^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)",?\s*$', re.M)
# AM10_REPORT.md, "F — what is still not in the chosen language": the 13 codes and the page each was on
FOUND = {
    "ALERT_SEVERITY": (["critical", "warning", "info"], "Alert events (/alerts/events)"),
    "ALERT_DELIVERY": (["delivered", "grouped", "log_only"], "Alert events (/alerts/events)"),
    "DLP_CATEGORY": (["credential", "financial", "identity", "network", "pii", "secret"], "DLP coverage (/dlp-coverage)"),
    "RUN_STATUS": (["succeeded"], "Agent Market → Problems (/agent-market)"),
}


def dictionaries():
    th, en = {}, {}
    for path in sorted(glob.glob(os.path.join(ROOT, "admin-ui", "lib", "i18n", "*.ts"))):
        if path.endswith(".test.ts"):
            continue
        text = io.open(path, encoding="utf-8").read()
        a, b = text.find("export const en"), text.find("export const th")
        if a < 0 or b < 0:
            continue
        first, second = (en, th) if a < b else (th, en)
        cut = max(a, b)
        for m in ENTRY.finditer(text[min(a, b):cut]):
            first[m.group(1)] = m.group(2)
        for m in ENTRY.finditer(text[cut:]):
            second[m.group(1)] = m.group(2)
    return th, en


def maps():
    text = io.open(os.path.join(ROOT, "admin-ui", "lib", "codeWords.ts"), encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"export const (\w+): Record<string, DictKey> = \{(.*?)\n\};", text, re.S):
        out[m.group(1)] = re.findall(r'^\s*(\w+): "([^"]+)",', m.group(2), re.M)
    return out


def main():
    th, en = dictionaries()
    families = maps()
    rows, missing = [], []
    for family, pairs in families.items():
        found, page = FOUND.get(family, ([], ""))
        for code, key in pairs:
            if key not in th or key not in en:
                missing.append(key)
            rows.append((family, code, "yes" if code in found else "sibling", page if code in found else "", en.get(key, "?"), th.get(key, "?")))
    # a run's status on the Problems section is worded by Agent Market's own outcome keys
    for key in sorted(k for k in th if re.match(r"am\.(outcome|status)\.", k)):
        code = key.rsplit(".", 1)[1]
        found, page = FOUND["RUN_STATUS"]
        rows.append(("RUN_STATUS", code, "yes" if code in found else "sibling", page if code in found else "", en.get(key, "?"), th.get(key, "?")))
    print("| family | stored code | one of AM10's 13 | where it showed raw | English | Thai |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join((r[0], "`%s`" % r[1], r[2], r[3], r[4], r[5])) + " |")
    thirteen = sum(1 for r in rows if r[2] == "yes")
    print("\n%d codes worded; %d of them are AM10's thirteen; unknown code → %s / %s" % (
        len(rows), thirteen, en.get("code.other", "?"), th.get("code.other", "?")))
    if missing or thirteen != 13:
        print("PROBLEM: keys missing %s; thirteen counted %d" % (missing, thirteen), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
