"""The procurement package's own words, and what it must not know (AM12).

Every sentence this package writes while it runs — a question, a column's heading, the reason a source
was not used, a gap on the face of the draft — is in its manifest: Thai under "strings", by key, and the
English of each under "en" (test_manifest_english keeps those two together). The code holds keys, never
words. This file keeps the code and the manifest together:

  * every key the code names is in the manifest, and every reason the code can give has its words;
  * no string of the manifest is one nothing asks for;
  * the code has no Thai of its own — a sentence written in a .py file would be Thai on every screen.

And the rule the agent is built on: it searches for whatever a person asks for. Nothing in its code,
its prompts or its manifest names a kind of thing to buy; the same build is run on two requests that
have nothing in common. The sample request (sample-data/, self-test.json) is a request, not code.
"""

import ast
import datetime
import json
import os
import re
import shutil
import tempfile
import unittest

from agent_market.packages import procurement as P
from agent_market.packages.procurement import words

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "packages", "procurement")
THAI = re.compile(r"[฀-๿]")
KEY = re.compile(r"^[a-z_]+(\.[a-z_0-9]+)*$")
# a family of keys whose last part is a code that arrives as data (the fetcher's reason, a column's name …)
FAMILY = re.compile(r'f"([a-z_]+(?:\.[a-z_]+)*)\.\{')


def sources() -> dict[str, str]:
    out = {}
    for d, _, files in os.walk(PKG):
        for f in files:
            if f.endswith(".py"):
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    out[os.path.relpath(os.path.join(d, f), PKG).replace(os.sep, "/")] = fh.read()
    return out


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


class TheCodeAndItsWords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = sources()
        cls.keys = set(words.STRINGS)
        cls.families = {m for s in cls.src.values() for m in FAMILY.findall(s)}
        cls.named = {v for s in cls.src.values() for _, v in literals(s) if KEY.match(v) and "." in v}

    def test_every_string_has_both_languages(self):
        self.assertGreater(len(self.keys), 250)
        for key in sorted(self.keys):
            thai = words.STRINGS[key]
            self.assertIn(thai, words.ENGLISH, key)
            self.assertFalse(THAI.search(words.ENGLISH[thai]), key)
            self.assertTrue(words.t("th", key, **self._values(thai)).strip() and words.t("en", key, **self._values(thai)).strip(), key)

    @staticmethod
    def _values(text: str) -> dict:
        return {name: "1" for name in re.findall(r"\{([a-z_]+)\}", text)}

    def test_every_key_the_code_names_is_in_the_manifest(self):
        # a dotted name in the code that begins as the keys of the manifest do is a key
        heads = {k.split(".")[0] for k in self.keys}
        for name in sorted(self.named):
            if name.split(".")[0] in heads and not name.endswith((".py", ".json", ".md", ".jpg", ".xlsx", ".docx")):
                self.assertIn(name, self.keys, name)

    def test_every_reason_the_code_can_give_has_its_words(self):
        compare, request = self.src["scripts/compare.py"], self.src["scripts/request.py"]
        whys = set(re.findall(r'"field": [^,{}]+, "why": "([a-z_]+)"', compare))
        # … and the two a figure's own line is refused for (_shown_as_a_figure returns them)
        self.assertIn('return "inside_text"', compare)
        self.assertIn('return "several_figures"', compare)
        whys |= {"inside_text", "several_figures"}
        self.assertGreaterEqual(len(whys), 12)
        for why in whys:
            self.assertIn(f"notfound.{why}", self.keys, why)
        # a price not taken is a gap of the draft, by the same reason
        for why in set(re.findall(r'\{"field": "price", "why": "([a-z_]+)"\}', compare)) | {"no_quote", "not_on_page", "not_stated"}:
            self.assertIn(f"gap.price.{why}", self.keys, why)
        # a source that is not used: the reasons this code gives (the fetcher's and the runner's are checked where they are made)
        dropped = set(re.findall(r'\*\*gone, "why": "([a-z_]+)"', compare))
        self.assertEqual(dropped, {"cannot_describe", "not_read", "read_refused", "snapshot_missing", "page_states_no"})
        for why in dropped | {"other"}:
            self.assertIn(f"drop.{why}", self.keys, why)
        pairs = set(re.findall(r'gap\("([a-z_]+)", "([a-z_]+)"', request))
        pairs |= {(c, w) for c, _, w in re.findall(r'gap\("([a-z_]+)", "([a-z_]+)" if [^\n]*? else "([a-z_]+)"', request)}
        pairs |= set(re.findall(r'\{"code": "([a-z_]+)", "why": "([a-z_]+)"', request))
        self.assertGreaterEqual(len(pairs), 20)
        for code, why in pairs:
            self.assertIn(f"gap.{code}", self.keys, code)
            self.assertIn(f"gap.{code}.{why}", self.keys, (code, why))
        # and no gap has words that no code can reach
        # (the price's gap is given the reason the figure was refused for: a variable, so it is named here)
        self.assertIn('gap("price", whys[0] if whys else "not_stated")', request)
        reachable = {f"gap.{c}" for c, _ in pairs} | {f"gap.{c}.{w}" for c, w in pairs} | {"gap.price"} | {f"gap.price.{w}" for w in whys | {"not_stated"}}
        self.assertEqual({k for k in self.keys if k.startswith("gap.")} - reachable, set())
        self.assertEqual({k for k in self.keys if k.startswith("notfound.")}, {f"notfound.{w}" for w in whys})

    def test_no_string_of_the_manifest_is_one_nothing_asks_for(self):
        unused = []
        for key in sorted(self.keys):
            if key in self.named or key in {v for s in self.src.values() for _, v in literals(s)}:
                continue
            if any(key.startswith(f + ".") for f in self.families):
                continue
            unused.append(key)
        self.assertEqual(unused, [])

    def test_the_code_holds_no_words_of_its_own(self):
        for path, src in self.src.items():
            for line, value in literals(src):
                self.assertFalse(THAI.search(value), f"{path}:{line}: {value[:40]!r} — a sentence belongs in the manifest, in both languages")

    def test_a_key_that_is_not_in_the_manifest_is_an_error_not_an_empty_cell(self):
        with self.assertRaises(KeyError):
            words.t("th", "gap.price.a_reason_nobody_wrote")


