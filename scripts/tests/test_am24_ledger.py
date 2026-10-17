"""AM24 §3.7 and Part 4 — the gate's ledger (scripts/gate/ledger.py): a check that says it passed looked at something.

One fixture per condition, each proven red (or a skip, never a pass):

* matched nothing — a lane whose output names no check that ran; a chunk that ran no test; a chunk one of whose
  names ran as no test (a renamed test);
* ran over nothing — a lane whose every check was skipped is a skip;
* disappeared — a lane that ran fewer checks than its last green run is red; a line in removed_checks.txt saying
  what replaced them lets it fall; the smoke's checks are named when they go;

and the verdict: a run in pieces with one lane left out is not green, and names the lane and the commit its
result carries; every lane green at HEAD is green; anything uncommitted is not green.

    python -m unittest discover -s scripts/tests -p "test_am24_ledger.py" -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "scripts" / "gate" / "ledger.py"

GO_V = "=== RUN   {0}\n--- PASS: {0} (1.20s)\n"
PYTEST = "........\n8 passed, 1 skipped in 2.31s\n"


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="am24-ledger-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.removed = self.tmp / "removed.txt"
        self.removed.write_text("# none\n", encoding="utf-8")
        self.standing = self.tmp / "standing.txt"
        self.standing.write_text("# none\n", encoding="utf-8")
        self.env = {**os.environ, "GATE_LEDGER": (self.tmp / "ledger.jsonl").as_posix(),
                    "GATE_REMOVED": self.removed.as_posix(), "GATE_STANDING": self.standing.as_posix(),
                    "GATE_COMMIT": "abc1234", "GATE_DIRTY": "0", "PYTHONIOENCODING": "utf-8"}

    def run_it(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(LEDGER), *args], env={**self.env, **env}, capture_output=True,
                              text=True, encoding="utf-8", timeout=60)

    def out(self, name: str, text: str) -> str:
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p.as_posix()

    def record(self, lane: str, text: str, rc: int = 0, **env: str) -> subprocess.CompletedProcess:
        return self.run_it("record", lane, self.out(lane + ".txt", text), str(rc), **env)

    # ── 1. matched nothing ──────────────────────────────────────────────────────────────────────────────

    def test_a_lane_whose_output_names_no_check_that_ran_is_red(self):
        r = self.record("backend_lane", "collected 0 items\n\nno tests ran in 0.01s\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("RED", r.stdout)
        self.assertIn("matched nothing", r.stdout)

    def test_a_chunk_that_ran_no_test_is_red(self):
        r = self.run_it("chunk", "^(TestA|TestB)$", self.out("c.txt", "testing: warning: no tests to run\nPASS\nok  x 0.1s\n"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ran no test", r.stdout)

    def test_a_chunk_one_of_whose_names_ran_as_no_test_is_red_naming_it(self):
        # (a test renamed: its chunk used to pass with one fewer)
        r = self.run_it("chunk", "^(TestA|TestRenamed|TestC)$", self.out("c.txt", GO_V.format("TestA") + GO_V.format("TestC")))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("TestRenamed", r.stdout)
        ok = self.run_it("chunk", "^(TestA|TestC)$", self.out("c2.txt", GO_V.format("TestA") + GO_V.format("TestC")))
        self.assertEqual(ok.returncode, 0, ok.stdout)

    def test_a_recorded_chunk_short_of_a_name_is_red_in_the_ledger(self):
        r = self.run_it("record-chunk", "^(TestA|TestGone)$", self.out("c.txt", GO_V.format("TestA")), "0")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("it:TestA+1 RED", r.stdout)
        self.assertIn("TestGone", r.stdout)

    # ── 2. ran over nothing ─────────────────────────────────────────────────────────────────────────────

    def test_a_lane_whose_every_check_was_skipped_is_a_skip_not_a_pass(self):
        r = self.record("host_tests", "ssss\n----------------------------------------------------------------------\nRan 4 tests in 0.1s\n\nOK (skipped=4)\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("SKIP", r.stdout)
        self.assertIn("ran over nothing", r.stdout)

    # ── 3. disappeared ──────────────────────────────────────────────────────────────────────────────────

    def test_a_count_that_falls_on_its_own_is_red_and_a_line_saying_what_replaced_it_lets_it_fall(self):
        self.assertEqual(self.record("backend_lane", PYTEST).returncode, 0)
        fell = self.record("backend_lane", "......\n6 passed, 1 skipped in 2.01s\n", GATE_COMMIT="def5678")
        self.assertEqual(fell.returncode, 1, fell.stdout)
        self.assertIn("disappeared: 7 checks ran, 9 at its last green run (abc1234)", fell.stdout)
        # (held to the last GREEN run: the same fall is red again, not the new normal)
        again = self.record("backend_lane", "......\n6 passed, 1 skipped in 2.01s\n", GATE_COMMIT="def5678")
        self.assertEqual(again.returncode, 1, again.stdout)
        self.removed.write_text("backend_lane | 2 | test_old_rule.py (2 tests) | test_new_rule.py, which checks both cases\n", encoding="utf-8")
        allowed = self.record("backend_lane", "......\n6 passed, 1 skipped in 2.01s\n", GATE_COMMIT="def5678")
        self.assertEqual(allowed.returncode, 0, allowed.stdout)
        # the line lets it fall once: a further fall is red again
        self.assertEqual(self.record("backend_lane", ".....\n5 passed, 1 skipped in 2.0s\n").returncode, 1)

    def test_the_smokes_checks_are_named_when_they_go(self):
        names = self.tmp / "names.json"
        names.write_text(json.dumps(["tls.cert", "desktop.startup", "desktop.session", "desktop.turn", "desktop.closed"]), encoding="utf-8")
        first = self.run_it("names", "smoke_live", names.as_posix())
        self.assertEqual(json.loads(first.stdout)["ok"], True, first.stdout)
        names.write_text(json.dumps(["tls.cert", "desktop.startup"]), encoding="utf-8")
        r = self.run_it("names", "smoke_live", names.as_posix())
        self.assertEqual(r.returncode, 1, r.stdout)
        got = json.loads(r.stdout)
        self.assertFalse(got["ok"])
        self.assertIn("gone: desktop.session, desktop.turn, desktop.closed", got["detail"])

    def test_a_test_name_dropped_from_every_chunk_list_is_red(self):
        # (no chunk runs it any more, so no chunk is red: the lists together are held to their last green run)
        first = self.run_it("selectors", "it_names", "^(TestA|TestB)$", "^(TestC)$")
        self.assertEqual(first.returncode, 0, first.stdout)
        r = self.run_it("selectors", "it_names", "^(TestA)$", "^(TestC)$")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("gone: TestB", json.loads(r.stdout)["detail"])

    # ── Part 4: the verdict ─────────────────────────────────────────────────────────────────────────────

    def test_a_run_in_pieces_with_one_lane_left_out_is_not_green_and_names_it(self):
        for lane in ("tracks", "go_vet"):
            self.record(lane, "tracks: nothing tracked that .gitignore excludes\n" if lane == "tracks" else "")
        r = self.run_it("verdict", "tracks go_vet host_tests")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("NOT GREEN at abc1234", r.stdout)
        self.assertIn("host_tests (no result at abc1234; never run)", r.stdout)
        self.assertNotIn("tracks (", r.stdout)

    def test_a_lane_green_at_another_commit_is_named_with_that_commit(self):
        self.record("tracks", "tracks: ok\n", GATE_COMMIT="0ld0ld0")
        self.record("go_vet", "")
        r = self.run_it("verdict", "tracks go_vet")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("tracks (no result at abc1234; its last: green at 0ld0ld0)", r.stdout)

    def test_every_lane_green_at_the_commit_is_green_and_uncommitted_work_is_not(self):
        self.record("tracks", "tracks: ok\n")
        self.record("go_vet", "")
        r = self.run_it("verdict", "tracks go_vet")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("gate: ALL GREEN at abc1234 — every one of the 2 lanes", r.stdout)
        dirty = self.run_it("verdict", "tracks go_vet", GATE_DIRTY="1")
        self.assertEqual(dirty.returncode, 1, dirty.stdout)
        self.assertIn("uncommitted", dirty.stdout)

    def test_every_lane_passed_with_backend_tests_still_failing_by_name_is_not_all_green(self):
        # (the gate's full run has said so since AM15: the list of tests that still fail must be empty)
        self.standing.write_text("# the list\nintegration/test_x.py::test_a\nintegration/test_y.py::test_b\n", encoding="utf-8")
        self.record("tracks", "tracks: ok\n")
        r = self.run_it("verdict", "tracks")
        self.assertIn("with 2 backend test(s) still failing by name", r.stdout)
        self.assertIn("not ALL GREEN", r.stdout)
        self.assertNotIn("gate: ALL GREEN", r.stdout)

    def test_a_result_recorded_with_uncommitted_changes_never_counts_for_the_commit(self):
        self.record("tracks", "tracks: ok\n", GATE_DIRTY="1")
        r = self.run_it("verdict", "tracks")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("its last: green at abc1234+uncommitted", r.stdout)

    # ── the counts, read from each runner's own summary ─────────────────────────────────────────────────

    def test_each_runners_summary_is_read(self):
        sys.path.insert(0, str(ROOT / "scripts" / "gate"))
        import ledger  # noqa: E402
        cases = {
            "Ran 666 tests in 1087.996s\n\nOK\n": ("unittest", 666),
            "5140 passed, 17 skipped, 660 deselected, 5 xfailed in 352.45s (0:05:52)\n": ("pytest", 5162),
            "ℹ tests 45\nℹ pass 45\n": ("node --test", 45),
            "      Tests  2094 passed (2094)\n": ("vitest", 2094),
            "test result: ok. 30 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.07s\n"
            "test result: ok. 50 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.1s\n": ("cargo test", 80),
            "compared 316 pictures; worst difference 0.0000; PASS\n": ("pictures compared", 316),
            "GREEN test: 27 pass (1 slow), 0 fail, 19 skip in 49 s → x.json\n": ("smoke", 46),
            "ok  \tgithub.com/x/a\t0.4s\nok  \tgithub.com/x/b\t(cached)\n?   \tgithub.com/x/c\t[no test files]\n": ("go test packages", 2),
        }
        for text, (how, ran) in cases.items():
            c = ledger.count("x", text, 0)
            self.assertEqual((c["how"], c["ran"]), (how, ran), text)


if __name__ == "__main__":
    unittest.main()
