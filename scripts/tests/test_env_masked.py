"""AM2 — scripts/env-masked.sh never prints a secret, not even part of one.

AM1c printed the live chat Redis password: a mask that expected
``scheme://user:pass@`` did not match ``redis://:pass@host`` (empty user), so
the line went out whole. FIX134's env-masked.sh had the same weakness in a
different place: it printed the first 3 characters of anything it judged
secret and every other value in full, so a secret under a plain key name (an
ntfy topic URL, a Slack webhook) printed whole. The script now shows a value
only for an allowlisted key with a plain word or number, and everything else
as length + SHA-256 prefix.

Runs the script on a fake env file and on a throwaway container (created, never
started) holding the same fake values:

    python -m unittest discover -s scripts/tests -p "test_env_masked.py" -v
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "env-masked.sh"

# Fake values only. Each secret is written so any fragment of it is findable.
REDIS_PW = "Pw9x7Qm2Lk4Zr8Tb6Vn3Hc5Jd1Fg0SaE"
NTFY_TOPIC = "q8Zt3xWv9LmN2bR7cY4kUu6Ee1Oo5Ii0"
HOOK = "zzH00kS3cr3tYy77Ww88Xx99"
PLAIN_SECRET = "hunter2hunter2hunter2"
ENV = {
    "APP_ENV": "production",
    "AGENT_MARKET_ENABLED": "true",
    "CHAT_EDGE_PORT": "8080",
    "WEBCHAT_REDIS_PASSWORD": REDIS_PW,
    "FIX41_BENCH_REDIS_URL": f"redis://:{REDIS_PW}@127.0.0.1:6380/1",
    "ALERT_NTFY_URL": f"https://ntfy.sh/{NTFY_TOPIC}",
    "SLACK_HOOK": f"https://hooks.slack.com/services/T0/B0/{HOOK}",
    "PLAIN_BUT_UNKNOWN": PLAIN_SECRET,
    "SOME_DB_HOST": "postgres:5432",
    "EMPTY_PASSWORD": "",
}
SECRETS = [REDIS_PW, NTFY_TOPIC, HOOK, PLAIN_SECRET]


def _bash() -> str:
    for cand in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash") or ""):
        if cand and Path(cand).exists():
            return cand
    raise unittest.SkipTest("bash not found")


def _run(*args: str) -> str:
    p = subprocess.run([_bash(), str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _lines(out: str) -> dict[str, str]:
    return {ln.split()[0]: ln for ln in out.splitlines() if ln.strip()}


class LeakAssertions:
    def assert_no_secret(self, out: str) -> None:
        for s in SECRETS:
            self.assertNotIn(s[:3], out, "a secret's first characters were printed")
            for i in range(len(s) - 5):
                self.assertNotIn(s[i:i + 6], out, "part of a secret was printed")

    def assert_shape(self, out: str) -> None:
        got = _lines(out)
        self.assertEqual(got["APP_ENV"].split("=", 1)[1].strip(), "production")
        self.assertEqual(got["AGENT_MARKET_ENABLED"].split("=", 1)[1].strip(), "true")
        self.assertEqual(got["CHAT_EDGE_PORT"].split("=", 1)[1].strip(), "8080")
        self.assertIn("(empty)", got["EMPTY_PASSWORD"])
        fp = hashlib.sha256(REDIS_PW.encode()).hexdigest()[:8]
        self.assertIn(f"{len(REDIS_PW)} chars", got["WEBCHAT_REDIS_PASSWORD"])
        self.assertIn(f"sha256:{fp}", got["WEBCHAT_REDIS_PASSWORD"])
        for k in ("FIX41_BENCH_REDIS_URL", "ALERT_NTFY_URL", "SLACK_HOOK", "PLAIN_BUT_UNKNOWN", "SOME_DB_HOST"):
            self.assertIn("sha256:", got[k], k)
            self.assertNotIn("=", got[k].split(None, 1)[1], k)


class EnvFileTest(LeakAssertions, unittest.TestCase):
    def test_env_file_prints_no_secret(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            body = "# a comment\n\n" + "".join(f"{k}={v}\r\n" for k, v in ENV.items())
            body += f'QUOTED_PASSWORD="{REDIS_PW}"\n'
            f.write_text(body, encoding="utf-8")
            out = _run("--env-file", str(f))
        self.assert_no_secret(out)
        self.assert_shape(out)
        # quotes and CR are stripped before hashing: same fingerprint as the bare value
        got = _lines(out)
        self.assertEqual(got["QUOTED_PASSWORD"].split("sha256:")[1], got["WEBCHAT_REDIS_PASSWORD"].split("sha256:")[1])

    def test_key_filter(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("".join(f"{k}={v}\n" for k, v in ENV.items()), encoding="utf-8")
            out = _run("--env-file", str(f), "redis")
        self.assertEqual(sorted(_lines(out)), ["FIX41_BENCH_REDIS_URL", "WEBCHAT_REDIS_PASSWORD"])


@unittest.skipUnless(shutil.which("docker"), "docker not available")
class ContainerTest(LeakAssertions, unittest.TestCase):
    def test_container_prints_no_secret(self):
        name = f"envmask-test-{uuid.uuid4().hex[:8]}"
        fd, envf = tempfile.mkstemp(suffix=".env")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(f"{k}={v}\n" for k, v in ENV.items()))
            subprocess.run(["docker", "create", "--name", name, "--env-file", envf, "alpine:3.20", "true"],
                           check=True, capture_output=True, timeout=120)
            out = _run(name)
        finally:
            os.unlink(envf)
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        self.assert_no_secret(out)
        self.assert_shape(out)


if __name__ == "__main__":
    unittest.main()
