"""AM1b fixtures (evals/nasty, made by tools/make_nasty_fixtures.py): the
export mess a real Thai close brings. Expected numbers come from the
generator's journal (expected.json), never from the parser."""

import datetime as dt
import json
import os
import unittest

from agent_market.packages import financial_report as pkg

NASTY = os.path.join(os.path.dirname(pkg.__file__), "evals", "nasty")
EXP = json.load(open(os.path.join(NASTY, "expected.json"), encoding="utf-8"))
TODAY = dt.date(2026, 10, 3)


def close(files, message):
    """Preflight → compute → postcompute → recompute, every question answered
    with its default (a hurried person clicking the recommended option)."""
    parsed = [pkg.parse(os.path.join(NASTY, f), f, {}) for f in files]
    answers, asked = {}, []
    for _ in range(4):
        pre = pkg.preflight(parsed, message=message, answers=answers, today=TODAY)
        new = [f for f in pre["findings"] if f["key"] not in answers]
        for f in new:
            asked.append(f["code"])
            answers[f["key"]] = f["default"]
        if not new:
            break
    computed = pkg.compute(parsed, pre["roles"], pre["period"], answers=answers)
    post = pkg.postcompute(parsed, pre["roles"], pre["period"], computed, answers=answers)
    for f in post["findings"]:
        asked.append(f["code"])
        answers[f["key"]] = f["default"]
    if post["findings"]:
        computed = pkg.compute(parsed, pre["roles"], pre["period"], answers=answers)
    return parsed, asked, {l["key"]: l["current"] for l in computed["lines"]}


class NastyThaiExport(unittest.TestCase):
    def test_cp874_csv_with_everything_wrong_closes_to_the_right_numbers(self):
        parsed, asked, pl = close(["GL_Express_2569-09.csv"], "ปิดงบเดือนกันยายน 2569")
        exp = EXP["GL_Express_2569-09.csv"]
        self.assertEqual(parsed[0].encoding, "cp874")
        self.assertEqual(parsed[0].data_rows, exp["data_rows"], "every posting read: 2-row header, 7 date formats, page breaks")
        self.assertEqual(sorted(asked), ["duplicate_suspect", "period_mixed", "unbalanced"])
        for k, v in exp["pl_correct"].items():
            self.assertEqual(pl[k], v, k)

    def test_merged_two_row_header_prior_month(self):
        parsed, asked, pl = close(["GL_Express_2569-08.xlsx"], "ปิดงบเดือนสิงหาคม 2569")
        self.assertEqual(parsed[0].data_rows, EXP["GL_Express_2569-08.xlsx"]["data_rows"])
        self.assertEqual(asked, [])
        for k, v in EXP["GL_Express_2569-08.xlsx"]["pl"].items():
            self.assertEqual(pl[k], v, k)

    def test_clean_export_asks_nothing(self):
        parsed, asked, pl = close(["GL_clean_2569-09.xlsx"], "ปิดงบเดือนกันยายน 2569")
        self.assertEqual(asked, [], "a clean export must not produce a single question")
        for k, v in EXP["GL_clean_2569-09.xlsx"]["pl"].items():
            self.assertEqual(pl[k], v, k)


SAP = "SAP_FBL3N_09-2026_EN.xlsx"


def sap_dates_as_written() -> dict[int, dt.date]:
    """Excel row → the date the export means, read straight from the cell
    text as SAP's US format writes it (MM/DD/YYYY) — independent of the parser."""
    from openpyxl import load_workbook

    wb = load_workbook(os.path.join(NASTY, SAP), read_only=True)
    out = {}
    try:
        for i, row in enumerate(wb.active.iter_rows(values_only=True), start=1):
            if i == 1:
                continue
            m, d, y = (int(x) for x in str(row[1]).split("/"))
            out[i] = dt.date(y, m, d)
    finally:
        wb.close()
    return out


class ForeignErp(unittest.TestCase):
    """An English SAP FBL3N export: US dates, S/H indicator, a 6-digit chart."""

    def test_sap_fbl3n_closes_to_the_golden_pl(self):
        parsed, asked, pl = close([SAP], "close September 2026")
        exp = EXP[SAP]
        pf = parsed[0]
        self.assertEqual(pf.data_rows, exp["data_rows"], "every posting read")
        self.assertEqual(pf.unreadable_date_rows, 0, "no US date is unreadable any more")
        self.assertEqual(pf.skipped_rows, 0)
        self.assertEqual(pf.dc_mode, "indicator", "'Debit/Credit Ind.' is the S/H column next to one amount")
        self.assertNotIn("unreadable_dates", asked)
        self.assertNotIn("period_mixed", asked, "no September row may look like another month")
        # The S/H reading is confirmed once (rememberable), as for every indicator column.
        self.assertEqual(asked, ["ambiguous_debit_credit"])
        for k, v in exp["pl"].items():
            self.assertEqual(pl[k], v, k)

    def test_every_row_reads_its_true_date(self):
        pf = pkg.parse(os.path.join(NASTY, SAP), SAP, {})
        truth = sap_dates_as_written()
        self.assertEqual(pf.date_order["order"], "mdy")
        self.assertEqual(pf.date_order["source"], "values", "decided by the dates themselves, not by a header")
        self.assertEqual({r.row: r.date for r in pf.rows}, truth)
        self.assertEqual(sum(1 for r in pf.rows if r.date.day > 12), EXP[SAP]["days_over_12"])
        self.assertEqual({r.period for r in pf.rows}, {"2026-09"})

    def test_the_runner_path_gives_the_same_numbers(self):
        """parse → parsed.json → preflight / compute in separate steps, as agent-runner runs them."""
        import json as _json

        from agent_market.engine import dataset

        blob = _json.dumps(dataset.dump(pkg.parse(os.path.join(NASTY, SAP), SAP, {})))
        pre = pkg.preflight([dataset.load(_json.loads(blob))], message="close September 2026", today=TODAY)
        answers = {f["key"]: f["default"] for f in pre["findings"]}
        self.assertEqual(list(answers), ["ambiguous_debit_credit"])
        self.assertEqual(answers["ambiguous_debit_credit"], {"S": "debit", "H": "credit"})
        self.assertIn("chart_of_accounts", [a["code"] for a in pre["assumptions"]])
        computed = pkg.compute([dataset.load(_json.loads(blob))], pre["roles"], pre["period"], answers=answers)
        lines = {l["key"]: l["current"] for l in computed["lines"]}
        for k, v in EXP[SAP]["pl"].items():
            self.assertEqual(lines[k], v, k)
        self.assertEqual(computed["balance"]["diff"], "0.00")
        self.assertEqual(computed["excluded"], [])
        groups = {a["code"]: a["group"] for a in computed["accounts"]}
        self.assertEqual(groups["480000"], "other_income")
        self.assertEqual(groups["700000"], "finance_costs")
        self.assertEqual(groups["690000"], "operating_expenses")

    def test_thai_exports_still_read_day_first(self):
        for name in ("GL_Express_2569-09.csv", "GL_Express_2569-08.xlsx", "GL_clean_2569-09.xlsx"):
            pf = pkg.parse(os.path.join(NASTY, name), name, {})
            self.assertEqual(pf.date_order["order"], "dmy", name)
            self.assertEqual(pf.unreadable_date_rows, 0, name)


if __name__ == "__main__":
    unittest.main()
