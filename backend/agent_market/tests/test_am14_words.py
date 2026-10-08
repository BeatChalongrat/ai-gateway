"""What a package says to a person is said in the language of the run (AM14).

Until this card a package's engine wrote Thai whatever the screen: a check's line, a question and its
answers, why a round does not tie, what was assumed, why a file was left out. Their sentences are now in
the package's manifest — Thai under "strings" by key, the English of each under "en" — and in
engine/words.json for the shared engine, and the engine says each in the language the step is told
(engine/words.py). This file keeps the code and the words together, and holds the rule itself:

  * every sentence the code names is there, with exactly the {places} the code fills, in both languages;
    no sentence is left that nothing asks for;
  * a run in English shows a person no Thai but their own data and the finished document's own wording
    — on every case of the package's own eval set, not on one happy path;
  * the working paper of that run is, cell for cell, the one a Thai run writes (the documents' own pass
    has not been made), and a Thai run is as it was before.

PACKAGES grows as each package's sentences are moved; test_package_words_ratchet counts what is left.
"""

import ast
import datetime
import json
import os
import re
import unittest

from agent_market.engine import words
from agent_market.tests import bankkit as K
from agent_market.tests import invoicekit as IK
from agent_market.tests.test_manifest_english import ERROR_SHAPE, PLACEHOLDER, same_numbers, without_literals

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
THAI = re.compile(r"[฀-๿]")
THAI_RUN = re.compile(r"[฀-๿]+(?:[  ]*[฀-๿]+)*")
PLACE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)[!:}]")
KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")

# whose sentences are where: the code that says them, the file that holds them, the name the code calls.
# A package whose sentences are in its manifest has a module of its own named words (W = engine words of the
# package) — every such package is held here without being listed; procurement keeps its own shape (AM12:
# test_am12_package_words.py).
SPEAKERS = {"the shared engine": (("engine", "deliverables"), os.path.join("engine", "words.json"), "S")}
for _pkg in sorted(os.listdir(os.path.join(ROOT, "packages"))):
    if _pkg != "procurement" and os.path.exists(os.path.join(ROOT, "packages", _pkg, "words.py")):
        SPEAKERS[_pkg] = ((os.path.join("packages", _pkg),), os.path.join("packages", _pkg, "agent.json"), "W")
# a dotted name in the code that begins as a key does and is not one: the name of a schema ("e3.match.v1")
SCHEMA_NAME = re.compile(r"\.v[0-9]+$")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sources(dirs):
    out = {}
    for d in dirs:
        for folder, _, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(folder, f)
                    with open(p, encoding="utf-8") as fh:
                        out[os.path.relpath(p, ROOT).replace(os.sep, "/")] = fh.read()
    return out


def thai_only(text) -> str:
    return re.sub(r"[^฀-๿]", "", str(text))


def strings_of(v, out=None) -> list:
    out = [] if out is None else out
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, dict):
        for x in v.values():
            strings_of(x, out)
    elif isinstance(v, (list, tuple)):
        for x in v:
            strings_of(x, out)
    return out


