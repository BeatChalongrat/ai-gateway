"""What a package's manifest says to a person exists in Thai and in English, or the lane fails (AM11).

A manifest holds the words a person reads before a run exists: what the agent is, what it needs and
gives back, what it cannot do, its settings, its guide. Their English is in the manifest itself, under
"en" — a flat map from each Thai string, exactly as the manifest has it, to its English — and the
server sends a manifest in the screen's language from it. This test keeps the two together:

  * every string of a manifest that has Thai in it has an English entry, and no entry is left over
    for a string the manifest no longer has;
  * an English entry holds no Thai, keeps every placeholder of its Thai, and keeps its numbers;
  * the English of "name" is the manifest's own "name_en";
  * no English sentence is shaped like an error (the server withholds such text from a person).

What is data is not words and is never translated: a sample file's path, and the values an install
starts with (install.defaults — the expense policy's categories and prohibited merchants are saved as
the customer's own and matched against the customer's Thai files). A literal a person must find or
type exactly as their files have it — a sheet's name — stays in Thai inside “quotes” in the English.

It does not cover what a package's engine writes while it runs — a check's question, a figure's
label, a document: those are Thai only (docs/agent-market/ARCHITECTURE.md, "Two languages").
"""

import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKGS = os.path.join(os.path.dirname(HERE), "packages")
PACKAGES = ("financial_report", "invoice_processing", "expense_audit", "bank_reconciliation", "po_matching")
THAI = re.compile(r"[฀-๿]")
PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}|\{[A-Za-z_][A-Za-z0-9_:.]*\}|%[sdvq]|%\[\d\][sdvq]")
ERROR_SHAPE = re.compile(
    r"\bnot found\b|\btimed out\b|\bpermission denied\b|\bundefined\b|\bconstraint\b|\bviolates\b|\b(?:error|warning|fatal|exception):"
    r"|\btraceback\b|\bline \d+\b|https?://|\b\d{1,3}(?:\.\d{1,3}){3}\b", re.I)
# a figure a sentence states: 7%, 13 digits, 2–6 items. Thai digits are read as the same numbers.
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


# What in a manifest is data, not words: a sample file's path, and the values an install starts with
# (the expense policy's categories and prohibited merchants are saved as the customer's own and are
# matched against the customer's Thai files). They are never translated, on any screen.
DATA = re.compile(r"(^|\.)sample(\[\d+\])?$|^install\.defaults(\.|$)")
# A literal a person must find or type exactly as their files have it — a sheet's name, a word in a
# bank statement — stays as written inside quotes in the English, with what it means beside it.
QUOTED = re.compile(r"“([^”]+)”")


def without_literals(thai, english):
    """The English with every “quoted” piece taken out that the Thai string itself holds."""
    return QUOTED.sub(lambda m: "" if m.group(1) in thai else m.group(0), english)


def manifest(pkg):
    with open(os.path.join(PKGS, pkg, "agent.json"), encoding="utf-8") as f:
        return json.load(f)


def thai_strings(m):
    """Every string of the manifest with Thai in it, with where it is; the "en" map itself is not read."""
    found = []

    def walk(v, path):
        if DATA.search(path):
            return
        if isinstance(v, str):
            if THAI.search(v):
                found.append((path, v))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, "%s[%d]" % (path, i))
        elif isinstance(v, dict):
            for k, x in v.items():
                if not path and k == "en":
                    continue
                walk(x, "%s.%s" % (path, k) if path else k)

    walk(m, "")
    return found


def same_numbers(thai, english):
    """Every number of two digits or more in the Thai is in the English, and the English adds none.
    One digit may be written as a word ("3 ทาง" / "three-way"). A Buddhist-era year in running text
    is the same year 543 less ("2569" / "2026"); in a file's name it is kept as written."""
    def numbers(text):
        return [n.replace(",", "") for n in NUMBER.findall(text) if len(re.sub(r"[.,]", "", n)) >= 2]

    left = numbers(english)
    for n in numbers(thai.translate(THAI_DIGITS)):
        if n in left:
            left.remove(n)
        elif n.isdigit() and 2400 <= int(n) <= 2700 and str(int(n) - 543) in left:
            left.remove(str(int(n) - 543))
        else:
            return False
    return not left


class ManifestEnglish(unittest.TestCase):
    def test_every_thai_string_has_its_english_and_nothing_is_left_over(self):
        for pkg in PACKAGES:
            m = manifest(pkg)
            en = m.get("en")
            self.assertIsInstance(en, dict, "%s: the manifest has no \"en\" map" % pkg)
            strings = thai_strings(m)
            self.assertTrue(strings, pkg)
            for where, s in strings:
                self.assertIsInstance(en.get(s), str, "%s %s: no English for %r" % (pkg, where, s[:60]))
                self.assertTrue(en[s].strip(), "%s %s: an empty English" % (pkg, where))
            have = {s for _, s in strings}
            for s in en:
                self.assertIn(s, have, "%s: an English entry for a string the manifest does not have: %r" % (pkg, s[:60]))

    def test_an_english_entry_is_english_and_says_the_same(self):
        for pkg in PACKAGES:
            m = manifest(pkg)
            for where, s in thai_strings(m):
                e = (m.get("en") or {}).get(s) or ""
                self.assertIsNone(THAI.search(without_literals(s, e)), "%s %s: Thai in the English: %r" % (pkg, where, e[:80]))
                self.assertEqual(sorted(PLACEHOLDER.findall(s)), sorted(PLACEHOLDER.findall(e)), "%s %s: placeholders differ" % (pkg, where))
                self.assertEqual(e, e.strip(), "%s %s: space at an end of the English" % (pkg, where))
                if not ERROR_SHAPE.search(s):
                    self.assertIsNone(ERROR_SHAPE.search(e), "%s %s: reads like an error: %r" % (pkg, where, e[:80]))

    def test_a_number_in_the_thai_is_in_the_english(self):
        for pkg in PACKAGES:
            m = manifest(pkg)
            for where, s in thai_strings(m):
                e = (m.get("en") or {}).get(s) or ""
                self.assertTrue(same_numbers(s, e), "%s %s: numbers differ:\n  %s\n  %s" % (pkg, where, s, e))

    def test_the_english_of_the_name_is_the_manifests_own(self):
        for pkg in PACKAGES:
            m = manifest(pkg)
            self.assertTrue(m.get("name_en"), pkg)
            self.assertEqual((m.get("en") or {}).get(m["name"]), m["name_en"], pkg)

    def test_what_is_data_has_no_english(self):
        # the policy an install starts with is the customer's data from the first save on
        m = manifest("expense_audit")
        en = m.get("en") or {}
        policy = m["install"]["defaults"]["policy"]
        for word in policy["prohibited_merchants"]:
            self.assertNotIn(word, en, "a prohibited merchant is data: %r" % word)
        for cat in policy["categories"].values():
            self.assertNotIn(cat["label"], en, "a policy category's label is data: %r" % cat["label"])
        for pkg in PACKAGES:
            mm = manifest(pkg)
            for spec in (mm.get("guide", {}).get("inputs") or {}).values():
                for path in spec.get("sample") or []:
                    self.assertNotIn(path, mm.get("en") or {}, "%s: a sample file's path is data: %r" % (pkg, path))


if __name__ == "__main__":
    unittest.main()
