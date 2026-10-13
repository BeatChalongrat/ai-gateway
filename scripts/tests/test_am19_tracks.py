"""AM19 — only what the code needs is tracked, and the gate says so by name.

Until AM19 the rule lived in CARD-RULES.md and depended on whoever was reading it: 223 probe files,
six phase inventories, four sprint sheets, thirty-three result files and a test report were tracked.
Now the rule is .gitignore's, and scripts/gate/tracks.py (the gate's `tracks` lane, the `drill`
phase, CI) fails naming any tracked file a rule there excludes — one force-added with `git add -f`,
or tracked since before the rule.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKS = ROOT / "scripts" / "gate" / "tracks.py"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )


def tracks(root: Path, rng: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(TRACKS), rng, str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


class ATrackedFileTheRulesExcludeIsNamed(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp(prefix="am19-tracks-"))
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / ".gitignore").write_text("notes/\n*_REPORT.md\n", encoding="utf-8")
        (self.repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
        git(self.repo, "add", ".gitignore", "app.py")
        git(self.repo, "commit", "-q", "-m", "the product")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_a_clean_tree_passes(self):
        r = tracks(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing tracked", r.stdout)

    def test_a_force_added_note_fails_by_name_and_passes_once_untracked(self):
        (self.repo / "notes").mkdir()
        (self.repo / "notes" / "how-we-got-here.md").write_text("…\n", encoding="utf-8")
        (self.repo / "AM0_REPORT.md").write_text("…\n", encoding="utf-8")
        git(self.repo, "add", "-f", "notes/how-we-got-here.md", "AM0_REPORT.md")
        git(self.repo, "commit", "-q", "-m", "a track slips in")
        r = tracks(self.repo)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("RED", r.stdout)
        self.assertIn("notes/how-we-got-here.md", r.stdout)
        self.assertIn("AM0_REPORT.md", r.stdout)
        # the range form names only what that range added
        r = tracks(self.repo, "HEAD~1..HEAD")
        self.assertEqual(r.returncode, 1)
        self.assertIn("added in HEAD~1..HEAD", r.stdout)
        git(self.repo, "rm", "-q", "--cached", "notes/how-we-got-here.md", "AM0_REPORT.md")
        git(self.repo, "commit", "-q", "-m", "untracked, kept on disk")
        self.assertTrue((self.repo / "notes" / "how-we-got-here.md").exists(), "untracking must not delete the file")
        r = tracks(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_pointer_at_an_untracked_file_fails_by_file_and_line(self):
        # the owner's rule: nothing is untracked while something still points at it
        (self.repo / "app.py").write_text("# the budget is explained in docs/HOW.md (and docs/notes/why.md)\nprint('hi')\n", encoding="utf-8")
        git(self.repo, "commit", "-q", "-am", "a comment cites two papers")
        r = tracks(self.repo)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("point at a docs/ or scripts/ file that is not tracked", r.stdout)
        self.assertIn("app.py:1  docs/HOW.md", r.stdout)
        self.assertIn("app.py:1  docs/notes/why.md", r.stdout)
        # track one, reword the other: green
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "HOW.md").write_text("# how\n", encoding="utf-8")
        (self.repo / "app.py").write_text("# the budget is explained in docs/HOW.md (the first sizing note stays with its author)\nprint('hi')\n", encoding="utf-8")
        git(self.repo, "add", "docs/HOW.md", "app.py")
        git(self.repo, "commit", "-q", "-m", "one tracked, one said in words")
        r = tracks(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing tracked points at", r.stdout)

    def test_a_longer_path_is_not_read_as_a_docs_path(self):
        # apps/desktop/docs/x.md is not docs/x.md; a line naming it must not fail for a file it does not name
        (self.repo / "apps" / "desktop" / "docs").mkdir(parents=True)
        (self.repo / "apps" / "desktop" / "docs" / "x.md").write_text("x\n", encoding="utf-8")
        (self.repo / "app.py").write_text("# see apps/desktop/docs/x.md\n", encoding="utf-8")
        git(self.repo, "add", "apps/desktop/docs/x.md", "app.py")
        git(self.repo, "commit", "-q", "-m", "a nested docs folder")
        r = tracks(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_not_a_repository_is_said_not_crashed(self):
        r = tracks(Path(tempfile.gettempdir()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("cannot ask git", r.stdout)


class ThisRepositoryHoldsTheRule(unittest.TestCase):
    def test_nothing_tracked_here_is_excluded_by_gitignore_and_nothing_points_at_an_untracked_file(self):
        r = tracks(ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing tracked points at", r.stdout)

    def test_the_rules_name_the_kinds_the_card_untracked(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for rule in ("scripts/probes/", "scripts/**/results/", "note-*.md", "PLAN-*.md", "TEST-SHEET-*.md", "*_INVENTORY.md", "docs/sprint-*.md", "**/PROGRESS.md", "backend/evals/**/results/"):
            self.assertIn(f"\n{rule}\n", ignore, f".gitignore lost the rule {rule}")

    def test_the_gate_and_ci_run_the_check(self):
        gate = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
        self.assertRegex(gate, r"(?m)^\s+lane tracks \d+ python scripts/gate/tracks\.py")
        drill = re.search(r"(?ms)^  drill\)\n(.*?)^    ;;\n", gate)
        self.assertTrue(drill and "python scripts/gate/tracks.py" in drill.group(1), "the drill no longer checks the tracked tree")
        for ci in (".gitlab-ci.yml", os.path.join(".github", "workflows", "ci.yml")):
            self.assertIn("python scripts/gate/tracks.py", (ROOT / ci).read_text(encoding="utf-8"), f"{ci} no longer runs the check")

    def test_no_probe_folder_is_tracked(self):
        out = subprocess.run(["git", "ls-files", "scripts/probes"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.stdout.strip(), "", "scripts/probes/ is tracked again:\n" + out.stdout)


if __name__ == "__main__":
    unittest.main()
