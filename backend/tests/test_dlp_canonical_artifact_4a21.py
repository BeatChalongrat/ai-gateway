"""Phase 4A.2.1 — dual canonical/display artifact production tests."""

from __future__ import annotations

import pytest
import structlog

import app.core.config as _cfg
from app.api.deps import ResolvedApiKey
from app.services.dlp.policy_engine import (
    ScanDirection,
    SourceType,
    build_dlp_request_context,
)
from app.services.dlp.canonical_artifact import (
    ARTIFACT_SCHEMA_VERSION,
    CANONICAL_FINAL_EVENT,
    STATUS_COMPLETE,
    ArtifactError,
    CanonicalArtifactFinalizer,
    CanonicalResponseArtifact,
    build_canonical_artifact,
    canonical_artifact_shadow_report,
    canonical_artifact_transport_allowed,
    canonical_sha256,
    safe_exception_fields,
)


@pytest.fixture(autouse=True)
def _scope_global(monkeypatch):
    # Phase 4A.2.7 — canonical transport now also requires tenant scope. These
    # tests target the managed/enabled gate; ``global`` scope lets any valid
    # tenant/project pass so the original assertions hold (scope is exercised
    # separately in test_dlp_feature_scope_4a27).
    monkeypatch.setattr(_cfg.settings, "dlp_feature_scope_mode", "global", raising=False)
    monkeypatch.setattr(_cfg.settings, "dlp_feature_allowlist", "", raising=False)


def _art(content="ผลลัพธ์ <r0>", status=STATUS_COMPLETE, **kw):
    base = dict(
        generation_id="req_abc", request_id="req_abc", canonical_content=content,
        channel="webchat", streaming=False, status=status, created_at=1_700_000_000,
    )
    base.update(kw)
    return build_canonical_artifact(**base)


def _auth(**kw):
    base = dict(key_id="k1", tenant_id="t1", project_id="p1", key_prefix="gw_")
    base.update(kw)
    return ResolvedApiKey(**base)


def _webchat_ctx():
    return build_dlp_request_context(
        auth=_auth(portal_user_id="u1", internal_trusted=True,
                   trusted_conversation_id="c1"),
        direction=ScanDirection.OUTPUT,
    )


def _public_ctx():
    return build_dlp_request_context(
        auth=_auth(), direction=ScanDirection.OUTPUT,
        client_conversation_id="c1",
    )


# ===========================================================================
# Artifact unit
# ===========================================================================


class TestArtifact:
    def test_immutable(self):
        a = _art()
        with pytest.raises(Exception):
            a.canonical_content = "x"  # frozen

    def test_sha_and_length(self):
        a = _art(content="ผลลัพธ์ <r0>")
        assert a.canonical_sha256 == canonical_sha256("ผลลัพธ์ <r0>")
        assert len(a.canonical_sha256) == 64 and a.canonical_sha256.islower()
        assert a.canonical_length == len("ผลลัพธ์ <r0>")
        a.validate()

    @pytest.mark.parametrize("text", ["ภาษาไทย", "emoji 😀", "line\nbreak",
                                      "<r0> <<NAME_1>>", ""])
    def test_digest_varies(self, text):
        assert canonical_sha256(text) == canonical_sha256(text)
        assert canonical_sha256(text) != canonical_sha256(text + "x")

    def test_invalid_length_rejected(self):
        a = _art()
        bad = CanonicalResponseArtifact(
            **{**a.__dict__, "canonical_length": a.canonical_length + 1})
        with pytest.raises(ArtifactError):
            bad.validate()

    def test_invalid_digest_rejected(self):
        a = _art()
        bad = CanonicalResponseArtifact(**{**a.__dict__, "canonical_sha256": "0" * 64})
        with pytest.raises(ArtifactError):
            bad.validate()

    def test_bad_schema_version_rejected(self):
        a = _art()
        bad = CanonicalResponseArtifact(**{**a.__dict__, "schema_version": 99})
        with pytest.raises(ArtifactError):
            bad.validate()

    def test_duplicate_finalize_idempotent(self):
        f = CanonicalArtifactFinalizer()
        a1 = f.finalize_once(generation_id="g", request_id="g",
                             canonical_content="first <r0>", channel="webchat",
                             streaming=False, status=STATUS_COMPLETE, created_at=1)
        # second call with DIFFERENT content returns the SAME artifact
        a2 = f.finalize_once(generation_id="g", request_id="g",
                             canonical_content="DIFFERENT", channel="webchat",
                             streaming=False, status=STATUS_COMPLETE, created_at=2)
        assert a1 is a2 and a2.canonical_content == "first <r0>"

    def test_forbidden_value_in_canonical_rejected(self):
        # defense-in-depth: a restored raw value must not appear in canonical
        with pytest.raises(ArtifactError):
            _art(content="salary is 50000", forbidden_values=["50000"])

    def test_forbidden_short_value_ignored(self):
        # 1-3 char values are not treated as leaks (false-positive guard)
        _art(content="answer 7", forbidden_values=["7"])  # no raise

    def test_transport_dict_has_no_display_or_raw_ids(self):
        a = _art(scope_fingerprint="abc123def456")
        d = a.transport_dict()
        assert set(d) == {"schema_version", "generation_id", "request_id",
                          "canonical_content", "canonical_sha256",
                          "canonical_length", "status"}
        assert "scope_fingerprint" not in d  # fingerprint not transported
        assert "display" not in str(d).lower()


