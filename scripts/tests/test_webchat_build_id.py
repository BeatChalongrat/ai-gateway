"""The web-chat image cannot be built without its version stamp.

Twice (AM1, AM2) web-chat was built without NEXT_PUBLIC_BUILD_ID: the
Dockerfile fell back to the epoch second, /api/version said a number instead
of the revision, and the live smoke's pages.web_version failed after the swap.
A mistake that repeats identically is a missing guard. Now:

* scripts/build-images.sh always stamps the image with the revision it builds
  (never a value left in the shell or in .env from another build);
* the Dockerfile refuses to build when the stamp is empty (a direct
  ``docker build`` or ``docker compose build`` without it fails at once).

    python -m unittest discover -s scripts/tests -p "test_webchat_build_id.py" -v
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "apps" / "web-chat" / "Dockerfile"
BUILD_IMAGES = ROOT / "scripts" / "build-images.sh"


def run_lines(text: str) -> list[str]:
    return [ln.strip()[4:] for ln in text.splitlines() if ln.strip().startswith("RUN ")]


class WebChatBuildIdTest(unittest.TestCase):
    def test_the_build_step_has_no_fallback_stamp(self):
        runs = run_lines(DOCKERFILE.read_text(encoding="utf-8"))
        build = [r for r in runs if "npm run build" in r]
        self.assertEqual(len(build), 1, runs)
        self.assertNotIn("date", build[0], "the build must not invent a stamp (an epoch second) when none was given")

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX sh")
    def test_the_guard_refuses_an_empty_stamp(self):
        runs = run_lines(DOCKERFILE.read_text(encoding="utf-8"))
        guard = [r for r in runs if "NEXT_PUBLIC_BUILD_ID" in r and "npm run build" not in r]
        self.assertEqual(len(guard), 1, "one RUN line checks the stamp before the build")
        build = [r for r in runs if "npm run build" in r]
        self.assertLess(runs.index(guard[0]), runs.index(build[0]), "the check runs before the build")
        empty = subprocess.run(["sh", "-c", guard[0]], env={"NEXT_PUBLIC_BUILD_ID": "", "PATH": "/usr/bin:/bin"},
                               capture_output=True, text=True)
        self.assertNotEqual(empty.returncode, 0, "an empty stamp must stop the build")
        self.assertIn("NEXT_PUBLIC_BUILD_ID is required", empty.stderr)
        given = subprocess.run(["sh", "-c", guard[0]], env={"NEXT_PUBLIC_BUILD_ID": "f8bdfcd", "PATH": "/usr/bin:/bin"},
                               capture_output=True, text=True)
        self.assertEqual(given.returncode, 0, given.stderr)

    def test_build_images_stamps_the_revision_it_builds(self):
        text = BUILD_IMAGES.read_text(encoding="utf-8")
        rev = text.index("REVISION=$(git")
        stamp = re.search(r"^NEXT_PUBLIC_BUILD_ID=\$REVISION$", text, re.M)
        self.assertIsNotNone(stamp, "build-images.sh sets the stamp to the revision, whatever the shell held")
        self.assertGreater(stamp.start(), rev)
        self.assertRegex(text, r"export [^\n]*\bNEXT_PUBLIC_BUILD_ID\b", "exported, so compose's build args pick it up")
        # it is set before the compose build block is read
        self.assertLess(stamp.start(), text.index("config --format json"))


if __name__ == "__main__":
    unittest.main()
