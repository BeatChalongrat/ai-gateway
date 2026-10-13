"""Invoice ↔ purchase order matching says what it says in the language of the run (AM14).

Until this card its engine wrote Thai whatever the screen: the questions about a sheet, a link, a seller,
a unit; what was assumed; why an invoice is blocked and what a person may do about it; every line's checks
and notes; the summary's own words; the lines of the gate on the working paper. Their sentences are in the
package's manifest now — Thai under "strings" by key, the English of each under "en" — and the code holds
keys (packages/po_matching/words.py, engine/words.py).

The rule itself is the shared one (test_am14_words.EnglishRuns), held here on the package's own eval set:
every case run in English beside the same case in Thai. What the set does not reach is held beside it — a
file left out, a document left out, a unit's name, and the words a parse keeps.
"""

import json
import os
import re
import shutil
import tempfile
import types
import unittest

from agent_market.engine import words
from agent_market.packages.po_matching.checks import inputs as I
from agent_market.packages.po_matching.checks import units as U
from agent_market.packages.po_matching.scripts import match as M
from agent_market.packages.po_matching.words import W
from agent_market.tests import pokit as K
from agent_market.tests.test_am14_words import THAI, EnglishRuns, said_to_a_person
from agent_market.tests.test_manifest_english import ERROR_SHAPE


