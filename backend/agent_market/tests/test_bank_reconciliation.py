"""Agent 3 — bank reconciliation (AM5 phase E).

  * the eval set, every case through the real steps (tests/bankkit.py), its expected numbers from
    the scenario and plain addition in tools/make_bank_fixtures.py;
  * THE GATE: a reconciliation whose adjusted balances differ is never called a success — it says
    "ยังไม่ลงตัว ต่างกัน X บาท", why, and what to do next, and the working paper is marked a draft;
  * the same input twice gives the same computed.json, byte for byte — matching groups included —
    and a renamed file changes nothing but the name it is cited by;
  * the working paper of one month is the list of items carried into the next;
  * statements in the layouts Thai banks export, Buddhist-era dates, the two meanings of "debit";
  * fees, interest and the tax withheld on it, returned cheques, direct debits — by rule;
  * no model: nothing in the package can ask one.
"""

import datetime as dt
import json
import os
import re
import shutil
import tempfile
import unittest
from decimal import Decimal as D

from agent_market.engine.findings import FORBIDDEN_WORDS, display_len
from agent_market.packages import bank_reconciliation as pkg
from agent_market.packages.bank_reconciliation.checks import classify as C
from agent_market.packages.bank_reconciliation.checks import inputs as I
from agent_market.packages.bank_reconciliation.checks.review import signed
from agent_market.tests import bankkit as K
from agent_market.tests import samplekit as S

FIX = K.FIX
STATEMENTS = ("kbank_sep.xlsx", "scb_sep.xlsx", "bbl_sep.xlsx", "ktb_sep.csv", "signed_sep.csv")
_RESULTS: dict = {}


def result(cid: str) -> dict:
    if cid not in _RESULTS:
        case = next(c for c in K.load_cases()["cases"] if c["id"] == cid)
        _RESULTS[cid] = (case, K.run_case(case))
    return _RESULTS[cid]


class EvalSet(unittest.TestCase):
    def test_every_case(self):
        cases = K.load_cases()["cases"]
        self.assertGreaterEqual(len(cases), 20)
        for case in cases:
            c, res = result(case["id"])
            self.assertEqual(K.check(c, res), [], case["id"])

    def test_the_set_covers_what_the_card_asks_for(self):
        cases = {c["id"]: c for c in K.load_cases()["cases"]}
        not_tied = [c for c in cases.values() if c["expect"].get("success") is False]
        self.assertGreaterEqual(len(not_tied), 3)
        asked = {code for c in cases.values() for code in c["expect"].get("asked", [])}
        self.assertTrue({"e3_candidate", "e3_confirm_all", "e3_unmatched", "bankrec_side", "bankrec_carried", "missing_file"} <= asked)
        shapes = {s for c in cases.values() for s in (c["expect"].get("shapes") or {})}
        self.assertIn("N:1", shapes)


