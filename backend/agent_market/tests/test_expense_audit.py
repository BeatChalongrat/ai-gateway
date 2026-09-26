"""AM2 Phase F — Agent 4 Expense Audit.

The eval set (packages/expense_audit/evals/cases.json, run by expensekit through engine/step.py
exactly as the runner runs it, checked against fixtures/expense/expected.json) and the units under
it: the policy table as the only source of a limit, the deterministic facts, the line between a
rule (a fact → an exception for a person) and judgement (a clarification card), the answers, the
step hooks and the self-test.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal

from agent_market.packages import expense_audit as EA
from agent_market.packages.expense_audit.checks import cards as C
from agent_market.packages.expense_audit.checks import policy as P
from agent_market.packages.expense_audit.checks import rules as R

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures", "expense")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def _policy(**patch):
    pol = _load("policy.json")
    for k, v in patch.items():
        pol[k] = v
    p, problems = P.load_policy({"policy": pol})
    assert p is not None, problems
    return p


def _claim(cid, emp, date, cat, merchant, amount, *, receipt=None, vat=False, wht=None, desc="ค่าใช้จ่ายในงานโครงการลูกค้า"):
    return R.Claim.from_row({"id": cid, "row": int(cid[1:]) + 1, "employee_id": emp, "employee_name": emp, "date": date,
                             "date_raw": date, "category": cat, "merchant": merchant, "amount": amount, "amount_raw": amount,
                             "receipt": receipt, "vat_claim": vat, "wht_deducted": wht, "description": desc},
                            sheet="claims.xlsx", sheet_name="Sheet1")


class Policy(unittest.TestCase):
    def test_the_stored_table_is_the_only_source_of_a_limit(self):
        p = _policy()
        self.assertEqual(p.categories["meals"].per_claim, Decimal("1500.00"))
        self.assertEqual(p.categories["phone"].per_claim, None)
        self.assertEqual(p.source, "install")
        self.assertEqual(P.load_policy({})[0], None)                      # no table: no audit, never a guessed limit
        self.assertTrue(P.load_policy({"policy": {"categories": {"meals": {"per_claim": "ประมาณ 2,000"}}}})[1])

    def test_policy_sheet_reads_into_the_same_structure(self):
        got = EA.policy_from_xlsx(os.path.join(FIX, "policy.xlsx"))
        want = _load("policy.json")
        for code, c in want["categories"].items():
            g = got["policy"]["categories"][code]
            self.assertEqual(g["label"], c["label"], code)
            for k in ("per_claim", "per_person_month", "wht_rate"):
                self.assertEqual(g.get(k) and Decimal(g[k]), c.get(k) and Decimal(c[k]), f"{code}.{k}")
            self.assertEqual(bool(g.get("needs_purpose")), bool(c.get("needs_purpose")), code)
        self.assertEqual(got["policy"]["prohibited_merchants"], want["prohibited_merchants"])
        self.assertEqual(got["holidays"], _load("holidays.json"))
        # what the sheet does not say is listed for the owner, never invented
        self.assertEqual(sorted(got["missing"]), ["approval_threshold", "categories.contractor.wht_from", "receipt_required_above"])
        self.assertTrue(P.load_policy({"policy": got["policy"]})[1])       # incomplete until the owner fills it

    def test_calendar(self):
        pkg = P.calendar_for({})
        self.assertEqual(pkg.source, "package")
        self.assertEqual(pkg.holiday(_dt.date(2026, 10, 13)), "วันนวมินทรมหาราช")
        self.assertEqual(pkg.holiday(_dt.date(2026, 12, 7)), "ชดเชยวันพ่อแห่งชาติ")
        own = P.calendar_for({"holidays": {"2026-10-16": "วันหยุดบริษัท"}})
        self.assertEqual((own.source, own.holiday(_dt.date(2026, 10, 16)), own.holiday(_dt.date(2026, 10, 13))),
                         ("install", "วันหยุดบริษัท", None))

    def test_the_kb_document_is_context_only(self):
        p = _policy()
        doc = EA.parse(os.path.join(FIX, "policy_kb.docx"), "policy_kb.docx", {})
        self.assertEqual(doc.kind, "policy_document")
        context, conflicts = P.kb_context(p, [doc])
        meals = [c for c in context if "meals" in c["categories"]]
        self.assertEqual(len(meals), 1)
        self.assertEqual((meals[0]["numbers"], meals[0]["used_as_limit"]), (["2000.00"], False))
        self.assertEqual([(c["category"], c["table"], c["document"]) for c in conflicts], [("meals", "1500.00", "2000.00")])
        self.assertEqual(p.categories["meals"].per_claim, Decimal("1500.00"))  # reading it changed nothing


class Rules(unittest.TestCase):
    def setUp(self):
        self.p = _policy()
        self.cal = P.calendar_for({"holidays": _load("holidays.json")})

    def audit(self, claims, **kw):
        return {a.claim.id: a for a in R.audit(claims, policy=self.p, calendar=self.cal, **kw)}

    def test_vague_purpose_is_a_trigger_for_a_person(self):
        self.assertEqual(R.vague_purpose("ค่าอาหาร", "ค่าอาหาร"), "restates_category")
        self.assertEqual(R.vague_purpose(" อาหาร ", "ค่าอาหาร"), "restates_category")
        self.assertEqual(R.vague_purpose("รับรองลูกค้า", "ค่ารับรองลูกค้า"), "restates_category")
        self.assertEqual(R.vague_purpose("", "ค่าอาหาร"), "empty")
        self.assertEqual(R.vague_purpose("ค่าใช้จ่าย", "ค่าอาหาร"), "generic")
        self.assertIsNone(R.vague_purpose("ค่าอาหารกลางวันประชุมทีมขาย", "ค่าอาหาร"))
        self.assertIsNone(R.vague_purpose("ค่าที่พักเชียงใหม่", "ค่าที่พัก"))

    def test_split_purchase_leaves_duplicates_out(self):
        a = self.audit([_claim("C01", "E1", "2026-10-08", "ค่าที่พัก", "โรงแรม ก", "2300.00", receipt="r1.pdf"),
                        _claim("C02", "E1", "2026-10-08", "ค่าที่พัก", "โรงแรม ก", "2300.00", receipt="r1.pdf"),
                        _claim("C03", "E2", "2026-10-19", "วัสดุสำนักงาน", "ร้าน ข", "2000.00"),
                        _claim("C04", "E2", "2026-10-19", "วัสดุสำนักงาน", "ร้าน  ข", "1900.00"),
                        _claim("C05", "E2", "2026-10-19", "วัสดุสำนักงาน", "ร้าน ค", "2900.00")])
        self.assertEqual(a["C01"].facts["split_purchase"].status, "pass")      # a duplicate is the same purchase
        self.assertEqual((a["C02"].facts["duplicate"].status, a["C02"].facts["duplicate"].evidence["duplicate_of"]),
                         ("fail", "C01"))
        for cid in ("C03", "C04"):
            self.assertEqual(a[cid].facts["split_purchase"].status, "fail")
            self.assertEqual(a[cid].facts["split_purchase"].evidence["group"], ["C03", "C04"])
            self.assertEqual(a[cid].facts["split_purchase"].evidence["sum"], "3900.00")
        self.assertEqual(a["C05"].facts["split_purchase"].status, "pass")

    def test_period_limit_counts_claimed_amounts(self):
        a = self.audit([_claim("C01", "E1", "2026-10-01", "ค่าอาหาร", "ร้าน 1", "850.00"),
                        _claim("C02", "E1", "2026-10-05", "ค่าอาหาร", "ร้าน 2", "1980.00"),
                        _claim("C03", "E1", "2026-10-20", "ค่าอาหาร", "ร้าน 3", "1200.00"),
                        _claim("C04", "E1", "2026-10-27", "ค่าอาหาร", "ร้าน 4", "1400.00"),
                        _claim("C05", "E2", "2026-10-27", "ค่าอาหาร", "ร้าน 4", "1400.00")])
        self.assertEqual(a["C02"].facts["within_claim_limit"].status, "fail")
        self.assertEqual([a[c].facts["within_period_limit"].status for c in ("C01", "C02", "C03", "C04", "C05")],
                         ["pass", "pass", "pass", "fail", "pass"])
        ev = a["C04"].facts["within_period_limit"].evidence
        self.assertEqual((ev["before"], ev["after"], ev["limit"], ev["counted"]), ("4030.00", "5430.00", "5000.00",
                                                                                  ["C01", "C02", "C03", "C04"]))

    def test_withholding_tax_from_the_table(self):
        a = self.audit([_claim("C01", "E1", "2026-10-16", "ค่าจ้างบุคคลภายนอก", "นาย ก", "5000.00", wht="0.00"),
                        _claim("C02", "E1", "2026-10-16", "ค่าจ้างบุคคลภายนอก", "นาย ข", "5000.00", wht="150.00"),
                        _claim("C03", "E1", "2026-10-16", "ค่าจ้างบุคคลภายนอก", "นาย ค", "5000.00", wht="100.00"),
                        _claim("C04", "E1", "2026-10-16", "ค่าจ้างบุคคลภายนอก", "นาย ง", "900.00")])
        self.assertEqual((a["C01"].facts["wht"].status, a["C01"].facts["wht"].code, a["C01"].facts["wht"].evidence["due"]),
                         ("fail", "wht_missing", "150.00"))
        self.assertEqual(a["C02"].facts["wht"].status, "pass")
        self.assertEqual(a["C03"].facts["wht"].code, "wht_mismatch")
        self.assertEqual(a["C04"].facts["wht"].status, "skip")

    def test_prohibited_merchant_is_read_on_the_receipt_too(self):
        rv = R.ReceiptView(key="dR", names=["r9.pdf"], sha256="ab" * 32, status="read", total=Decimal("900.00"),
                           currency="THB", document_type="receipt", seller_name="ผับริมน้ำ", seller_tax_id=None)
        a = self.audit([_claim("C01", "E1", "2026-10-14", "ค่าอาหาร", "ร้านอาหารริมน้ำ", "900.00", receipt="r9.pdf")],
                       receipts=[rv])
        f = a["C01"].facts["prohibited_merchant"]
        self.assertEqual((f.status, f.evidence["word"], f.evidence["where"]), ("fail", "ผับ", ["receipt_seller"]))

    def test_receipt_total_and_vat_document(self):
        rv = R.ReceiptView(key="dR", names=["r1.pdf"], sha256="cd" * 32, status="read", total=Decimal("2400.00"),
                           currency="THB", document_type="abbreviated_tax_invoice", seller_name="ร้าน", seller_tax_id=None)
        a = self.audit([_claim("C01", "E1", "2026-10-15", "วัสดุสำนักงาน", "ร้าน", "2500.00", receipt="r1.pdf", vat=True)],
                       receipts=[rv])
        self.assertEqual((a["C01"].facts["receipt_amount"].code, a["C01"].facts["receipt_amount"].evidence["difference"]),
                         ("receipt_amount_mismatch", "100.00"))
        self.assertEqual(a["C01"].facts["vat_document"].code, "vat_claim_without_full_tax_invoice")
        self.assertEqual(a["C01"].outcome, "exception")

    def test_rules_never_reject(self):
        self.assertEqual(R.OUTCOMES, ("pass", "exception", "question"))
        a = self.audit([_claim("C01", "E1", "2026-10-10", "ค่าอาหาร", "ร้าน", "450.00")])
        self.assertEqual((a["C01"].outcome, a["C01"].flags), ("question", ["weekend_or_holiday"]))


class Cards(unittest.TestCase):
    def setUp(self):
        self.p = _policy()
        self.cal = P.calendar_for({"holidays": _load("holidays.json")})

    def test_judgement_is_a_card_per_claim_and_a_skipped_card_goes_to_a_person(self):
        audits = R.audit([_claim("C07", "E4", "2026-10-10", "ค่าอาหาร", "ร้านกาแฟ", "450.00"),
                          _claim("C14", "E1", "2026-10-20", "ค่าอาหาร", "ร้านอาหาร", "480.00", desc="ค่าอาหาร"),
                          _claim("C09", "E1", "2026-10-14", "ค่ารับรองลูกค้า", "ร้านคาราโอเกะ", "480.00", desc="รับรองลูกค้า")],
                         policy=self.p, calendar=self.cal)
        fs = C.cards_for(audits, {})
        self.assertEqual(sorted(f.key for f in fs), ["expense_vague_purpose:C14", "expense_weekend_holiday:C07"])
        for f in fs:
            self.assertEqual(f.default, "review")
            self.assertEqual(sorted(o.value for o in f.options), ["accept", "ask_employee", "review"])
        # a claim already going to a person as an exception is not asked about: the flag rides with it
        c09 = next(a for a in audits if a.claim.id == "C09")
        self.assertEqual((c09.outcome, c09.codes, c09.flags, c09.also_flagged), ("exception", ["prohibited_merchant"], [],
                                                                               ["vague_purpose"]))
        self.assertEqual(C.cards_for(audits, {"expense_weekend_holiday:C07": "review", "expense_vague_purpose:C14": "review"}), [])

    def test_answers(self):
        self.assertEqual(C.resolution("accept"), ("accepted", None))
        self.assertEqual(C.resolution("ask_employee"), ("ask_employee", None))
        self.assertEqual(C.resolution({"typed": "  เลี้ยงลูกค้าบริษัท ก.  "}), ("typed", "เลี้ยงลูกค้าบริษัท ก."))
        for v in (None, "review", "whatever", {"typed": "  "}, 3):
            self.assertEqual(C.resolution(v), ("review", None), v)   # never a pass by default

    def test_free_text_never_breaks_a_card(self):
        audits = R.audit([_claim("C01", "E1", "2026-10-11", "ค่าอาหาร", "Shopping Center token PIN", "450.00", desc="ค่าอาหาร")],
                         policy=self.p, calendar=self.cal)
        fs = C.cards_for(audits, {})                              # a Sunday, and a description that says nothing
        self.assertEqual(sorted(f.code for f in fs), ["expense_vague_purpose", "expense_weekend_holiday"])
        for f in fs:
            self.assertIn("Center", f.question)                   # the merchant is named; the words a card may not say are not


class Reading(unittest.TestCase):
    def test_a_csv_claims_sheet_in_english(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "claims.csv")
            with open(p, "w", encoding="utf-8-sig") as f:
                f.write("Claim ID,Employee ID,Employee,Date,Category,Merchant,Amount,Receipt,Description\n"
                        "X1,E9,Somchai,15/10/2569,meals,Cafe A,\"1,250.50\",a.pdf,lunch with client A\n"
                        "X2,E9,Somchai,2026-10-16,ค่าแท็กซี่/เดินทางในเมือง,Taxi,300,,to client B\n")
            up = EA.parse(p, "claims.csv", {})
            self.assertEqual(up.kind, "claims")
            rows = up.data["rows"]
            self.assertEqual([(r["id"], r["date"], r["amount"], r["receipt"]) for r in rows],
                             [("X1", "2026-10-15", "1250.50", "a.pdf"), ("X2", "2026-10-16", "300.00", None)])
            pol = _policy()
            self.assertEqual([pol.category_of(r["category"]) for r in rows], ["meals", "taxi"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_policy_sheet_in_a_run_is_not_a_policy(self):
        up = EA.parse(os.path.join(FIX, "policy.xlsx"), "policy.xlsx", {})
        self.assertEqual((up.kind, up.data["reason"]), ("other", "policy_sheet"))


class EvalSet(unittest.TestCase):
    """Every case of the package's own eval set, checked against the golden expected.json."""

    def test_every_case(self):
        from agent_market.tests import expensekit as K

        failures = {}
        for case in K.load_cases()["cases"]:
            errs = K.check(case, K.run_case(case))
            if errs:
                failures[case["id"]] = errs
        self.assertEqual(failures, {})