class APoMatchingRunInEnglish(EnglishRuns, unittest.TestCase):
    package, kit = "po_matching", K
    documents = True   # AM18
    own_more = ("ผู้จัดทำ ตัวอย่าง", "สมหญิง ผู้ตรวจ")   # pokit.run_files' preparer, and the person who answers its cards

    @classmethod
    def files_of(cls, case):
        # a case's earlier rounds are the install's history of its last one: their files are the person's too
        names = [n for r in case.get("rounds") or [] for n in r["files"]] + list(case["files"])
        return [(os.path.join(K.FIX, n), n) for n in dict.fromkeys(names)]

    def test_the_set_asks_every_kind_of_question_and_gives_every_reason(self):
        codes = {f["code"] for r in self.en.values() for f in r["asked"]}
        self.assertEqual(codes, {"pomatch_kind", "missing_file", "ap_gr_practice", "e3_candidate", "pomatch_supplier", "pomatch_unit"})
        reasons = {e["reason_code"] for r in self.en.values() if r.get("computed") for e in r["computed"]["run_exceptions"]}
        self.assertEqual(reasons, set(M.REASONS))
        self.assertTrue(any(r.get("stops_at") for r in self.en.values()))

    def test_what_is_said_is_english_and_not_only_free_of_thai(self):
        def card(cid, code, of=None):
            return next(f for f in (of or self.en)[cid]["asked"] if f["code"] == code)

        link = card("a_link_is_proposed_and_confirmed", "e3_candidate")
        self.assertEqual(link["header"], "Match to PO")
        self.assertRegex(link["question"], r"^Invoice no\. \S+ from “[^”]+”: “[^”]+”, .*unit price [\d,.]+ baht and purchase order PO \S+ line no\. \d+: ")
        self.assertRegex(link["question"], r" — the document .+\. Are they the same item\?$")
        self.assertEqual([o["label"] for o in link["options"]], ["Same item", "Different items", "Not sure yet"])
        self.assertEqual(link["evidence"]["columns"], ["Document", "Date", "Description", "Quantity", "Unit", "Unit price", "Amount", "File", "Row"])
        twice = card("an_invoice_that_may_be_the_same_is_asked", "e3_candidate")
        self.assertEqual(twice["header"], "Duplicate?")
        self.assertRegex(twice["question"], r"^Invoice no\. \S+ from “[^”]+”, total [\d,.]+ baht, and invoice no\. \S+ dated \d+ [A-Z][a-z]{2} \d{4}, "
                                            r"total [\d,.]+ baht, sent earlier — .+\. Is it the same invoice\?$")
        self.assertEqual([o["label"] for o in twice["options"]], ["The same invoice (a duplicate)", "A different invoice", "Not sure yet"])
        self.assertEqual(twice["evidence"]["columns"], ["Invoice", "Date", "Number", "Total", "File"])
        kind = card("a_sheet_that_does_not_say_what_it_is", "pomatch_kind")
        self.assertEqual(kind["header"], "Which doc?")
        self.assertEqual([o["label"] for o in kind["options"]], ["Purchase order (PO)", "Goods receipt (GR)", "Invoice or tax invoice",
                                                                 "Do not use this sheet"])
        practice = card("practice_guess_without_a_receipt_file", "ap_gr_practice")
        self.assertEqual(practice["header"], "GR issued?")
        self.assertRegex(practice["question"], r"^Does your company issue a goods receipt \(GR\) when it receives goods\? \(this run has no goods "
                                               r"receipt file, so it probably does not\) This is asked once")
        self.assertEqual([o["label"] for o in practice["options"]], ["Issues goods receipts", "Does not issue goods receipts"])
        seller = card("same_name_another_legal_form_is_asked", "pomatch_supplier")
        self.assertEqual((seller["header"], seller["evidence"]["columns"]), ("Seller", ["Document", "Seller name", "Tax ID"]))
        self.assertRegex(seller["question"], r" — the names agree but the legal forms differ\. Is it the same seller\?$")
        missing = card("a_sheet_nobody_names_is_set_aside", "missing_file")
        self.assertRegex(missing["question"], r"^Still to come: Tax invoices or invoices from the seller\. I need both ")
        self.assertEqual(missing["request"][0]["label"], "Tax invoices or invoices from the seller")
        # a unit the files are read by is said by a name an English reader types back; one nobody listed, as written
        unit = card("unit_answered_by_a_person", "pomatch_unit")
        self.assertEqual(unit["header"], "Unit")
        self.assertRegex(unit["question"], r"^The PO orders in “box” but the invoice is in “[^”]+” for .+ — how many \S+ is 1 box\?")
        self.assertRegex(unit["input"]["hint"], r"^Type how many \S+ are in 1 box, for example 12, or in full, for example 1 box = 12 \S+$")
        self.assertEqual(unit["facts"], card("unit_answered_by_a_person", "pomatch_unit", self.th)["facts"])   # what the engine compares by: as it was
        notes = [n for x in self.en["unit_converted_by_a_measure"]["computed"]["lines"] for n in x["notes"]]
        self.assertTrue(any(re.match(r"^Unit converted: 1 pcs = [\d.]+ dozen$", n) for n in notes), notes)

        res = self.en["clean_three_way"]
        c = res["computed"]
        self.assertRegex(c["fallback_narrative"]["summary"],
                         r"^Invoices checked against purchase orders: \d+ — passed \d+ · blocked \d+ · need a decision \d+$")
        self.assertRegex(c["fallback_narrative"]["movements"][0], r"^Three-way match \d+ lines · two-way match \d+ lines$")
        self.assertEqual((c["period_label"], c["practice"]["label"]), ("September 2026", "Issues goods receipts"))
        self.assertEqual({i["status_label"] for i in c["invoices"]}, {"Passed · ready to submit for approval"})
        self.assertTrue(self.th["clean_three_way"]["computed"]["fallback_narrative"]["summary"].startswith("ตรวจใบแจ้งหนี้กับใบสั่งซื้อ"))
        labels = [k["label"] for a in res["render"]["artifacts"] for k in a["quality"]["checks"]]
        self.assertIn("Every sheet is there", labels)
        self.assertIn("Every invoice has its status, how it was checked, and the reason when it did not pass", labels)
        self.assertTrue(all(re.match(r"^[A-Z]", x) for x in labels), labels)
        figures = next(k for a in res["render"]["artifacts"] for k in a["quality"]["checks"] if k["name"] == "xlsx.figures")
        self.assertRegex(figures["detail"], r"^Read the figures back from the file: \d+ values, every one as computed$")

    def test_an_exception_says_why_and_what_may_be_done_in_english(self):
        seen = set()
        for cid, res in self.en.items():
            for e, t in zip((res.get("computed") or {}).get("run_exceptions") or [], (self.th[cid].get("computed") or {}).get("run_exceptions") or []):
                seen.add(e["reason_code"])
                self.assertEqual((e["reason_code"], e["subject"], [x["value"] for x in e["choices"]]),
                                 (t["reason_code"], t["subject"], [x["value"] for x in t["choices"]]), cid)
                self.assertRegex(e["reason"], r"^[A-Z]", cid)
                for text in [e["reason"], e["proposed_action"]] + [x["label"] for x in e["choices"]]:
                    self.assertIsNone(THAI.search(text), f"{cid}: {text}")
                self.assertEqual(e["proposed_action"], e["choices"][0]["label"], cid)
                self.assertRegex(e["detail"], r"^Invoice ", cid)
        self.assertEqual(seen, set(M.REASONS))

    def test_no_english_sentence_has_the_shape_of_an_error_or_says_what_no_result_may(self):
        # a sentence with an error's shape ("line 12") is shown to nobody; and no result of this agent, in either
        # language, says that an invoice was approved or paid
        for cid, res in self.en.items():
            for text in said_to_a_person(res):
                self.assertIsNone(ERROR_SHAPE.search(text), f"{cid}: {text[:160]}")
            if res.get("computed"):
                blob = json.dumps({k: v for k, v in res["computed"].items() if k != "document"}, ensure_ascii=False)
                for word in ("approved", "paid", "อนุมัติแล้ว", "จ่ายแล้ว"):
                    self.assertNotIn(word, blob, cid)

    def test_a_figure_is_asked_about_by_its_english_name_and_by_the_papers_own(self):
        en = {c["id"]: c for c in self.en["clean_three_way"]["computed"]["citations"]}
        th = {c["id"]: c for c in self.th["clean_three_way"]["computed"]["citations"]}
        self.assertEqual(en["s1"]["label"], "Passed · ready to submit for approval (invoices)")
        self.assertEqual(en["v2"]["label"], "Value: Blocked (THB)")
        for cid, c in th.items():
            self.assertEqual(c["aliases"], [], cid)                                         # a Thai run: as it was
            self.assertEqual(en[cid]["aliases"], [c["label"]], cid)
            where = lambda x: [(s["file"], s["sheet"], s["row"], s["value"]) for s in x["sources"]]   # noqa: E731
            self.assertEqual((en[cid]["value"], where(en[cid])), (c["value"], where(c)), cid)