class TheGate(unittest.TestCase):
    """Adjusted bank balance = adjusted book balance, or the round is not a success."""

    def test_a_reconciliation_that_does_not_tie_refuses_to_be_a_success(self):
        for cid, amount in (("does_not_tie_statement_rows_missing", "21,435.00"), ("does_not_tie_opening_unexplained", "1,070.00"),
                            ("does_not_tie_recorded_twice", "8,560.00"), ("candidates_skipped_stay_suspect", "0.50"),
                            ("large_row_without_partner_skipped", "75,000.00")):
            _, res = result(cid)
            c = res["computed"]
            gate = c["gate"]
            self.assertFalse(gate["passed"], cid)
            self.assertEqual(gate["code"], "not_tied", cid)
            self.assertEqual(gate["what"], f"ยังไม่ลงตัว ต่างกัน {amount} บาท", cid)
            self.assertTrue(gate["reasons"] and gate["next"], cid)
            self.assertNotEqual(c["statement"]["bank"]["adjusted"], c["statement"]["book"]["adjusted"], cid)
            # what the runner reads, and what the thread shows
            self.assertEqual(res["summary"]["gate"], {k: gate.get(k) for k in ("passed", "code", "what", "next", "difference")}, cid)
            self.assertFalse(res["summary"]["tied"], cid)
            self.assertTrue(c["fallback_narrative"]["summary"].endswith(gate["what"]), cid)
            self.assertNotIn("ลงตัว ยอดหลังปรับปรุง", c["fallback_narrative"]["summary"], cid)
            # the working paper is delivered, and says on its face that it is a draft that does not tie
            face = res["sheets"]["งบกระทบยอด"]
            self.assertEqual(face[2][0], "ร่าง — ยังไม่ลงตัว ห้ามใช้เป็นงบกระทบยอดที่เสร็จแล้ว", cid)
            verdict = next(r[1] for r in face if r and r[0] == "ผลการกระทบยอด")
            self.assertEqual(verdict, gate["what"], cid)
            self.assertTrue(res["render"]["passed"], cid)
            self.assertTrue(res["render"]["artifacts"][0]["meta"]["draft"], cid)

    def test_nothing_is_forced_to_tie(self):
        # a row nobody has classified, a pair nobody has confirmed, a row that may be there twice: in neither adjusted balance
        _, res = result("large_row_without_partner_skipped")
        loose = [i for i in res["computed"]["items"] if i["type"] == "unclassified"]
        self.assertEqual([(i["side"], i["effect"], i["amount"]) for i in loose], [(None, None, "75000.00")])
        _, res = result("candidates_skipped_stay_suspect")
        in_items = {i["row_id"] for i in res["computed"]["items"]}
        for s in res["computed"]["match"]["groups"]["suspect"]:
            for r in s["left"] + s["right"]:
                self.assertNotIn(r["id"], in_items)

    def test_a_reconciliation_that_ties_says_so(self):
        _, res = result("clean_kbank_layout")
        c = res["computed"]
        self.assertEqual(c["gate"], {"passed": True, "code": "tied", "difference": "0.00",
                                     "what": "ลงตัว ยอดหลังปรับปรุงของธนาคารและของบัญชีเท่ากัน", "next": "", "reasons": []})
        self.assertEqual((c["statement"]["bank"]["adjusted"], c["statement"]["book"]["adjusted"]), ("1538519.65", "1538519.65"))
        face = res["sheets"]["งบกระทบยอด"]
        self.assertIsNone(face[2][0])
        self.assertEqual(next(r[1] for r in face if r and r[0] == "ผลการกระทบยอด"), "ลงตัว")
        self.assertFalse(res["render"]["artifacts"][0]["meta"]["draft"])


class SameInputTwice(unittest.TestCase):
    def test_byte_identical_computed_json(self):
        for cid in ("clean_kbank_layout", "candidates_confirmed_by_a_person", "items_carried_in_and_cleared",
                    "does_not_tie_statement_rows_missing", "three_cheques_one_deposit"):
            case, first = result(cid)
            again = K.run_case(case)
            self.assertEqual(first["computed_bytes"], again["computed_bytes"], cid)
            self.assertEqual(first["sheets"], again["sheets"], cid)
            self.assertEqual([f["key"] for f in first["asked"]], [f["key"] for f in again["asked"]], cid)

    def test_upload_order_changes_nothing(self):
        case, first = result("candidates_confirmed_by_a_person")
        turned = K.run_case({**case, "files": list(reversed(case["files"]))})
        self.assertEqual(first["computed"]["match"]["groups"], turned["computed"]["match"]["groups"])
        self.assertEqual(first["computed"]["statement"], turned["computed"]["statement"])

    def test_a_renamed_file_is_the_same_file(self):
        case, named = result("candidates_confirmed_by_a_person")
        cases = K.load_cases()
        paths = [(os.path.join(FIX, n), {"kbank_sep_candidates.xlsx": "aaa.xlsx", "ledger_sep_candidates.xlsx": "bbb.xlsx"}[n])
                 for n in case["files"]]
        answers = {k.replace("kbank_sep_candidates.xlsx", "aaa.xlsx").replace("ledger_sep_candidates.xlsx", "bbb.xlsx"): v
                   for k, v in case["answers"].items()}
        renamed = K.run_files(paths, defaults=cases["defaults"], answers=answers, today=cases["today"])
        # a row is known by its file's content: the same ids, the same pairs, the same answers
        self.assertEqual([f["key"] for f in named["asked"]], [f["key"] for f in renamed["asked"]])
        back = json.loads(json.dumps(renamed["computed"], ensure_ascii=False)
                          .replace("aaa.xlsx", "kbank_sep_candidates.xlsx").replace("bbb.xlsx", "ledger_sep_candidates.xlsx"))
        self.assertEqual(back, named["computed"])