class SelfTest(unittest.TestCase):
    def test_bundled_sample_asks_nothing(self):
        from agent_market.tests import expensekit as K

        pkg = os.path.dirname(EA.__file__)
        with open(os.path.join(pkg, "self-test.json"), encoding="utf-8") as f:
            st = json.load(f)
        with open(os.path.join(pkg, "agent.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        res = K.run_case({"id": "self_test", "expect": {}}, defaults=dict(manifest["install"]["defaults"]),
                         files=[os.path.join(pkg, p) for p in st["files"]])
        comp = res["computed"]
        self.assertEqual(res["first"]["findings"], st["expect"]["findings"])
        self.assertEqual(comp["period"], st["expect"]["period"])
        self.assertEqual({k: comp["totals"][k] for k in st["expect"]["totals"]}, st["expect"]["totals"])
        self.assertTrue(res["render"]["passed"])
        self.assertEqual(comp["policy"]["source"], "package_sample")      # said out loud, in the output
        self.assertIn("package_sample_policy", [a["code"] for a in res["first"]["assumptions"]])


class Hooks(unittest.TestCase):
    def test_step_hooks(self):
        from agent_market.engine import step

        for fn in (EA.preflight, EA.compute):
            for k in ("history", "proposals", "today"):
                self.assertTrue(step._accepts(fn, k), (fn.__name__, k))
        with open(os.path.join(os.path.dirname(EA.__file__), "agent.json"), encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual((m["id"], m["version"], m["python_package"], m["narrative"]),
                         ("sran.expense-audit", "1.0.0", "expense_audit", "rules"))
        self.assertEqual(m["install"]["questions"], ["company", "policy", "theme", "formats"])
        self.assertEqual(sorted(m["capabilities"]["required"]),
                         sorted(["document.extract.e2", "compute.python.decimal", "deliverable.table.xlsx", "spreadsheet.read.xlsx"]))
        self.assertEqual(m["capabilities"]["optional"], ["model.vision"])
        for d in ("email_send", "accounting_write", "payment", "schedule"):
            self.assertIn(d, m["permissions"]["denied"])

    def test_no_policy_is_asked_not_guessed(self):
        pre = EA.preflight([], defaults={}, answers={}, today=_dt.date(2026, 11, 2))
        self.assertIn("expense_no_policy", [f["code"] for f in pre["findings"]])
        with self.assertRaises(ValueError):
            EA.compute([], {}, "2026-10", defaults={}, answers={})


if __name__ == "__main__":
    unittest.main()