class TheRunsLanguage(unittest.TestCase):
    def tearDown(self):
        words.use("th")

    def test_a_step_says_it_once(self):
        self.assertEqual(words.lang(), "th")
        self.assertEqual((words.use("en"), words.lang()), ("en", "en"))
        self.assertEqual((words.use("EN"), words.use(None), words.use(""), words.use("fr")), ("en", "th", "th", "th"))

    def test_another_language_lasts_for_a_block_and_no_longer(self):
        with words.speaking("en"):
            self.assertEqual(words.lang(), "en")
            with words.speaking("th"):
                self.assertEqual(words.lang(), "th")
            self.assertEqual(words.lang(), "en")
        self.assertEqual(words.lang(), "th")
        with self.assertRaises(KeyError):
            with words.speaking("en"):
                raise KeyError("x")
        self.assertEqual(words.lang(), "th")

    def test_a_sentence_and_a_table_are_said_when_they_are_read(self):
        S = words.SHARED
        table = S.table({"exact": "e3.tier.exact", "fuzzy": "e3.tier.fuzzy"})   # made before the language is known
        thai = table["exact"]
        self.assertTrue(THAI.search(thai))
        with words.speaking("en"):
            self.assertEqual(table["exact"], "Exact match")
            self.assertEqual(dict(table), {"exact": "Exact match", "fuzzy": "Name or reference alike"})
            self.assertEqual(S("e3.why.days_apart", days=3), "dates 3 days apart")
        self.assertEqual((table["exact"], len(table), "exact" in table, "none" in table), (thai, 2, True, False))
        self.assertEqual(S.both("e3.tier.exact"), {"th": thai, "en": "Exact match"})

    def test_days_months_and_lists_are_set_as_each_language_sets_them(self):
        self.assertEqual((words.date("2026-09-05"), words.period("2026-09")), ("5 ก.ย. 2569", "กันยายน 2569"))
        self.assertEqual(words.date(datetime.date(2026, 12, 31)), "31 ธ.ค. 2569")
        self.assertEqual((words.date(None), words.date("")), ("", ""))
        self.assertEqual(words.clauses("ก", "", "ข"), "ก ข")
        self.assertEqual(words.and_(["ก", "ข"]), "ก และข")
        self.assertEqual(words.cap("ธนาคาร"), "ธนาคาร")
        with words.speaking("en"):
            self.assertEqual((words.date("2026-09-05"), words.period("2026-09")), ("5 Sep 2026", "September 2026"))
            self.assertEqual(words.clauses("a", "", "b"), "a, b")
            self.assertEqual(words.and_(["a", "b"]), "a and b")
            self.assertEqual(words.cap("the bank's statement"), "The bank's statement")

    def test_a_run_that_is_not_thai_carries_the_table_its_files_are_written_from(self):
        said = []

        def table():
            said.append(words.lang())
            return {"in": words.lang()}

        self.assertEqual(words.with_document(table(), table), {"in": "th"})
        self.assertEqual(said, ["th"])                                  # a Thai run computes once
        del said[:]
        words.use("en")
        got = words.with_document(table(), table)
        self.assertEqual((got, said, words.lang()), ({"in": "en", "document": {"in": "th"}}, ["en", "th"], "en"))
        self.assertEqual((words.for_document(got), words.for_document({"in": "th"})), ({"in": "th"}, {"in": "th"}))


