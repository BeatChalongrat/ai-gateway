"""scripts/ops/envsafe.sh — a value is read out of .env only by NAME, and only a NAME on the list of settings
that are not secrets is ever answered with its value.

Three times a secret was printed by a command that only meant to look something up (the last on
2026-10-08: listing a container's DLP_* settings printed a key among them). This is the check that the
one tool now used for looking things up cannot do that: whatever it is asked, no secret value of the
file appears in what it prints — not for a secret's own name, not for a prefix, not for a name that is
not a name.

    python -m unittest discover -s scripts/tests -p "test_envsafe.py" -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
SECRETS = {"DLP_AUDIT_KEY": "5ec2e7a1" * 8, "QA_PASSWORD": "not-a-real-password-0123456789", "PORTAL_JWT_SECRET": "jwt-secret-value-abcdef",
           "DATABASE_URL": "postgresql://user:hunter2hunter2@db:5432/x", "EMPTY_SECRET": ""}
PLAIN = {"QA_EMAIL": "qa@example.test", "DLP_ENGINE": "rust", "APP_ENV": "production"}


@unittest.skipIf(BASH is None or "system32" in (BASH or "").lower(), "no POSIX bash on PATH")
class EnvSafeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="envsafe-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        lines = ["# a comment with a = sign", ""] + [f"{k}={v}" for k, v in {**SECRETS, **PLAIN}.items()] + ['QUOTED_SECRET="quoted-secret-value-xyz"']
        (self.tmp / ".env").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

    def run_it(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "ENVSAFE_FILE": (self.tmp / ".env").as_posix()}
        return subprocess.run([BASH, "scripts/ops/envsafe.sh", *args], cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)

    def assert_no_secret(self, r: subprocess.CompletedProcess, what: str):
        said = r.stdout + r.stderr
        for name, value in {**SECRETS, "QUOTED_SECRET": "quoted-secret-value-xyz"}.items():
            if value:
                self.assertNotIn(value, said, f"{what}: the value of {name} was printed")
        self.assertNotIn("hunter2", said, what)

    def test_names_are_names(self):
        r = self.run_it("names")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(sorted(r.stdout.split()), sorted([*SECRETS, *PLAIN, "QUOTED_SECRET"]))
        self.assert_no_secret(r, "names")
        r = self.run_it("names", "DLP_")
        self.assertEqual(sorted(r.stdout.split()), ["DLP_AUDIT_KEY", "DLP_ENGINE"])
        self.assert_no_secret(r, "names DLP_")

    def test_has_says_set_empty_or_absent_and_nothing_else(self):
        for name, want in (("DLP_AUDIT_KEY", "set"), ("EMPTY_SECRET", "empty"), ("NOT_THERE", "absent"), ("QA_EMAIL", "set")):
            r = self.run_it("has", name)
            self.assertEqual(r.stdout.strip(), want, name)
            self.assert_no_secret(r, f"has {name}")

    def test_a_value_is_given_only_for_a_name_on_the_list(self):
        for name, value in PLAIN.items():
            r = self.run_it("get", name)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, value), name)
        for name in [*SECRETS, "QUOTED_SECRET", "NOT_THERE"]:
            r = self.run_it("get", name)
            self.assertEqual(r.returncode, 3, name)
            self.assertEqual(r.stdout.strip(), "", name)
            self.assert_no_secret(r, f"get {name}")

    def test_what_is_not_a_name_is_refused_and_not_echoed(self):
        for wrong in (SECRETS["DLP_AUDIT_KEY"] + "!", "DLP_AUDIT_KEY|QA_EMAIL", ".*", "QA_EMAIL DLP_AUDIT_KEY", "$(cat .env)", "-e"):
            for verb in ("get", "has"):
                r = self.run_it(verb, wrong)
                self.assertEqual(r.returncode, 2, (verb, wrong))
                self.assertEqual(r.stdout.strip(), "")
                self.assert_no_secret(r, f"{verb} {wrong!r}")
                self.assertNotIn(wrong, r.stderr)

    def test_set_writes_only_a_setting_that_is_not_a_secret_and_leaves_every_other_line(self):
        env = self.tmp / ".env"
        before = env.read_bytes()
        project = "7f45890e-f32e-4ec3-ba62-9fac4530a896"
        r = self.run_it("set", "DLP_IN_HOUSE_PROJECTS", project)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "DLP_IN_HOUSE_PROJECTS added"), r.stderr)
        self.assertEqual(env.read_bytes(), before + f"DLP_IN_HOUSE_PROJECTS={project}".encode() + bytes([13, 10]))
        self.assertEqual(self.run_it("get", "DLP_IN_HOUSE_PROJECTS").stdout.strip(), project)
        r = self.run_it("set", "DLP_IN_HOUSE_PROJECTS", "a,b")
        self.assertEqual(r.stdout.strip(), "DLP_IN_HOUSE_PROJECTS changed")
        written = before + b"DLP_IN_HOUSE_PROJECTS=a,b" + bytes([13, 10])
        self.assertEqual(env.read_bytes(), written)
        self.assert_no_secret(r, "set")
        # a secret is never written from a command line, and a value with a shell character is refused
        for name, value, code in (("DLP_AUDIT_KEY", "00" * 32, 3), ("QA_PASSWORD", "x1y2z3-not-a-password", 3),
                                  ("DLP_IN_HOUSE_PROJECTS", "a b", 2), ("DLP_IN_HOUSE_PROJECTS", "$(id)", 2)):
            r = self.run_it("set", name, value)
            self.assertEqual(r.returncode, code, (name, value))
            self.assertNotIn(value, r.stdout + r.stderr)
        self.assertEqual(env.read_bytes(), written)

    def test_with_no_verb_it_explains_itself_and_prints_nothing_of_the_file(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 2)
        self.assert_no_secret(r, "usage")
        self.assertIn("envsafe.sh get NAME", r.stdout)


if __name__ == "__main__":
    unittest.main()
