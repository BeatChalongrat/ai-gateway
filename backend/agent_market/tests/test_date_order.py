"""DD/MM or MM/DD, decided per date column (AM2 SAP).

The column's own values decide: a first number over 12 cannot be a month
(MM/DD impossible), a second number over 12 cannot be a month (DD/MM
impossible), a Buddhist-era year is Thai and day-first. Only when every date
is ambiguous do the headers break the tie (Thai headers → DD/MM; an English
date header in an SAP export → SAP's format for the separator). Anything
else is asked, never guessed.
"""

import datetime as dt
import json
import os
import tempfile
import unittest

from agent_market.engine import dataset
from agent_market.engine.headers import decide_date_order
from agent_market.engine.ledger import parse_file
from agent_market.engine.thai import parse_date
from agent_market.packages import financial_report as pkg

TODAY = dt.date(2026, 10, 3)
SAP_HEADERS = ["Company Code", "Posting Date", "Document Number", "Account", "Account Name", "Text",
               "Amount in Local Currency", "Debit/Credit Ind.", "Local Currency"]
EN_HEADERS = ["Date", "Account Code", "Account Name", "Description", "Debit", "Credit"]


class Rules(unittest.TestCase):
    def test_a_first_number_over_12_makes_mm_dd_impossible(self):
        do = decide_date_order(["13/09/2026", "05/09/2026", "01/09/2026"], "Date", EN_HEADERS)
        self.assertEqual((do.order, do.source), ("dmy", "values"))

    def test_a_second_number_over_12_makes_dd_mm_impossible(self):
        do = decide_date_order(["09/13/2026", "09/05/2026", "09/01/2026"], "Date", EN_HEADERS)
        self.assertEqual((do.order, do.source), ("mdy", "values"))

    def test_both_proven_is_a_conflict_not_a_choice(self):
        do = decide_date_order(["13/09/2026", "09/15/2026"], "Date", EN_HEADERS)
        self.assertEqual(do.source, "conflict")

    def test_a_buddhist_year_is_thai_and_day_first(self):
        do = decide_date_order(["05/09/2569", "01/09/2569"], "Date", EN_HEADERS)
        self.assertEqual((do.order, do.source), ("dmy", "values"))
        # …and contradicts a second number over 12 in the same column
        self.assertEqual(decide_date_order(["05/09/2569", "09/15/2026"], "Date", EN_HEADERS).source, "conflict")

    def test_values_win_over_the_sap_tie_breaker(self):
        do = decide_date_order(["13.09.2026", "05.09.2026"], "Posting Date", SAP_HEADERS)
        self.assertEqual((do.order, do.source), ("dmy", "values"))
        do = decide_date_order(["13/09/2026", "05/09/2026"], "Posting Date", SAP_HEADERS)
        self.assertEqual((do.order, do.source), ("dmy", "values"), "a day over 12 is a fact; SAP is only a hint")

    def test_ambiguous_dates_in_an_sap_export_take_sap_s_format(self):
        amb = ["09/05/2026", "09/01/2026", "09/12/2026"]
        self.assertEqual(decide_date_order(amb, "Posting Date", SAP_HEADERS).order, "mdy")
        self.assertEqual(decide_date_order([a.replace("/", "-") for a in amb], "Posting Date", SAP_HEADERS).order, "mdy")
        do = decide_date_order([a.replace("/", ".") for a in amb], "Posting Date", SAP_HEADERS)
        self.assertEqual((do.order, do.source), ("dmy", "sap"), "SAP writes DD.MM.YYYY with dots")
        # S/H values mark an SAP export as well as its headers; its file name does not (AM4)
        plain = ["Posting Date", "Account", "Amount", "D/C"]
        self.assertEqual(decide_date_order(amb, "Posting Date", plain).source, "undecided")
        self.assertEqual(decide_date_order(amb, "Posting Date", plain, ["S", "H"]).source, "sap")

    def test_ambiguous_dates_under_thai_headers_are_day_first(self):
        do = decide_date_order(["05/09/2026", "01/09/2026"], "วันที่", ["วันที่", "รหัสบัญชี", "เดบิต", "เครดิต"])
        self.assertEqual((do.order, do.source), ("dmy", "thai"))

    def test_ambiguous_dates_with_nothing_to_decide_stay_undecided(self):
        do = decide_date_order(["09/05/2026", "09/01/2026"], "Date", EN_HEADERS)
        self.assertEqual((do.order, do.source), (None, "undecided"))
        # an English date header alone is not an SAP export
        self.assertEqual(decide_date_order(["09/05/2026"], "Posting Date", ["Posting Date", "Account", "Amount"]).source,
                         "undecided")

    def test_real_date_cells_need_no_order(self):
        do = decide_date_order([dt.datetime(2026, 9, 5), 46270, "2026-09-05", "5 ก.ย. 2569"], "Date", EN_HEADERS)
        self.assertEqual(do.source, "none")

    def test_parse_date_in_either_order(self):
        self.assertEqual(parse_date("09/15/2026", "mdy"), dt.date(2026, 9, 15))
        self.assertIsNone(parse_date("09/15/2026"))
        self.assertEqual(parse_date("15/09/2569"), dt.date(2026, 9, 15))
        self.assertEqual(parse_date("2026-09-15", "mdy"), dt.date(2026, 9, 15), "year first has no order question")
        self.assertEqual(parse_date("5 ก.ย. 2569", "mdy"), dt.date(2026, 9, 5))


