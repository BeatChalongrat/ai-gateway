"""AM16 — a question that shows a model's reading says which model read the page.

The engine's question said "the in-house model read this from the image" and its source label "the
in-house model reading the image", whoever had read the page. Since the admin's switch can let an
agent's documents leave, a page may be read by a model outside the organisation — and the run's own
card says the page was sent out while the question beside it named an in-house model. The owner: a
sentence on screen that names the wrong model is the same disease as a card that says masked when
nothing was masked.

Held here — the refusal first: a page read outside is never said to have been read in the organisation,
in either language, in the question, in the two readings of a conflict, or under an option. In-house is
said only when the runner said so (the flag is the JSON ``true``, nothing that merely looks like it),
a model outside is named, and with nothing said about the reader the sentence claims neither. The key
of a question does not move with the reader, so an answer stays on its card. And the three packages
that read pages hand the reader to every question they ask.
"""

import inspect
import json
import os
import unittest

from agent_market.engine import words
from agent_market.engine.e2 import cards
from agent_market.engine.e2.cards import findings_for, reader_of

OUTSIDE = {"model": "google/gemini-2.5-flash", "self_hosted": False}
INSIDE = {"model": "qwen3.8-27b-fp8", "self_hosted": True}
IN_HOUSE_TH, IN_HOUSE_EN = "โมเดลในองค์กร", "in-house"
OUT_TH, OUT_EN = "โมเดลนอกองค์กร “google/gemini-2.5-flash”", "the model outside the organisation “google/gemini-2.5-flash”"
PKGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packages")


def doc() -> dict:
    """A scanned invoice: one value only the model read, one the text and the model read differently."""
    return {"document": {"name": "scan.pdf", "triage_only": False}, "method": "ocr",
            "fields": {"document_number": {"normalized": "IV1"},
                       "total": {"normalized": "12850.70", "source": {"page": 1}}},
            "questions": [
                {"key": "low_confidence:seller_tax_id", "code": "low_confidence", "subject": "seller_tax_id",
                 "field": "seller_tax_id", "reason": "only a model read this value", "proposed": "0105556000011",
                 "evidence": {"candidates": [{"method": "vision:x", "normalized": "0105556000011"}]}},
                {"key": "source_conflict:total", "code": "source_conflict", "subject": "total", "field": "total",
                 "reason": "two readings", "proposed": "12850.70", "evidence": {"other": "12580.70"}}]}


def said(reader, lang: str) -> str:
    """Everything a person reads on the cards of that document: question, option labels, what is under them."""
    with words.speaking(lang):
        fs = findings_for("d1", doc(), reader)
    assert len(fs) == 2, fs
    return "\n".join([f.question for f in fs] + [o.label + " | " + (o.description or "") for f in fs for o in f.options])


class APageReadOutsideIsNeverSaidToBeReadInside(unittest.TestCase):
    def test_in_thai(self):
        text = said(OUTSIDE, "th")
        self.assertNotIn(IN_HOUSE_TH, text)
        self.assertIn(OUT_TH + " อ่านจากภาพได้ “0105556000011”", text)        # the question
        self.assertIn(OUT_TH + " อ่านจากภาพ: “12,580.70”", text)             # the conflict's second reading
        self.assertIn("ค่าจาก" + OUT_TH + " อ่านจากภาพ", text)               # under the option

    def test_in_english(self):
        text = said(OUTSIDE, "en")
        self.assertNotIn(IN_HOUSE_EN, text)
        self.assertNotIn("inside the organisation", text)
        self.assertIn(OUT_EN + " read “0105556000011” from the image", text)
        self.assertIn(OUT_EN + " reading the image: “12,580.70”", text)
        self.assertIn("The value from " + OUT_EN + " reading the image", text)