class OneMonthIntoTheNext(unittest.TestCase):
    def test_the_working_paper_of_september_is_octobers_list_of_items_carried_in(self):
        keep = tempfile.mkdtemp(prefix="am_bank_keep_")
        try:
            os.environ["BANKKIT_KEEP"] = keep
            sep = K.run_case(next(c for c in K.load_cases()["cases"] if c["id"] == "clean_kbank_layout"))
        finally:
            os.environ.pop("BANKKIT_KEEP", None)
        try:
            paper = os.path.join(keep, "bank_reconciliation.xlsx")
            out = [i for i in sep["computed"]["items"] if i["id"] in sep["computed"]["carried_out"]]
            self.assertEqual(sorted(i["amount"] for i in out), ["-32100.00", "-9630.00", "40660.00"])
            u = I.parse(paper, "งบกระทบยอด_2569-09.xlsx", {})
            self.assertEqual(u.kind, "carried")
            self.assertEqual([(r["ref"], r["amount"], r["kind"]) for r in u.data["rows"]],
                             [("10001205", "9630.00", "outstanding_cheque"), ("10001206", "32100.00", "outstanding_cheque"),
                              ("RV6909-005", "40660.00", "deposit_in_transit")])
            cases = K.load_cases()
            oct_ = next(c for c in cases["cases"] if c["id"] == "items_carried_in_and_cleared")
            res = K.run_files([(os.path.join(FIX, "kbank_oct.xlsx"), "kbank_oct.xlsx"), (os.path.join(FIX, "ledger_oct.xlsx"), "ledger_oct.xlsx"),
                               (paper, "งบกระทบยอด_2569-09.xlsx")], defaults=cases["defaults"], answers={"e3_candidate": "match"},
                              today=cases["today"])
            c = res["computed"]
            self.assertTrue(c["gate"]["passed"], c["gate"])
            self.assertEqual(c["statement"]["bank"]["adjusted"], oct_["expect"]["adjusted_bank"])
            self.assertEqual([(x["ref"], x["cleared"]) for x in c["carried_in"]],
                             [("10001205", True), ("10001206", False), ("RV6909-005", True)])
            self.assertEqual(c["opening_gap"], "0.00")
        finally:
            shutil.rmtree(keep, ignore_errors=True)