class TheCodeAndItsWords(unittest.TestCase):
    """The sentences a file names and the sentences its words file holds are the same set."""

    @classmethod
    def setUpClass(cls):
        cls.speakers = {}
        for who, (dirs, file, call) in SPEAKERS.items():
            m = _load(os.path.join(ROOT, file))
            cls.speakers[who] = {"src": _sources(dirs), "strings": m.get("strings") or {}, "en": m.get("en") or {},
                                 "by_key": m.get("en_by_key") or {}, "call": call}

    def test_every_sentence_the_code_names_is_there_with_the_places_it_fills(self):
        for who, s in self.speakers.items():
            named = 0
            for path, src in s["src"].items():
                if not re.search(r"^from [.a-z_]+ import (SHARED as S|W)$", src, re.M):
                    continue   # (a file that does not take the words has no call of theirs: another S or W is its own)
                for n in ast.walk(ast.parse(src)):
                    if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == s["call"] and n.args):
                        continue
                    first = n.args[0]
                    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                        continue   # a key that arrives from a table, checked below
                    key, where = first.value, f"{who} {path}:{n.lineno}"
                    self.assertIn(key, s["strings"], where)
                    thai = s["strings"][key]
                    given = sorted(k.arg for k in n.keywords if k.arg)
                    self.assertEqual(given, sorted(set(PLACE.findall(thai))) if given or "{" in thai else [], f"{where} {key}: the places of the Thai")
                    english = s["by_key"].get(key) or s["en"].get(thai, thai)
                    self.assertEqual(given, sorted(set(PLACE.findall(english))) if given else [], f"{where} {key}: the places of the English")
                    named += 1
            self.assertGreater(named, 40, who)

    def test_no_sentence_is_said_when_a_module_is_imported(self):
        # a module is imported before the step has said the run's language: a sentence said there would be
        # Thai in every run. A table keeps keys (W.table) and a function says its sentence when it is called.
        for who, s in self.speakers.items():
            for path, src in s["src"].items():
                if not re.search(r"^from [.a-z_]+ import (SHARED as S|W)$", src, re.M):
                    continue
                tree = ast.parse(src)
                inside = set()
                for n in ast.walk(tree):
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        inside.update(id(x) for x in ast.walk(n) if x is not n)
                for n in ast.walk(tree):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == s["call"] and id(n) not in inside:
                        self.fail(f"{who} {path}:{n.lineno}: a sentence is said when the module is imported")

    def _literals(self, s) -> set:
        out = set()
        for src in s["src"].values():
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    out.add(n.value)
        return out

    def _families(self, s) -> set:
        """The heads of keys the code makes from a code that arrives as data: f"e3.setting.{k}"."""
        out = set()
        for src in s["src"].values():
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.JoinedStr) and n.values and isinstance(n.values[0], ast.Constant) \
                        and re.match(r"^[a-z][a-z0-9_.]*\.$", str(n.values[0].value)):
                    out.add(n.values[0].value)
        return out

    def test_every_key_in_a_table_is_a_sentence(self):
        # a dotted name in the code that begins as this speaker's keys do is a key (a table keeps keys, not words)
        for who, s in self.speakers.items():
            heads = {k.split(".")[0] for k in s["strings"]}
            for lit in sorted(self._literals(s)):
                if KEY.match(lit) and lit.split(".")[0] in heads and not SCHEMA_NAME.search(lit) and not lit.startswith("xlsx."):
                    self.assertIn(lit, s["strings"], f"{who}: {lit}")

    def test_no_sentence_is_left_that_nothing_asks_for(self):
        for who, s in self.speakers.items():
            lits, families = self._literals(s), self._families(s)
            for key in s["strings"]:
                self.assertTrue(key in lits or any(key.startswith(f) for f in families), f"{who}: nothing asks for {key}")

    def test_every_sentence_is_in_both_languages_and_says_the_same(self):
        self.assertGreaterEqual(len(self.speakers), 3)
        for who, s in self.speakers.items():
            self.assertGreater(len(s["strings"]), 60, who)
            for key, thai in s["strings"].items():
                where = f"{who} {key}"
                if not THAI.search(thai):
                    # words with no Thai in them (“VAT”) are said as they are in both languages
                    self.assertNotIn(thai, s["en"], f"{where}: words with no Thai in them have no English of their own")
                    self.assertNotIn(key, s["by_key"], where)
                    continue
                self.assertIn(thai, s["en"], where)
                en = s["by_key"].get(key) or s["en"][thai]
                self.assertTrue(en.strip() and en == en.strip(), where)
                self.assertIsNone(THAI.search(without_literals(thai, en)), f"{where}: Thai in the English: {en[:80]}")
                self.assertEqual(sorted(PLACEHOLDER.findall(thai)), sorted(PLACEHOLDER.findall(en)), f"{where}: places differ")
                self.assertTrue(same_numbers(thai, en), f"{where}: numbers differ:\n  {thai}\n  {en}")
                if not ERROR_SHAPE.search(thai):
                    self.assertIsNone(ERROR_SHAPE.search(en), f"{where}: reads like an error: {en[:80]}")
                values = {name: "1" for name in PLACE.findall(thai)}
                if values:
                    self.assertTrue(thai.format(**values).strip() and en.format(**values).strip(), where)

    def test_a_cards_header_fits_in_both_languages(self):
        from agent_market.engine.findings import display_len

        seen = 0
        for who, s in self.speakers.items():
            for key, thai in s["strings"].items():
                if key.startswith(("e3.header.", "e2.header.")) or key.endswith(".head") or (key.startswith("card.") and key.endswith(".header")):
                    seen += 1
                    self.assertLessEqual(display_len(thai), 12, f"{who} {key}")
                    english = s["by_key"].get(key) or s["en"].get(thai, thai)
                    self.assertLessEqual(display_len(english), 12, f"{who} {key}: {english}")
        self.assertGreaterEqual(seen, 8)

    def test_an_english_of_one_key_is_kept_only_where_the_thai_is_said_two_ways(self):
        for who, s in self.speakers.items():
            thais = list(s["strings"].values())
            for key, en in s["by_key"].items():
                self.assertIn(key, s["strings"], f"{who}: en_by_key names {key}, which is no sentence")
                thai = s["strings"][key]
                self.assertGreater(thais.count(thai), 1, f"{who} {key}: its Thai is said under no other key — its English belongs in \"en\"")
                self.assertNotEqual(en, s["en"][thai], f"{who} {key}: the same English as \"en\" has")

    def test_the_shared_engines_words_are_no_packages(self):
        # a package says its own words; the shared engine's are in words.json and are every package's
        shared = self.speakers["the shared engine"]
        for who, s in self.speakers.items():
            if s is shared:
                continue
            self.assertFalse(set(shared["strings"]) & set(s["strings"]), who)
            for path, src in s["src"].items():
                self.assertNotIn("SHARED as S", src, path)