def _gl_csv(dates: list[str], headers=EN_HEADERS) -> str:
    """A small balanced English GL, one sale and one expense per date."""
    rows = []
    for d in dates:
        rows += [[d, "1131-01", "Trade Receivables", "Invoice", "1070.00", ""],
                 [d, "4100-01", "Sales Revenue", "Invoice", "", "1000.00"],
                 [d, "2154-00", "Output VAT", "Invoice", "", "70.00"],
                 [d, "5400-01", "Office Rent", "Rent", "300.00", ""],
                 [d, "2111-01", "Trade Payables", "Rent", "", "300.00"]]
    path = os.path.join(tempfile.mkdtemp(), "gl_export.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(",".join(f'"{c}"' for c in r) for r in [headers] + rows))
    return path


US_SEPT = [f"09/{d:02d}/2026" for d in range(1, 13)]   # MM/DD, every day ≤ 12


class UndecidedColumn(unittest.TestCase):
    """English headers, no SAP marker, every day ≤ 12: nothing decides, so it is asked."""

    def setUp(self):
        self.path = _gl_csv(US_SEPT)

    def _parsed(self):
        # through parsed.json, as the runner hands files between steps
        pf = pkg.parse(self.path, "gl_export.csv", {})
        return dataset.load(json.loads(json.dumps(dataset.dump(pf))))

    def test_it_is_asked_with_the_reading_that_fits_the_month_recommended(self):
        pf = self._parsed()
        self.assertEqual(pf.date_order["source"], "undecided")
        self.assertEqual(pf.unreadable_date_rows, 0)
        pre = pkg.preflight([pf], message="ปิดงบเดือนกันยายน 2569", today=TODAY)
        codes = [f["code"] for f in pre["findings"]]
        self.assertEqual(codes, ["date_order"], "asked alone: the months are not known until it is answered")
        f = pre["findings"][0]
        self.assertEqual(f["severity"], "blocking")
        self.assertEqual(f["key"], "date_order:gl_export.csv")
        self.assertEqual(f["default"], "mdy", "under MM/DD every row is in September")
        self.assertEqual({o["value"] for o in f["options"]}, {"dmy", "mdy"})
        self.assertEqual(f["evidence"]["months"]["mdy"], {"2026-09": 60})
        self.assertEqual(len(f["evidence"]["months"]["dmy"]), 12)

    def test_answered_month_first_every_row_is_in_september(self):
        pf = self._parsed()
        answers = {"date_order:gl_export.csv": "mdy"}
        pre = pkg.preflight([pf], message="ปิดงบเดือนกันยายน 2569", answers=answers, today=TODAY)
        self.assertEqual(pre["findings"], [])
        self.assertEqual(pre["period"], "2026-09")
        computed = pkg.compute([self._parsed()], pre["roles"], pre["period"], answers=answers)
        lines = {l["key"]: l["current"] for l in computed["lines"]}
        self.assertEqual((lines["revenue"], lines["operating_expenses"], lines["net_profit"]),
                         ("12000.00", "3600.00", "8400.00"))
        self.assertEqual(computed["source"]["primary"]["rows_used"], 60)

    def test_answered_day_first_the_same_rows_spread_over_the_year(self):
        pf = self._parsed()
        pre = pkg.preflight([pf], message="ปิดงบเดือนกันยายน 2569", answers={"date_order:gl_export.csv": "dmy"},
                            today=TODAY)
        self.assertIn("period_mixed", [f["code"] for f in pre["findings"]])

    def test_the_answer_is_applied_the_same_way_every_time(self):
        pf = self._parsed()
        for order, month in (("mdy", {9}), ("dmy", set(range(1, 13))), ("mdy", {9})):
            pkg.preflight([pf], message="ปิดงบเดือนกันยายน 2569", answers={"date_order:gl_export.csv": order},
                          today=TODAY)
            self.assertEqual({r.date.month for r in pf.rows}, month, order)

    def test_a_remembered_answer_is_used_and_said(self):
        pre = pkg.preflight([self._parsed()], message="ปิดงบเดือนกันยายน 2569",
                            defaults={"answers": {"date_order": "mdy"}}, today=TODAY)
        self.assertEqual(pre["findings"], [])
        self.assertIn(("date_order", "remembered"), [(a["code"], a["source"]) for a in pre["assumptions"]])


class ConflictingColumn(unittest.TestCase):
    def test_rows_that_fit_neither_reading_are_asked_about_not_guessed(self):
        # 13/09 is day-first, 09/15 month-first, 05/09/2026 could be either; 05/09/2569 is Thai.
        path = _gl_csv(["13/09/2026", "09/15/2026", "05/09/2026", "05/09/2569"])
        pf = parse_file(path, "gl_export.csv")
        self.assertEqual(pf.date_order["source"], "conflict")
        self.assertEqual({r.date for r in pf.rows}, {dt.date(2026, 9, 13), dt.date(2026, 9, 5)})
        self.assertEqual(pf.unreadable_date_rows, 10, "the 09/15 and the CE 05/09 rows: 2 dates × 5 rows")
        pre = pkg.preflight([pkg.parse(path, "gl_export.csv", {})], message="ปิดงบเดือนกันยายน 2569", today=TODAY)
        self.assertIn("unreadable_dates", [f["code"] for f in pre["findings"]])


class SapTieBreak(unittest.TestCase):
    def test_an_sap_marker_only_in_the_name_is_not_evidence_the_agent_asks(self):
        # AM4: until AM3 a file NAMED like an SAP export (generic English headers, every date ≤ 12)
        # was read month-first without a question; the same file renamed was asked about. A name
        # is a label the person typed — now both ask.
        path = _gl_csv(US_SEPT[:3], headers=["Posting Date", "G/L Account", "Account Name", "Text", "Debit", "Credit"])
        sap_path = os.path.join(os.path.dirname(path), "SAP_FBL3N_export.csv")
        os.replace(path, sap_path)
        pf = pkg.parse(sap_path, "SAP_FBL3N_export.csv", {})
        self.assertEqual((pf.date_order["order"], pf.date_order["source"]), (None, "undecided"))
        pre = pkg.preflight([pf], message="close September 2026", today=TODAY)
        self.assertIn("date_order", [f["code"] for f in pre["findings"]])


if __name__ == "__main__":
    unittest.main()
