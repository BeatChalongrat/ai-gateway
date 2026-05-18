"""End-to-end DLP flow tests: mask → provider → restore → log.

These tests verify the round-trip wiring added in the orchestrator:

    1. Inbound messages are scanned, blocking categories raise 403.
    2. Mask spans are rewritten to indexed tokens ([EMAIL_0], [CREDENTIAL_0])
       before the provider is called.
    3. The provider receives only the masked text (verified via a
       capturing stub that records the messages passed to dispatch).
    4. The response text is scanned for NEW PII introduced by the model;
       any new findings are masked with indexed tokens and do NOT get
       restored — they're leaks, not echoes.
    5. Indexed INPUT tokens in the response are restored to their original
       values so the user sees what they typed.
    6. Request_logs carries the DLP summary (findings count + action +
       categories).

Pattern: each test installs a per-test monkeypatch that captures the
``messages`` argument passed to ``ProviderDispatcher.dispatch`` and
returns a controllable response, then makes a real HTTP call through
the FastAPI TestClient. The request_logs row is looked up via the DB
session factory used by the integration test conftest.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.domain.providers import ProviderResponse as _PR
from app.main import app
from app.models.request_log import RequestLogORM
from app.services.policy.profiles import STRICT_PROFILE
from app.services.provider_dispatcher import ProviderDispatcher
from tests.conftest import _integration_engine


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat(content: str):
    return client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": content}],
            "metadata": {},
        },
    )


def _install_capture(monkeypatch, response_text: str | None = None):
    """Install a ProviderDispatcher.dispatch spy.

    Records the messages the orchestrator sends to the provider into
    ``captured['messages']`` and returns a ProviderResponse whose content
    is either ``response_text`` (if given) or an echo of the masked input
    — useful for verifying restore.
    """
    captured: dict = {"messages": None, "called": False}

    async def _spy(self, route, payload, messages, api_key=None):
        captured["messages"] = [m.content for m in messages]
        captured["called"] = True
        body = (
            response_text
            if response_text is not None
            else (messages[-1].content if messages else "")
        )
        return _PR(content=body, model=route.model_name)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _spy)
    return captured


def _log_for(request_id: str) -> RequestLogORM | None:
    Session = sessionmaker(bind=_integration_engine)
    s = Session()
    try:
        return s.get(RequestLogORM, request_id)
    finally:
        s.close()


class TestCleanMessagePassesThrough:
    def test_clean_message_passes_through(self, monkeypatch):
        captured = _install_capture(monkeypatch, response_text="clean reply")

        resp = _chat("what is the weather today?")

        assert resp.status_code == 200
        body = resp.json()
        # Provider received the exact user input, untouched.
        assert captured["called"] is True
        assert captured["messages"] == ["what is the weather today?"]
        # User sees the provider reply verbatim.
        assert body["choices"][0]["message"]["content"] == "clean reply"
        assert body["policy_action"] == "allow"

        log = _log_for(body["id"])
        assert log is not None
        assert log.dlp_input_findings == 0
        assert log.dlp_input_action == "allow"
        assert log.dlp_output_findings == 0


class TestPiiMaskedBeforeProvider:
    def test_pii_masked_before_provider(self, monkeypatch):
        captured = _install_capture(monkeypatch)  # echo masked input

        resp = _chat("my email is john@acme.com, help me write a reply")

        assert resp.status_code == 200
        body = resp.json()

        # Provider received masked text — NOT the raw email.
        # DLP system prompt injection may prepend a system message,
        # so check the user message specifically.
        user_msgs = [m for m in captured["messages"] if "john" in m or "[EMAIL_0]" in m]
        assert len(user_msgs) >= 1
        sent = user_msgs[0]
        assert "john@acme.com" not in sent
        assert "[EMAIL_0]" in sent

        # User sees the original email restored in the final response.
        content = body["choices"][0]["message"]["content"]
        assert "john@acme.com" in content
        assert "[EMAIL_0]" not in content

        assert body["policy_action"] == "mask"

        log = _log_for(body["id"])
        assert log is not None
        assert log.dlp_input_findings == 1
        assert log.dlp_input_action == "mask"
        assert log.dlp_categories is not None and "pii" in log.dlp_categories


class TestCredentialBlockedBeforeProvider:
    def test_credential_blocked_before_provider(self, monkeypatch):
        captured = _install_capture(monkeypatch)

        # Anthropic key pattern: sk-ant-api03- followed by 32+ chars.
        # Unique marker so this test's row is distinguishable from
        # other ``dlp_violation`` rows produced in the shared
        # integration DB.
        marker = "credential-blocked-before-provider-marker"
        key = "sk-ant-api03-" + ("a" * 40)
        resp = _chat(f"my api key is {key}, is it valid? ({marker})")

        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "dlp_violation"
        # Provider MUST NOT have been called (blocked before dispatch).
        assert captured["called"] is False

        # Block path MUST persist a request_logs row — compliance and
        # incident-response queries depend on every blocked request
        # being visible.
        Session = sessionmaker(bind=_integration_engine)
        s = Session()
        try:
            row = (
                s.query(RequestLogORM)
                .filter(RequestLogORM.error_code == "dlp_violation")
                .filter(RequestLogORM.input_raw.like(f"%{marker}%"))
                .order_by(RequestLogORM.created_at.desc())
                .first()
            )
        finally:
            s.close()
        assert row is not None, "expected a BLOCKED row for the DLP-blocked credential request"
        assert row.status == "blocked"
        assert row.policy_action == "block"
        assert row.error_code == "dlp_violation"
        assert row.dlp_findings_json is not None


class TestBlockStopsRequest:
    def test_block_stops_request(self, monkeypatch):
        # Force the orchestrator to pick the strict profile, which blocks
        # the "credential" category.
        monkeypatch.setattr(
            "app.services.orchestrator.get_profile",
            lambda _pid: STRICT_PROFILE,
        )
        captured = _install_capture(monkeypatch)

        key = "sk-ant-api03-" + ("a" * 40)
        resp = _chat(f"my api key is {key}")

        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "dlp_violation"
        # Provider MUST NOT have been called.
        assert captured["called"] is False


class TestProviderIntroducedPiiMaskedUnderDefault:
    def test_provider_introduced_pii_is_masked_by_default(self, monkeypatch):
        """Outbound PII (email) is MASKED under DEFAULT_PROFILE
        (May 2026 tightening).

        Earlier the default profile let AI-generated PII flow through.
        The audit's RT-04 round-trip showed this was a real outbound
        leak surface (an LLM hallucinating a fake email delivers it
        verbatim to the user). The profile now masks ``pii`` outbound
        — the user sees ``[EMAIL_N]`` instead of the raw value. The
        ``network`` category is still ALLOW; tenants who genuinely
        want raw outbound PII opt into a custom profile.
        """
        _install_capture(
            monkeypatch,
            response_text="A common example is john@example.com",
        )

        resp = _chat("what is a common example email address?")

        assert resp.status_code == 200
        body = resp.json()
        content = body["choices"][0]["message"]["content"]

        # The AI's email is now masked before reaching the user.
        assert "john@example.com" not in content
        assert "[EMAIL_" in content

        # Outbound transformation fired; overall action reflects the mask.
        assert body["policy_action"] == "mask"

        log = _log_for(body["id"])
        assert log is not None
        assert log.dlp_input_findings == 0
        # Output detector + masking both fire.
        assert log.dlp_output_findings >= 1
