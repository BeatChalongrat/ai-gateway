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

    def test_a_test_container_gives_way_before_anything_live(self):
        """The live services each declare a floor (``mem_reservation``) and a protected place in the
        out-of-memory order (``oom_score_adj`` below zero). Copied into the test stack as they are, a
        test database would be as protected as the live one, and the kernel would have to choose
        between them. A test container keeps its ceiling, has no floor, and is the first to go."""
        merged = _merged()
        merged["services"]["postgres"].update({"mem_limit": "2g", "mem_reservation": "768m", "oom_score_adj": -900})
        merged["services"]["backend"].update({"mem_limit": "3g", "mem_reservation": "1g", "oom_score_adj": -700})
        merged["services"]["kb-models"]["deploy"] = {"resources": {"limits": {"memory": "6g"}, "reservations": {"memory": "2g"}}}
        rc, doc, _ = self._generate(merged)
        self.assertEqual(rc, 0)
        for name in gen.SERVICES:
            svc = doc["services"][name]
            self.assertEqual(svc.get("oom_score_adj"), gen.GATE_OOM_SCORE_ADJ, name)
            self.assertNotIn("mem_reservation", svc, name)
            self.assertNotIn("reservations", (svc.get("deploy") or {}).get("resources") or {}, name)
        self.assertGreater(gen.GATE_OOM_SCORE_ADJ, 0)
        self.assertEqual(doc["services"]["postgres"]["mem_limit"], "2g")  # (the ceiling stays)
        self.assertEqual(doc["services"]["kb-models"]["deploy"]["resources"]["limits"]["memory"], "6g")

    def test_every_test_stack_alert_is_labelled_test(self):
        """FIX131 D4: a test-stack page looked exactly like a real one. The
        alert senders run with ALERT_ENVIRONMENT=test ([TEST] in title and
        body, routed to ALERT_TEST_NTFY_URL only), and the test topic is the
        test stack's own TS_ twin."""
        merged = _merged()
        for svc in ("backend", "backup"):
            merged["services"][svc]["environment"].update({
                "ALERT_ENVIRONMENT": "${ALERT_ENVIRONMENT:-}",
                "ALERT_TEST_NTFY_URL": "${ALERT_TEST_NTFY_URL:-}",
            })
        rc, doc, _ = self._generate(merged)
        self.assertEqual(rc, 0)
        for svc in ("backend", "backup"):
            env = doc["services"][svc]["environment"]
            self.assertEqual(env["ALERT_ENVIRONMENT"], "test", svc)
            self.assertEqual(env["ALERT_TEST_NTFY_URL"], "${TS_ALERT_TEST_NTFY_URL:-}", svc)

    def test_the_guard_refuses_an_owner_destination_that_slips_through(self):
        # A key the redirect does not know (not ALERT_*/BACKUP_OFFSITE_*) that
        # still carries a public ntfy topic must stop generation.
        rc, doc, written = self._generate(_merged({"PAGER_URL": "https://ntfy.sh/owner-topic"}))
        self.assertEqual(rc, 1)
        self.assertIsNone(doc)
        self.assertIn("REFUSED", written)
        self.assertIn("ntfy.sh/", written)

    def test_kb_models_reads_the_live_weights_read_only_and_never_downloads(self):
        """FIX132 B: the test stack's kb-models mounts the live weights volume
        (pinned model files, ~4.6 GB) instead of downloading them again — but
        read-only, as an EXTERNAL volume (so `test-stack.sh reset`, which is
        `down --volumes`, can never delete it), offline, with no host port."""
        merged = _merged()
        merged["services"]["kb-models"] = {
            "image": "ai-gateway-kb-models:latest", "container_name": "ai-gateway-kb-models",
            "build": {"context": "./services/kb-models"},
            "environment": {"KB_THREADS": "${KB_THREADS:-6}", "HF_HUB_OFFLINE": "${KB_MODELS_OFFLINE:-1}"},
            "volumes": [{"type": "volume", "source": "kb_models", "target": "/models", "volume": {}}],
        }
        merged["services"]["chat-edge-api"]["environment"]["KB_MODELS_URL"] = "${KB_MODELS_URL:-http://kb-models:8090}"
        rc, doc, written = self._generate(merged)
        self.assertEqual(rc, 0, written)
        kb = doc["services"]["kb-models"]
        self.assertEqual(kb["volumes"], [{"type": "volume", "source": "ts_kb_models_live_ro", "target": "/models", "read_only": True}])
        self.assertEqual(doc["volumes"]["ts_kb_models_live_ro"], {"external": True, "name": "ai-gateway_kb_models"})
        self.assertEqual(kb["environment"]["HF_HUB_OFFLINE"], "1")
        self.assertEqual(kb["image"], "ai-gateway-kb-models:${TS_KB_MODELS_TAG:-latest}")
        self.assertNotIn("ports", kb)
        self.assertNotIn("container_name", kb)
        # Every other volume stays the test stack's own.
        for name, vol in doc["volumes"].items():
            if name != "ts_kb_models_live_ro":
                self.assertEqual(vol, {}, name)
        # chat-edge reaches the TEST stack's kb-models by service name.
        self.assertEqual(doc["services"]["chat-edge-api"]["environment"]["KB_MODELS_URL"], "${KB_MODELS_URL:-http://kb-models:8090}")

    def test_the_guard_refuses_a_writable_mount_of_the_live_weights(self):
        writable = {"kb-models": {"volumes": [{"type": "volume", "source": "ts_kb_models_live_ro", "target": "/models"}]}}
        with self.assertRaises(ValueError):
            gen.shared_volumes(writable)
        ro = {"kb-models": {"volumes": [{"type": "volume", "source": "ts_kb_models_live_ro", "target": "/models", "read_only": True}]}}
        self.assertEqual(gen.shared_volumes(ro), {"ts_kb_models_live_ro": {"external": True, "name": "ai-gateway_kb_models"}})
        self.assertEqual(gen.shared_volumes({"x": {}}), {})