# what of a computed table reaches a screen, whatever the package
SCREEN_KEYS = ("gate", "exceptions", "run_exceptions", "not_included", "fallback_narrative", "assumptions", "reason_codes",
               "names", "period_label")


def said_to_a_person(res: dict) -> list:
    """Every string of a run that reaches a screen: its questions, what it assumed, how it ended and why, its
    exceptions, what was left out, its summary, the names of its figures, and every line of its gate."""
    out = []
    asked = res.get("asked") or []
    for f in (asked.values() if isinstance(asked, dict) else asked):
        out += [f["header"], f["question"]] + [o[k] for o in f["options"] for k in ("label", "description")]
        out += list((f.get("evidence") or {}).get("columns") or [])
        out += [r.get(k) or "" for r in f.get("request") or [] for k in ("label", "why")]
        out += [(f.get("input") or {}).get("hint") or ""]
    pre = res.get("preflight") or res.get("first") or {}
    out += [a["text"] for a in pre.get("assumptions") or []]
    c = res.get("computed")
    if c:
        out += strings_of([c.get(k) for k in SCREEN_KEYS])
        out += [x.get(k) or "" for x in c.get("citations") or [] for k in ("label", "formula")]
        match = c.get("match") or {}
        out += strings_of([[t["label"] for t in match.get("tiers") or []],
                           [m["pass_label"] for m in (match.get("groups") or {}).get("matched") or []]])
        out += strings_of(res.get("summary"))
        out += strings_of([[k["label"], k["detail"]] for a in res["render"]["artifacts"] for k in a["quality"]["checks"]])
    return [s for s in out if s]


def checks_of(res: dict) -> list:
    return [(k["name"], k["ok"]) for a in res["render"]["artifacts"] for k in a["quality"]["checks"]]


class EnglishRuns:
    """A package's own eval set — every layout it reads, every question it asks, every way a run of it ends —
    run in English beside the same set in Thai. Mixed into a TestCase that names the package and its kit."""

    package = ""
    kit = None

    @classmethod
    def files_of(cls, case: dict) -> list:
        """[(path, the name the person gave the file)] of a case."""
        raise NotImplementedError

    @classmethod
    def paper_follows_the_run(cls, case: dict) -> bool:
        """A case whose file holds words written when its input was parsed (so in the run's language)."""
        return False

    @classmethod
    def setUpClass(cls):
        import importlib

        from agent_market.engine import dataset

        pkg = importlib.import_module(f"agent_market.packages.{cls.package}")
        dump = getattr(pkg, "dump", dataset.dump)
        loaded = cls.kit.load_cases()
        cls.cases = loaded["cases"] if isinstance(loaded, dict) else loaded
        cls.th = {c["id"]: cls.kit.run_case(c) for c in cls.cases}
        cls.en = {c["id"]: cls.kit.run_case(c, lang="en") for c in cls.cases}
        # what is the person's own: the names of their files, everything read out of them, their settings and answers
        shared = strings_of({k: v for k, v in loaded.items() if k != "cases"}) if isinstance(loaded, dict) else []
        read: dict = {}
        cls.own = {}
        for c in cls.cases:
            mine = strings_of(c) + shared
            for path, name in cls.files_of(c):
                if path not in read:
                    read[path] = strings_of(dump(pkg.parse(path, name, {})))
                mine += [name] + read[path]
            cls.own[c["id"]] = thai_only("\n".join(mine))
        words.use("th")

    def case(self, cid: str) -> dict:
        return next(c for c in self.cases if c["id"] == cid)

    def test_a_person_who_reads_only_english_meets_no_thai_but_their_own_data_and_the_papers_own_words(self):
        left = []
        for cid, res in self.en.items():
            paper = thai_only("\n".join(list(res.get("sheets") or {}) + strings_of(list((res.get("sheets") or {}).values()))))
            for text in said_to_a_person(res):
                for run in THAI_RUN.findall(text):
                    k = thai_only(run)
                    if k not in self.own[cid] and k not in paper:
                        left.append(f"{cid}: “{run}” in: {text[:140]}")
        self.assertEqual(left, [], "\n" + "\n".join(sorted(set(left))[:40]))

    def test_a_run_in_english_is_the_same_run(self):
        # the same questions by key, the same answers, the same figures and the same end
        for cid, en in self.en.items():
            th = self.th[cid]
            self.assertEqual(sorted(f if isinstance(f, str) else f["key"] for f in en["asked"]),
                             sorted(f if isinstance(f, str) else f["key"] for f in th["asked"]), cid)
            self.assertEqual((en["answers"], en.get("stops_at")), (th["answers"], th.get("stops_at")), cid)
            if th.get("computed"):
                self.assertEqual(en["computed"]["totals"], th["computed"]["totals"], cid)
                self.assertEqual((en["computed"].get("gate") or {}).get("code"), (th["computed"].get("gate") or {}).get("code"), cid)
                self.assertEqual((en["render"]["passed"], checks_of(en)), (th["render"]["passed"], checks_of(th)), cid)
            # (the eval set's own expectations are of the Thai run — its words included — and are held by its own test)
            self.assertEqual(self.kit.check(self.case(cid), th), [], cid)

    def test_the_paper_is_the_one_a_thai_run_writes(self):
        # the documents' own pass has not been made: the file is written from the Thai table, whatever the run
        seen = 0
        for cid, en in self.en.items():
            th = self.th[cid]
            if not th.get("computed"):
                continue
            self.assertNotIn("document", th["computed"], cid)           # a Thai run carries one table, as before
            self.assertIn("document", en["computed"], cid)
            if self.paper_follows_the_run(self.case(cid)):
                continue
            self.assertEqual(en["computed"]["document"], th["computed"], cid)
            self.assertEqual(en["sheets"], th["sheets"], cid)
            seen += 1
        self.assertGreater(seen, 3)

    def test_a_thai_run_after_an_english_one_is_thai(self):
        cid = next(c for c, r in self.th.items() if r.get("computed"))
        self.kit.run_case(self.case(cid), lang="en")
        again = self.kit.run_case(self.case(cid))
        self.assertEqual(again["computed"], self.th[cid]["computed"])


