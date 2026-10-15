"""The financial report says what it says in the language of the run (AM14).

Its question cards, what it assumed, what it left out and why, its notes, the record of every figure (the
name and the way it is worked out, which an answer about a figure is built from) and every line of its gate
are in the manifest by key, Thai and English; the gate's lines of the shared report, deck and workbook and
the names of the fields on the screen of columns are in engine/words.json. Held here on the package's own
eval set, run in English beside the same set in Thai.

What is not said to a screen is not in the run's language: the report, the deck and the workbook are
written from the Thai table whatever the run (the documents' own pass has not been made), and so are the
parts of the computed table that only they and the model that writes their commentary read — the lines'
names, the names of the figures a sentence may quote, the exceptions, the commentary the rules write, the
period as the platform's own label. The documents here are a .docx, a .pptx and a .xlsx: "the paper is the
one a Thai run writes" compares every text each of the three prints (paragraphs, table cells, shapes, chart
categories, sheet names and cells — evalkit.texts), on the cases RENDERED below; on every other case the
table the files are written from is compared.
"""

import json
import os
import re
import unittest

from agent_market.engine import dataset, words
from agent_market.packages import financial_report as pkg
from agent_market.tests import evalkit as K
from agent_market.tests.test_am14_words import THAI, THAI_RUN, EnglishRuns, checks_of, strings_of, thai_only

FORMATS = ("docx", "pptx", "xlsx")
# the cases whose three files are written and read back in both languages (a plain month, a prior period, a
# budget, a trend, a trial balance, an income statement export, rows left out for two reasons)
RENDERED = ("clean_express_th", "clean_with_prior", "clean_with_budget", "clean_ytd_trend", "trial_balance", "pl_export",
            "period_mixed_excluded", "duplicate_excluded")
# what of the computed table the files and the model that writes their commentary read, and no screen
OF_THE_DOCUMENTS = ("period_label", "prior_label", "lines", "ratios", "top_movements", "composition", "exceptions", "figures",
                    "quotable", "fallback_narrative")


class Kit:
    """evalkit in the shape every package's runs are held in (test_am14_words.EnglishRuns)."""

    check = staticmethod(K.check)
    load_cases = staticmethod(K.load_cases)

    @staticmethod
    def run_case(case: dict, lang: str | None = None) -> dict:
        res = K.run_case(case, lang=lang, formats=FORMATS if case["id"] in RENDERED else ())
        res["asked"] = res["findings"]
        res["sheets"] = res.get("texts") or {}
        return res


def said(res: dict) -> list:
    """Every string of a report's run that reaches a screen."""
    out = []
    for f in res["asked"]:
        out += [f["header"], f["question"]] + [o[k] for o in f["options"] for k in ("label", "description")]
        ev = f.get("evidence") or {}
        out += list(ev.get("columns") or []) + list(ev.get("sample_columns") or []) + [str(ev.get("detected") or "")]
        out += [r.get(k) or "" for r in f.get("request") or [] for k in ("label", "why")]
    out += [a["text"] for a in res["assumptions"]]
    c = res.get("computed")
    if c:
        out += [x.get(k) or "" for x in c["citations"] for k in ("label", "formula")]
        out += [e["reason"] for e in c["excluded"]] + [n["text"] for n in c["notes"]]
        out += strings_of({k: v for k, v in res["summary"].items() if k != "period_label"})
        out += strings_of([[k["label"], k["detail"]] for a in (res.get("render") or {}).get("artifacts") or [] for k in a["quality"]["checks"]])
    return [s for s in out if s]


