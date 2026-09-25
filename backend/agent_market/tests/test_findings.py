"""Finding shape rules: the engine never receives a malformed or unsafe card."""

import unittest

from agent_market.engine.findings import FindingError, display_len, finding
from agent_market.tests import evalkit


class FindingShape(unittest.TestCase):
    def test_header_counts_thai_marks_as_zero_width(self):
        # 12 code points, two of them the combining vowel ิ → 10 columns.
        self.assertEqual(display_len("เดบิต/เครดิต"), 10)

    def test_header_longer_than_12_is_refused(self):
        with self.assertRaises(FindingError):
            finding("x", "blocking", "preflight", "หัวข้อที่ยาวเกินสิบสองตัว", "q?", [("a", 1, ""), ("b", 2, "")], 1)

    def test_options_must_be_two_to_four(self):
        with self.assertRaises(FindingError):
            finding("x", "blocking", "preflight", "h", "q?", [("a", 1, "")], 1)
        with self.assertRaises(FindingError):
            finding("x", "blocking", "preflight", "h", "q?", [(str(i), i, "") for i in range(5)], 1)

    def test_default_must_be_an_option(self):
        with self.assertRaises(FindingError):
            finding("x", "blocking", "preflight", "h", "q?", [("a", 1, ""), ("b", 2, "")], 3)

    def test_never_asks_for_a_secret(self):
        for word in ("รหัสผ่าน", "password", "token", "OTP"):
            with self.assertRaises(FindingError):
                finding("x", "blocking", "preflight", "h", f"กรุณาใส่{word}", [("a", 1, ""), ("b", 2, "")], 1)

    def test_every_eval_finding_is_well_formed(self):
        for case in evalkit.load_cases():
            for f in evalkit.run_case(case)["findings"]:
                self.assertLessEqual(display_len(f["header"]), 12, f["code"])
                if f["kind"] == "question":
                    self.assertTrue(2 <= len(f["options"]) <= 4, f["code"])
                    self.assertIn(f["default"], [o["value"] for o in f["options"]], f["code"])
                    self.assertTrue(f["evidence"], f"{f['code']} has no evidence")
                else:
                    self.assertTrue(f["request"], f["code"])


if __name__ == "__main__":
    unittest.main()
