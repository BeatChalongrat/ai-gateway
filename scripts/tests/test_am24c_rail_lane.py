"""AM24c — the sidebar's rows, measured in a browser, are measured by the gate (CARD-RULES §6d: a test that runs in no
lane is no test, and the wiring is tested with the rule).

* the gate's ledger reads Playwright's own summary: without that rule the lane's output names no checks, and a lane
  that "ran nothing" is never green — a measuring test the gate could only ever skip or refuse;
* a Playwright run that fails is counted as a check that ran, and one that found no test is not a pass;
* the lane is in the gate's list of lanes and runs the spec that measures the rows, and that spec still measures what
  the card asks — rows 1 and 2 where they were after a click on row 3, every row where it was after the window
  regains focus — in a browser, outside the jsdom lane.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "gate"))
import ledger  # noqa: E402

SPEC = "tests/e2e/am24c-rail-holds-still.spec.ts"
PASSED = (
    "Running 1 test using 1 worker\n\n"
    "  ✓  1 tests\\e2e\\am24c-rail-holds-still.spec.ts:96:5 › AM24c: a click and a return to the window move no row (14.2s)\n\n"
    "  1 passed (52.3s)\n"
)
FAILED = (
    "Running 1 test using 1 worker\n\n"
    "  ✘  1 tests\\e2e\\am24c-rail-holds-still.spec.ts:96:5 › AM24c: a click and a return to the window move no row (20.1s)\n\n"
    "  1) tests\\e2e\\am24c-rail-holds-still.spec.ts:96:5 › AM24c: a click and a return to the window move no row\n\n"
    "    Error: expect(received).toEqual(expected) // deep equality\n\n"
    "  1 failed\n"
    "    tests\\e2e\\am24c-rail-holds-still.spec.ts:96:5 › AM24c: a click and a return to the window move no row\n"
)
NO_TESTS = "Error: No tests found.\nMake sure that arguments are regular expressions matching test files.\n"


class TheLedgerReadsPlaywright(unittest.TestCase):
    def test_a_passing_run_is_its_checks(self):
        c = ledger.count("web_rail_browser", PASSED, 0)
        self.assertEqual((c["how"], c["ran"], c["skipped"]), ("playwright", 1, 0))

    def test_a_failing_run_is_counted_as_a_check_that_ran(self):
        c = ledger.count("web_rail_browser", FAILED, 1)
        self.assertEqual((c["how"], c["ran"]), ("playwright", 1))

    def test_a_run_that_found_no_test_ran_nothing(self):
        self.assertIsNone(ledger.count("web_rail_browser", NO_TESTS, 1)["ran"])

    def test_skipped_and_flaky_are_read(self):
        c = ledger.count("web_rail_browser", "Running 4 tests using 1 worker\n\n  1 skipped\n  1 flaky\n  2 passed (1.1m)\n", 0)
        self.assertEqual((c["ran"], c["skipped"]), (4, 1))


class TheGateRunsTheSpec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
        cls.spec = (ROOT / "apps" / "web-chat" / SPEC).read_text(encoding="utf-8")

    def test_the_lane_is_in_the_list_and_runs_the_spec(self):
        self.assertIn(" web_rail_browser ", self.gate.split("LANES=", 1)[1].split("\n", 1)[0])
        self.assertIn(f"lane web_rail_browser 900 bash -c 'cd apps/web-chat && npx playwright test {SPEC} --reporter=list'", self.gate)

    def test_the_spec_measures_the_rows_above_the_pointer(self):
        self.assertIn("boundingBox()", self.spec)
        self.assertIn('href="/chat/agents/i/c"]\').click()', self.spec)
        self.assertIn("expect({ a: afterClick.a, b: afterClick.b }).toEqual({ a: before.a, b: before.b });", self.spec)
        self.assertIn("expect(afterFocus).toEqual(afterClick);", self.spec)

    def test_it_runs_in_a_browser_not_in_the_jsdom_lane(self):
        vitest = (ROOT / "apps" / "web-chat" / "vitest.config.ts").read_text(encoding="utf-8")
        playwright = (ROOT / "apps" / "web-chat" / "playwright.config.ts").read_text(encoding="utf-8")
        self.assertIn('"tests/e2e/**"', vitest)
        self.assertIn('testDir: "./tests/e2e"', playwright)


if __name__ == "__main__":
    unittest.main()