class Layouts(unittest.TestCase):
    def test_every_statement_layout_reads_as_the_same_month(self):
        seen = set()
        for name in STATEMENTS:
            u = I.parse(os.path.join(FIX, name), name, {})
            self.assertEqual((u.kind, u.side), ("table", "bank"), name)
            rows = u.data["rows"]
            self.assertEqual(len(rows), 14, name)
            self.assertEqual({r["date"][:7] for r in rows}, {"2026-09"}, name)          # 2569, 69 and date cells alike
            amounts = sorted(f"{signed(r, u.data['layout'], 'bank'):.2f}" for r in rows)
            seen.add(tuple(amounts))
            self.assertEqual(sum(D(a) for a in amounts), D("39589.65"), name)
            for r in rows:
                self.assertTrue(r["cells"].get("date") and r["row"], name)
        self.assertEqual(len(seen), 1)

    def test_cheque_numbers_are_read_from_a_column_or_from_the_text(self):
        for name in ("kbank_sep.xlsx", "scb_sep.xlsx"):
            u = I.parse(os.path.join(FIX, name), name, {})
            self.assertEqual(sorted(r["cheque"] for r in u.data["rows"] if r["cheque"]),
                             ["10001201", "10001202", "10001203", "10001204"], name)

    def test_the_account_is_read_from_the_heading(self):
        m = I.parse(os.path.join(FIX, "kbank_sep.xlsx"), "kbank_sep.xlsx", {}).data["meta"]
        self.assertEqual((m["bank"], m["account_number"], m["account_name"]),
                         ("ธนาคารกสิกรไทย จำกัด (มหาชน)", "123-4-56789-0", "บจก. สยามตัวอย่างการค้า"))
        self.assertEqual(m["period"], ["2026-09-01", "2026-09-30"])

    def test_buddhist_era_dates(self):
        for text, want in (("01/09/2569", "2026-09-01"), ("01-09-69", "2026-09-01"), ("1 ก.ย. 2569", "2026-09-01"),
                           ("30/09/2026", "2026-09-30"), ("2026-09-30", "2026-09-30"), (dt.datetime(2026, 9, 30), "2026-09-30"),
                           ("๓๐/๐๙/๒๕๖๙", "2026-09-30"), ("ไม่ใช่วันที่", None), ("", None), (None, None)):
            self.assertEqual(I.date_of(text), want, text)

    def test_money_cells(self):
        for cell, want in ((1250, "1250.00"), (1250.5, "1250.50"), ("1,250.50", "1250.50"), ("(1,250.50)", "-1250.50"),
                           ("1,250.50-", "-1250.50"), ("-35.00", "-35.00"), ("๑,๒๕๐.๕๐ บาท", "1250.50"), ("", None), ("-", None),
                           ("ประมาณ 500", None), (None, None), (True, None)):
            self.assertEqual(I.money(cell), want, cell)

    def test_debit_means_opposite_things_on_the_two_sides(self):
        row = {"a": "45000.00", "b": None}                      # 45,000.00 in the debit column
        self.assertEqual(signed(row, "debit_credit", "bank"), D("-45000.00"))   # the bank took it out
        self.assertEqual(signed(row, "debit_credit", "book"), D("45000.00"))    # the company put it in
        self.assertEqual(signed({"a": "45000.00", "b": None}, "out_in", "bank"), D("-45000.00"))
        self.assertEqual(signed({"a": "45000.00", "b": None}, "out_in", "book"), D("-45000.00"))

    def test_a_file_that_does_not_say_whose_it_is_is_not_guessed(self):
        u = I.parse(os.path.join(FIX, "bare_sep.xlsx"), "bare_sep.xlsx", {})
        self.assertEqual((u.kind, u.side), ("table", None))
        self.assertEqual(I.parse(os.path.join(FIX, "ledger_sep.xlsx"), "ledger_sep.xlsx", {}).side, "book")
        self.assertEqual(I.parse(os.path.join(FIX, "gl_sep.xlsx"), "gl_sep.xlsx", {}).side, "book")
        pdf = I.parse(os.path.join(FIX, "statement_sep.pdf"), "statement_sep.pdf", {})
        self.assertEqual((pdf.kind, pdf.data["reason"]), ("other", "unsupported_type"))


