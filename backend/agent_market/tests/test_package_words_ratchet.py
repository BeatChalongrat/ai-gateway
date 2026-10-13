"""What a package's code says, in one language only — counted, and not allowed to grow (AM14).

A sentence written in a package's Python is Thai on every screen: the engine is told the run's language
and has nothing else to say it in. One package was built the other way from its first day (procurement,
AM12): its code holds keys, its manifest holds each sentence in Thai and in English, and a test of its own
forbids a Thai word in its code. The other five packages and the engine they share were written before
that, and hold their sentences in the code.

This is the same rule for all of them, as a ratchet. ``package_words_baseline.json`` records every Thai
string each code file holds today (by fingerprint — the words stay in the code). From here on:

  * a Thai string that is NOT in the record fails: a new sentence belongs in the package's manifest, under a
    key, in both languages (``words.t(lang, key)``) — the pile does not grow while it is being worked through;
  * a recorded string that is no longer in the code fails too, until the record is written again without it
    (``python backend/agent_market/tests/test_package_words_ratchet.py --shrink``): the record is always the
    true size of what is left, never an allowance. ``--shrink`` can only remove; it refuses to add.

What is in the pile is of three kinds, and the record does not yet tell them apart: sentences a person meets
on screen (AM14, first pass), the wording of the documents (second pass), and the Thai words the engine
READS — the headings it looks for in a customer's file — which are data and will stay. When a kind is
finished its strings are gone from the code, or moved to the list of words that are read, not said.

    python backend/agent_market/tests/test_package_words_ratchet.py            # the test
    python backend/agent_market/tests/test_package_words_ratchet.py --count    # the pile, by part
    python backend/agent_market/tests/test_package_words_ratchet.py --shrink   # record a smaller pile
"""

import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # backend/agent_market
BASELINE = os.path.join(HERE, "package_words_baseline.json")
THAI = re.compile("[฀-๿]")
# the code a run executes: every package, and the engine and document makers they share
PARTS = ["packages", "deliverables", "engine"]
NL = chr(10)

# AM18 — the documents' own words. Of the Thai a package's code may still hold, one kind is data and stays:
# the words the engine READS from a person's files — the headings it looks for, the words it classifies a
# line by, a bank's name. Those live in the files named here, and nowhere else. A package whose documents
# are written in both languages (DOCUMENTS_DONE) holds Thai in no other file: what its working paper says
# is in its manifest, under a key, in both languages, like what it says on a screen (AM14).
READS = {
    "bank_reconciliation": ("checks/inputs.py", "checks/classify.py", "checks/review.py"),
    "invoice_processing": (),
    "expense_audit": (),
    "po_matching": (),
    "financial_report": (),
}
DOCUMENTS_DONE = ("bank_reconciliation",)


def documents_left(p: dict[str, dict[str, int]]) -> dict[str, int]:
    """Per package: the Thai strings in files that are not its list of words read — the documents' pile."""
    out: Counter = Counter()
    for rel, marks in p.items():
        bits = rel.split("/")
        if bits[0] != "packages" or len(bits) < 3:
            continue
        pkg, inside = bits[1], "/".join(bits[2:])
        if inside not in READS.get(pkg, ()):
            out[pkg] += sum(marks.values())
    return dict(sorted(out.items()))


