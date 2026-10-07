"""AM11b — the gate runs one heavy thing at a time and starts nothing on a host with no room.

On 7 Oct 2026 a gate's lanes, test stack, image builds and desktop build ran as each came to mind on the
laptop that also runs live; the host went to 1.1 GB free five times and the integration lane was cut
twice with no verdict. ``scripts/gate.sh`` is now the only way a gate's heavy phases are started. Held
here, without Docker:

* a phase is refused while another holds the lock, and what it would have run does not run;
* a phase stops, saying how much the host had, when there is not room — and has started nothing;
* a stopped or refused phase leaves the lock as it found it.

    python -m unittest discover -s scripts/tests -p "test_am11b_gate.py" -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")  # (by PATH: on Windows a bare "bash" can be the WSL launcher)


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.marker = self.tmp / "ran"

    def gate(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        e = {**os.environ, "GATE_LOCK": (self.tmp / "lock").as_posix(), "GATE_RESULTS": (self.tmp / "results").as_posix(),
             "GATE_WAIT_S": "0", **env}
        return subprocess.run([BASH, "scripts/gate.sh", *args], cwd=ROOT, env=e, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def test_with_no_phase_it_says_what_the_phases_are_and_runs_nothing(self):
        r = self.gate()
        self.assertEqual(r.returncode, 2)
        for phase in ("preflight", "lanes", "it ", "build <sha>", "stack <sha>", "walk --", "smoke <sha>", "drill", "backup"):
            self.assertIn("gate.sh " + phase, r.stdout)

    def test_a_second_heavy_phase_is_refused_while_one_holds_the_lock(self):
        (self.tmp / "lock").mkdir()
        (self.tmp / "lock" / "what").write_text("it since 08:00:00Z pid 1\n", encoding="utf-8")
        r = self.gate("walk", "--", "touch", self.marker.as_posix(), GATE_FREE_MB="99999")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout)
        self.assertIn("it since 08:00:00Z", r.stdout)  # (it says who holds it)
        self.assertFalse(self.marker.exists(), "the refused phase ran its command")
        self.assertTrue((self.tmp / "lock").is_dir(), "a refused phase took the other's lock away")

    def test_a_phase_stops_before_it_starts_anything_when_the_host_has_no_room(self):
        r = self.gate("walk", "--", "touch", self.marker.as_posix(), GATE_FREE_MB="900")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("STOPPED", r.stdout)
        self.assertIn("900 MB free", r.stdout)
        self.assertIn("nothing was started", r.stdout)
        self.assertFalse(self.marker.exists(), "a phase with no room ran its command")
        self.assertFalse((self.tmp / "lock").exists(), "a stopped phase left its lock behind")

    def test_the_room_a_phase_needs_is_three_gigabytes_unless_a_card_says_otherwise(self):
        r = self.gate("walk", "--", "touch", self.marker.as_posix(), GATE_FREE_MB="2999")
        self.assertEqual(r.returncode, 4)
        self.assertIn("needs 3000", r.stdout)
        r = self.gate("walk", "--", "touch", self.marker.as_posix(), GATE_FREE_MB="2999", GATE_MIN_FREE_MB="5000")
        self.assertIn("needs 5000", r.stdout)


if __name__ == "__main__":
    unittest.main()