# ===========================================================================
# Transport gate
# ===========================================================================


class TestGate:
    def test_allowed_for_managed_webchat_enabled(self):
        assert canonical_artifact_transport_allowed(
            auth=_auth(internal_trusted=True),
            context=_webchat_ctx(), enabled=True) is True

    def test_denied_when_disabled(self):
        assert canonical_artifact_transport_allowed(
            auth=_auth(internal_trusted=True),
            context=_webchat_ctx(), enabled=False) is False

    def test_denied_without_internal_trust(self):
        # public context: not internal_trusted, channel api, client metadata
        assert canonical_artifact_transport_allowed(
            auth=_auth(), context=_public_ctx(), enabled=True) is False

    def test_denied_spoofed_channel_without_trust(self):
        # An attacker can't get the gate by faking channel — internal_trusted
        # on the auth is the real signal, and the context is built from auth.
        ctx = _public_ctx()
        assert canonical_artifact_transport_allowed(
            auth=_auth(), context=ctx, enabled=True) is False

    def test_denied_without_actor(self):
        ctx = build_dlp_request_context(
            auth=_auth(key_id=None, internal_trusted=True,
                       trusted_conversation_id="c1"),
            direction=ScanDirection.OUTPUT)
        assert canonical_artifact_transport_allowed(
            auth=_auth(internal_trusted=True), context=ctx, enabled=True) is False

    def test_denied_when_conversation_not_trusted_header(self):
        # internal_trusted but conversation came from client metadata
        a = _auth(internal_trusted=True)  # no trusted_conversation_id
        ctx = build_dlp_request_context(auth=a, direction=ScanDirection.OUTPUT,
                                        client_conversation_id="c1")
        assert canonical_artifact_transport_allowed(
            auth=a, context=ctx, enabled=True) is False


# ===========================================================================
# Shadow report + log hygiene
# ===========================================================================


class TestShadowAndLogs:
    def test_shadow_report_no_text(self):
        a = _art(content="SENTINEL_SECRET <r0>")
        rep = canonical_artifact_shadow_report(a, display_length=10, diverged=True)
        assert "SENTINEL_SECRET" not in str(rep)
        assert rep["artifact_created"] is True
        assert rep["canonical_contains_tokens"] is True
        assert rep["canonical_length"] == len("SENTINEL_SECRET <r0>")
        assert rep["status"] == STATUS_COMPLETE

    def test_shadow_report_none(self):
        assert canonical_artifact_shadow_report(
            None, display_length=0, diverged=False) == {"artifact_created": False}

    def test_safe_exception_fields_no_raw_message(self):
        exc = RuntimeError("failed for SECRET-SENTINEL-0812345678")
        f = safe_exception_fields(exc, "canonical_stream")
        assert f == {"error_code": "canonical_stream", "exception_type": "RuntimeError"}
        assert "SENTINEL" not in str(f)


# ===========================================================================
# Non-stream orchestrator integration
# ===========================================================================


