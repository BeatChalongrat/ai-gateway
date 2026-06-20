"""Phase 4A.0 — authenticated DLP context threading tests.

Verifies: actor resolution rule, context builder identity sourcing
(authenticated-only), conversation-ID provenance, channel/source labeling,
the deps trust gate for X-Conversation-ID/X-User-ID, secret-free context,
and behavior purity (context changes nothing in the DLP decision).
"""

from __future__ import annotations

import pytest

import app.api.deps as deps
import app.core.config as _cfg
from app.api.deps import ResolvedApiKey, _internal_signal_trusted
from app.domain.dlp import DetectionResult, SensitivityLevel, DetectionMatch
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.policy_engine import (
    CHANNEL_API,
    CHANNEL_WEBCHAT,
    CONV_SRC_ABSENT,
    CONV_SRC_CLIENT_METADATA,
    CONV_SRC_TRUSTED_HEADER,
    DLPPolicyEngine,
    ScanDirection,
    SourceType,
    build_dlp_request_context,
    resolve_authenticated_actor,
)
from app.services.policy.profiles import DEFAULT_PROFILE


def _auth(**kw):
    base = dict(key_id="k1", tenant_id="t1", project_id="p1", key_prefix="gw_")
    base.update(kw)
    return ResolvedApiKey(**base)


# ---------------------------------------------------------------------------
# Actor resolution rule
# ---------------------------------------------------------------------------


class TestActorRule:
    def test_portal_user_wins(self):
        assert resolve_authenticated_actor(trusted_portal_user_id="u1", key_id="k1") == "u1"

    def test_internal_actor_second(self):
        assert resolve_authenticated_actor(
            trusted_portal_user_id=None, trusted_internal_user_id="svc1", key_id="k1"
        ) == "svc1"

    def test_key_id_fallback(self):
        assert resolve_authenticated_actor(trusted_portal_user_id=None, key_id="k1") == "k1"

    def test_none_when_no_identity(self):
        assert resolve_authenticated_actor(trusted_portal_user_id=None, key_id=None) is None

    def test_no_shared_constant_fallback(self):
        # Empty/whitespace must not become "anonymous"/"default".
        assert resolve_authenticated_actor(trusted_portal_user_id="  ", key_id="  ") is None


# ---------------------------------------------------------------------------
# Context builder — identity from auth only
# ---------------------------------------------------------------------------


class TestContextBuilder:
    def test_direct_api(self):
        c = build_dlp_request_context(
            auth=_auth(), direction=ScanDirection.INPUT,
            source_type=SourceType.DIRECT_API, streaming=False,
            client_conversation_id="conv-abc",
        )
        assert (c.tenant_id, c.project_id, c.key_id, c.actor_id) == ("t1", "p1", "k1", "k1")
        assert c.conversation_id == "conv-abc"
        assert c.conversation_id_source == CONV_SRC_CLIENT_METADATA
        assert c.source_type == SourceType.DIRECT_API and c.channel == CHANNEL_API
        assert c.streaming is False

    def test_webchat_trusted(self):
        c = build_dlp_request_context(
            auth=_auth(key_id="shared", portal_user_id="end9",
                       internal_trusted=True, trusted_conversation_id="conv-web"),
            direction=ScanDirection.INPUT, source_type=SourceType.DIRECT_API,
            streaming=True, client_conversation_id="IGNORED-BODY",
        )
        assert c.actor_id == "end9"                       # portal user, not key
        assert c.conversation_id == "conv-web"            # trusted header wins
        assert c.conversation_id_source == CONV_SRC_TRUSTED_HEADER
        assert c.source_type == SourceType.WEBCHAT and c.channel == CHANNEL_WEBCHAT
        assert c.streaming is True

    def test_tool_source_preserved_for_trusted(self):
        # internal_trusted labels channel webchat but keeps an explicit
        # TOOL_ARGUMENT source.
        c = build_dlp_request_context(
            auth=_auth(internal_trusted=True), direction=ScanDirection.OUTPUT,
            source_type=SourceType.TOOL_ARGUMENT, streaming=False,
        )
        assert c.source_type == SourceType.TOOL_ARGUMENT
        assert c.channel == CHANNEL_WEBCHAT

    def test_missing_conversation_is_none(self):
        c = build_dlp_request_context(auth=_auth(), direction=ScanDirection.INPUT)
        assert c.conversation_id is None
        assert c.conversation_id_source == CONV_SRC_ABSENT

    def test_actor_falls_back_to_key_when_no_portal(self):
        c = build_dlp_request_context(auth=_auth(portal_user_id=None),
                                      direction=ScanDirection.INPUT)
        assert c.actor_id == "k1"

    def test_identity_only_from_auth_not_body(self):
        # The builder has NO parameter for tenant/project/actor — they can
        # only come from auth. The only client-influenced input is the
        # conversation locator. Prove a different "auth" fully determines them.
        c = build_dlp_request_context(
            auth=_auth(tenant_id="REAL-TENANT", project_id="REAL-PROJ", key_id="REAL-KEY"),
            direction=ScanDirection.INPUT, client_conversation_id="anything",
        )
        assert c.tenant_id == "REAL-TENANT" and c.project_id == "REAL-PROJ"
        assert c.actor_id == "REAL-KEY"


