"""scripts/ops/rotate_dlp_audit_key.py — the part that writes .env: the key in use is kept under another
name, a fresh one takes its place, every other line is left byte for byte, and neither key is ever printed.

    python -m unittest discover -s scripts/tests -p "test_rotate_dlp_audit_key.py" -v
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops" / "rotate_dlp_audit_key.py"
OLD_KEY = "0f" * 32
OTHER = "another-secret-value-123456"


class RotateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rotate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = self.tmp / ".env"
        self.before = f"# top\r\nA=1\r\nDLP_AUDIT_KEY={OLD_KEY}\r\nPORTAL_JWT_SECRET={OTHER}\r\nDLP_AUDIT_KEY_NOTE=x\r\n"
        self.env.write_bytes(self.before.encode("utf-8"))

    def run_it(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env={**os.environ, "ROTATE_ENV_FILE": str(self.env)}, timeout=60)

    def lines(self) -> dict[str, str]:
        return dict(l.split("=", 1) for l in self.env.read_bytes().decode("utf-8").split("\r\n") if "=" in l and not l.startswith("#"))

    def test_new_keeps_the_old_key_under_another_name_and_prints_neither(self):
        r = self.run_it("new")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        now = self.lines()
        self.assertEqual(now["DLP_AUDIT_KEY_OLD"], OLD_KEY)
        self.assertRegex(now["DLP_AUDIT_KEY"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(now["DLP_AUDIT_KEY"], OLD_KEY)
        for secret in (OLD_KEY, now["DLP_AUDIT_KEY"], OTHER):
            self.assertNotIn(secret, r.stdout + r.stderr)
        # every other line byte for byte, in place, same line ends
        after = self.env.read_bytes().decode("utf-8")
        self.assertEqual(re.sub(r"DLP_AUDIT_KEY=[0-9a-f]{64}\r\nDLP_AUDIT_KEY_OLD=", "DLP_AUDIT_KEY=", after), self.before)

    def test_a_second_new_is_refused_while_the_old_key_is_still_kept(self):
        self.assertEqual(self.run_it("new").returncode, 0)
        first = self.lines()
        r = self.run_it("new")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.lines(), first, "a refused rotation changes nothing — the old key would be lost")
        self.assertNotIn(OLD_KEY, r.stdout + r.stderr)

    def test_drop_old_removes_that_line_only(self):
        self.run_it("new")
        current = self.lines()["DLP_AUDIT_KEY"]
        r = self.run_it("drop-old")
        self.assertEqual(r.returncode, 0)
        now = self.lines()
        self.assertNotIn("DLP_AUDIT_KEY_OLD", now)
        self.assertEqual((now["DLP_AUDIT_KEY"], now["PORTAL_JWT_SECRET"], now["DLP_AUDIT_KEY_NOTE"], now["A"]), (current, OTHER, "x", "1"))
        self.assertNotIn(OLD_KEY, r.stdout + r.stderr)

    def test_nothing_to_rotate_and_no_verb(self):
        self.env.write_bytes(b"A=1\r\nDLP_AUDIT_KEY=\r\n")
        self.assertEqual(self.run_it("new").returncode, 2)
        self.assertEqual(self.env.read_bytes(), b"A=1\r\nDLP_AUDIT_KEY=\r\n")
        self.assertEqual(self.run_it().returncode, 2)
        self.assertEqual(self.run_it("check").returncode, 2, "check needs both keys in the file")


if __name__ == "__main__":
    unittest.main()
