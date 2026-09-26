"""AM2 Phase D — Agent 1 Invoice Processing.

The eval set (packages/invoice_processing/evals/cases.json, run by invoicekit through
engine/step.py exactly as the runner runs it) and the units under it: the answer → decision
mapping, the exclusion reason when several cards are skipped, the step hooks.
"""

from __future__ import annotations

import unittest

from agent_market.packages.invoice_processing.checks import decisions as D


class Decisions(unittest.TestCase):
    def test_keys_are_per_document(self):
        self.assertEqual(D.split_key("e2_low_confidence:d1a2b3c4d5e:total"), ("e2_low_confidence", "d1a2b3c4d5e", "total"))
        self.assertEqual(D.split_key("e2_duplicate:d1a2b3c4d5e"), ("e2_duplicate", "d1a2b3c4d5e", ""))
        d = D.for_document("dAAA", {"e2_low_confidence:dBBB:total": {"typed": "1.00"}})
        self.assertEqual((d.answered, d.overrides), (0, {}))       # another document's answer never lands here

    def test_typed_values_are_parsed_never_guessed(self):
        d = D.for_document("d1", {"e2_field_missing:d1:total": {"typed": "12,850.70 บาท"}})
        self.assertEqual(str(d.overrides["total"]), "12850.70")
        d = D.for_document("d1", {"e2_field_missing:d1:total": {"typed": "about twelve thousand"}})
        self.assertIn("total", d.invalid)                            # asked again, not interpreted
        self.assertIsNone(d.excluded)
        d = D.for_document("d1", {"e2_field_missing:d1:total": {"typed": "x"}, "e2_typed_invalid:d1:total": {"typed": "y"}})
        self.assertEqual(d.excluded["code"], "typed_value_invalid")  # a second bad value leaves it out
        d = D.for_document("d1", {"e2_field_missing:d1:seller_tax_id": {"typed": "0105545009870"}})
        self.assertIn("seller_tax_id", d.invalid)                    # a bad check digit is not a tax ID

    def test_skipped_cards_leave_the_document_out_with_the_fundamental_reason(self):
        d = D.for_document("d1", {"e2_copy:d1": "exclude", "e2_duplicate:d1": "exclude"})
        self.assertEqual(d.excluded["code"], "duplicate")
        self.assertEqual(d.excluded["codes"], ["duplicate", "copy_not_original"])

    def test_exchange_rate(self):
        d = D.for_document("d1", {"e2_check_failed:d1:currency": {"typed": "36.25"}})
        self.assertEqual((str(d.fx_rate), "currency" in d.accepted_checks), ("36.25", True))
        d = D.for_document("d1", {"e2_check_failed:d1:currency": "rate"})             # picked, nothing typed
        self.assertIn("fx_rate", d.invalid)
        d = D.for_document("d1", {"e2_check_failed:d1:currency": "rate", "e2_typed_invalid:d1:fx_rate": {"typed": "35.9"}})
        self.assertEqual(str(d.fx_rate), "35.9")

    def test_a_confirmed_reading_keeps_its_source(self):
        d = D.for_document("d1", {"e2_low_confidence:d1:total": {"accept": "12850.70"}})
        read = {"total": {"normalized": "12850.70", "source": {"page": 1}}}
        kw = d.judge_kwargs(read)
        self.assertEqual((kw["confirmed"], kw["overrides"]), ({"total"}, {}))
        kw = d.judge_kwargs({})                                      # only a model proposed it
        self.assertEqual(kw["override_methods"], {"total": "confirmed_by_person"})


class EvalSet(unittest.TestCase):
    """Every case of the package's own eval set; the invariant on each: no recorded value differs from
    the golden output unless the person typed it."""

    def test_every_case(self):
        from agent_market.tests import invoicekit as K

        failures = {}
        for case in K.load_cases()["cases"]:
            errs = K.check(case, K.run_case(case))
            if errs:
                failures[case["id"]] = errs
        self.assertEqual(failures, {})


class Hooks(unittest.TestCase):
    def test_extra_inputs_only_reach_packages_that_take_them(self):
        from agent_market.engine import step
        from agent_market.packages import financial_report, invoice_processing

        req = {"history": "h.json", "proposals": "p.json", "today": "2026-09-30"}
        self.assertFalse(step._accepts(financial_report.preflight, "history"))
        self.assertTrue(step._accepts(invoice_processing.preflight, "history"))
        self.assertTrue(step._accepts(invoice_processing.compute, "today"))
        self.assertEqual(step._extra({}, invoice_processing.compute, with_today=True), {})
        self.assertEqual(step._extra({"today": req["today"]}, financial_report.compute, with_today=True), {})


if __name__ == "__main__":
    unittest.main()
