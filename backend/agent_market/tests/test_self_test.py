"""A self-test runs on the sample data's own identity, not the person's company (AM3).

The self-test (and the catalog's sample run) is the moment a stranger decides whether to trust an
agent. The invoice agent's self-test used to run on the install's settings: a person who had just
typed their REAL company tax ID got a "made out to another company" question on every sample
invoice, and the self-test failed its own "no findings" expectation. self-test.json now carries
"settings" — only the identity the sample documents were made out to — and the runner lays them
over the install's settings for a self-test run (never for a real run).
"""

import json
import os
import unittest

from agent_market.tests import samplekit as S

HERE = os.path.dirname(os.path.abspath(__file__))
E2_EXPECTED = os.path.join(os.path.dirname(HERE), "fixtures", "e2", "expected")
IDENTITY_KEYS = {"company", "company_tax_id", "company_branch"}


def tax_id(first12: str) -> str:
    s = sum(int(c) * (13 - i) for i, c in enumerate(first12))
    return first12 + str((11 - s % 11) % 10)


# a real-looking company that is not the sample documents' buyer (valid mod-11 check digit)
SOMEONE_ELSE = {"company": "บริษัท ของจริง จำกัด", "company_tax_id": tax_id("010555600012"), "company_branch": "00000"}


class InvoiceSelfTest(unittest.TestCase):
    def test_settings_are_the_sample_documents_own_buyer(self):
        st = S.self_test("invoice_processing")
        settings = st.get("settings") or {}
        self.assertEqual(set(settings), IDENTITY_KEYS)
        for fid in ("d01_clean_full_tax_invoice", "d05_wht_service_invoice", "e02_etax_xml_receipt"):
            with open(os.path.join(E2_EXPECTED, fid + ".json"), encoding="utf-8") as f:
                truth = json.load(f)["truth"]
            self.assertEqual(settings["company_tax_id"], truth["buyer_tax_id"]["value"], fid)
            self.assertEqual(settings["company_branch"], truth["buyer_branch"], fid)
            self.assertEqual(settings["company"], truth["buyer_name"]["value"], fid)

    def test_without_the_overlay_a_real_company_turns_every_sample_into_a_question(self):
        # what happened before AM3: the person's own tax ID against documents made out to the sample buyer
        d = S.self_test_defaults("invoice_processing", SOMEONE_ELSE, overlay=False)
        res = S.render_sample("invoice_processing", defaults=d)
        keys = [f["key"] for f in res["first_findings"]]
        self.assertEqual(len(keys), 3)
        self.assertTrue(all(k.endswith(":buyer_match") for k in keys), keys)

    def test_with_the_self_test_settings_it_asks_nothing_and_records_all_three(self):
        d = S.self_test_defaults("invoice_processing", SOMEONE_ELSE)
        self.assertEqual(d["company_tax_id"], S.self_test("invoice_processing")["settings"]["company_tax_id"])
        res = S.render_sample("invoice_processing", defaults=d)
        self.assertEqual(res["first_findings"], [])
        exp = S.self_test("invoice_processing")["expect"]["totals"]
        self.assertEqual({k: res["summary"][k] for k in ("documents", "included", "excluded")}, exp)


class EverySelfTest(unittest.TestCase):
    """Whatever company the person set, every package's self-test asks nothing."""

    def test_every_package(self):
        for pkg in ("financial_report", "invoice_processing", "expense_audit", "bank_reconciliation"):
            st = S.self_test(pkg)
            self.assertTrue(set(st.get("settings") or {}) <= IDENTITY_KEYS, pkg)
            res = S.render_sample(pkg, defaults=S.self_test_defaults(pkg, SOMEONE_ELSE))
            self.assertEqual([f["code"] for f in res["first_findings"]], [], pkg)
            self.assertTrue(res["render"]["passed"], pkg)


if __name__ == "__main__":
    unittest.main()