def _response(content="ผลลัพธ์ 50000"):
    from app.schemas.public import ChatCompletionsResponse, ChatCompletionsChoice
    from app.schemas.common import Message
    from app.schemas.public import UsageInfo
    return ChatCompletionsResponse(
        id="req_abc", model="m", provider="p",
        choices=[ChatCompletionsChoice(index=0,
                 message=Message(role="assistant", content=content))],
        usage=UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class TestNonStreamIntegration:
    def _orch(self):
        from app.services.orchestrator import GatewayOrchestrator
        return GatewayOrchestrator()

    def _run(self, monkeypatch, *, enabled, shadow, ctx, auth, content="ผลลัพธ์ <r0>",
             forbidden=("50000",)):
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_enabled", enabled)
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_shadow", shadow)
        out_meta: dict = {}
        self._orch()._produce_canonical_artifact_nonstream(
            request_id="req_abc", canonical_content=content,
            context=ctx, auth=auth,
            span_map={"<r0>": forbidden[0]}, restore_allowed_tokens={"<r0>"},
            out_meta=out_meta)
        return out_meta

    def test_disabled_no_envelope(self, monkeypatch):
        out = self._run(monkeypatch, enabled=False, shadow=False,
                        ctx=_webchat_ctx(), auth=_auth(internal_trusted=True))
        assert "canonical_artifact" not in out

    def test_shadow_no_envelope(self, monkeypatch):
        out = self._run(monkeypatch, enabled=False, shadow=True,
                        ctx=_webchat_ctx(), auth=_auth(internal_trusted=True))
        assert "canonical_artifact" not in out  # shadow never transports

    def test_enabled_managed_sets_envelope(self, monkeypatch):
        out = self._run(monkeypatch, enabled=True, shadow=True,
                        ctx=_webchat_ctx(), auth=_auth(internal_trusted=True))
        env = out["canonical_artifact"]
        assert env["status"] == STATUS_COMPLETE
        assert env["canonical_content"] == "ผลลัพธ์ <r0>"
        assert "50000" not in env["canonical_content"]

    def test_enabled_public_no_envelope(self, monkeypatch):
        out = self._run(monkeypatch, enabled=True, shadow=True,
                        ctx=_public_ctx(), auth=_auth())  # not internal-trusted
        assert "canonical_artifact" not in out

    def test_build_failure_yields_unavailable_envelope(self, monkeypatch):
        # forbidden value present in canonical → ArtifactError → unavailable.
        out = self._run(monkeypatch, enabled=True, shadow=True,
                        ctx=_webchat_ctx(), auth=_auth(internal_trusted=True),
                        content="ผลลัพธ์ 50000")  # contains restored raw
        env = out["canonical_artifact"]
        assert env["status"] == "unavailable"
        assert "error_code" in env and "canonical_content" not in env


# ===========================================================================
# Streaming frame builders (capability / terminal / unavailable)
# ===========================================================================


class TestStreamFrame:
    def _terminal(self, monkeypatch, *, enabled, shadow, ctx, auth,
                  content="ผลลัพธ์ <r0>"):
        from app.api.routes.public_chat import _build_canonical_terminal_frame
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_enabled", enabled)
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_shadow", shadow)
        return _build_canonical_terminal_frame(
            request_id="req_abc", canonical_content=content, context=ctx,
            auth=auth, span_map={"<r0>": "50000"}, restore_allowed_tokens={"<r0>"},
            display_length=12)

    def test_shadow_terminal_none(self, monkeypatch):
        assert self._terminal(monkeypatch, enabled=False, shadow=True,
                              ctx=_webchat_ctx(), auth=_auth(internal_trusted=True)) is None

    def test_managed_terminal_final(self, monkeypatch):
        out = self._terminal(monkeypatch, enabled=True, shadow=True,
                             ctx=_webchat_ctx(), auth=_auth(internal_trusted=True))
        assert out.startswith(f"event: {CANONICAL_FINAL_EVENT}\n")
        assert "<r0>" in out and "50000" not in out

    def test_managed_terminal_unavailable_on_failure(self, monkeypatch):
        from app.services.dlp.canonical_artifact import CANONICAL_UNAVAILABLE_EVENT
        out = self._terminal(monkeypatch, enabled=True, shadow=True,
                             ctx=_webchat_ctx(), auth=_auth(internal_trusted=True),
                             content="ผลลัพธ์ 50000")  # forbidden raw → unavailable
        assert out.startswith(f"event: {CANONICAL_UNAVAILABLE_EVENT}\n")
        assert "unavailable" in out and "50000" not in out

    def test_public_terminal_none(self, monkeypatch):
        assert self._terminal(monkeypatch, enabled=True, shadow=True,
                              ctx=_public_ctx(), auth=_auth()) is None

    def test_disabled_terminal_none(self, monkeypatch):
        assert self._terminal(monkeypatch, enabled=False, shadow=False,
                              ctx=_webchat_ctx(), auth=_auth(internal_trusted=True),
                              content="x") is None

    def test_capability_managed_only(self, monkeypatch):
        from app.api.routes.public_chat import _build_canonical_capability_frame
        from app.services.dlp.canonical_artifact import CANONICAL_CAPABILITY_EVENT
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_enabled", True)
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_shadow", True)
        managed = _build_canonical_capability_frame(
            request_id="req_abc", context=_webchat_ctx(),
            auth=_auth(internal_trusted=True))
        assert managed.startswith(f"event: {CANONICAL_CAPABILITY_EVENT}\n")
        assert "req_abc" in managed
        public = _build_canonical_capability_frame(
            request_id="req_abc", context=_public_ctx(), auth=_auth())
        assert public is None

    def test_capability_none_when_shadow_only(self, monkeypatch):
        from app.api.routes.public_chat import _build_canonical_capability_frame
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_enabled", False)
        monkeypatch.setattr(_cfg.settings, "dlp_canonical_artifact_shadow", True)
        assert _build_canonical_capability_frame(
            request_id="req_abc", context=_webchat_ctx(),
            auth=_auth(internal_trusted=True)) is None


# ===========================================================================
# F2 — public OpenAPI must NOT expose the internal extension
# ===========================================================================


class TestPublicSchemaIsolation:
    def test_openapi_has_no_x_sran_dlp(self):
        from app.main import app
        schema = app.openapi()
        props = (schema.get("components", {}).get("schemas", {})
                 .get("ChatCompletionsResponse", {}).get("properties", {}))
        assert "x_sran_dlp" not in props
        assert "canonical_content" not in props

    def test_response_model_has_no_field(self):
        from app.schemas.public import ChatCompletionsResponse
        assert "x_sran_dlp" not in ChatCompletionsResponse.model_fields
