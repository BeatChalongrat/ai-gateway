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
from agent_market.tests.test_manifest_english import ERROR_SHAPE, PLACEHOLDER, same_numbers, without_literals

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
THAI = re.compile(r"[฀-๿]")
THAI_RUN = re.compile(r"[฀-๿]+(?:[  ]*[฀-๿]+)*")
PLACE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)[!:}]")
KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")

# whose sentences are where: the code that says them, the file that holds them, the name the code calls
SPEAKERS = {
    "the shared engine": (("engine", "deliverables"), os.path.join("engine", "words.json"), "S"),
    "bank_reconciliation": ((os.path.join("packages", "bank_reconciliation"),), os.path.join("packages", "bank_reconciliation", "agent.json"), "W"),
}
# dotted names in the code that begin as a key does and are not one
NOT_KEYS = {"e3.match.v1"}


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
            cls.speakers[who] = {"src": _sources(dirs), "strings": m.get("strings") or {}, "en": m.get("en") or {}, "call": call}

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
                    self.assertEqual(given, sorted(set(PLACE.findall(s["en"][thai]))) if given else [], f"{where} {key}: the places of the English")
                    named += 1
            self.assertGreater(named, 40, who)

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
                if KEY.match(lit) and lit.split(".")[0] in heads and lit not in NOT_KEYS and not lit.startswith("xlsx."):
                    self.assertIn(lit, s["strings"], f"{who}: {lit}")

    def test_no_sentence_is_left_that_nothing_asks_for(self):
        for who, s in self.speakers.items():
            lits, families = self._literals(s), self._families(s)
            for key in s["strings"]:
                self.assertTrue(key in lits or any(key.startswith(f) for f in families), f"{who}: nothing asks for {key}")

    def test_every_sentence_is_in_both_languages_and_says_the_same(self):
        for who, s in self.speakers.items():
            self.assertGreater(len(s["strings"]), 60, who)
            for key, thai in s["strings"].items():
                where = f"{who} {key}"
                self.assertTrue(THAI.search(thai), f"{where}: a sentence with no Thai in it cannot have an English of its own")
                self.assertIn(thai, s["en"], where)
                en = s["en"][thai]
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
                if key.startswith("e3.header.") or (key.startswith("card.") and key.endswith(".header")):
                    seen += 1
                    self.assertLessEqual(display_len(thai), 12, f"{who} {key}")
                    self.assertLessEqual(display_len(s["en"][thai]), 12, f"{who} {key}: {s['en'][thai]}")
        self.assertGreaterEqual(seen, 8)

    def test_the_shared_engines_words_are_no_packages(self):
        # a package says its own words; the shared engine's are in words.json and are every package's
        shared, bank = self.speakers["the shared engine"], self.speakers["bank_reconciliation"]
        self.assertFalse(set(shared["strings"]) & set(bank["strings"]))
        for path, src in bank["src"].items():
            self.assertNotIn("SHARED as S", src, path)


def said_to_a_person(res: dict) -> list:
    """Every string of a bank run that reaches a screen: its questions, what it assumed, why it does or
    does not tie, its exceptions, what was left out, its summary, and every line of its gate."""
    out = []
    for f in res.get("asked") or []:
        out += [f["header"], f["question"]] + [o[k] for o in f["options"] for k in ("label", "description")]
        out += list((f.get("evidence") or {}).get("columns") or [])
        out += [r[k] for r in f.get("request") or [] for k in ("label", "why")]
        out += [(f.get("input") or {}).get("hint") or ""]
    pre = res.get("preflight") or {}
    out += [a["text"] for a in pre.get("assumptions") or []]
    c = res.get("computed")
    if c:
        out += strings_of([c["gate"][k] for k in ("what", "next", "reasons") if k in c["gate"]] + [c["gate"].get("next_steps")])
        out += strings_of([[e[k] for k in ("reason", "detail", "proposed_action")] + [x["label"] for x in e["choices"]] for e in c["run_exceptions"]])
        out += strings_of([[e[k] for k in ("reason", "detail", "how")] for e in c["not_included"]])
        out += strings_of([c["fallback_narrative"], [a["text"] for a in c["assumptions"]], c["reason_codes"], c["names"], c["period_label"]])
        out += [x[k] for x in c["citations"] for k in ("label", "formula")]
        out += strings_of([[t["label"] for t in c["match"]["tiers"]], [m["pass_label"] for m in c["match"]["groups"]["matched"]]])
        out += strings_of([res["summary"]["gate"], res["summary"]["tiers"], res["summary"]["not_included"]])
        out += strings_of([[k["label"], k["detail"]] for a in res["render"]["artifacts"] for k in a["quality"]["checks"]])
    return [s for s in out if s]