class ByRule(unittest.TestCase):
    def test_what_a_row_of_the_statement_is(self):
        for text, amount, want in (
                ("ค่าธรรมเนียมโอนเงิน", "-35.00", "bank_fee"), ("ค่ารักษาบัญชี", "-200.00", "bank_fee"), ("SERVICE CHARGE", "-50.00", "bank_fee"),
                ("ดอกเบี้ยรับ", "1250.00", "interest"), ("INTEREST", "88.10", "interest"),
                ("ภาษีหัก ณ ที่จ่าย", "-12.50", "wht_interest"), ("W/T", "-0.88", "wht_interest"),
                ("เช็คคืน 10009911", "-15000.00", "returned_cheque"), ("RETURNED CHEQUE", "-15000.00", "returned_cheque"),
                ("หักบัญชีอัตโนมัติ ค่าไฟฟ้า", "-4820.15", "direct_debit"), ("DIRECT DEBIT", "-990.00", "direct_debit"),
                ("เงินโอนเข้า", "75000.00", "unrecorded_receipt"), ("โอนเงินออก", "-6420.00", "unrecorded_payment"),
                # a fee that came back, interest that went out: not what the word says
                ("ค่าธรรมเนียม", "35.00", "unrecorded_receipt"), ("ดอกเบี้ย", "-500.00", "unrecorded_payment")):
            self.assertEqual(C.of_bank_row(text, D(amount)), want, text)

    def test_a_tax_line_is_the_tax_on_interest_only_beside_interest(self):
        self.assertTrue(C.is_tax_on("ภาษี", D("-12.50"), D("1250.00")))
        self.assertTrue(C.is_tax_on("TAX", D("-187.50"), D("1250.00")))            # 15%
        self.assertFalse(C.is_tax_on("ชำระภาษี ภ.พ.30", D("-20000.00"), D("1250.00")))   # a tax payment of another kind
        self.assertFalse(C.is_tax_on("ภาษี", D("-12.50"), D("0.00")))

    def test_what_a_row_of_the_ledger_is(self):
        self.assertEqual(C.of_book_row("10001205", D("-9630.00")), "outstanding_cheque")
        self.assertEqual(C.of_book_row("", D("-9630.00")), "outstanding_payment")
        self.assertEqual(C.of_book_row("", D("40660.00")), "deposit_in_transit")
        self.assertEqual(C.of_book_row("", D("-1.00"), "outstanding_cheque"), "outstanding_cheque")

    def test_interest_and_the_tax_withheld_on_it_are_one_drafted_entry(self):
        _, res = result("clean_kbank_layout")
        j = next(x for x in res["computed"]["journal"] if "ดอกเบี้ย" in x["memo"])
        self.assertEqual([(l["account"], l["debit"], l["credit"]) for l in j["lines"]],
                         [("เงินฝากธนาคาร ธนาคารกสิกรไทย 123-4-56789-0", "1237.50", "0.00"),
                          ("ภาษีเงินได้ถูกหัก ณ ที่จ่าย", "12.50", "0.00"), ("ดอกเบี้ยรับ", "0.00", "1250.00")])
        self.assertIn("1.00%", j["note"])
        self.assertTrue(all(x["draft"] for x in res["computed"]["journal"]))

    def test_ageing_and_stale_cheques(self):
        _, res = result("stale_cheque_carried_in")
        c = res["computed"]
        self.assertEqual([(b["bucket"], b["count"]) for b in c["ageing"]["buckets"]], [("0-30", 3), ("31-60", 0), ("61+", 1)])
        stale = [i for i in c["items"] if i["id"] in c["ageing"]["stale"]]
        self.assertEqual([(i["ref"], i["age_days"], i["carried"]) for i in stale], [("10000977", 230, True)])
        self.assertEqual(C.bucket(30), "0-30")
        self.assertEqual(C.bucket(31), "31-60")
        self.assertEqual(C.bucket(61), "61+")

    def test_cut_off(self):
        _, res = result("recorded_after_the_cut_off")
        c = res["computed"]
        self.assertEqual([(r["side"], r["date"], r["ref"]) for r in c["cutoff"]["rows"]], [("book", "2026-10-02", "10001204")])
        matched = {r["id"] for m in c["match"]["groups"]["matched"] for r in m["left"] + m["right"]}
        self.assertFalse(any(r["date"] > "2026-09-30" for m in c["match"]["groups"]["matched"] for r in m["left"] + m["right"]))
        self.assertTrue(matched)
        ex = [e for e in c["run_exceptions"] if e["reason_code"] == "cut_off"]
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]["severity"], "review")


