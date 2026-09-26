"""SAP exports (AM2 SAP): the S/H indicator next to one amount column, and a
6-digit chart of accounts that is not the Thai standard."""

import datetime as dt
import os
import tempfile
import unittest
from decimal import Decimal

from openpyxl import Workbook

from agent_market.engine.ledger import LedgerRow, ParsedFile, indicator_guess
from agent_market.packages import financial_report as pkg
from agent_market.packages.financial_report.scripts.rules import chart_for, group_of, name_group

TODAY = dt.date(2026, 10, 3)
MSG = "close September 2026"
HEAD = ["Company Code", "Posting Date", "Document Number", "Account", "Account Name", "Text", "Cost Center",
        "Amount in Local Currency", "Debit/Credit Ind.", "Local Currency"]
# (date, document, account, name, amount, S/H)
BOOK = [
    ("09/15/2026", "5100000001", "140000", "Trade Receivables", 107000, "S"),
    ("09/15/2026", "5100000001", "400000", "Sales Revenue - Goods", 100000, "H"),
    ("09/15/2026", "5100000001", "215000", "Output VAT", 7000, "H"),
    ("09/16/2026", "5100000002", "500000", "Cost of Goods Sold", 60000, "S"),
    ("09/16/2026", "5100000002", "150000", "Finished Goods", 60000, "H"),
    ("09/20/2026", "5100000003", "630000", "Office Rent", 20000, "S"),
    ("09/20/2026", "5100000003", "210000", "Trade Payables", 20000, "H"),
    ("09/30/2026", "5100000004", "110100", "Bank - KBank Current", 500, "S"),
    ("09/30/2026", "5100000004", "480000", "Interest Income", 500, "H"),
    ("09/30/2026", "5100000005", "700000", "Interest Expense", 1500, "S"),
    ("09/30/2026", "5100000005", "110100", "Bank - KBank Current", 1500, "H"),
]
GOLD = {"revenue": "100000.00", "cost_of_sales": "60000.00", "operating_expenses": "20000.00",
        "other_income": "500.00", "finance_costs": "1500.00", "net_profit": "19000.00"}
SH = {"S": "debit", "H": "credit"}


def write_fbl3n(book, signed=False, name="FBL3N.xlsx") -> str:
    wb = Workbook()
    ws = wb.active
    ws.append(HEAD)
    for date, doc, acct, acct_name, amt, ind in book:
        value = -amt if (signed and ind == "H") else amt
        ws.append(["TH01", date, doc, acct, acct_name, "posting", "HQ01", float(value), ind, "THB"])
    path = os.path.join(tempfile.mkdtemp(), name)
    wb.save(path)
    return path


def run(path, answers=None, name="FBL3N.xlsx"):
    pf = pkg.parse(path, name, {})
    answers = dict(answers or {})
    pre = pkg.preflight([pf], message=MSG, answers=answers, today=TODAY)
    computed = pkg.compute([pf], pre["roles"], pre["period"], answers=answers)
    return pf, pre, computed, {l["key"]: l["current"] for l in computed["lines"]}


class Indicator(unittest.TestCase):
    def test_debit_credit_ind_is_the_indicator_next_to_one_amount(self):
        pf = pkg.parse(write_fbl3n(BOOK), "FBL3N.xlsx", {})
        self.assertEqual(pf.dc_mode, "indicator")
        self.assertEqual(pf.header.header_of("dc_indicator"), "Debit/Credit Ind.")
        self.assertEqual(pf.header.header_of("amount"), "Amount in Local Currency")
        self.assertEqual(sorted(pf.indicator_values), ["H", "S"])

    def test_the_s_h_reading_is_confirmed_and_revenue_comes_out_positive(self):
        _, pre, _, _ = run(write_fbl3n(BOOK))
        f = next(f for f in pre["findings"] if f["code"] == "ambiguous_debit_credit")
        self.assertEqual(f["default"], SH, "S = Soll = debit")
        self.assertIn("100,000.00", f["options"][0]["description"], "the default puts revenue on the income side")

    def test_signed_credits_with_h_are_not_negated_twice(self):
        _, _, c_plain, plain = run(write_fbl3n(BOOK), {"ambiguous_debit_credit": SH})
        _, _, c_signed, signed = run(write_fbl3n(BOOK, signed=True), {"ambiguous_debit_credit": SH})
        self.assertEqual(plain, signed)
        for k, v in GOLD.items():
            self.assertEqual(signed[k], v, k)
        self.assertEqual(c_signed["balance"]["diff"], "0.00")
        self.assertEqual(c_signed["balance"]["debit"], c_plain["balance"]["debit"])

    def test_indicator_words(self):
        self.assertEqual(indicator_guess(["Soll", "Haben"]), {"Soll": "debit", "Haben": "credit"})
        self.assertEqual(indicator_guess(["Dr.", "Cr."]), {"Dr.": "debit", "Cr.": "credit"})
        self.assertEqual(indicator_guess(["เดบิต", "เครดิต"]), {"เดบิต": "debit", "เครดิต": "credit"})
        self.assertEqual(indicator_guess(["S", "H"]), SH)