def literals(src: str) -> list[tuple[int, str]]:
    """Every string a file's code holds (not its docstrings, not its comments)."""
    tree = ast.parse(src)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docs.add(id(first.value))
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def mark(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def code_files(root: str = ROOT) -> list[str]:
    out = []
    for part in PARTS:
        for d, dirs, files in os.walk(os.path.join(root, part)):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for f in sorted(files):
                if f.endswith(".py"):
                    out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
    return out


def thai_in(rel: str, root: str = ROOT) -> list[tuple[int, str]]:
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return [(line, value) for line, value in literals(fh.read()) if THAI.search(value)]


def pile(root: str = ROOT) -> dict[str, dict[str, int]]:
    """file -> {fingerprint: how many times the file holds that string}; files with none are left out."""
    out = {}
    for rel in code_files(root):
        c = Counter(mark(v) for _, v in thai_in(rel, root))
        if c:
            out[rel] = dict(sorted(c.items()))
    return out


def new_strings(recorded: dict, root: str = ROOT) -> list[str]:
    """Thai strings the code holds and the record does not: ``file:line: 'the words'``."""
    new = []
    for rel in code_files(root):
        known = Counter(recorded.get(rel, {}))
        seen: Counter = Counter()
        for line, value in thai_in(rel, root):
            m = mark(value)
            seen[m] += 1
            if seen[m] > known.get(m, 0):
                new.append(f"{rel}:{line}: {value[:60]!r}")
    return new


def gone_strings(recorded: dict, now: dict) -> list[str]:
    """Recorded strings the code no longer holds: ``file: n × fingerprint``."""
    gone = []
    for rel, marks in recorded.items():
        have = Counter(now.get(rel, {}))
        for m, n in marks.items():
            if have.get(m, 0) < n:
                gone.append(f"{rel}: {n - have.get(m, 0)} × {m}")
    return gone


def part_of(rel: str) -> str:
    bits = rel.split("/")
    return "/".join(bits[:2]) if bits[0] == "packages" else bits[0]


def totals(p: dict[str, dict[str, int]]) -> dict[str, int]:
    out: Counter = Counter()
    for rel, marks in p.items():
        out[part_of(rel)] += sum(marks.values())
    return dict(sorted(out.items()))


def read_baseline(baseline: str = BASELINE) -> dict:
    with open(baseline, encoding="utf-8") as fh:
        return json.load(fh)


def write_baseline(p: dict[str, dict[str, int]], baseline: str = BASELINE) -> None:
    doc = {
        "what": "Thai strings held by the code of the packages and the engine they share, by fingerprint "
                "(test_package_words_ratchet.py). It may only get smaller.",
        "total": sum(sum(m.values()) for m in p.values()),
        "by_part": totals(p),
        "files": p,
    }
    with open(baseline, "w", encoding="utf-8", newline=NL) as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write(NL)


def shrink(root: str = ROOT, baseline: str = BASELINE) -> tuple[bool, str]:
    """Record the pile as it is — only if nothing was added. Returns (done, what to say)."""
    now = pile(root)
    if os.path.exists(baseline):
        grown = sorted({x.split(":", 1)[0] for x in new_strings(read_baseline(baseline)["files"], root)})
        if grown:
            return False, "refused: the code holds Thai strings the record does not — the record only shrinks. In: " + ", ".join(grown[:8])
    write_baseline(now, baseline)
    return True, f"recorded {sum(sum(m.values()) for m in now.values())} strings in {len(now)} files"


class ThePileDoesNotGrow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recorded = read_baseline()["files"]
        cls.now = pile()

    def test_no_code_file_holds_a_thai_string_that_is_not_in_the_record(self):
        self.assertEqual(new_strings(self.recorded), [],
                         "a new Thai sentence in a package's code — it belongs in the package's manifest, under a key, in both "
                         "languages (words.t(lang, key)); a word the engine only READS goes in its list of read words. The "
                         "record is not edited to let one in.")

    def test_the_record_is_the_true_size_of_what_is_left(self):
        self.assertEqual(gone_strings(self.recorded, self.now), [],
                         "strings have left the code and the record still counts them — write it again: "
                         "python backend/agent_market/tests/test_package_words_ratchet.py --shrink")

    def test_the_package_built_on_keys_holds_none(self):
        held = {rel: sum(m.values()) for rel, m in self.now.items() if rel.startswith("packages/procurement/")}
        self.assertEqual(held, {}, "procurement's code holds keys, never words")

    def test_the_record_says_its_own_totals(self):
        doc = read_baseline()
        self.assertEqual(doc["by_part"], totals(doc["files"]))
        self.assertEqual(doc["total"], sum(doc["by_part"].values()))

    def test_a_package_whose_documents_speak_both_languages_holds_only_the_words_it_reads(self):
        # AM18: the documents' own pass, one package at a time. Once a package is done, a Thai string in
        # any file of it but the readers named in READS is a sentence that belongs in its manifest.
        left = {}
        for pkg in DOCUMENTS_DONE:
            self.assertIn(pkg, READS, f"{pkg}: name the files that read a person's words")
            for rel, marks in self.now.items():
                if rel.startswith(f"packages/{pkg}/") and rel[len(f"packages/{pkg}/"):] not in READS[pkg]:
                    left[rel] = sum(marks.values())
        self.assertEqual(left, {}, "a document's words in the code of a package whose documents are in both languages — "
                                   "they belong in agent.json under a key, in Thai and in English")

    def test_the_files_that_read_a_persons_words_exist(self):
        for pkg, files in READS.items():
            for rel in files:
                self.assertTrue(os.path.exists(os.path.join(ROOT, "packages", pkg, rel)), f"{pkg}/{rel}")


class TheRatchetItself(unittest.TestCase):
    """The lint, tried on a tree of its own: it must bite both ways, or it holds nothing."""

    A = NL.join(['"""เอกสารประกอบ ไม่นับ"""', 'A = "ชีตครบ"', 'B = f"พบ {1} รายการ"', 'C = "ชีตครบ"  # ความเห็น ไม่นับ', ""])

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="package-words-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.baseline = os.path.join(self.root, "record.json")
        os.makedirs(os.path.join(self.root, "packages", "one", "scripts"))
        os.makedirs(os.path.join(self.root, "deliverables"))
        os.makedirs(os.path.join(self.root, "tools"))
        self.put("packages/one/scripts/a.py", self.A)
        self.put("deliverables/b.py", 'X = "sheet"' + NL)
        self.put("tools/fixtures.py", 'DATA = "ข้อมูลตัวอย่าง"' + NL)  # not code a run executes: not counted
        done, said = shrink(self.root, self.baseline)
        self.assertTrue(done, said)

    def put(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8", newline=NL) as fh:
            fh.write(text)

    def recorded(self):
        return read_baseline(self.baseline)

    def test_it_counts_what_code_holds_and_nothing_else(self):
        doc = self.recorded()
        self.assertEqual(doc["total"], 4, "ชีตครบ twice, and the two halves of the f-string; no docstring, comment or tool")
        self.assertEqual(doc["by_part"], {"packages/one": 4})
        self.assertEqual(new_strings(doc["files"], self.root), [])
        self.assertEqual(gone_strings(doc["files"], pile(self.root)), [])

    def test_a_new_sentence_is_refused_and_the_record_will_not_take_it(self):
        self.put("deliverables/b.py", 'X = "sheet"' + NL + 'Y = "ยอดไม่ตรง"' + NL)
        new = new_strings(self.recorded()["files"], self.root)
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0].startswith("deliverables/b.py:2:"), new)
        done, said = shrink(self.root, self.baseline)
        self.assertFalse(done)
        self.assertIn("deliverables/b.py", said)
        self.assertEqual(self.recorded()["total"], 4, "a refused shrink changes nothing")

    def test_one_more_of_a_sentence_already_there_is_new_too(self):
        self.put("packages/one/scripts/a.py", self.A + 'D = "ชีตครบ"' + NL)
        self.assertEqual(len(new_strings(self.recorded()["files"], self.root)), 1)

    def test_a_sentence_that_left_the_code_must_leave_the_record(self):
        self.put("packages/one/scripts/a.py", 'A = words.t(lang, "check.sheets")' + NL + 'B = f"พบ {1} รายการ"' + NL)
        doc = self.recorded()
        self.assertEqual(new_strings(doc["files"], self.root), [])
        gone = gone_strings(doc["files"], pile(self.root))
        self.assertEqual(len(gone), 1)
        self.assertIn("2 ×", gone[0])
        done, _ = shrink(self.root, self.baseline)
        self.assertTrue(done)
        self.assertEqual(self.recorded()["total"], 2)
        self.assertEqual(gone_strings(self.recorded()["files"], pile(self.root)), [])


def main(argv: list[str]) -> int:
    if "--count" in argv:
        now = pile()
        for part, n in totals(now).items():
            print(f"{n:6d}  {part}")
        print(f"{sum(sum(m.values()) for m in now.values()):6d}  in all, in {len(now)} files")
        print("documents' own words left (strings outside the files that read a person's words):")
        for pkg, n in documents_left(now).items():
            print(f"{n:6d}  {pkg}{'  (done — must be 0)' if pkg in DOCUMENTS_DONE else ''}")
        return 0
    if "--shrink" in argv:
        done, said = shrink()
        print(said)
        return 0 if done else 1
    unittest.main(argv=[argv[0]] + [a for a in argv[1:] if not a.startswith("--")])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