class WhatTheSetDoesNotReach(unittest.TestCase):
    """No case of the eval set leaves a file or a document out of a finished round, and none has a parse that fails."""

    def tearDown(self):
        words.use("th")

    def test_a_file_that_is_not_used_is_said_why_in_the_runs_language(self):
        from openpyxl import Workbook

        cases = K.load_cases()
        case = next(c for c in cases["cases"] if c["id"] == "clean_three_way")
        tmp = tempfile.mkdtemp(prefix="am14_po_")
        try:
            shutil.copy(os.path.join(K.FIX, case["files"][0]), os.path.join(tmp, "the_same_again.xlsx"))
            wb = Workbook()
            wb.active.append(["alpha", "beta"])
            wb.active.append([1, 2])
            wb.save(os.path.join(tmp, "nothing_of_ours.xlsx"))
            paths = [(os.path.join(K.FIX, n), n) for n in case["files"]] + [(os.path.join(tmp, n), n) for n in sorted(os.listdir(tmp))]
            run = lambda lang: K.run_files(paths, defaults={**cases["defaults"], **(case.get("defaults") or {})},   # noqa: E731
                                           today=cases["today"], lang=lang)
            th, en = run(None), run("en")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        left = {e["name"]: e for e in en["computed"]["not_included"]}
        self.assertEqual({n: e["code"] for n, e in left.items()}, {"the_same_again.xlsx": "same_file", "nothing_of_ours.xlsx": "unrecognised_sheet"})
        self.assertEqual(left["the_same_again.xlsx"]["reason"], "It is the same file as one already sent")
        self.assertEqual(left["the_same_again.xlsx"]["detail"], f"The same file as {case['files'][0]} (every byte the same)")
        self.assertEqual(left["the_same_again.xlsx"]["how"], "Nothing to bring back: this same file is already in use")
        self.assertRegex(left["nothing_of_ours.xlsx"]["reason"], r"^It has no column headings of a purchase order, a goods receipt or an invoice")
        self.assertEqual(left["nothing_of_ours.xlsx"]["how"], "Send the file again in a format that can be read")
        said = [a["text"] for a in en["computed"]["assumptions"] if a["code"] == "file_not_used"]
        self.assertEqual(sorted(said), ["The file nothing_of_ours.xlsx was not used: it has no column headings of a purchase order, a goods "
                                        "receipt or an invoice",
                                        "The file the_same_again.xlsx was not used: it is the same file as one already sent; counted once"])
        # the same round in Thai: the same files left out, for the manifest's own Thai reasons — and one table each, in its own
        # language (AM18: the paper follows the run; test_am14_words holds the two papers against each other cell for cell)
        for e, t in zip(en["computed"]["not_included"], th["computed"]["not_included"]):
            self.assertEqual((e["doc_key"], e["code"], e["can_restore"]), (t["doc_key"], t["code"], t["can_restore"]))
            self.assertTrue(all(THAI.search(t[k]) for k in ("reason", "detail", "how")), t)
        self.assertNotIn("document", en["computed"])
        self.assertEqual(list(en["sheets"]) != list(th["sheets"]), True)
        self.assertEqual(en["computed"]["totals"], th["computed"]["totals"])

    def test_a_document_that_is_left_out_is_said_why_in_the_runs_language(self):
        def left(code, restore):
            doc = types.SimpleNamespace(status="excluded", upload=types.SimpleNamespace(key="d1", name="x.pdf"),
                                        exclusion={"code": code, "detail": "", "decided_by": "rule", "restore": restore})
            return M._not_included(types.SimpleNamespace(docs=[doc], others=[], answers={}))[0]

        ways = (None, {"set": {}, "drop": ["k"]}, {"set": {"k": "v"}, "drop": []})
        for code in list(M.EXCLUDED_TH) + ["a_code_nobody_listed"]:
            for restore in ways:
                thai = left(code, restore)
                with words.speaking("en"):
                    english = left(code, restore)
                self.assertTrue(THAI.search(thai["reason"]) and THAI.search(thai["how"]), code)
                self.assertRegex(english["reason"], r"^[A-Z][^฀-๿]+$", code)
                self.assertRegex(english["how"], r"^(It can(not)? be brought back in)[^฀-๿]+$", code)
                self.assertEqual((english["can_restore"], english["restore"]), (thai["can_restore"], thai["restore"]), code)
        self.assertEqual(left("copy_not_original", None)["reason"], W.strings["excluded.copy_not_original"])
        with words.speaking("en"):
            self.assertEqual(left("excluded_by_person", None)["reason"], "You had this document left out")
            self.assertEqual(left("a_code_nobody_listed", None)["reason"], "This document was not included")

    def test_a_parse_keeps_the_key_of_why_a_file_was_not_read(self):
        # what a parse writes is read again by every later step — and by the step that writes the Thai paper of an
        # English run: it keeps the sentence's key, never its words
        tmp = tempfile.mkdtemp(prefix="am14_po_")
        try:
            path = os.path.join(tmp, "notes.docx")
            with open(path, "wb") as f:
                f.write(b"not a file this agent reads")
            with words.speaking("en"):
                u = I.parse(path, "notes.docx", {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual((u.kind, u.data["reason"]), ("other", "unsupported_type"))
        self.assertIn(u.data["say"], W.strings)
        self.assertIsNone(THAI.search(json.dumps(I.dump(u), ensure_ascii=False)))
        thai = I.said(u.data["say"])
        self.assertEqual(thai, W.strings[u.data["say"]])
        with words.speaking("en"):
            self.assertRegex(I.said(u.data["say"]), r"^Only Excel or CSV files of purchase orders and goods receipts")
            # a parse made before this card kept the words themselves: they are given as they are, in any run
            self.assertEqual((I.said(thai), I.said(None), I.said("")), (thai, "", ""))

    def test_a_unit_is_said_in_english_by_a_name_that_is_read_back_as_the_same_unit(self):
        names = {k: v for k, v in W.strings.items() if k.startswith("unit.")}
        self.assertGreaterEqual(len(names), 30)
        for key, thai in names.items():
            self.assertIn(thai, U.UNITS, key)                               # the name the files are read by
            self.assertEqual(U.said(thai), thai, key)                       # a Thai run: as it was
            with words.speaking("en"):
                english = U.said(thai)
            self.assertIsNone(THAI.search(english), key)
            self.assertEqual(U.canonical(english), thai, key)               # typed back in an answer, it is that unit
        with words.speaking("en"):
            self.assertEqual((U.said("PCS."), U.said("กล่อง"), U.said("Box")), ("pcs", "box", "box"))
            # a unit with no Latin way of writing, and one nobody listed, is said as it is
            unlisted = next(u for u in U.UNITS if u not in names.values())
            self.assertEqual((U.said(unlisted), U.said("pallet")), (unlisted, "pallet"))

    def test_what_was_asked_of_the_paper_is_named_as_the_paper_names_it(self):
        from agent_market.packages import po_matching as PM
        from agent_market.packages.po_matching.scripts.workbook import H_LINES

        # the gate's line about the order of the lines says the heading of the column they are ordered by — in both languages
        self.assertIn(PM.VARIANCE, H_LINES.keys)
        for key in ("asked.variance.not", "asked.variance.ok"):
            self.assertIn(W.strings[PM.VARIANCE], W.strings[key], key)
            self.assertIn(W.english_of(PM.VARIANCE).lower(), W.english_of(key), key)

    def test_a_table_of_the_engine_says_its_words_when_it_is_read(self):
        # the tables other code reads by code: the same codes in both languages, each label said in the run's
        self.assertEqual(M.STATUS_TH["pass"], W.strings["status.pass"])
        with words.speaking("en"):
            self.assertEqual(M.STATUS_TH["pass"], "Passed · ready to submit for approval")
            self.assertEqual((M.REASON_TH["po_closed"], M.DOC_TYPE_TH["credit_note"], M.DEPTH_SHORT["three_way"]), ("PO closed", "Credit note", "3-way"))
            english = {code: (M.REASON_TH[code], [W(label) for label, _ in M.CHOICES[code]]) for code in M.REASONS}
        for code, (reason, choices) in english.items():
            self.assertIsNone(THAI.search(reason + " ".join(choices)), code)
            self.assertGreaterEqual(len(choices), 2, code)
            self.assertTrue(THAI.search(M.REASON_TH[code]), code)


if __name__ == "__main__":
    unittest.main()