def ledger(accounts, name="gl.xlsx") -> ParsedFile:
    pf = ParsedFile(name=name, sha256="", size=0, kind="xlsx", doc_type="gl")
    for i, (code, acct_name) in enumerate(accounts, start=2):
        pf.rows.append(LedgerRow(file=name, sheet="S", row=i, date=dt.date(2026, 9, 1), account_code=code,
                                 account_name=acct_name, description="", debit=Decimal("1.00"),
                                 credit=Decimal("0.00")))
        if acct_name:
            pf.account_names[code] = acct_name
    return pf


class Chart(unittest.TestCase):
    def test_a_6_digit_sap_chart_by_first_digit_and_name(self):
        chart = chart_for([ledger([(a, n) for _, _, a, n, _, _ in BOOK] + [
            ("400100", "Service Revenue - Installation"), ("500100", "Cost of Installation Services"),
            ("610000", "Salaries and Wages"), ("610100", "Overtime"), ("690000", "Bank Charges"),
            ("650000", "Depreciation"), ("179000", "Acc. Depreciation - Office Equip.")])])
        self.assertEqual(chart.style, "extended")
        want = {"400000": "revenue", "400100": "revenue", "480000": "other_income", "500000": "cost_of_sales",
                "500100": "cost_of_sales", "610000": "operating_expenses", "610100": "operating_expenses",
                "630000": "operating_expenses", "650000": "operating_expenses", "690000": "operating_expenses",
                "700000": "finance_costs", "140000": None, "179000": None, "215000": None, "110100": None}
        self.assertEqual({c: group_of(c, chart) for c in want}, want)
        self.assertEqual(chart.pending, [])

    def test_sap_zero_padded_account_numbers(self):
        chart = chart_for([ledger([("0000400000", "Sales"), ("0000610000", "Salaries"), ("0000110100", "Bank")])])
        self.assertEqual([group_of(c, chart) for c in ("0000400000", "0000610000", "0000110100")],
                         ["revenue", "operating_expenses", None])

    def test_thai_charts_are_read_by_number_as_before(self):
        for codes in (["4100-01", "4200-01", "5100-01", "5300-01", "5700-03", "5800-01", "1112-01"],
                      ["410001", "420001", "510001", "530001", "570003", "580001", "111201"]):
            chart = chart_for([ledger([(c, "") for c in codes])])
            self.assertEqual(chart.style, "th", codes)
            self.assertEqual([group_of(c, chart) for c in codes],
                             ["revenue", "other_income", "cost_of_sales", "operating_expenses",
                              "operating_expenses", "finance_costs", None])
        # English names never move a Thai-standard account (Xero-style export)
        chart = chart_for([ledger([("4200-01", "Sales"), ("5800-01", "Salaries")])])
        self.assertEqual([group_of("4200-01", chart), group_of("5800-01", chart)], ["other_income", "finance_costs"])

    def test_names(self):
        self.assertEqual(name_group("Interest Expense"), ("expense", "finance_costs"))
        self.assertEqual(name_group("Cost of Goods Sold"), ("expense", "cost_of_sales"))
        self.assertEqual(name_group("Sales Commission"), ("expense", None))
        self.assertEqual(name_group("Interest Income"), ("income", "other_income"))
        self.assertEqual(name_group("Sales Revenue - Goods"), ("income", "revenue"))
        self.assertEqual(name_group("Rental Income"), ("income", None))
        self.assertEqual(name_group("ดอกเบี้ยจ่าย"), ("expense", "finance_costs"))
        self.assertEqual(name_group("Overtime"), ("expense", None))
        self.assertEqual(name_group("Suspense Account"), (None, None))

    def test_what_neither_number_nor_name_decides_is_pending(self):
        chart = chart_for([ledger([("400000", "Sales"), ("610000", "Salaries"), ("750000", ""),
                                   ("760000", "Exchange Rate Differences"), ("400500", "Raw Material Consumption"),
                                   ("780000", "Rental Income")])])
        self.assertEqual(chart.style, "extended")
        self.assertEqual(sorted(p["code"] for p in chart.pending), ["400500", "750000", "760000"])
        self.assertEqual(group_of("780000", chart), "other_income", "income outside the 4-range is other income")
        answered = chart_for([ledger([("750000", "")])], {"account_group:750000": "finance_costs"})
        self.assertEqual(group_of("750000", answered), "finance_costs")
        self.assertEqual(answered.pending, [])

    def test_a_stray_account_in_a_thai_chart_is_asked_about(self):
        chart = chart_for([ledger([("4100-01", "รายได้จากการขาย"), ("5200-01", "เงินเดือน"),
                                   ("6100-01", "ค่าที่ปรึกษา"), ("6200-01", "ดอกเบี้ยจ่ายเงินกู้")])])
        self.assertEqual(chart.style, "mixed")
        self.assertEqual([p["code"] for p in chart.pending], ["6100-01"])
        self.assertEqual(group_of("5200-01", chart), "operating_expenses")
        self.assertEqual(group_of("6200-01", chart), "finance_costs", "a specific name places it")