class InHouseIsSaidOnlyWhenTheRunnerSaidSo(unittest.TestCase):
    def test_a_page_read_in_the_organisation_says_so_and_names_no_one_outside(self):
        th, en = said(INSIDE, "th"), said(INSIDE, "en")
        self.assertIn("โมเดลในองค์กรอ่านจากภาพได้ “0105556000011”", th)
        self.assertIn("the in-house model read “0105556000011” from the image", en)
        self.assertNotIn("นอกองค์กร", th)
        self.assertNotIn("outside", en)

    def test_with_nothing_said_about_the_reader_the_sentence_claims_neither(self):
        for reader in (None, {}, {"model": "some-model"}, {"model": "some-model", "self_hosted": None},
                       {"self_hosted": False}, {"model": "", "self_hosted": False}):
            th, en = said(reader, "th"), said(reader, "en")
            self.assertNotIn(IN_HOUSE_TH, th, reader)
            self.assertNotIn("นอกองค์กร", th, reader)
            self.assertNotIn(IN_HOUSE_EN, en, reader)
            self.assertNotIn("outside", en, reader)
            self.assertIn("โมเดลอ่านจากภาพได้ “0105556000011”", th, reader)
            self.assertIn("a model read “0105556000011” from the image", en, reader)

    def test_only_the_flag_itself_counts_as_in_house(self):
        for flag in ("true", "True", 1, "yes", [True]):
            th = said({"model": "some-model", "self_hosted": flag}, "th")
            self.assertNotIn(IN_HOUSE_TH, th, flag)

    def test_what_the_runner_wrote_beside_the_reading_is_what_is_read(self):
        props = {"d1": {"model": "google/gemini-2.5-flash", "self_hosted": False, "fields": {}},
                 "d2": {"model": "qwen3.8-27b-fp8", "self_hosted": True, "fields": {}},
                 "d3": {"fields": {}}, "d4": "nonsense"}
        self.assertEqual(reader_of(props, "d1"), OUTSIDE)
        self.assertEqual(reader_of(props, "d2"), INSIDE)
        for key in ("d3", "d4", "d5"):
            self.assertIsNone(reader_of(props, key), key)
        self.assertIsNone(reader_of(None, "d1"))


class TheAnswerStaysOnItsCard(unittest.TestCase):
    def test_the_key_of_a_question_does_not_move_with_the_reader(self):
        keys = []
        for reader in (None, INSIDE, OUTSIDE):
            for lang in ("th", "en"):
                with words.speaking(lang):
                    keys.append([f.key for f in findings_for("d1", doc(), reader)])
        self.assertTrue(all(k == keys[0] for k in keys), keys)

    def test_an_e_tax_file_s_two_readings_are_named_as_before(self):
        d = doc()
        d["method"] = "etax_xml"
        with words.speaking("en"):
            q = findings_for("d1", d, OUTSIDE)[1].question
        self.assertNotIn("model", q)