class ABankRunInEnglish(EnglishRuns, unittest.TestCase):
    package, kit = "bank_reconciliation", K

    @classmethod
    def files_of(cls, case):
        return [(os.path.join(K.FIX, n), n) for n in case["files"]]

    @classmethod
    def paper_follows_the_run(cls, case):
        # where a row of a PDF is ("หน้า 1 บรรทัด 4") is written when the file is parsed
        return "statement_sep.pdf" in case["files"]

    def test_the_set_asks_every_kind_of_question_and_ends_every_way(self):
        codes = {f["code"] for r in self.en.values() for f in r["asked"]}
        self.assertEqual(codes, {"bankrec_side", "missing_file", "bankrec_account", "bankrec_closing", "bankrec_carried",
                                 "e3_candidate", "e3_confirm_all", "e3_unmatched"} & codes)
        self.assertGreaterEqual(len(codes), 6, codes)
        gates = {r["computed"]["gate"]["code"] for r in self.en.values() if r.get("computed")}
        self.assertEqual(gates, {"tied", "not_tied"})
        reasons = {e["reason_code"] for r in self.en.values() if r.get("computed") for e in r["computed"]["run_exceptions"]}
        self.assertGreaterEqual(len(reasons), 8, reasons)

    def test_what_is_said_is_english_and_not_only_free_of_thai(self):
        res = self.en["does_not_tie_recorded_twice"]
        gate = res["computed"]["gate"]
        self.assertRegex(gate["what"], r"^Not yet reconciled: the two adjusted balances differ by [\d,]+\.\d\d baht$")
        self.assertTrue(all(re.match(r"^[A-Z0-9]", r) for r in gate["reasons"]), gate["reasons"])
        self.assertTrue(self.th["does_not_tie_recorded_twice"]["computed"]["gate"]["what"].startswith("ยังไม่ลงตัว"))
        tied = self.en["clean_kbank_layout"]
        self.assertEqual(tied["computed"]["gate"]["what"], "Reconciled: the adjusted bank balance and the adjusted book balance are equal")
        self.assertEqual(tied["computed"]["period_label"], "September 2026")
        labels = [k["label"] for a in tied["render"]["artifacts"] for k in a["quality"]["checks"]]
        self.assertIn("Every sheet is there", labels)
        self.assertIn("The balances in the statement are as computed", labels)
        self.assertTrue(all(re.match(r"^[A-Z]", x) for x in labels), labels)
        figures = next(k for a in tied["render"]["artifacts"] for k in a["quality"]["checks"] if k["name"] == "xlsx.figures")
        self.assertRegex(figures["detail"], r"^Read the amounts back from the file: \d+ values, every one as computed$")
        card = next(f for r in self.en.values() for f in r["asked"] if f["code"] == "bankrec_side")
        self.assertEqual(card["header"], "Which side?")
        self.assertEqual([o["label"] for o in card["options"]], ["The bank's statement", "The company's ledger", "Do not use this file"])
        self.assertEqual(card["evidence"]["columns"], ["Date", "Description", "Debit", "Credit", "Balance"])

    def test_a_figure_is_asked_about_by_its_english_name_and_by_the_papers_own(self):
        # "where does the adjusted bank balance come from?" — and a person looking at the Thai paper may
        # ask by the words it prints
        en = {c["id"]: c for c in self.en["clean_kbank_layout"]["computed"]["citations"]}
        th = {c["id"]: c for c in self.th["clean_kbank_layout"]["computed"]["citations"]}
        self.assertEqual(en["a1"]["label"], "Adjusted bank balance (baht)")
        self.assertEqual(en["b2"]["label"], "Balance per the company's ledger (baht)")
        self.assertEqual(en["d1"]["aliases"][:2], ["difference", "differ by"])
        for cid, c in th.items():
            self.assertFalse([a for a in c["aliases"] if not THAI.search(a)], cid)          # a Thai run: as it was
            self.assertEqual(en[cid]["aliases"][-len(c["aliases"]) - 1:], [c["label"]] + c["aliases"], cid)
            where = lambda x: [(s["file"], s["sheet"], s["row"], s["value"]) for s in x["sources"]]   # noqa: E731
            self.assertEqual((en[cid]["value"], where(en[cid])), (c["value"], where(c)), cid)


