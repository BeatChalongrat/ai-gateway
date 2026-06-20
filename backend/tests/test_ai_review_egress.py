"""Phase 1 — AI Sensitive-Data Review egress guard tests.

Verifies the realtime reviewer:
- receives the POST-DLP, PRE-RESTORE sanitized output (never raw),
- never receives an unmasked secret/credential (egress guard fails
  closed and skips the review),
- does not fall back to raw on guard failure.

Unit lane only — the external review API and DB persistence are
monkeypatched out.
"""

from __future__ import annotations

import asyncio

import pytest

import app.api.routes.admin_ai_review as air
import app.core.config as cfg
from app.services.ai_review_provider import (
    classify_review_host,
    review_egress_allowed,
)


# ---------------------------------------------------------------------------
# _assert_no_unmasked_secrets — the egress boundary guard
# ---------------------------------------------------------------------------


class TestEgressGuard:
    def test_clean_text_passes(self):
        assert air._assert_no_unmasked_secrets("the weather is nice today", "req1") is True

    def test_masked_text_passes(self):
        # Already-masked content (placeholder tokens) is safe to send.
        assert air._assert_no_unmasked_secrets("the account is <r0> and <r1>", "req2") is True

    def test_email_does_not_trip_secret_guard(self):
        # An email is PII, not a secret/credential — the guard only fails
        # closed on secret/credential (the reviewer is meant to SEE missed
        # non-secret PII so it can report coverage gaps).
        assert air._assert_no_unmasked_secrets("contact me at john@example.com", "req3") is True

    def test_raw_api_key_is_blocked(self):
        leaked = "here is the key sk-ant-api03-" + ("A" * 40)
        assert air._assert_no_unmasked_secrets(leaked, "req4") is False

    def test_raw_bearer_token_is_blocked(self):
        leaked = "Authorization: Bearer " + ("a1B2c3D4" * 6)
        assert air._assert_no_unmasked_secrets(leaked, "req5") is False


# ---------------------------------------------------------------------------
# _maybe_ai_review — sends sanitized content, fails closed on secrets
# ---------------------------------------------------------------------------


@pytest.fixture
def _wire_review(monkeypatch):
    """Wire the realtime hook so it would call the reviewer, capturing the
    payload, without touching the DB or network."""
    calls: list[dict] = []

    def _snapshot():
        return {
            "enabled": True,
            "realtime_enabled": True,
            "realtime_sample_rate": 100,
            "monitored_key_ids": None,
            "review_provider": "anthropic",
            "review_model": "claude-haiku-4-5-20251001",
            "review_api_key_enc": None,
            "review_endpoint": None,
        }

    async def _fake_call(row, *, system_text, user_text):
        calls.append({"system": system_text, "user": user_text})
        # Stop before DB persistence — the call itself is what we assert on.
        raise air.ReviewApiError("stopped after capture")

    monkeypatch.setattr(air, "_load_cached_settings", _snapshot)
    monkeypatch.setattr(air, "call_review_api", _fake_call)
    # base_url is internal so the host gate allows it; these tests focus
    # on payload/guard behavior, not the host trust boundary (covered
    # separately below).
    monkeypatch.setattr(
        air,
        "resolve_provider_meta",
        lambda row: {"provider": "anthropic", "model": "m", "base_url": "http://localhost:9999/v1"},
    )
    monkeypatch.setattr(air, "_persist_failed_scan_run", lambda **k: None)
    # Force the sampling gate to always pass.
    monkeypatch.setattr(air.random, "randint", lambda a, b: 1)
    return calls


def test_reviewer_receives_sanitized_not_raw(_wire_review):
    # A sanitized output that contains a missed email (non-secret) — the
    # reviewer SHOULD be called with exactly this text.
    sanitized = "the customer email john@example.com was not masked"
    asyncio.run(air._maybe_ai_review("reqA", sanitized, api_key_id=None))
    assert len(_wire_review) == 1
    assert "john@example.com" in _wire_review[0]["user"]


def test_reviewer_not_called_when_secret_present(_wire_review):
    # If a raw secret somehow reaches the hook, the egress guard must trip
    # and the reviewer must NOT be called (fail closed, no raw egress).
    leaked = "api key sk-ant-api03-" + ("A" * 40) + " leaked through"
    asyncio.run(air._maybe_ai_review("reqB", leaked, api_key_id=None))
    assert _wire_review == []


def test_reviewer_not_called_for_empty(_wire_review):
    asyncio.run(air._maybe_ai_review("reqC", "   ", api_key_id=None))
    assert _wire_review == []


# ---------------------------------------------------------------------------
# B1 — host trust boundary
# ---------------------------------------------------------------------------


