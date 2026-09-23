"""FIX130 — the generated test stack never pages the owner or touches the
owner's off-site backups.

Before FIX130 ``scripts/test_stack_gen.py`` copied ``ALERT_NTFY_URL`` /
``ALERT_EMAIL_TO`` / ``ALERT_WEBHOOK_URL`` / ``ALERT_NTFY_TOKEN`` /
``RESEND_API_KEY`` from the real compose files into the test stack's backend
and backup services, so a test-stack alert went to the owner's phone. The
off-site backup variables (``BACKUP_OFFSITE_*``) would have done worse: a
test backup uploaded into — and pruned by retention — the owner's bucket.

Runs ``main()`` on a synthetic ``docker compose config`` (no Docker needed):

    python -m unittest discover -s scripts/tests -p "test_test_stack_gen.py" -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("test_stack_gen", HERE.parent / "test_stack_gen.py")
gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen)

OWNER_KEYS = ("ALERT_NTFY_URL", "ALERT_NTFY_TOKEN", "ALERT_EMAIL_TO", "ALERT_WEBHOOK_URL", "RESEND_API_KEY")


def _svc(env: dict) -> dict:
    return {"image": "x", "environment": dict(env)}


def _merged(extra_backend_env: dict | None = None) -> dict:
    """A minimal `docker compose config --no-interpolate` for the SERVICES."""
    alert_env = {k: "${" + k + ":-}" for k in OWNER_KEYS}
    services = {name: _svc({}) for name in gen.SERVICES}
    services["backend"] = _svc({**alert_env, "APP_ENV": "${APP_ENV:-development}", **(extra_backend_env or {})})
    services["backup"] = _svc({
        **alert_env,
        "ALERT_EMAIL_FROM": "${ALERT_EMAIL_FROM:-no-reply@sran.ai}",
        "BACKUP_OFFSITE_REPOSITORY": "${BACKUP_OFFSITE_REPOSITORY:-}",
        "BACKUP_OFFSITE_PASSWORD": "${BACKUP_OFFSITE_PASSWORD:-}",
        "BACKUP_OFFSITE_ACCESS_KEY_ID": "${BACKUP_OFFSITE_ACCESS_KEY_ID:-}",
        "BACKUP_OFFSITE_SECRET_ACCESS_KEY": "${BACKUP_OFFSITE_SECRET_ACCESS_KEY:-}",
        "POSTGRES_HOST": "postgres",
    })
    services["chat-edge-api"] = _svc({"ALERT_WEBHOOK_URL": "https://ntfy.sh/some-live-topic"})
    return {"services": services}


class GeneratedTestStackTest(unittest.TestCase):
    def _generate(self, merged: dict) -> tuple[int, dict | None, str]:
        with tempfile.TemporaryDirectory() as tmp:
            run = mock.Mock(return_value=SimpleNamespace(stdout=json.dumps(merged)))
            err = mock.Mock()
            with mock.patch.object(gen, "ROOT", Path(tmp)), \
                 mock.patch.object(gen.subprocess, "run", run), \
                 mock.patch.object(sys, "stderr", err), \
                 mock.patch("builtins.print"):
                rc = gen.main()
            out = Path(tmp) / "docker-compose.test-stack.yml"
            doc = None
            if out.exists():
                text = out.read_text(encoding="utf-8")
                doc = json.loads(text[text.index("\n{") + 1:])  # after the "#" header
            written = "".join(str(c.args[0]) for c in err.write.call_args_list)
            return rc, doc, written

    def test_alert_channels_are_the_test_stacks_own(self):
        rc, doc, _ = self._generate(_merged())
        self.assertEqual(rc, 0)
        for svc in ("backend", "backup"):
            env = doc["services"][svc]["environment"]
            for k in OWNER_KEYS:
                self.assertEqual(env[k], "${TS_" + k + ":-}", f"{svc}.{k}")
        self.assertEqual(doc["services"]["backup"]["environment"]["ALERT_EMAIL_FROM"],
                         "${TS_ALERT_EMAIL_FROM:-no-reply@sran.ai}")
        # A literal live destination is replaced too, not just ${VAR} references.
        self.assertEqual(doc["services"]["chat-edge-api"]["environment"]["ALERT_WEBHOOK_URL"],
                         "${TS_ALERT_WEBHOOK_URL:-}")

    def test_offsite_backup_never_points_at_the_owners_repository(self):
        rc, doc, _ = self._generate(_merged())
        self.assertEqual(rc, 0)
        env = doc["services"]["backup"]["environment"]
        for k in ("BACKUP_OFFSITE_REPOSITORY", "BACKUP_OFFSITE_PASSWORD",
                  "BACKUP_OFFSITE_ACCESS_KEY_ID", "BACKUP_OFFSITE_SECRET_ACCESS_KEY"):
            self.assertEqual(env[k], "${TS_" + k + ":-}", k)

    def test_nothing_in_the_file_references_a_live_owner_variable(self):
        rc, doc, _ = self._generate(_merged())
        self.assertEqual(rc, 0)
        text = json.dumps(doc)
        for marker in ("${ALERT_", "${RESEND_API_KEY", "${BACKUP_OFFSITE_", "ntfy.sh/"):
            self.assertNotIn(marker, text)

    def test_the_guard_refuses_an_owner_destination_that_slips_through(self):
        # A key the redirect does not know (not ALERT_*/BACKUP_OFFSITE_*) that
        # still carries a public ntfy topic must stop generation.
        rc, doc, written = self._generate(_merged({"PAGER_URL": "https://ntfy.sh/owner-topic"}))
        self.assertEqual(rc, 1)
        self.assertIsNone(doc)
        self.assertIn("REFUSED", written)
        self.assertIn("ntfy.sh/", written)


if __name__ == "__main__":
    unittest.main()