class NothingKnowsWhatIsBeingBought(unittest.TestCase):
    """The words a category would bring with it — in Thai and in English — are nowhere in what decides."""

    # the two requests the build is proven on, and the sample's; a list of things a search could be for can
    # never be complete, which is the point: the code must not need one
    WORDS = re.compile(r"chair|เก้าอี้|\bpoe\b|สวิตช์|network switch|whiteboard|ไวท์บอร์ด|กระดาน|printer|เครื่องพิมพ์|laptop|โน้ตบุ๊ก|"
                       r"furniture|เฟอร์นิเจอร์|stationery|เครื่องเขียน|projector|โปรเจกเตอร์|toner|หมึก|คอมพิวเตอร์|\bcomputers?\b", re.I)

    def test_code_prompts_and_manifest_name_no_kind_of_thing(self):
        looked = 0
        for d, _, files in os.walk(PKG):
            if os.path.basename(d) in ("sample-data", "__pycache__"):
                continue
            for f in files:
                if not f.endswith((".py", ".md", ".json")) or f == "self-test.json":
                    continue
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    for n, line in enumerate(fh, start=1):
                        found = self.WORDS.search(line)
                        self.assertIsNone(found, f"{f}:{n}: {found.group(0) if found else ''}")
                looked += 1
        self.assertGreaterEqual(looked, 14)

    def test_the_prompts_are_the_four_the_manifest_names_and_ask_for_json_only(self):
        with open(os.path.join(PKG, "agent.json"), encoding="utf-8") as f:
            web = json.load(f)["web"]
        self.assertEqual(set(web["prompts"]), {"brief", "plan", "select", "read"})
        for name, rel in web["prompts"].items():
            with open(os.path.join(PKG, rel), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("JSON", text, name)
            # a prompt tells a model what to return; it offers it nothing to call and nowhere to go
            for word in ("tool", "function_call", "browse", "http://", "https://"):
                self.assertNotIn(word, text.lower(), (name, word))
        caps = web["caps"]
        self.assertTrue(all(isinstance(caps[k], int) and caps[k] > 0 for k in ("searches", "pages", "pages_per_host", "seconds", "source_mb", "results")))


class TheSampleGivesWhatItsVersionSays(unittest.TestCase):
    """The package's own sample (three stored pages and what a reader read on them) through the package, in
    each language: it asks nothing, and its totals are the ones self-test.json states."""

    def run_sample(self, lang: str) -> tuple[dict, dict, str]:
        with open(os.path.join(PKG, "self-test.json"), encoding="utf-8") as f:
            st = json.load(f)
        base = os.path.join(PKG, os.path.dirname(st["research"]))
        with open(os.path.join(PKG, st["research"]), encoding="utf-8") as f:
            research = json.load(f)
        today = datetime.date(2026, 10, 6)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        defaults = dict(st.get("settings") or {})
        pre = P.preflight([], message=st.get("message") or "", defaults=defaults, answers={}, today=today, proposals={"brief": research["brief"]}, lang=lang)
        self.assertEqual(pre["findings"], [], lang)
        first = P.compute([], {}, pre["period"], answers={}, defaults=defaults, today=today, proposals=research, in_dir=base, out_dir=tmp, lang=lang)
        post = P.postcompute([], {}, pre["period"], first, answers={}, defaults=defaults, lang=lang)
        return st, {"pre": pre, "first": first, "post": post, "research": research, "defaults": defaults, "base": base, "today": today}, tmp

    def test_the_sample_in_thai_and_in_english(self):
        for lang in ("th", "en"):
            st, r, tmp = self.run_sample(lang)
            # with the sample's own choice set, nothing is asked and the totals are the stated ones
            self.assertEqual([f["code"] for f in r["post"]["findings"]], [], lang)
            got = r["first"]["totals"]
            self.assertEqual({k: got[k] for k in st["expect"]["totals"]}, st["expect"]["totals"], lang)
            ind = os.path.join(tmp, "in")
            os.makedirs(ind)
            for name in r["first"]["evidence_files"]:
                shutil.copy(os.path.join(tmp, name), os.path.join(ind, name))
            made = P.render(r["first"], {"formats": ["xlsx", "docx"], "names": {"xlsx": "s.xlsx", "docx": "s.docx"},
                                         "theme": {"id": "navy", "company": "Sample Co"},
                                         "run": {"preparer": "Somchai", "organisation": "Sample Co", "date": "2026-10-06"}}, in_dir=ind, out_dir=tmp)
            failed = [(a["name"], c["name"], c["detail"]) for a in made["artifacts"] for c in a["quality"]["checks"] if not c["ok"]]
            self.assertEqual((made["passed"], failed), (True, []), lang)
            self.assertEqual({c["name"] for a in made["artifacts"] for c in a["quality"]["checks"]},
                             {"xlsx.figures", "xlsx.print", "xlsx.print.one_across", "xlsx.look", "look.markers", "docx.request",
                              "xlsx.commentary", "look.fit", "file.no_model"}, lang)  # AM23 §3.1: no model's name in either file
            if lang == "en":
                from docx import Document
                from openpyxl import load_workbook

                doc = Document(os.path.join(tmp, "s.docx"))
                text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for row in t.rows for c in row.cells])
                book = load_workbook(os.path.join(tmp, "s.xlsx"))
                cells = [str(c.value) for ws in book.worksheets for row in ws.iter_rows() for c in row if c.value is not None]
                cells += [ws.title for ws in book.worksheets]
                # what the package wrote is English; what a page printed and what the person typed stay as they are
                own = [line for line in text.split("\n") + cells if THAI.search(line)]
                theirs = json.dumps([r["research"]["brief"], [b for s in ("s1", "s2", "s3") for b in self._blocks(r["base"], s)]], ensure_ascii=False)
                for line in own:
                    for part in re.findall(r"[฀-๿][฀-๿ ]*[฀-๿]", line):
                        self.assertIn(part.strip(), theirs, f"Thai the package wrote on an English file: {line[:80]}")

    @staticmethod
    def _blocks(base: str, sid: str) -> list[str]:
        with open(os.path.join(base, f"text_{sid}.json"), encoding="utf-8") as f:
            return [b["text"] for b in json.load(f)["blocks"]]

    def test_without_the_samples_choice_the_one_thing_asked_is_which_candidate(self):
        st, r, _ = self.run_sample("th")
        defaults = {k: v for k, v in r["defaults"].items() if k != "proc_sample_pick"}
        first = P.compute([], {}, r["pre"]["period"], answers={}, defaults=defaults, today=r["today"], proposals=r["research"],
                          in_dir=r["base"], out_dir=tempfile.mkdtemp(), lang="th")
        post = P.postcompute([], {}, r["pre"]["period"], first, answers={}, defaults=defaults, lang="th")
        self.assertEqual([(f["code"], f["kind"], f["severity"]) for f in post["findings"]], [("proc_pick", "pick", "blocking")])
        pick = post["findings"][0]
        self.assertEqual(len(pick["options"]), 4)                      # three candidates, and "none of these"
        self.assertEqual(json.loads(json.dumps(pick["options"][-1]["value"])), "none")
        self.assertEqual((first["totals"]["chosen"], first["request"]), (0, None))
        table = pick["evidence"]["comparison"]
        self.assertEqual([c["no"] for c in table["candidates"]], [1, 2, 3])
        # the order is fewest things still to settle, then the lowest total that could be worked out — said, never "best"
        self.assertEqual([len(c["open"]) for c in table["candidates"]], sorted(len(c["open"]) for c in table["candidates"]))
        # no answer of the card is marked as the one to take
        self.assertIsNone(pick.get("recommended"))
        blob = " ".join([pick["header"], pick["question"]] + [f"{o['label']} {o.get('description') or ''}" for o in pick["options"]]).lower()
        # the card says what the order is and that it is not advice; it never calls a candidate the one to take
        for word in ("ดีที่สุด", "ขอแนะนำ", "แนะนำให้", "ควรเลือก", "คุ้มที่สุด", "best", "we recommend", "recommended"):
            self.assertNotIn(word, blob)


if __name__ == "__main__":
    unittest.main()
