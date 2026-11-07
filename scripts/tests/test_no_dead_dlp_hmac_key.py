"""DLP_HMAC_KEY is named nowhere in the checkout that could make it look like a key in use.

The setting was read by nothing and was removed on 8 Oct 2026 (backend/tests/test_no_dead_dlp_hmac_key.py
holds the application and the compose files). This sweeps everything git tracks — compose files, env
templates, scripts, documents — for the name. The only places allowed to say it are the ones that say it
is gone: the settings' comment and the two tests.

    python -m unittest discover -s scripts/tests -p "test_no_dead_dlp_hmac_key.py" -v
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAYS_IT_IS_GONE = {
    "backend/app/core/config.py",
    "backend/tests/test_no_dead_dlp_hmac_key.py",
    "scripts/tests/test_no_dead_dlp_hmac_key.py",
}


class NoDeadKeyTest(unittest.TestCase):
    def test_the_name_is_only_where_it_is_said_to_be_gone(self):
        r = subprocess.run(["git", "grep", "-l", "-I", "-i", "-E", "DLP_HMAC_KEY|dlp_hmac_key"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        self.assertIn(r.returncode, (0, 1), r.stderr)
        named = {line.strip() for line in r.stdout.splitlines() if line.strip()}
        self.assertEqual(sorted(named - SAYS_IT_IS_GONE), [], "DLP_HMAC_KEY is read by nothing; these files name it as if it were in use")
        self.assertTrue(named, "the sweep found nothing at all — it is not looking at the checkout")

    def test_the_env_example_and_the_templates_do_not_offer_it(self):
        for rel in (".env.example", "scripts/uat/.env.uat.template"):
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            self.assertNotIn("DLP_HMAC_KEY", path.read_text(encoding="utf-8", errors="replace"), rel)


if __name__ == "__main__":
    unittest.main()