class PendingAccountEndToEnd(unittest.TestCase):
    BOOK_7 = BOOK + [("09/30/2026", "5100000006", "750000", "", 800, "S"),
                     ("09/30/2026", "5100000006", "110100", "Bank - KBank Current", 800, "H")]

    def test_it_is_asked_and_the_answer_places_it(self):
        path = write_fbl3n(self.BOOK_7)
        pf = pkg.parse(path, "FBL3N.xlsx", {})
        pre = pkg.preflight([pf], message=MSG, today=TODAY)
        f = next(f for f in pre["findings"] if f["code"] == "account_group")
        self.assertEqual(f["key"], "account_group:750000")
        self.assertEqual(f["severity"], "blocking")
        self.assertIn("not_pl", [o["value"] for o in f["options"]])
        self.assertEqual(f["evidence"]["amount"], "800.00")
        _, pre2, _, lines = run(path, {"ambiguous_debit_credit": SH, "account_group:750000": "finance_costs"})
        self.assertEqual(pre2["findings"], [])
        self.assertEqual(lines["finance_costs"], "2300.00")
        self.assertEqual(lines["net_profit"], "18200.00")

    def test_a_name_the_card_validator_would_reject_stays_in_the_evidence(self):
        book = BOOK + [("09/30/2026", "5100000007", "760000", "Shipping Charges", 300, "S"),
                       ("09/30/2026", "5100000007", "110100", "Bank - KBank Current", 300, "H")]
        pre = pkg.preflight([pkg.parse(write_fbl3n(book), "FBL3N.xlsx", {})], message=MSG, today=TODAY)
        f = next(f for f in pre["findings"] if f["code"] == "account_group")
        self.assertNotIn("Shipping", f["question"], "'Shipping' holds 'pin'")
        self.assertEqual(f["evidence"]["name"], "Shipping Charges")

    def test_unanswered_it_is_left_out_with_a_word_never_silently(self):
        _, _, computed, lines = run(write_fbl3n(self.BOOK_7), {"ambiguous_debit_credit": SH})
        self.assertEqual(lines["net_profit"], GOLD["net_profit"])
        self.assertEqual([e["code"] for e in computed["excluded"]], ["account_group"])
        self.assertIn("750000", computed["excluded"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