class Cards(unittest.TestCase):
    ENGLISH = re.compile(r"[A-Za-z]{2,}")

    def test_every_card_of_the_set_is_one_a_person_can_answer(self):
        n = 0
        for case in K.load_cases()["cases"]:
            _, res = result(case["id"])
            for f in res["asked"]:
                n += 1
                self.assertLessEqual(display_len(f["header"]), 12, f["code"])
                self.assertEqual(f["severity"], "blocking", f["code"])
                low = (f["question"] + " " + " ".join(o["label"] for o in f["options"])).lower()
                for w in FORBIDDEN_WORDS:
                    self.assertNotIn(w, low, f["key"])
                # what the agent itself wrote is Thai: file names and the text a file holds are the person's own
                text = re.sub(r"“[^”]*”", " ", f["question"])
                text = re.sub(r"\S+\.(?:xlsx|csv|pdf)\b", " ", text)
                self.assertIsNone(self.ENGLISH.search(text), (f["key"], text))
                for o in f["options"]:
                    self.assertIsNone(self.ENGLISH.search(o["label"] + " " + o["description"]), (f["key"], o))
                self.assertTrue(f["evidence"], f["key"])
                self.assertFalse(f.get("rememberable"), f["key"])
                if f["code"].startswith("e3_"):
                    self.assertIn(f["default"], ("undecided", "review"), f["key"])
        self.assertGreaterEqual(n, 12)

    def test_a_pair_is_never_recommended_unless_it_is_sure(self):
        _, res = result("candidates_confirmed_by_a_person")
        by = {f["facts"]["difference"]: f for f in res["asked"]}
        self.assertEqual(by["0.00"]["recommended"], "match")
        self.assertIsNone(by["-0.50"]["recommended"])


class SelfTest(unittest.TestCase):
    def test_the_sample_month_asks_nothing_and_ties(self):
        st = S.self_test("bank_reconciliation")
        res = S.render_sample("bank_reconciliation", defaults=S.self_test_defaults("bank_reconciliation"))
        self.assertEqual(res["first_findings"], [])
        self.assertTrue(res["render"]["passed"])
        t = res["computed"]["totals"]
        self.assertEqual({k: t[k] for k in st["expect"]["totals"]}, st["expect"]["totals"])
        self.assertEqual(res["period"], st["expect"]["period"])

    def test_the_manifest(self):
        m = S.manifest("bank_reconciliation")
        self.assertEqual((m["id"], m["python_package"], m["narrative"]), ("sran.bank-reconciliation", "bank_reconciliation", "rules"))
        self.assertTrue(m["completion_gate"])
        self.assertEqual(m["review"], {"separate_reviewer": True})
        self.assertIn("document.match.e3", m["capabilities"]["required"])
        self.assertEqual(pkg.VERSION, m["version"])
        # tolerances are install settings with defaults — the engine's own
        from agent_market.engine.e3.match import Tolerance
        from agent_market.packages.bank_reconciliation.checks.review import matching_settings
        from agent_market.engine.e3.match import tolerance_from

        t, bad = tolerance_from(matching_settings(m["install"]["defaults"]))
        self.assertEqual((t, bad), (Tolerance(), []))
        self.assertEqual(m["permissions"]["default"], "read_only")
        self.assertIn("accounting_write", m["permissions"]["denied"])


class NoModel(unittest.TestCase):
    def test_nothing_in_the_package_can_ask_a_model(self):
        m = S.manifest("bank_reconciliation")
        caps = m["capabilities"]["required"] + m["capabilities"]["optional"]
        self.assertEqual([c for c in caps if c.startswith("model.")], [])
        self.assertEqual(m["guide"]["model_inputs"], {})
        self.assertFalse(hasattr(pkg, "parse_extras"))
        base = os.path.dirname(os.path.abspath(pkg.__file__))
        for root, _, names in os.walk(base):
            for n in names:
                if n.endswith(".py"):
                    with open(os.path.join(root, n), encoding="utf-8") as f:
                        src = re.sub(r'"""[\s\S]*?"""', "", f.read())
                    for banned in (r"\brandom\b", r"\.now\(", r"\.today\(", r"\brequests\b", r"\bhttpx\b", r"\burllib\b",
                                   r"\bopenai\b", r"\bfloat\(", r"proposals"):
                        if n == "workbook.py" and "float" in banned:
                            continue    # a cell of the file holds a number; every sum is made before, in Decimal
                        self.assertIsNone(re.search(banned, src), (n, banned))
        _, res = result("clean_kbank_layout")
        self.assertEqual(res["computed"]["models"], [])
        self.assertEqual(res["summary"]["models"], [])


if __name__ == "__main__":
    unittest.main()
