"""AM15 — the gate's backend lane is the suite, and a run of pieces is never a verdict.

Until AM15 a card named the backend files its lane ran (GATE_BACKEND_TESTS, ten of 418), so a test in
any other file could fail for months with no lane to say so — 26 did, and 91 of the 661 tests every
lane deselected. Nothing ran the host's own tests either. This holds what scripts/gate.sh says about
its lanes: there is no list of backend files and no setting to give it one, the tests marked
integration are run and held to their list by name, the trees that had no lane have one, a lane that
does not exist is refused before anything is touched, and a run of named lanes says PARTIAL.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
BASH = shutil.which("bash")


def lanes_phase() -> str:
    m = re.search(r"(?ms)^  lanes\)\n(.*?)^    ;;\n", GATE)
    assert m, "scripts/gate.sh has no lanes phase"
    return m.group(1)


class TheBackendLaneIsTheSuite(unittest.TestCase):
    def test_no_setting_and_no_list_of_backend_files(self):
        code = "\n".join(l for l in GATE.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("GATE_BACKEND_TESTS", code, "a card can name the backend lane's files again")
        self.assertNotIn("BACKEND_TESTS", code)
        backend = [l for l in lanes_phase().splitlines() if l.strip().startswith("lane backend_lane ")]
        self.assertEqual(len(backend), 1, backend)
        self.assertIn('-m "not integration"', backend[0])
        self.assertNotRegex(backend[0], r"tests/\S+\.py", "the backend lane names files: a file not named runs nowhere")
        self.assertNotRegex(backend[0], r"\s-k\s|--deselect|--ignore", "the backend lane leaves tests out")

    def test_the_tests_marked_integration_are_run_and_held_to_their_list(self):
        m = re.search(r"(?ms)^backend_marked\(\) \{.*?^\}", GATE)
        self.assertTrue(m, "no backend_marked")
        body = m.group(0)
        self.assertIn("-m integration", body)
        self.assertIn("python scripts/gate/marked.py", body)
        self.assertIn("backend_marked", lanes_phase())
        self.assertTrue((ROOT / "scripts" / "gate" / "backend_marked_failing.txt").exists(), "the list of the ones that still fail is gone")

    def test_every_lane_that_is_called_is_in_the_list_of_lanes_and_the_other_way(self):
        phase = lanes_phase()
        # (AM24: the list is declared once at the top of the script, where the gate's verdict reads it too)
        declared = set(re.search(r'(?m)^LANES="([^"]+)"', GATE).group(1).split())
        called = set(re.findall(r"(?m)^\s+lane ([a-z_]+) \d+ ", phase)) | {"backend_marked"}
        self.assertEqual(declared, called)
        for name in ("backend_lane", "backend_marked", "host_tests", "smoke_units", "alert_rules", "gateway_go", "log_worker_go", "portal", "dlp_sidecar"):
            self.assertIn(name, declared, f"the {name} lane is gone: its tests run nowhere again")

    def test_the_host_side_tests_are_found_by_pattern_not_by_name(self):
        host = [l for l in lanes_phase().splitlines() if l.strip().startswith("lane host_tests ")][0]
        self.assertIn("unittest discover -s scripts/tests -p 'test_*.py'", host)
        units = [l for l in lanes_phase().splitlines() if l.strip().startswith("lane smoke_units ")][0]
        self.assertIn("scripts/smoke/*.test.js", units)

    def test_the_last_line_of_a_whole_run_does_not_say_all_green_while_tests_stand_on_the_list(self):
        phase = lanes_phase()
        self.assertIn("still failing by name", phase)
        self.assertLess(phase.index("PARTIAL"), phase.index("ALL GREEN"), "a run of pieces can reach the line that says ALL GREEN")


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class APieceIsNotAVerdict(unittest.TestCase):
    def test_a_lane_that_does_not_exist_is_refused_before_anything_is_touched(self):
        tmp = Path(tempfile.mkdtemp(prefix="gate-lanes-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        e = {**os.environ, "GATE_LOCK": (tmp / "lock").as_posix(), "GATE_RESULTS": (tmp / "results").as_posix(), "GATE_WAIT_S": "0", "GATE_FREE_MB": "99999"}
        r = subprocess.run([BASH, "scripts/gate.sh", "lanes", "backend"], cwd=ROOT, env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no such lane: backend", r.stdout)
        self.assertIn("backend_lane", r.stdout)  # (it says what the lanes are)
        self.assertFalse((tmp / "lock").exists(), "a refused run took the lock")
        self.assertEqual([p.name for p in (tmp / "results").glob("*")], [], "a refused run started something")


if __name__ == "__main__":
    unittest.main()