if __name__ == "__main__":
    unittest.main()


class AgentMarketMailSinkTest(unittest.TestCase):
    """AM7 — Agent Market's mail relay on the test stack is a local sink.

    The approved-send path, the admin's test message and reminders are proved
    there against a real SMTP conversation; only the relay is simulated. The
    customer's relay password (AGENT_MARKET_SMTP_PASSWORD) never reaches a
    test-stack container: it becomes its empty TS_ twin like every other
    owner destination.
    """

    _generate = GeneratedTestStackTest._generate

    def _doc(self) -> dict:
        merged = _merged()
        merged["services"]["chat-edge-api"] = _svc({"AGENT_MARKET_SMTP_PASSWORD": "${AGENT_MARKET_SMTP_PASSWORD:-}"})
        rc, doc, written = self._generate(merged)
        self.assertEqual(rc, 0, written)
        return doc

    def test_the_stack_ships_its_own_mail_sink_on_loopback(self):
        sink = self._doc()["services"].get("mail-sink")
        self.assertIsNotNone(sink, "the test stack has no mail sink")
        self.assertTrue(sink["image"].startswith("axllent/mailpit:"))
        self.assertTrue(sink["ports"])
        for p in sink["ports"]:
            self.assertTrue(p.startswith("127.0.0.1:"), p)
        self.assertEqual(sink["labels"], {"sran.test-stack": "true"})

    def test_the_relay_password_is_never_the_owners(self):
        env = self._doc()["services"]["chat-edge-api"]["environment"]
        self.assertEqual(env["AGENT_MARKET_SMTP_PASSWORD"], "${TS_AGENT_MARKET_SMTP_PASSWORD:-}")
        self.assertIn("${AGENT_MARKET_SMTP_PASSWORD", gen.LIVE_REFERENCES)
