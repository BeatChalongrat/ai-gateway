"""A new round inherits settings and remembered answers only — never an earlier round's data decisions (AM4).

"Remembered" is for how an export is written: which column is the date, which sign is a debit,
whether amounts are already baht, how the report shows names and years. Which rows of THIS file
to leave out is a decision about this round's data: September's "leave the October rows out" must
not quietly leave out rows in October's file. Those questions are not rememberable, and an answer
stored by an older version is not applied.
"""

from __future__ import annotations

import os
import unittest

from agent_market.packages import financial_report as pkg

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "packages", "financial_report", "evals", "fixtures")


def _pre(name: str, defaults: dict, message: str = "ปิดงบเดือนกันยายน"):
    files = [pkg.parse(os.path.join(FIX, name), name, {})]
    return files, pkg.preflight(files, message=message, defaults=defaults)


class RoundDataIsNotRemembered(unittest.TestCase):
    def test_the_stray_rows_question_is_not_rememberable(self):
        _, pre = _pre("period_mixed_sep.xlsx", {})
        f = next(f for f in pre["findings"] if f["code"] == "period_mixed")
        self.assertFalse(f["rememberable"], "a question about this file's rows is not a setting")

    def test_an_earlier_rounds_exclusion_is_asked_again_not_applied(self):
        # what an older version stored as a default after September's round
        files, pre = _pre("period_mixed_sep.xlsx", {"answers": {"period_mixed": "exclude"}})
        self.assertIn("period_mixed", [f["code"] for f in pre["findings"]])
        self.assertNotIn("period_mixed", [a["code"] for a in pre["assumptions"]])
        # answered "count them" this round, the stored "leave out" from last round changes nothing
        computed = pkg.compute(files, pre["roles"], "2026-09", answers={"period_mixed": "include"},
                               defaults={"answers": {"period_mixed": "exclude"}})
        self.assertNotIn("period_mixed", [e["code"] for e in computed["excluded"]])

    def test_leaving_foreign_currency_rows_out_is_asked_again(self):
        _, pre = _pre("currency_mixed_sep.xlsx", {"answers": {"currency_mixed": "exclude_foreign"}}, "")
        self.assertIn("currency_mixed", [f["code"] for f in pre["findings"]])

    def test_the_exports_convention_still_carries_over(self):
        # "the amounts in the file are already baht" describes the export, not the round
        _, pre = _pre("currency_mixed_sep.xlsx", {"answers": {"currency_mixed": "amounts_in_base"}}, "")
        self.assertNotIn("currency_mixed", [f["code"] for f in pre["findings"]])
        self.assertIn(("currency_mixed", "remembered"), [(a["code"], a["source"]) for a in pre["assumptions"]])

    def test_every_rememberable_question_is_about_how_the_export_is_written(self):
        conventions = {"unmapped_column", "date_order", "ambiguous_debit_credit", "account_name_missing",
                       "year_convention_mixed", "currency_mixed"}
        seen = set()
        for name in os.listdir(FIX):
            if not name.endswith((".xlsx", ".csv")):
                continue
            try:
                _, pre = _pre(name, {}, "")
            except Exception:  # noqa: BLE001 — a fixture that is not a ledger asks nothing rememberable
                continue
            seen |= {f["code"] for f in pre["findings"] if f.get("rememberable")}
        self.assertTrue(seen, "the fixtures raise at least one rememberable question")
        self.assertLessEqual(seen, conventions, seen - conventions)


if __name__ == "__main__":
    unittest.main()
