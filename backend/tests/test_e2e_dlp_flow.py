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
        [sent] = captured["messages"]
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


class TestCredentialMaskedBeforeProvider:
    def test_credential_masked_before_provider(self, monkeypatch):
        captured = _install_capture(monkeypatch)  # echo masked input

        # Anthropic key pattern: sk-ant-api03- followed by 32+ chars.
        key = "sk-ant-api03-" + ("a" * 40)
        resp = _chat(f"my api key is {key}, is it valid?")

        assert resp.status_code == 200
        body = resp.json()

        [sent] = captured["messages"]
        assert key not in sent
        assert "[CREDENTIAL_0]" in sent

        # Restored to the original on the user side.
        content = body["choices"][0]["message"]["content"]
        assert key in content
        assert "[CREDENTIAL_0]" not in content


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


class TestProviderIntroducedPiiMaskedInResponse:
    def test_provider_introduced_pii_masked_in_response(self, monkeypatch):
        # Mock the provider to introduce a fresh email that the user did
        # not include in their request.
        _install_capture(
            monkeypatch,
            response_text="A common example is john@example.com",
        )

        resp = _chat("what is a common example email address?")

        assert resp.status_code == 200
        body = resp.json()
        content = body["choices"][0]["message"]["content"]

        # The new email is masked (it's a leak, not an echo) and does NOT
        # get restored because it was never in span_map.
        assert "john@example.com" not in content
        assert "[EMAIL_0]" in content

        # The overall policy action reflects the output-side masking.
        assert body["policy_action"] == "mask"

        log = _log_for(body["id"])
        assert log is not None
        assert log.dlp_input_findings == 0
        assert log.dlp_output_findings >= 1
