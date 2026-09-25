"""The eval set is part of the gate: every case must pass.

A case asserts which findings fired and the computed numbers — never prose.
The clean case asking anything at all is a failure.
"""

import unittest

from agent_market.tests import evalkit


class EvalSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = evalkit.load_cases()
        cls.results = {c["id"]: evalkit.run_case(c) for c in cls.cases}

    def test_at_least_twenty_cases(self):
        self.assertGreaterEqual(len(self.cases), 20)

    def test_every_case_passes(self):
        failures = {}
        for case in self.cases:
            errs = evalkit.check(case, self.results[case["id"]])
            if errs:
                failures[case["id"]] = errs
        self.assertEqual(failures, {})

    def test_clean_input_asks_zero_questions(self):
        for cid in ("clean_express_th", "clean_xero_en", "clean_with_prior", "clean_with_budget",
                    "clean_ytd_trend", "messy_subtotals_page_headers", "trial_balance", "pl_export"):
            r = self.results[cid]
            self.assertEqual(r["preflight"] + r["postcompute"], [], cid)

    def test_each_check_fires_on_its_fixture(self):
        fired = set()
        for r in self.results.values():
            fired.update(r["preflight"] + r["postcompute"])
        for code in ("missing_file", "unmapped_column", "ambiguous_debit_credit", "period_undetermined",
                     "period_mixed", "currency_mixed", "wrong_document_type", "unbalanced",
                     "duplicate_suspect", "materiality_exceeded", "prior_period_mismatch"):
            self.assertIn(code, fired, code)


if __name__ == "__main__":
    unittest.main()
