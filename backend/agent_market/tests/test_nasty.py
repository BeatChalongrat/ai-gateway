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


class ForeignErp(unittest.TestCase):
    def test_us_dates_are_never_dropped_silently(self):
        parsed = [pkg.parse(os.path.join(NASTY, "SAP_FBL3N_09-2026_EN.xlsx"), "SAP_FBL3N_09-2026_EN.xlsx", {})]
        pre = pkg.preflight(parsed, message="close September 2026", today=TODAY)
        codes = [f["code"] for f in pre["findings"]]
        self.assertIn("unreadable_dates", codes)
        self.assertEqual(parsed[0].unreadable_date_rows, EXP["SAP_FBL3N_09-2026_EN.xlsx"]["days_over_12"])


if __name__ == "__main__":
    unittest.main()