class ABankRunInEnglish(unittest.TestCase):
    """The agent's own eval set — every layout, every question, every way of not tying — run in English."""

    @classmethod
    def setUpClass(cls):
        from agent_market.packages.bank_reconciliation.checks import inputs as I

        cls.cases = K.load_cases()["cases"]
        cls.th = {c["id"]: K.run_case(c) for c in cls.cases}
        cls.en = {c["id"]: K.run_case(c, lang="en") for c in cls.cases}
        # what is the person's own: the names of their files and everything read out of them
        cls.own = {}
        for c in cls.cases:
            mine = list(c["files"])
            for name in c["files"]:
                mine += strings_of(I.dump(I.parse(os.path.join(K.FIX, name), name, {})))
            mine += strings_of([c.get("defaults"), c.get("answers"), K.load_cases()["defaults"]])
            cls.own[c["id"]] = thai_only("\n".join(mine))
        words.use("th")

    def test_the_set_asks_every_kind_of_question_and_ends_every_way(self):
        codes = {f["code"] for r in self.en.values() for f in r["asked"]}
        self.assertEqual(codes, {"bankrec_side", "missing_file", "bankrec_account", "bankrec_closing", "bankrec_carried",
                                 "e3_candidate", "e3_confirm_all", "e3_unmatched"} & codes)
        self.assertGreaterEqual(len(codes), 6, codes)
        gates = {r["computed"]["gate"]["code"] for r in self.en.values() if r.get("computed")}
        self.assertEqual(gates, {"tied", "not_tied"})
        reasons = {e["reason_code"] for r in self.en.values() if r.get("computed") for e in r["computed"]["run_exceptions"]}
        self.assertGreaterEqual(len(reasons), 8, reasons)

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

    def test_what_is_said_is_english_and_not_only_free_of_thai(self):
        res = self.en["does_not_tie_recorded_twice"]
        gate = res["computed"]["gate"]
        self.assertRegex(gate["what"], r"^Not yet reconciled: the two adjusted balances differ by [\d,]+\.\d\d baht$")
        self.assertTrue(all(re.match(r"^[A-Z0-9]", r) for r in gate["reasons"]), gate["reasons"])
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

    def test_a_run_in_english_is_the_same_run(self):
        # the same questions by key, the same answers, the same figures and the same end
        for cid, en in self.en.items():
            th = self.th[cid]
            self.assertEqual([f["key"] for f in en["asked"]], [f["key"] for f in th["asked"]], cid)
            self.assertEqual((en["answers"], en["stops_at"]), (th["answers"], th["stops_at"]), cid)
            if th.get("computed"):
                self.assertEqual(en["computed"]["totals"], th["computed"]["totals"], cid)
                self.assertEqual(en["computed"]["gate"]["code"], th["computed"]["gate"]["code"], cid)
                self.assertEqual(en["render"]["passed"], th["render"]["passed"], cid)
                self.assertEqual([k["name"] for a in en["render"]["artifacts"] for k in a["quality"]["checks"]],
                                 [k["name"] for a in th["render"]["artifacts"] for k in a["quality"]["checks"]], cid)
                self.assertEqual([k["ok"] for a in en["render"]["artifacts"] for k in a["quality"]["checks"]],
                                 [k["ok"] for a in th["render"]["artifacts"] for k in a["quality"]["checks"]], cid)
            # (the eval set's own expectations are of the Thai run — its words included — and are held by its own test)
            self.assertEqual(K.check(next(c for c in self.cases if c["id"] == cid), th), [], cid)

    def test_the_working_paper_is_the_one_a_thai_run_writes(self):
        # the documents' own pass has not been made: the file is written from the Thai table, whatever the run
        for cid, en in self.en.items():
            th = self.th[cid]
            if not th.get("computed"):
                continue
            self.assertNotIn("document", th["computed"], cid)           # a Thai run carries one table, as before
            self.assertIn("document", en["computed"], cid)
            if any(n.endswith(".pdf") and "statement_sep.pdf" == n for n in next(c for c in self.cases if c["id"] == cid)["files"]):
                continue   # where a row of a PDF is ("หน้า 1 บรรทัด 4") is written when the file is parsed, in the run's language
            self.assertEqual(en["computed"]["document"], th["computed"], cid)
            self.assertEqual(en["sheets"], th["sheets"], cid)

    def test_a_thai_run_after_an_english_one_is_thai(self):
        case = next(c for c in self.cases if c["id"] == "does_not_tie_recorded_twice")
        K.run_case(case, lang="en")
        again = K.run_case(case)
        self.assertEqual(again["computed"], self.th[case["id"]]["computed"])
        self.assertTrue(again["computed"]["gate"]["what"].startswith("ยังไม่ลงตัว"))


if __name__ == "__main__":
    unittest.main()