class AnInvoiceRunInEnglish(EnglishRuns, unittest.TestCase):
    package, kit = "invoice_processing", IK

    @classmethod
    def files_of(cls, case):
        fmap = IK._files()
        return [(os.path.join(IK.E2, fmap[fid]), os.path.basename(fmap[fid])) for fid in case["files"]]

    def test_the_set_asks_the_questions_of_a_document_and_leaves_documents_out(self):
        codes = {f["code"] for r in self.en.values() for f in r["asked"].values()}
        self.assertGreaterEqual(len(codes & {"e2_check_failed", "e2_field_missing", "e2_low_confidence", "e2_duplicate", "e2_source_conflict",
                                             "e2_copy", "e2_doc_type", "e2_typed_invalid", "e2_page_quality"}), 4, codes)
        self.assertTrue(any(r["computed"]["not_included"] for r in self.en.values()))

    def test_what_is_said_is_english_and_not_only_free_of_thai(self):
        cards = [f for r in self.en.values() for f in r["asked"].values()]
        failed = next(f for f in cards if f["code"] == "e2_check_failed" and f["facts"]["check"] == "vat_7_percent")
        self.assertEqual(failed["header"], "VAT?")
        self.assertRegex(failed["question"], r": the VAT is not 7% of the base \(the document prints VAT of [\d,.]+ baht, but 7% of the amount "
                                             r"before VAT is [\d,.]+ baht\)$")
        self.assertEqual([o["label"] for o in failed["options"]], ["Record as printed, with an exception", "Leave this document out"])
        missing = next(f for f in cards if f["code"] == "e2_field_missing")
        self.assertRegex(missing["question"], r"Please type the value you see, or confirm that the document does not print it")
        self.assertRegex(missing["input"]["hint"], r"^Type ")
        res = self.en["clean_batch_zero_questions"]
        self.assertRegex(res["computed"]["fallback_narrative"]["summary"],
                         r"^This run received \d+ documents and entered \d+ in the register: total [\d,.]+ baht, VAT [\d,.]+ baht")
        labels = [k["label"] for a in res["render"]["artifacts"] for k in a["quality"]["checks"]]
        self.assertIn("The number of rows equals the documents recorded", labels)
        self.assertTrue(all(re.match(r"^[A-Z]", x) for x in labels), labels)
        self.assertEqual({c["id"]: c["label"] for c in res["computed"]["citations"]}["i3"], "Total (THB)")
        left = [e for r in self.en.values() for e in r["computed"]["not_included"]]
        self.assertTrue(left and all(not THAI.search(e["reason"] + e["how"]) for e in left), left[:2])


if __name__ == "__main__":
    unittest.main()
