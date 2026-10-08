"""The expense audit says what it says in the language of the run (AM14).

Its eval set — the golden month with its cards skipped and answered, a policy document that is never a
limit, a weekend rule that is an exception, a receipt claimed before, a receipt not sent, a calendar that
is off — is run in English beside the same set in Thai (test_am14_words.EnglishRuns): a person who reads
only English meets no Thai but their own data and the workbook's own words, the run is the same run, and
the workbook is cell for cell the one a Thai run writes.

Two cards no case of the set raises — no policy table, no claims file — are said here directly.
"""

import os
import re
import unittest

from agent_market.engine import words
from agent_market.tests import expensekit as K
from agent_market.tests.test_am14_words import THAI, THAI_RUN, EnglishRuns, strings_of, thai_only


class AnExpenseRunInEnglish(EnglishRuns, unittest.TestCase):
    package, kit = "expense_audit", K

    @classmethod
    def files_of(cls, case):
        return [(p, os.path.basename(p)) for p in K.files_for(case)]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the policy table and the holiday calendar are the company's own words too (a category's name, a holiday's)
        for c in cls.cases:
            cls.own[c["id"]] += thai_only("\n".join(strings_of(K.defaults_for(c))))

    def test_the_set_asks_for_judgement_and_hands_exceptions_to_a_person(self):
        codes = {f["code"] for r in self.en.values() for f in r["asked"].values()}
        self.assertLessEqual({"expense_weekend_holiday", "expense_vague_purpose"}, codes)
        reasons = {e["reason_code"] for r in self.en.values() for e in r["computed"]["run_exceptions"]}
        self.assertGreaterEqual(len(reasons), 10, reasons)

    def test_what_is_said_is_english_and_not_only_free_of_thai(self):
        res = self.en["golden_month_cards_skipped"]
        cards = list(res["asked"].values())
        weekend = next(f for f in cards if f["code"] == "expense_weekend_holiday")
        self.assertEqual(weekend["header"], "Holiday")
        self.assertRegex(weekend["question"], r" claims .+, [\d,]+\.\d\d baht, at .+, dated \d+ [A-Z][a-z]{2} 20\d\d "
                                              r"\((Saturday|Sunday|the holiday ).*\)\. Is it the company's work\? ")
        self.assertEqual([o["label"] for o in weekend["options"]],
                         ["It is company work, accept", "Ask the employee", "Send to the approver to decide"])
        purpose = next(f for f in cards if f["code"] == "expense_vague_purpose")
        self.assertEqual(purpose["header"], "Purpose")
        self.assertRegex(purpose["question"], r"which does not yet say the business purpose\. Is it acceptable\?")
        self.assertRegex(purpose["input"]["hint"], r"^Or type the purpose")
        comp = res["computed"]
        self.assertRegex(comp["fallback_narrative"]["summary"],
                         r"^Checked \d+ expense claims, [\d,]+\.\d\d baht claimed in all: \d+ passed, \d+ are exceptions awaiting "
                         r"a person's decision, and \d+ need judgement\. No claim was rejected automatically")
        self.assertEqual(comp["period_label"], "October 2026")
        by_code = {e["reason_code"]: e for e in comp["run_exceptions"]}
        self.assertEqual(by_code["over_claim_limit"]["reason"], "Over the limit per claim")
        self.assertRegex(by_code["over_claim_limit"]["detail"], r"^[\d,]+\.\d\d baht claimed, over the limit per claim for ")
        self.assertRegex(by_code["over_claim_limit"]["proposed_action"], r"^Decide whether to approve going over the limit per claim")
        self.assertEqual([c["label"] for c in by_code["over_claim_limit"]["choices"]],
                         ["Approve as an exception", "Reimburse up to the limit", "Ask the employee", "Do not reimburse this claim"])
        for e in comp["run_exceptions"]:
            self.assertRegex(e["reason"], r"^[A-Z]", e["reason_code"])
            self.assertTrue(all(re.match(r"^[A-Z]", c["label"]) for c in e["choices"]), e["reason_code"])
        self.assertEqual(set(comp["reason_codes"].values()) & {e["reason"] for e in comp["run_exceptions"]},
                         {e["reason"] for e in comp["run_exceptions"]})
        labels = [k["label"] for a in res["render"]["artifacts"] for k in a["quality"]["checks"]]
        self.assertIn("Every claim has its outcome", labels)
        self.assertIn("The limits on the policy sheet are the table's; the policy document is context only", labels)
        self.assertTrue(all(re.match(r"^[A-Z]", x) for x in labels), labels)
        figures = next(k for a in res["render"]["artifacts"] for k in a["quality"]["checks"] if k["name"] == "xlsx.figures")
        self.assertRegex(figures["detail"], r"^Read the figures back from the file: \d+ values, every one as computed$")
        cites = {c["id"]: c for c in comp["citations"]}
        self.assertEqual(cites["e1"]["label"], "Total claimed (baht)")
        self.assertEqual(cites["e1"]["aliases"][:3], ["total claimed", "amount claimed", "claimed in all"])
        thai = {c["id"]: c for c in self.th["golden_month_cards_skipped"]["computed"]["citations"]}
        self.assertEqual(cites["e1"]["aliases"][3:], [thai["e1"]["label"]] + thai["e1"]["aliases"])   # … and the workbook's own names
        # a claim's weekday and the outcome a source line names are the run's words; the workbook keeps its own
        self.assertIn(comp["claims"][0]["weekday"], ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))
        self.assertTrue(THAI.search(comp["document"]["claims"][0]["outcome_label"]))

    def test_an_answer_given_on_a_card_is_said_in_english_where_the_run_records_it(self):
        res = self.en["golden_month_cards_answered"]
        info = [e for e in res["computed"]["run_exceptions"] if e["severity"] == "info" and e["source_ref"].get("answered")]
        self.assertTrue(info)
        for e in info:
            self.assertRegex(e["proposed_action"], r"^The user (confirmed|gave) ", e["reason_code"])

    def test_the_two_cards_that_stop_a_run_before_it_starts(self):
        from agent_market.packages.expense_audit.checks import cards as C
        from agent_market.packages.expense_audit.checks import policy as P

        _, problems = P.load_policy({"policy": {"categories": {"meals": {"per_claim": "about 2,000"}}}})
        thai = C.no_policy(problems)
        self.assertTrue(THAI.search(thai.question) and THAI.search(thai.header))
        with words.speaking("en"):
            _, problems = P.load_policy({"policy": {"categories": {"meals": {"per_claim": "about 2,000"}}}})
            card = C.no_policy(problems)
            self.assertEqual(card.header, "Claim policy")
            self.assertIn("category meals · limit per claim: 'about 2,000' is not an amount of money", card.question)
            self.assertIn("amount above which a receipt is required: not set yet", card.question)
            self.assertNotIn("per_claim", card.question)
            self.assertEqual([o.label for o in card.options], ["It is set, continue", "Cancel this run"])
            self.assertIsNone(THAI.search(card.question + card.header + "".join(o.label + o.description for o in card.options)))
            _, none = P.load_policy({})
            self.assertEqual(none, ["No expense policy table (policy) has been set in the agent"])
            missing = C.missing_claims(["notes.xlsx"])
            self.assertEqual(missing.header, "Missing file")
            self.assertEqual(missing.question, "There is no expense claims file yet (Excel or CSV) (the file notes.xlsx has no column "
                                               "headings of expense claims). I need this file before I can start checking")
            self.assertEqual(C.missing_claims([]).question,
                             "There is no expense claims file yet (Excel or CSV). I need this file before I can start checking")
            self.assertEqual(missing.request[0]["label"], "Expense claims file")
            cal = P.calendar_for({"holidays": {"kind": "company", "confirmed": True, "confirmed_by": "A", "confirmed_at": "2026-09-01T00:00:00",
                                               "dates": {"2026-10-16": ""}}})
            self.assertEqual((cal.note, cal.holiday(__import__("datetime").date(2026, 10, 16))),
                             ("the company's holiday calendar confirmed by A on 2026-09-01", "holiday"))
            self.assertEqual(P.calendar_for({}).note, "no company holiday calendar has been set")
        self.assertTrue(THAI.search(P.calendar_for({}).note))