class ThePackagesHandTheReaderOn(unittest.TestCase):
    def test_every_question_a_package_asks_about_a_page_is_given_its_reader(self):
        from agent_market.packages.expense_audit.checks import receipts
        from agent_market.packages.invoice_processing.checks import review as invoice
        from agent_market.packages.po_matching.checks import review as po

        for mod in (invoice, receipts, po):
            src = inspect.getsource(mod)
            calls = src.count("findings_for(")
            self.assertGreater(calls, 0, mod.__name__)
            self.assertEqual(src.count("reader_of(proposals, "), 1 if mod is receipts else 2, mod.__name__)
            for line in src.splitlines():
                if "findings_for(" in line:
                    self.assertRegex(line, r"findings_for\([^)]*(who|reader_of\(proposals, \w+(\.\w+)?\))\)", f"{mod.__name__}: {line.strip()}")

    def test_no_engine_sentence_and_no_guide_says_in_house_without_being_told(self):
        engine = json.load(open(os.path.join(os.path.dirname(cards.__file__), "..", "words.json"), encoding="utf-8"))
        # the two sentences that may say it are the ones the code picks only on the runner's word
        allowed = {engine["strings"]["e2.reader.inside"], engine["strings"]["e2.source.model"]}
        # (AM23 §3.1: the documents no longer say which model wrote their words, nor where it ran — the two notes
        # AM18 allowed here are gone: docs.deck.inside, docs.model.inside)
        self.assertNotIn("docs.deck.inside", engine["strings"])
        self.assertNotIn("docs.model.inside", engine["strings"])
        for key, s in engine["strings"].items():
            if "ในองค์กร" in s.replace("นอกองค์กร", ""):
                self.assertIn(s, allowed, key)
        from agent_market.deliverables import content, deck_pptx

        self.assertNotIn("docs.model.inside", inspect.getsource(content))
        self.assertNotIn("docs.deck.inside", inspect.getsource(deck_pptx))
        src = inspect.getsource(cards)
        self.assertEqual(src.count('S("e2.reader.inside")'), 1)
        self.assertEqual(src.count('S("e2.source.model")'), 1)
        self.assertIn("if place is True:\n        return S(\"e2.reader.inside\"), S(\"e2.source.model\")", src)
        for pkg in ("invoice_processing", "expense_audit", "po_matching"):
            m = json.load(open(os.path.join(PKGS, pkg, "agent.json"), encoding="utf-8"))
            th = m["guide"]["model_inputs"]["model.vision"]
            en = m["en"][th]
            self.assertNotIn("ส่งให้โมเดลที่รันในองค์กร", th, pkg)
            self.assertNotIn("sent to a model that runs inside the organisation", en, pkg)
            self.assertIn("ออกนอกองค์กรก็ต่อเมื่อผู้ดูแลอนุญาต", th, pkg)
            self.assertIn("only when an admin has allowed this agent's documents to leave", en, pkg)
            guide = json.dumps(m["guide"], ensure_ascii=False)
            self.assertNotIn("ให้โมเดลในองค์กรอ่านซ้ำ", guide, pkg)
            agent_md = open(os.path.join(PKGS, pkg, "AGENT.md"), encoding="utf-8").read()
            self.assertNotIn("qwen3.8-27b-fp8 ที่รันในองค์กร", agent_md, pkg)
            self.assertNotRegex(agent_md, r"(?m)^โมเดลที่รันในองค์กรอ่านภาพ", pkg)


class ARealScanThroughTheStep(unittest.TestCase):
    """The tax-invoice agent on its own scanned fixture, as the runner runs it: parse, preflight, compute,
    render — with the page's reading handed in as an outside model's, then as the in-house model's."""

    def _run(self, reader, lang):
        from agent_market.tests import invoicekit

        case = next(c for c in invoicekit.load_cases()["cases"] if c["id"] == "scan_vision_disagrees")
        run = invoicekit.run_case({**case, "reader": reader}, lang)
        lines = []
        for f in run["asked"].values():
            lines.append(f["question"])
            lines.extend(o["label"] + " | " + (o.get("description") or "") for o in f["options"])
        cards_text = " ¦ ".join(lines)
        self.assertTrue(any(f["code"] == "e2_source_conflict" for f in run["asked"].values()), list(run["asked"]))
        return cards_text

    def test_a_scan_read_by_a_model_outside_names_it_and_never_says_in_house(self):
        th, en = self._run(OUTSIDE, None), self._run(OUTSIDE, "en")
        self.assertIn(OUT_TH + " อ่านจากภาพ", th)
        self.assertNotIn(IN_HOUSE_TH, th)
        self.assertIn(OUT_EN + " reading the image", en)
        self.assertNotIn(IN_HOUSE_EN, en)

    def test_the_same_scan_read_in_the_organisation_says_so(self):
        th = self._run(INSIDE, None)
        self.assertIn("โมเดลในองค์กรอ่านจากภาพ", th)
        self.assertNotIn("นอกองค์กร", th)


if __name__ == "__main__":
    unittest.main()
