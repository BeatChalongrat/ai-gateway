"""AM15 — the gate's hold on the backend tests that still fail (scripts/gate/marked.py).

The backend tests marked `integration` ran in no lane, and 91 of them failed unseen. The gate runs them
now; the ones that still fail are on a list by name. This holds the script that reads the lane's output
against that list: a failure not on the list is red, a name on the list that no longer fails is red (the
list only gets shorter), an unfinished run is red, the standing failures and the skipped tests are said
on every run — and a name can never be added to make the gate green.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate" / "marked.py"

LIST = """# why
## the credential policy masks where these expect a block
tests/test_a.py::TestX::test_one
tests/test_a.py::TestX::test_two  # its own reason
## needs a real Redis
tests/integration/test_b.py::test_three
"""
LANE = """...F..
SKIPPED [2] tests/test_pg.py:34: TEST_POSTGRES_URL not set
FAILED tests/test_a.py::TestX::test_one - assert 200 == 403
FAILED tests/test_a.py::TestX::test_two - assert False
FAILED tests/integration/test_b.py::test_three - redis.exceptions.ResponseError
3 failed, 556 passed, 2 skipped, 5137 deselected in 61.47s (0:01:01)
"""


class Marked(unittest.TestCase):
    def run_it(self, lane: str, listed: str = LIST, *flags: str):
        d = pathlib.Path(tempfile.mkdtemp(prefix="am15-marked-"))
        (d / "list.txt").write_text(listed, encoding="utf-8")
        (d / "lane.txt").write_text(lane, encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(d / "lane.txt"), *flags], capture_output=True, text=True, encoding="utf-8",
                           # (the console of the host this gate runs on is cp874: the script must say its words there too)
                           env={**os.environ, "GATE_MARKED_LIST": str(d / "list.txt"), "PYTHONIOENCODING": "cp874"})
        return r.returncode, r.stdout + r.stderr, (d / "list.txt").read_text(encoding="utf-8")

    def test_nothing_moved_is_not_called_green_and_names_what_stands(self):
        rc, out, _ = self.run_it(LANE)
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing moved", out)
        self.assertNotIn("GREEN", out)
        self.assertIn("STILL FAILING, BY NAME", out)
        self.assertIn("test_a.py ×2", out)
        self.assertIn("integration/test_b.py ×1", out)
        self.assertIn("the credential policy masks where these expect a block", out)
        self.assertIn("its own reason", out)
        self.assertIn("SKIPPED IN THIS LANE (2)", out)
        self.assertIn("TEST_POSTGRES_URL not set", out)

    def test_it_says_its_words_on_a_console_that_is_not_utf8(self):
        # found on the first real run: the output has dashes and dots the host's cp874 console cannot
        # encode, the script raised on its first line and the gate would have read the crash as red
        rc, out, _ = self.run_it(LANE)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("UnicodeEncodeError", out)
        self.assertIn("— not green", out)

    def test_a_failure_that_is_not_on_the_list_is_red_and_named(self):
        rc, out, _ = self.run_it(LANE.replace("3 failed", "4 failed").replace(
            "FAILED tests/test_a.py::TestX::test_one", "FAILED tests/test_new.py::test_broken - boom\nFAILED tests/test_a.py::TestX::test_one"))
        self.assertEqual(rc, 1, out)
        self.assertIn("RED — 1 failing test(s) are not on the list", out)
        self.assertIn("+ tests/test_new.py::test_broken", out)

    def test_a_collection_error_is_a_failure_too(self):
        rc, out, _ = self.run_it(LANE + "ERROR tests/test_broken_import.py - ImportError\n")
        self.assertEqual(rc, 1, out)
        self.assertIn("+ tests/test_broken_import.py", out)

    def test_a_name_on_the_list_that_passes_now_is_red_the_list_only_gets_shorter(self):
        rc, out, _ = self.run_it(LANE.replace("FAILED tests/test_a.py::TestX::test_two - assert False\n", "").replace("3 failed", "2 failed"))
        self.assertEqual(rc, 1, out)
        self.assertIn("do not fail any more", out)
        self.assertIn("- tests/test_a.py::TestX::test_two", out)

    def test_an_unfinished_run_is_red(self):
        rc, out, _ = self.run_it("FAILED tests/test_a.py::TestX::test_one - x\n")
        self.assertEqual(rc, 2, out)
        self.assertIn("no pytest summary line", out)

    def test_write_takes_names_off_and_keeps_the_reasons(self):
        lane = LANE.replace("FAILED tests/test_a.py::TestX::test_two - assert False\n", "").replace("3 failed", "2 failed")
        rc, out, listed = self.run_it(lane, LIST, "--write")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("test_two", listed)
        self.assertIn("tests/test_a.py::TestX::test_one", listed)
        self.assertIn("## needs a real Redis", listed)

    def test_write_never_adds_a_name(self):
        lane = LANE.replace("3 failed", "4 failed") + "FAILED tests/test_new.py::test_broken - boom\n"
        rc, out, listed = self.run_it(lane, LIST, "--write")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED", out)
        self.assertEqual(listed, LIST)


if __name__ == "__main__":
    unittest.main()