@pytest.fixture
def _trust_flags(monkeypatch):
    monkeypatch.setattr(cfg.settings, "ai_review_require_trusted_endpoint", True)
    monkeypatch.setattr(cfg.settings, "ai_review_allow_external", False)
    monkeypatch.setattr(cfg.settings, "ai_review_allowed_hosts", "api.anthropic.com")


class TestHostClassification:
    def test_localhost_internal(self, _trust_flags):
        assert classify_review_host("http://localhost:8001/v1") == "internal"

    def test_docker_service_internal(self, _trust_flags):
        assert classify_review_host("http://ner-service:8001/analyze") == "internal"

    def test_private_ip_internal(self, _trust_flags):
        assert classify_review_host("http://10.1.2.3/v1") == "internal"

    def test_allowlisted_trusted(self, _trust_flags):
        assert classify_review_host("https://api.anthropic.com/v1/messages") == "trusted"

    def test_arbitrary_external(self, _trust_flags):
        assert classify_review_host("https://evil.example.com/v1") == "external"


class TestEgressDecision:
    def test_internal_allowed(self, _trust_flags):
        allowed, cls, _ = review_egress_allowed("http://localhost:8001/v1")
        assert allowed and cls == "internal"

    def test_trusted_allowed(self, _trust_flags):
        allowed, cls, _ = review_egress_allowed("https://api.anthropic.com/v1/messages")
        assert allowed and cls == "trusted"

    def test_external_blocked(self, _trust_flags):
        allowed, cls, _ = review_egress_allowed("https://evil.example.com/v1")
        assert not allowed and cls == "external"

    def test_external_allowed_when_flag_set(self, monkeypatch, _trust_flags):
        monkeypatch.setattr(cfg.settings, "ai_review_allow_external", True)
        allowed, cls, _ = review_egress_allowed("https://evil.example.com/v1")
        assert allowed and cls == "external"

    def test_gate_disabled_allows_all(self, monkeypatch, _trust_flags):
        monkeypatch.setattr(cfg.settings, "ai_review_require_trusted_endpoint", False)
        allowed, _, _ = review_egress_allowed("https://evil.example.com/v1")
        assert allowed


def _wire_external(monkeypatch, base_url):
    """Wire the realtime hook to a given reviewer base_url, capturing calls."""
    calls: list[dict] = []

    def _snapshot():
        return {
            "enabled": True, "realtime_enabled": True, "realtime_sample_rate": 100,
            "monitored_key_ids": None, "review_provider": "custom",
            "review_model": "m", "review_api_key_enc": None,
            "review_endpoint": base_url,
        }

    async def _fake_call(row, *, system_text, user_text):
        calls.append({"user": user_text})
        raise air.ReviewApiError("stopped")

    monkeypatch.setattr(air, "_load_cached_settings", _snapshot)
    monkeypatch.setattr(air, "call_review_api", _fake_call)
    monkeypatch.setattr(
        air, "resolve_provider_meta",
        lambda row: {"provider": "custom", "model": "m", "base_url": base_url},
    )
    monkeypatch.setattr(air, "_persist_failed_scan_run", lambda **k: None)
    monkeypatch.setattr(air.random, "randint", lambda a, b: 1)
    return calls


def test_realtime_skips_arbitrary_external_host(monkeypatch, _trust_flags):
    calls = _wire_external(monkeypatch, "https://evil.example.com/v1/chat")
    asyncio.run(air._maybe_ai_review("reqX", "the client is Somchai", api_key_id=None))
    assert calls == []  # blocked before any egress


def test_realtime_calls_internal_host(monkeypatch, _trust_flags):
    calls = _wire_external(monkeypatch, "http://localhost:9000/v1/chat")
    asyncio.run(air._maybe_ai_review("reqY", "the client is Somchai", api_key_id=None))
    assert len(calls) == 1


def test_realtime_calls_allowlisted_host(monkeypatch, _trust_flags):
    calls = _wire_external(monkeypatch, "https://api.anthropic.com/v1/messages")
    asyncio.run(air._maybe_ai_review("reqZ", "the client is Somchai", api_key_id=None))
    assert len(calls) == 1


def test_call_review_api_hard_gate_blocks_external(monkeypatch, _trust_flags):
    # Defense-in-depth: the real call_review_api refuses a non-allowlisted
    # external host even if a caller skipped the pre-check.
    import app.services.ai_review_provider as prov

    class _Row:
        review_provider = "custom"
        review_model = "m"
        review_api_key_enc = prov.encode_key("k")
        review_endpoint = "https://evil.example.com/v1/chat"

    with pytest.raises(prov.ReviewApiError):
        asyncio.run(prov.call_review_api(_Row(), system_text="s", user_text="u"))
