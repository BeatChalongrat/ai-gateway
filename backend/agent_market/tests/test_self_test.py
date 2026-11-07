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
# AM6: what else a sample can be made for — whether the sample company issues goods receipts. Like its
# name, it is the sample's, laid over the install's own for a self-test run and never for a real one.
SAMPLE_KEYS = IDENTITY_KEYS | {"ap_gr_practice"}


def tax_id(first12: str) -> str:
    s = sum(int(c) * (13 - i) for i, c in enumerate(first12))
    return first12 + str((11 - s % 11) % 10)


# a real-looking company that is not the sample documents' buyer (valid mod-11 check digit)
SOMEONE_ELSE = {"company": "บริษัท ของจริง จำกัด", "company_tax_id": tax_id("010555600012"), "company_branch": "00000"}


class InvoiceSelfTest(unittest.TestCase):
    def test_settings_are_the_sample_documents_own_buyer(self):
        # AM6: the samples are made out to a company that does not exist (tools/make_generic_samples.py),
        # so that the agent is shown the same way at any company that installs it
        from agent_market.engine.e2.extract import extract
        from agent_market.tools.make_generic_samples import BUYER

        st = S.self_test("invoice_processing")
        settings = st.get("settings") or {}
        self.assertEqual(set(settings), IDENTITY_KEYS)
        self.assertEqual(settings, {"company": BUYER["name"], "company_tax_id": BUYER["tax_id"], "company_branch": BUYER["branch"]})
        for rel in st["files"]:
            out = extract(os.path.join(S.package_dir("invoice_processing"), rel), os.path.basename(rel))
            self.assertEqual(out["fields"]["buyer_tax_id"]["normalized"], BUYER["tax_id"], rel)
            self.assertEqual(out["questions"], [], rel)
        # everything else on the three documents is the fixture pack's: the same sellers, numbers and totals
        for fid, rel in (("d01_clean_full_tax_invoice", st["files"][0]), ("d05_wht_service_invoice", st["files"][1]),
                         ("e02_etax_xml_receipt", st["files"][2])):
            with open(os.path.join(E2_EXPECTED, fid + ".json"), encoding="utf-8") as f:
                truth = json.load(f)["truth"]
            out = extract(os.path.join(S.package_dir("invoice_processing"), rel), os.path.basename(rel))
            for k in ("document_number", "total", "seller_tax_id"):
                want = truth[k]["value"] if isinstance(truth[k], dict) else truth[k]
                self.assertEqual(str(out["fields"][k]["normalized"]), str(want), (fid, k))

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
        for pkg in ("financial_report", "invoice_processing", "expense_audit", "bank_reconciliation", "po_matching"):
            st = S.self_test(pkg)
            self.assertTrue(set(st.get("settings") or {}) <= SAMPLE_KEYS, pkg)
            res = S.render_sample(pkg, defaults=S.self_test_defaults(pkg, SOMEONE_ELSE))
            self.assertEqual([f["code"] for f in res["first_findings"]], [], pkg)
            self.assertTrue(res["render"]["passed"], pkg)


if __name__ == "__main__":
    unittest.main()