# ---------------------------------------------------------------------------
# Security — no secrets / raw ids in context or logs
# ---------------------------------------------------------------------------


class TestSecrecy:
    def test_no_secret_in_repr(self):
        c = build_dlp_request_context(auth=_auth(), direction=ScanDirection.INPUT)
        r = repr(c)
        assert "key_prefix" not in r and "Bearer" not in r and "secret" not in r.lower()

    def test_log_dict_hashes_ids(self):
        c = build_dlp_request_context(
            auth=_auth(), direction=ScanDirection.INPUT, client_conversation_id="c1"
        )
        d = c.safe_log_dict()
        assert d["tenant_id"] not in ("t1", None)
        assert d["key_id"] not in ("k1", None)
        assert d["conversation_id"] not in ("c1", None)
        # enums/flags raw
        assert d["channel"] == CHANNEL_API and d["streaming"] is False


# ---------------------------------------------------------------------------
# deps trust gate for X-Conversation-ID / internal-trusted
# ---------------------------------------------------------------------------


class _FakeKey:
    key_id = "shared-key"
    tenant_id = "t1"
    project_id = "p1"
    key_prefix = "gw_"
    role = "user"


@pytest.fixture
def _stub_verify(monkeypatch):
    import contextlib

    class _Sess:
        def commit(self):
            pass

    @contextlib.contextmanager
    def _sess():
        yield _Sess()

    monkeypatch.setattr(deps, "SessionLocal", _sess)
    monkeypatch.setattr(deps._api_key_service, "verify_key", lambda s, k: _FakeKey())
    monkeypatch.setattr(_cfg.settings, "internal_service_secret", "the-secret", raising=False)


class TestDepsTrustGate:
    def test_internal_signal_gate(self, monkeypatch):
        monkeypatch.setattr(_cfg.settings, "internal_service_secret", "s", raising=False)
        assert _internal_signal_trusted("s") is True
        assert _internal_signal_trusted("wrong") is False
        assert _internal_signal_trusted(None) is False
        monkeypatch.setattr(_cfg.settings, "internal_service_secret", "", raising=False)
        assert _internal_signal_trusted("s") is False  # unset server-side → never trust

    def test_trusted_conversation_honored_with_secret(self, _stub_verify):
        r = deps.get_resolved_api_key(
            authorization="Bearer gw_abc",
            x_user_id="end9",
            x_internal_service_secret="the-secret",
            x_conversation_id="conv-web",
        )
        assert r.internal_trusted is True
        assert r.trusted_conversation_id == "conv-web"
        assert r.portal_user_id == "end9"

    def test_conversation_ignored_without_secret(self, _stub_verify):
        r = deps.get_resolved_api_key(
            authorization="Bearer gw_abc",
            x_user_id="attacker",
            x_internal_service_secret=None,        # no secret
            x_conversation_id="conv-spoof",
        )
        assert r.internal_trusted is False
        assert r.trusted_conversation_id is None    # header ignored
        assert r.portal_user_id is None             # X-User-ID ignored


# ---------------------------------------------------------------------------
# Behavior purity — context changes no DLP decision
# ---------------------------------------------------------------------------


class TestBehaviorPurity:
    @pytest.mark.parametrize("text", ["email a@b.com", "เงินเดือน 50000", "OTP 123456", "hi there"])
    def test_decision_independent_of_context(self, text):
        reg = get_default_registry(ner_enabled=False)
        eng = DLPPolicyEngine()

        def decide(ctx):
            dr = DetectionResult(matches=reg.scan_text(text), scanned_direction="input")
            return eng.evaluate_decision(
                detection_result=dr, profile=DEFAULT_PROFILE, text=text, context=ctx
            ).overall_action

        direct = build_dlp_request_context(auth=_auth(), direction=ScanDirection.INPUT)
        web = build_dlp_request_context(
            auth=_auth(internal_trusted=True, portal_user_id="u9"),
            direction=ScanDirection.INPUT,
        )
        # Different identity/channel context → identical decision (shadow).
        assert decide(direct) == decide(web) == decide(None)
