"""The catalog page a stranger reads before installing (AM3): every package's manifest carries a
"guide" in plain Thai — who it is for, what using it looks like, what a good file looks like (with the
package's own sample files), what each output holds, what it asks, exactly what a model is sent, what
it will never do. The web renders it as is; this test keeps it true to the package."""

import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKGS = os.path.join(os.path.dirname(HERE), "packages")
PACKAGES = ("financial_report", "invoice_processing", "expense_audit", "bank_reconciliation", "po_matching")
GUIDE_KEYS = {"for", "how", "inputs", "outputs", "questions", "model_inputs", "never", "wrong_file", "time_note"}
# words a person outside accounting IT would stop at
JARGON = re.compile(r"materiality|D/C|S/H|debit/credit convention|cosmetic|schema|checksum|sandbox|E2\b", re.I)


def manifest(pkg):
    with open(os.path.join(PKGS, pkg, "agent.json"), encoding="utf-8") as f:
        return json.load(f)


class Guide(unittest.TestCase):
    def test_every_package_has_a_complete_guide(self):
        for pkg in PACKAGES:
            g = manifest(pkg).get("guide")
            self.assertIsInstance(g, dict, pkg)
            self.assertEqual(set(g), GUIDE_KEYS, pkg)
            self.assertTrue(3 <= len(g["how"]) <= 5, pkg)
            self.assertTrue(g["never"], pkg)

    def test_inputs_and_outputs_are_the_manifests_own(self):
        for pkg in PACKAGES:
            m = manifest(pkg)
            g = m["guide"]
            roles = {i["role"] for i in m["inputs"]}
            self.assertTrue(set(g["inputs"]) <= roles, pkg)
            required = {i["role"] for i in m["inputs"] if i.get("required")}
            self.assertTrue(required <= set(g["inputs"]), f"{pkg}: every required input is explained")
            self.assertEqual(set(g["outputs"]), {o["format"] for o in m["outputs"]}, pkg)
            for role, spec in g["inputs"].items():
                self.assertTrue(spec.get("good"), f"{pkg}.{role}")
                for rel in spec.get("sample") or []:
                    self.assertTrue(os.path.isfile(os.path.join(PKGS, pkg, rel)), f"{pkg}: {rel}")

    def test_model_inputs_name_only_what_the_package_binds(self):
        for pkg in PACKAGES:
            m = manifest(pkg)
            caps = set(m["capabilities"].get("required") or []) | set(m["capabilities"].get("optional") or [])
            models = {c for c in caps if c.startswith("model.")}
            self.assertEqual(set(m["guide"]["model_inputs"]), models, pkg)
            if m.get("narrative") == "rules":
                self.assertNotIn("model.text", m["guide"]["model_inputs"], pkg)

    def test_plain_thai_for_a_stranger(self):
        for pkg in PACKAGES:
            m = manifest(pkg)
            blob = json.dumps({"summary": m["summary"], "guide": m["guide"]}, ensure_ascii=False)
            self.assertIsNone(JARGON.search(blob), f"{pkg}: {JARGON.search(blob)}")

    def test_versions_were_bumped_with_the_guide(self):
        self.assertEqual({p: manifest(p)["version"] for p in PACKAGES},
                         {"financial_report": "1.3.3", "invoice_processing": "1.3.1", "expense_audit": "1.2.2",
                          "bank_reconciliation": "1.1.0", "po_matching": "1.0.0"})

    def test_every_package_says_whom_it_is_for_and_whose_law_it_follows(self):
        # AM6 "generic by default": scope and jurisdiction are the manifest's, never assumed
        for pkg in PACKAGES:
            m = manifest(pkg)
            self.assertIn(m.get("scope"), ("generic", "org-specific"), pkg)
            self.assertEqual(m.get("jurisdiction"), "TH", pkg)


if __name__ == "__main__":
    unittest.main()