class AReportRunInEnglish(EnglishRuns, unittest.TestCase):
    package, kit = "financial_report", Kit

    @classmethod
    def files_of(cls, case):
        return [(os.path.join(K.PKG_DIR, "evals", "fixtures", n), n) for n in case["files"]]

    def test_a_person_who_reads_only_english_meets_no_thai_but_their_own_data_and_the_papers_own_words(self):
        left, rendered = [], 0
        for cid, res in self.en.items():
            # (AM18: the files are in the run's language too — nothing of theirs is excused)
            paper = ""
            rendered += bool(res.get("render"))
            for text in said(res):
                for run in THAI_RUN.findall(text):
                    k = thai_only(run)
                    if k not in self.own[cid] and k not in paper and run not in ("ค่าใช้จ่ายในการบริหาร", "กันยายน"):
                        left.append(f"{cid}: “{run}” in: {text[:140]}")
        self.assertEqual(left, [], "\n" + "\n".join(sorted(set(left))[:40]))
        self.assertEqual(rendered, len(RENDERED))

    def test_a_run_in_english_is_the_same_run(self):
        for cid, en in self.en.items():
            th = self.th[cid]
            self.assertEqual([f["key"] for f in en["asked"]], [f["key"] for f in th["asked"]], cid)
            self.assertEqual([(f["default"], [o["value"] for o in f["options"]]) for f in en["asked"]],
                             [(f["default"], [o["value"] for o in f["options"]]) for f in th["asked"]], cid)
            self.assertEqual((en["answers"], en["period"], [(a["code"], a["source"]) for a in en["assumptions"]]),
                             (th["answers"], th["period"], [(a["code"], a["source"]) for a in th["assumptions"]]), cid)
            if th.get("computed"):
                e, t = en["computed"], th["computed"]
                for k in ("accounts", "balance", "trend", "ledger", "source", "materiality", "quotable_ids", "quotable_cites"):
                    if k == "trend":
                        self.assertEqual(dict(e[k], cite=None, labels=None), dict(t[k], cite=None, labels=None), (cid, k))   # (AM18: the labels follow the run)
                    else:
                        self.assertEqual(e[k], t[k], (cid, k))
                where = lambda c: (c["id"], c["value"], c["sources"], c.get("derived_from"), c["script"], c.get("unit"))   # noqa: E731
                self.assertEqual([where(c) for c in e["citations"]], [where(c) for c in t["citations"]], cid)
                self.assertEqual([(x["code"], x["rows"]) for x in e["excluded"]], [(x["code"], x["rows"]) for x in t["excluded"]], cid)
                self.assertEqual([n["code"] for n in e["notes"]], [n["code"] for n in t["notes"]], cid)
                self.assertEqual(dict(en["summary"], excluded=None, period_label=None), dict(th["summary"], excluded=None, period_label=None), cid)
                if th.get("render"):
                    self.assertTrue(th["render"]["passed"], (cid, [k for a in th["render"]["artifacts"] for k in a["quality"]["checks"] if not k["ok"]]))
                    self.assertEqual((en["render"]["passed"], checks_of(en)), (th["render"]["passed"], checks_of(th)), cid)
                    values = lambda r: [k.get("values") for a in r["render"]["artifacts"] for k in a["quality"]["checks"]]   # noqa: E731
                    self.assertEqual(values(en), values(th), cid)
            self.assertEqual(self.kit.check(self.case(cid), th), [], cid)
            self.assertEqual(self.kit.check(self.case(cid), en), [], cid)

    def test_the_paper_is_the_one_a_thai_run_writes(self):
        # AM18: the .docx, the .pptx and the .xlsx — every text each prints — are the Thai run's files in English:
        # the same texts in the same order, every number and date the same, the person's own words in the same
        # places, and only the documents' own words different; none of those is left in Thai.
        from agent_market.tests import dockit

        seen, bad = 0, []
        for cid, en in self.en.items():
            th = self.th[cid]
            if not th.get("computed"):
                continue
            self.assertNotIn("document", th["computed"], cid)   # one table, in the run's language, both ways
            self.assertNotIn("document", en["computed"], cid)
            if not th.get("render"):
                continue
            self.assertEqual(sorted(en["sheets"]), ["report.docx", "report.pptx", "report.xlsx"], cid)
            as_rows = lambda files: {name: [[t] for t in texts] for name, texts in files.items()}   # noqa: E731
            w = words.of("agent_market.packages.financial_report")
            bad += [f"{cid}: {x}" for x in dockit.same_but_words(as_rows(th["sheets"]), as_rows(en["sheets"]), w)]
            bad += [f"{cid}: Thai left — {x}" for x in dockit.thai_left(as_rows(en["sheets"]), self.own[cid])]
            self.assertFalse(en["assumptions"], cid)
            seen += 1
        self.assertEqual(bad, [], "\n" + "\n".join(bad[:60]))
        self.assertEqual(seen, len(RENDERED))

    def test_what_only_the_files_and_their_writer_read_is_the_documents_own(self):
        # the runner hands a model the lines, the ratios, the movements, the exceptions and the figures of the
        # table to write the Thai commentary from, and takes the rules' own commentary from it when no model
        # answers; the platform says the period itself when the label is its own
        # AM18: those words follow the run too. What the two runs share is everything but the words — the same
        # keys, figures, citations and codes — and an English run's label is English where the Thai run's is Thai.
        def shape(v):
            if isinstance(v, dict):
                return {k: shape(x) for k, x in v.items() if k not in ("label", "text", "title", "detail", "name", "labels", "summary",
                                                                       "movements", "watch", "next_steps", "period_label", "prior_label")}
            if isinstance(v, list):
                return [shape(x) for x in v]
            return v if not (isinstance(v, str) and re.search(r"[A-Za-z฀-๿]", v)) else "…"   # (a word, in either language)

        for cid, en in self.en.items():
            th = self.th[cid]
            if th.get("computed"):
                for k in OF_THE_DOCUMENTS:
                    self.assertEqual(shape(en["computed"][k]), shape(th["computed"][k]), (cid, k))
                for line_en, line_th in zip(en["computed"]["lines"], th["computed"]["lines"]):
                    self.assertEqual(line_en["key"], line_th["key"], cid)
                    self.assertTrue(THAI.search(line_th["label"]) and not THAI.search(line_en["label"]), (cid, line_en["label"]))
                self.assertNotEqual(en["summary"]["period_label"], th["summary"]["period_label"], cid)
                self.assertIsNone(THAI.search(en["summary"]["period_label"]), cid)
                tv_en, tv_th = pkg.template_values(en["computed"]), pkg.template_values(th["computed"])
                self.assertEqual([l["current"] for l in tv_en["lines"]], [l["current"] for l in tv_th["lines"]], cid)
                self.assertEqual((tv_en["revenue"], tv_en["net_profit"]), (tv_th["revenue"], tv_th["net_profit"]), cid)

    def test_the_set_asks_many_kinds_of_question_and_leaves_rows_out(self):
        codes = {f["code"] for r in self.en.values() for f in r["asked"]}
        self.assertGreaterEqual(codes, {"wrong_document_type", "missing_file", "unmapped_column", "ambiguous_debit_credit",
                                        "period_undetermined", "period_mixed", "currency_mixed", "account_name_missing",
                                        "year_convention_mixed", "unbalanced", "duplicate_suspect", "materiality_exceeded",
                                        "prior_period_mismatch"})
        left = {x["code"] for r in self.en.values() if r.get("computed") for x in r["computed"]["excluded"]}
        self.assertGreaterEqual(left, {"period_mixed", "duplicate_suspect"})

    def test_what_is_said_is_english_and_not_only_free_of_thai(self):
        card = lambda code: next(f for r in self.en.values() for f in r["asked"] if f["code"] == code)   # noqa: E731
        f = card("unbalanced")
        self.assertEqual(f["header"], "Unbalanced")
        self.assertRegex(f["question"], r"^Debits total [\d,]+\.\d\d but credits total [\d,]+\.\d\d \((debits|credits) are higher than "
                                        r"(credits|debits) by [\d,]+\.\d\d baht\)\. ")
        self.assertTrue(f["question"].endswith("What should I do?"))
        self.assertEqual([o["label"] for o in f["options"]], ["Go on and show the difference in the report", "Stop, I will send a corrected file"])
        self.assertEqual(f["evidence"]["columns"], ["Row", "Date", "Doc. no.", "Account", "Description", "Debit", "Credit"])
        f = card("period_mixed")
        self.assertEqual(f["header"], "Other months")
        self.assertRegex(f["question"], r"^The file also holds \d+ entries dated \d\d/\d\d–\d\d/\d\d \(October\), [\d,]+\.\d\d baht in total "
                                        r"\(debit side\)\. Should they be counted in September or left out\?$")
        self.assertEqual(f["options"][0]["label"], "Leave them out, use September only")
        f = card("period_undetermined")
        self.assertRegex(f["options"][0]["label"], r"^[A-Z][a-z]+ 20\d\d$")
        self.assertRegex(f["options"][0]["description"], r"^[\d,]+ entries$")
        f = card("ambiguous_debit_credit")
        self.assertEqual(f["header"], "Debit/credit")
        f = next(f for f in self.en["csv_cp874_signed_asks"]["asked"] if f["code"] == "ambiguous_debit_credit")
        self.assertIn("is a revenue account, which is normally recorded on the credit side, and in the file it is a", f["question"])
        self.assertEqual([o["label"] for o in f["options"]], ["Yes, positive = debit", "Positive = credit"])
        f = card("unmapped_column")
        self.assertRegex(f["question"], r"I guessed from the values in the columns: Date = “[^”]+”, Debit = “[^”]+”, Credit = “[^”]+”\. "
                                        r"Is that right\?$")
        f = card("missing_file")
        self.assertEqual((f["request"][0]["label"], f["request"][0]["why"]),
                         ("The general ledger (GL) for the month to be closed", "Every figure in the report is computed from this file"))
        self.assertEqual(card("wrong_document_type")["evidence"]["detected"], "Not an accounting file")
        for r in self.en.values():      # a card's header fits, and every line a person is given starts as a sentence does
            for f in r["asked"]:
                self.assertLessEqual(len(f["header"]), 12, f["header"])
                self.assertTrue(all(re.match(r"^[A-Z0-9“]", o["label"]) for o in f["options"]), [o["label"] for o in f["options"]])
        left = self.en["duplicate_excluded"]["computed"]["excluded"]
        self.assertRegex(left[0]["reason"], r"^Duplicate entries \(\d+ rows\)$")
        self.assertEqual(self.en["duplicate_excluded"]["summary"]["excluded"], [{"code": "duplicate_suspect", "reason": left[0]["reason"]}])
        self.assertTrue(self.th["duplicate_excluded"]["computed"]["excluded"][0]["reason"].startswith("รายการซ้ำ"))
        self.assertRegex(self.en["period_mixed_excluded"]["computed"]["excluded"][0]["reason"], r"^Dated outside the period \(\d+ entries\)$")
        checks = {k["name"]: k for a in self.en["clean_with_budget"]["render"]["artifacts"] for k in a["quality"]["checks"]}
        self.assertEqual(checks["docx.valid"]["label"], "The file opens and its structure is sound")
        self.assertRegex(checks["docx.figures"]["detail"], r"^Read the figures in the tables back from the file: \d+ values, every one as computed$")
        self.assertRegex(checks["docx.narrative"]["detail"], r"^\d+ figures in the commentary, each with the source of that very figure$")
        self.assertRegex(checks["docx.charts"]["detail"], r"^Drew the \d+ charts again from the computed figures; every picture in the file is the same$")
        self.assertRegex(checks["citations"]["detail"], r"^\d+ reference numbers printed in the report, every one with a source$")
        self.assertRegex(checks["assumptions"]["detail"], r"^\d+ items$")
        self.assertRegex(checks["pptx.slide_count"]["detail"], r"^\d+ slides$")
        self.assertEqual(checks["pptx.slide_count"]["label"], "8–24 slides")
        self.assertEqual(checks["pptx.overflow"]["detail"], "None (estimated from the type size, not rendered)")
        self.assertEqual(checks["xlsx.sheets"]["label"], "Every sheet is there")
        self.assertRegex(checks["xlsx.figures"]["detail"], r"^Read the figures back from the file: \d+ values, every one as computed$")
        self.assertEqual(checks["xlsx.print"]["label"][:1], checks["xlsx.print"]["label"][:1].upper())
        for a in self.en["clean_with_budget"]["render"]["artifacts"]:
            for k in a["quality"]["checks"]:
                self.assertIsNone(THAI.search(k["label"]), k)
                self.assertRegex(k["label"], r"^[A-Z0-9]", k)
        th = {k["name"]: k for a in self.th["clean_with_budget"]["render"]["artifacts"] for k in a["quality"]["checks"]}
        self.assertEqual(th["docx.valid"]["label"], "ไฟล์เปิดได้และโครงสร้างถูกต้อง")
        self.assertEqual(th["xlsx.sheets"]["label"], "ชีตครบ")

    def test_a_figure_is_asked_about_by_its_english_name_and_by_the_reports_own(self):
        en = {c["id"]: c for c in self.en["clean_with_budget"]["computed"]["citations"]}
        th = {c["id"]: c for c in self.th["clean_with_budget"]["computed"]["citations"]}
        labels = {c["label"] for c in en.values()}
        for want in ("Revenue from sales and services", "Revenue from sales and services, budget", "Net profit, variance from budget",
                     "Gross margin", "Total debit for the period", "Total credit for the period", "Number of entries used in the computation",
                     "Monthly trend", "Revenue, September 2026"):
            self.assertIn(want, labels)
        formulas = {c["formula"] for c in en.values()}
        for want in ("Sum of the accounts under “Cost of sales”", "Revenue from sales and services − cost of sales",
                     "This period − budget (per cent: ÷ |budget| × 100)", "Sum of the debit column over the period's entries"):
            self.assertIn(want, formulas)
        for cid, c in th.items():
            self.assertNotIn("aliases", c, cid)                       # a Thai run: the record as it always was
            self.assertEqual(en[cid]["aliases"], [c["label"]], cid)   # asked by the words the report prints
            self.assertIsNone(THAI.search(re.sub(r"“[^”]*”", "", en[cid]["formula"])), en[cid]["formula"])
        named = [c for c in en.values() if re.match(r"^\d{4}-\d\d ", c["label"])]
        self.assertTrue(named and all(re.search(r", (this period|budget|variance from budget)$", c["label"]) for c in named))

    def test_a_date_whose_order_nothing_decides_is_asked_about_in_english(self):
        from agent_market.tests.test_date_order import US_SEPT, _gl_csv

        def run(lang, **kw):
            words.use(lang)
            try:
                pf = dataset.load(json.loads(json.dumps(dataset.dump(pkg.parse(_gl_csv(US_SEPT), "gl_export.csv", {})))))
                return pkg.preflight([pf], message="ปิดงบเดือนกันยายน 2569", today=K.TODAY, **kw)
            finally:
                words.use("th")

        en, th = run("en")["findings"][0], run(None)["findings"][0]
        self.assertEqual((en["code"], en["header"], en["key"], en["default"]), ("date_order", "Date order", th["key"], th["default"]))
        self.assertRegex(en["question"], r"^The dates in the column “[^”]+” of the file “gl_export.csv” have a day and a month of 12 or less "
                                         r"in every row, such as “09/01/2026”")
        self.assertTrue(en["question"].endswith("Which way does this file write its dates?"))
        self.assertEqual([o["label"] for o in en["options"]], ["Month/day/year", "Day/month/year"])
        self.assertRegex(en["options"][0]["description"], r"^“09/0\d/2026” = \d+ Sep 2026 · Most entries fall in September 2026 \(60 of 60 entries\)$")
        self.assertEqual(th["header"], "ลำดับวันที่")
        self.assertTrue(th["question"].startswith("วันที่ในคอลัมน์ “") and th["question"].endswith("ไฟล์นี้เขียนวันที่แบบไหนครับ"))
        self.assertEqual([o["label"] for o in th["options"]], ["เดือน/วัน/ปี", "วัน/เดือน/ปี"])
        remembered = {"answers": {"date_order": "mdy"}}
        a_en, a_th = run("en", defaults=remembered)["assumptions"], run(None, defaults=remembered)["assumptions"]
        self.assertRegex(a_en[0]["text"], r"^Used the remembered answer: the dates in the column “[^”]+” are read as month/day/year$")
        self.assertTrue(a_th[0]["text"].startswith("ใช้ค่าที่จำไว้: อ่านวันที่ในคอลัมน์ “") and a_th[0]["text"].endswith("เป็นเดือน/วัน/ปี"))

    def test_the_names_of_the_fields_on_the_screen_of_columns_follow_the_run(self):
        from agent_market.engine.headers import FIELD_LABEL

        self.assertEqual((FIELD_LABEL["date"], FIELD_LABEL["debit"], FIELD_LABEL["cost_center"]), ("วันที่", "เดบิต", "ศูนย์ต้นทุน"))
        self.assertEqual(json.loads(json.dumps(dict(FIELD_LABEL), ensure_ascii=False))["budget"], "งบประมาณ")
        with words.speaking("en"):
            self.assertEqual(dict(FIELD_LABEL), {
                "date": "Date", "doc_no": "Document number", "account_code": "Account code", "account_name": "Account name",
                "description": "Description", "debit": "Debit", "credit": "Credit", "amount": "Amount", "dc_indicator": "Debit/credit",
                "currency": "Currency", "cost_center": "Cost centre", "budget": "Budget"})

    def test_why_a_field_of_a_form_was_not_filled_follows_the_run(self):
        from agent_market.deliverables import template_out as T

        self.assertEqual((T._why("no_value"), T._why(None), T._why("something_else")), ("รอบนี้ไม่มีค่าให้ช่องนี้", "เติมไม่ได้", "something_else"))
        with words.speaking("en"):
            self.assertEqual((T._why("no_value"), T._why(None), T._why("something_else")),
                             ("This round has no value for this field", "Cannot be filled", "something_else"))
            self.assertTrue(all(re.match(r"^[A-Z]", T._why(k)) and not THAI.search(T._why(k)) for k in T.WHY), dict(T.WHY))


if __name__ == "__main__":
    unittest.main()