class EveryRuleSaysItInEnglish(unittest.TestCase):
    """The rules the eval set does not make fail — a row that cannot be read, a category the table does not have,
    a receipt whose amount differs or is not confirmed, tax withheld at another rate, a purpose a category
    requires — each said in English: its fact, and what the person who decides is told and offered."""

    def test_no_rule_speaks_thai_in_an_english_run(self):
        from decimal import Decimal

        from agent_market.packages.expense_audit.checks import policy as P
        from agent_market.packages.expense_audit.checks import rules as R
        from agent_market.packages.expense_audit.scripts import audit as A
        from agent_market.tests.test_expense_audit import _claim, _company, _policy

        claims = [
            R.Claim.from_row({"id": "C01", "row": 2, "date": None, "date_raw": "not a date", "category": "ค่าอาหาร", "merchant": "ร้าน",
                              "amount": None, "amount_raw": "abc"}, sheet="claims.xlsx", sheet_name="Sheet1"),
            _claim("C02", "E1", "2026-10-14", "หมวดที่ไม่มี", "ร้าน", "300.00"),
            _claim("C03", "E1", "2026-10-15", "วัสดุสำนักงาน", "ร้านเครื่องเขียน", "2500.00", receipt="r1.pdf", vat=True),
            _claim("C04", "E1", "2026-10-15", "วัสดุสำนักงาน", "ร้านเครื่องเขียน", "700.00", receipt="r2.pdf"),
            _claim("C05", "E1", "2026-10-16", "ค่าจ้างบุคคลภายนอก", "นาย ก", "5000.00", wht="100.00"),
            _claim("C06", "E2", "2026-10-16", "ค่ารับรองลูกค้า", "ร้านอาหาร", "900.00", desc=""),
            _claim("C07", "E2", "2026-10-16", "ค่าจ้างบุคคลภายนอก", "นาย ข", "5000.00"),
            _claim("C08", "E3", "2026-10-19", "ค่าอาหาร", "ร้านผับริมทาง", "2000.00", receipt="r3.pdf"),
            _claim("C09", "E3", "2026-10-19", "ค่าอาหาร", "ร้านผับริมทาง", "2000.00", receipt="r3.pdf"),
            _claim("C10", "E4", "2026-10-17", "ค่าโทรศัพท์มือถือ", "ค่ายมือถือ", "1200.00"),
        ]
        receipts = [
            R.ReceiptView(key="d1", names=["r1.pdf"], sha256="aa" * 32, status="read", total=Decimal("2400.00"), currency="THB",
                          document_type="abbreviated_tax_invoice", seller_name="ร้านเครื่องเขียน", seller_tax_id=None),
            R.ReceiptView(key="d2", names=["r2.pdf"], sha256="bb" * 32, status="unconfirmed", total=None, currency="THB",
                          document_type="receipt", seller_name=None, seller_tax_id=None,
                          unconfirmed_reason=""),
            R.ReceiptView(key="d3", names=["r3.pdf"], sha256="cc" * 32, status="read", total=Decimal("2000.00"), currency="USD",
                          document_type="receipt", seller_name="ผับริมทาง", seller_tax_id=None),
        ]
        own = thai_only("".join(strings_of([c.__dict__ for c in claims])) + "".join(strings_of([r.__dict__ for r in receipts])))
        with words.speaking("en"):
            policy, cal = _policy(), P.calendar_for({"holidays": _company()})
            own += thai_only("".join(strings_of(policy.table)) + "".join(str(v) for v in cal.dates.values()))
            audits = R.audit(claims, policy=policy, calendar=cal, receipts=receipts)
            said, failed = [], set()
            for a in audits:
                said += [f.detail for f in a.facts.values()]
                for code in a.codes + a.flags + a.also_flagged:
                    f = next(x for x in a.facts.values() if x.code == code and x.status == "fail")
                    row = A._row(code, "review", a, f, [])
                    failed.add(code)
                    said += [row["reason"], row["detail"], row["proposed_action"]] + [c["label"] for c in row["choices"]]
                    self.assertRegex(row["proposed_action"], r"^[A-Z]", code)
                for flag in a.flags + a.also_flagged:
                    for res in ("accepted", "typed", "ask_employee"):
                        said.append(A._proposed(flag, a, a.facts[A._fact_of(flag)], res, "meeting a client"))
            said += [A._same_expense({}), A._same_expense({"earlier_run": {"name": "r9.pdf"}}),
                     A._same_expense({"duplicate_of": "C08", "earlier_run": {"name": "r9.pdf"}})]
            self.assertEqual(A._same_expense({"duplicate_of": "C08"}),
                             "Check whether it is the same expense as claim C08; if it is a duplicate, pay once")
        self.assertLessEqual({"claim_row_unreadable", "unknown_category", "receipt_amount_mismatch", "receipt_unconfirmed", "wht_mismatch",
                              "wht_missing", "missing_purpose", "duplicate_claim", "prohibited_merchant", "over_period_limit",
                              "vat_claim_without_full_tax_invoice", "over_claim_limit"}, failed)
        left = []
        for text in said:
            for run in THAI_RUN.findall(text or ""):
                if thai_only(run) not in own:
                    left.append(f"“{run}” in: {text[:150]}")
        self.assertEqual(left, [], "\n" + "\n".join(sorted(set(left))[:30]))
        # and in Thai the same rules say what they said
        thai = {a.claim.id: a for a in R.audit(claims, policy=_policy(), calendar=P.calendar_for({"holidays": _company()}), receipts=receipts)}
        self.assertTrue(THAI.search(thai["C05"].facts["wht"].detail) and THAI.search(thai["C01"].facts["claim_data"].detail))


if __name__ == "__main__":
    unittest.main()
