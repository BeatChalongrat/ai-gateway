"""AM22 — the integration lane's file keeps every chunk's verdict until the gate reads it.

``gate.sh it`` collects the whole lane into one file (``{ …; } >> it_lane.txt 2>&1``) and counts its PASS
and FAIL lines. Since AM19 every card's lane printed the lane it wraps through ``tee /dev/stderr``. When
stderr is a file, ``tee`` opens ``/dev/stderr`` afresh, without append, and truncates it: on 17 Oct 2026
the file was emptied the moment the card's piece started, taking with it two failures of the AM10 chunks
that ran before, and the nested lanes then wrote over each other's lines. The gate counted what was left.
Each lane now tees in append mode.

Held here twice: no lane script tees into stderr without ``-a``; and, with the shell the gate runs, a first
verdict written before a nested piece starts is still in the file after it.

    python -m unittest discover -s scripts/tests -p "test_am22_lane_output_kept.py" -v
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")  # the shell on PATH (Git's on Windows), never the WSL launcher a bare "bash" finds
LANES = sorted((ROOT / "scripts" / "gate" / "it").glob("*.sh"))


class TheLaneFileKeepsEveryVerdict(unittest.TestCase):
    def test_no_lane_tees_into_stderr_without_appending(self):
        self.assertTrue(LANES, "the integration lanes are in scripts/gate/it")
        bad = []
        for f in LANES:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for m in re.finditer(r"\btee\b([^|]*)/dev/stderr", line):
                    if not re.search(r"(^|\s)-a\b|--append", m.group(1)):
                        bad.append(f"{f.relative_to(ROOT)}:{n}")
        self.assertEqual(bad, [], "tee into stderr truncates the gate's results file: use tee -a")

    @unittest.skipUnless(BASH, "the gate's own shell")
    def test_a_verdict_written_before_a_nested_lane_starts_is_still_there_after_it(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "it_lane.txt"
            script = ('{ echo "--- FAIL: TestAnEarlierChunk"; '
                      'echo "lane_exit=0" | tee -a /dev/stderr | grep -q "^lane_exit=0$"; '
                      'echo "ended"; } >> "$1" 2>&1')
            subprocess.run([BASH, "-c", script, "lane", str(out)], check=True, timeout=60)
            text = out.read_text(encoding="utf-8")
            self.assertIn("--- FAIL: TestAnEarlierChunk", text, "the earlier chunk's failure must reach the verdict")
            self.assertIn("lane_exit=0", text)
            self.assertIn("ended", text)


if __name__ == "__main__":
    unittest.main()
