"""Phase 1 — Central DLP Policy Engine tests.

Covers:
- Policy parity: the shared DLPPolicyEngine produces the SAME action as
  the legacy classifier+PolicyEvaluator seam, for DEFAULT/STRICT,
  inbound/outbound, across MASK/REDACT/BLOCK/RESTORE and secret handling.
- Shadow mode: default flags keep the legacy decision authoritative;
  enabling V2 makes it authoritative with identical actions + policy_v2
  metadata. Shadow divergence count is zero in Phase 1 (parity).
- Failure policy: a thrown evaluator falls back to a deterministic
  fail-closed decision (secret/credential → BLOCK, others → MASK, never
  ALLOW a detected finding).
- Request context: immutable, log-safe (no raw values), full SourceType.
- Channel/stream decision parity: the same findings decided with
  streaming=True vs False yield identical actions.

Unit lane only — no DB, no network, no app boot.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.domain.dlp import (
    DetectionMatch,
    DetectionResult,
    EnforcementAction,
    SensitivityLevel,
)
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.policy_engine import (
    CHANNEL_API,
    CHANNEL_WEBCHAT,
    POLICY_V2_METADATA_KEY,
    DLPPolicyEngine,
    DLPRequestContext,
    ScanDirection,
    SourceType,
)
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match(
    entity_type: str = "email_address",
    category: str = "pii",
    severity: SensitivityLevel = SensitivityLevel.MEDIUM,
    start: int = 0,
    end: int = 10,
    detector_name: str = "test",
) -> DetectionMatch:
    return DetectionMatch(
        detector_name=detector_name,
        entity_type=entity_type,
        category=category,
        start=start,
        end=end,
        matched_text="x" * (end - start),
        severity=severity,
    )


def _legacy_actions(matches, profile, direction):
    """Reproduce the legacy seam: classify then evaluate."""
    classifier = SensitivityClassifier()
    evaluator = PolicyEvaluator()
    classified = classifier.classify(list(matches)) if matches else []
    result = DetectionResult(matches=classified, scanned_direction=direction)
    decision = evaluator.evaluate(result, profile)
    return {
        (ma.match.detector_name, ma.match.start, ma.match.end): ma.action
        for ma in decision.match_actions
    }


def _engine_actions(matches, profile, direction, context=None):
    engine = DLPPolicyEngine()
    result = DetectionResult(matches=list(matches), scanned_direction=direction)
    decision = engine.evaluate_decision(
        detection_result=result, profile=profile, context=context
    )
    return {
        (ma.match.detector_name, ma.match.start, ma.match.end): ma.action
        for ma in decision.match_actions
    }


_DIVERSE_MATCHES = [
    _match("email_address", "pii", SensitivityLevel.MEDIUM, 0, 10, "email"),
    _match("credit_card_number", "financial", SensitivityLevel.HIGH, 10, 26, "credit_card"),
    _match("bearer_token", "secret", SensitivityLevel.CRITICAL, 26, 60, "bearer_token"),
    _match("anthropic_api_key", "credential", SensitivityLevel.CRITICAL, 60, 100, "api_key_secret"),
    _match("thai_national_id", "identity", SensitivityLevel.HIGH, 100, 113, "thai_national_id"),
    _match("ipv4_address", "network", SensitivityLevel.MEDIUM, 113, 126, "ip_address"),
]


# ---------------------------------------------------------------------------
# Policy parity — engine == legacy
# ---------------------------------------------------------------------------


class TestPolicyParity:
    @pytest.mark.parametrize("profile", [DEFAULT_PROFILE, STRICT_PROFILE])
    @pytest.mark.parametrize("direction", ["input", "output"])
    def test_engine_matches_legacy(self, profile, direction):
        legacy = _legacy_actions(_DIVERSE_MATCHES, profile, direction)
        engine = _engine_actions(_DIVERSE_MATCHES, profile, direction)
        assert engine == legacy, (
            f"parity break profile={profile.profile_id} dir={direction}: "
            f"legacy={legacy} engine={engine}"
        )

    def test_default_inbound_pii_masks(self):
        actions = _engine_actions([_match()], DEFAULT_PROFILE, "input")
        assert set(actions.values()) == {EnforcementAction.MASK}

    def test_strict_inbound_financial_blocks(self):
        m = _match("credit_card_number", "financial", SensitivityLevel.HIGH)
        actions = _engine_actions([m], STRICT_PROFILE, "input")
        assert set(actions.values()) == {EnforcementAction.BLOCK}

    def test_default_outbound_pii_restores(self):
        actions = _engine_actions([_match()], DEFAULT_PROFILE, "output")
        assert set(actions.values()) == {EnforcementAction.RESTORE}

    def test_default_outbound_secret_never_restores(self):
        # credential + secret on the OUTPUT direction must be MASK, never
        # RESTORE — the engine must preserve the legacy anti-echo posture.
        secret = _match("bearer_token", "secret", SensitivityLevel.CRITICAL, 0, 30, "bearer_token")
        cred = _match("anthropic_api_key", "credential", SensitivityLevel.CRITICAL, 30, 70, "api_key_secret")
        actions = _engine_actions([secret, cred], DEFAULT_PROFILE, "output")
        for action in actions.values():
            assert action != EnforcementAction.RESTORE
            assert action == EnforcementAction.MASK

    def test_empty_findings_allow(self):
        engine = DLPPolicyEngine()
        result = DetectionResult(matches=[], scanned_direction="input")
        decision = engine.evaluate_decision(
            detection_result=result, profile=DEFAULT_PROFILE
        )
        assert decision.overall_action == EnforcementAction.ALLOW
        assert decision.match_actions == []

    def test_classify_side_effect_preserved(self):
        # The legacy seam wrote classified (escalated) matches back onto
        # detection_result.matches. The engine must keep that behavior.
        matches = [
            _match("email_address", "pii", SensitivityLevel.MEDIUM, 0, 10, "email"),
            _match("credit_card_number", "financial", SensitivityLevel.HIGH, 10, 26, "credit_card"),
        ]
        result = DetectionResult(matches=matches, scanned_direction="input")
        DLPPolicyEngine().evaluate_decision(
            detection_result=result, profile=DEFAULT_PROFILE
        )
        # cross-category escalation raises the MEDIUM email to HIGH
        sev = {m.detector_name: m.severity for m in result.matches}
        assert sev["email"] == SensitivityLevel.HIGH


# ---------------------------------------------------------------------------
# Shadow mode
# ---------------------------------------------------------------------------


class TestShadowMode:
    def test_default_flags_use_legacy(self, monkeypatch):
        monkeypatch.setattr(settings, "dlp_policy_engine_v2_enabled", False)
        monkeypatch.setattr(settings, "dlp_policy_engine_v2_shadow", True)
        engine = DLPPolicyEngine()
        res = engine.evaluate(
            text="",
            findings=[_match()],
            direction="input",
            context=DLPRequestContext(),
            profile=DEFAULT_PROFILE,
        )
        assert res.engine_version == "legacy"
        assert res.shadow_divergences == 0
        assert res.decision.overall_action == EnforcementAction.MASK

    def test_v2_enabled_authoritative_and_parity(self, monkeypatch):
        monkeypatch.setattr(settings, "dlp_policy_engine_v2_enabled", True)
        monkeypatch.setattr(settings, "dlp_policy_engine_v2_shadow", True)
        engine = DLPPolicyEngine()
        res = engine.evaluate(
            text="",
            findings=list(_DIVERSE_MATCHES),
            direction="input",
            context=DLPRequestContext(channel=CHANNEL_API),
            profile=DEFAULT_PROFILE,
        )
        assert res.engine_version == "v2"
        # Parity: V2 actions equal legacy for the same fixtures.
        legacy = _legacy_actions(_DIVERSE_MATCHES, DEFAULT_PROFILE, "input")
        v2 = {
            (ma.match.detector_name, ma.match.start, ma.match.end): ma.action
            for ma in res.decision.match_actions
        }
        assert v2 == legacy
        assert res.shadow_divergences == 0

    def test_v2_stamps_policy_metadata(self, monkeypatch):
        monkeypatch.setattr(settings, "dlp_policy_engine_v2_enabled", True)
        engine = DLPPolicyEngine()
        ctx = DLPRequestContext(
            channel=CHANNEL_WEBCHAT,
            source_type=SourceType.TOOL_ARGUMENT,
            direction=ScanDirection.OUTPUT,
        )
        res = engine.evaluate(
            text="",
            findings=[_match()],
            direction="output",
            context=ctx,
            profile=DEFAULT_PROFILE,
        )
        ma = res.decision.match_actions[0]
        meta = (ma.match.metadata or {}).get(POLICY_V2_METADATA_KEY)
        assert meta is not None
        assert meta["source_type"] == "tool_argument"
        assert meta["channel"] == CHANNEL_WEBCHAT
        assert meta["direction"] == "output"
        assert "rule_id" in meta and "decision_reason" in meta

    def test_legacy_path_does_not_stamp_metadata(self, monkeypatch):
        # Default (shadow only) → legacy decision returned; forward matches
        # must NOT carry policy_v2 metadata (zero behavior change).
        monkeypatch.setattr(settings, "dlp_policy_engine_v2_enabled", False)
        engine = DLPPolicyEngine()
        res = engine.evaluate(
            text="",
            findings=[_match()],
            direction="input",
            context=DLPRequestContext(),
            profile=DEFAULT_PROFILE,
        )
        ma = res.decision.match_actions[0]
        assert (ma.match.metadata or {}).get(POLICY_V2_METADATA_KEY) is None


# ---------------------------------------------------------------------------
# Failure policy (deterministic fail-closed)
# ---------------------------------------------------------------------------


class TestFailurePolicy:
    def test_failsafe_blocks_secrets_masks_others(self, monkeypatch):
        engine = DLPPolicyEngine()

        def _boom(*_a, **_k):
            raise RuntimeError("evaluator exploded")

        # Force the internal evaluator to throw.
        monkeypatch.setattr(engine._evaluator, "evaluate", _boom)

        res = engine.evaluate(
            text="",
            findings=list(_DIVERSE_MATCHES),
            direction="input",
            context=DLPRequestContext(),
            profile=DEFAULT_PROFILE,
        )
        assert res.used_failsafe is True
        by_cat = {
            ma.match.category: ma.action for ma in res.decision.match_actions
        }
        assert by_cat["secret"] == EnforcementAction.BLOCK
        assert by_cat["credential"] == EnforcementAction.BLOCK
        assert by_cat["pii"] == EnforcementAction.MASK
        assert by_cat["financial"] == EnforcementAction.MASK
        # Never ALLOW a detected finding (no raw pass-through).
        assert EnforcementAction.ALLOW not in by_cat.values()

    def test_failsafe_empty_findings_is_allow(self, monkeypatch):
        engine = DLPPolicyEngine()
        monkeypatch.setattr(
            engine._classifier,
            "classify",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")),
        )
        res = engine.evaluate(
            text="",
            findings=[],
            direction="input",
            context=DLPRequestContext(),
            profile=DEFAULT_PROFILE,
        )
        # No findings → nothing to block; no PII present so no leak.
        assert res.decision.overall_action == EnforcementAction.ALLOW


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------


class TestRequestContext:
    def test_frozen(self):
        ctx = DLPRequestContext()
        with pytest.raises(Exception):
            ctx.tenant_id = "x"  # type: ignore[misc]

    def test_safe_log_dict_keys(self):
        ctx = DLPRequestContext(
            tenant_id="t1",
            user_id="u1",
            project_id="p1",
            key_id="k1",
            actor_id="a1",
            conversation_id="c1",
            source_type=SourceType.WEBCHAT,
            channel=CHANNEL_WEBCHAT,
            streaming=True,
            direction=ScanDirection.OUTPUT,
        )
        d = ctx.safe_log_dict()
        assert d["source_type"] == "webchat"
        assert d["channel"] == CHANNEL_WEBCHAT
        assert d["streaming"] is True
        assert d["direction"] == "output"
        # Identifier fields are HASHED (never raw) in logs.
        assert d["tenant_id"] != "t1" and d["tenant_id"] is not None
        assert d["actor_id"] != "a1" and d["actor_id"] is not None
        assert d["conversation_id"] != "c1" and d["conversation_id"] is not None
        assert set(d.keys()) == {
            "tenant_id", "project_id", "key_id", "actor_id",
            "conversation_id", "conversation_id_source", "message_role",
            "source_type", "channel", "streaming", "provider", "model",
            "direction",
        }

    def test_all_source_types_present(self):
        expected = {
            "direct_api", "webchat", "tool_argument", "tool_result",
            "memory", "rag", "file", "reasoning", "system_message",
        }
        assert {s.value for s in SourceType} == expected


# ---------------------------------------------------------------------------
# Channel / stream decision parity
# ---------------------------------------------------------------------------


class TestChannelStreamParity:
    @pytest.mark.parametrize("profile", [DEFAULT_PROFILE, STRICT_PROFILE])
    @pytest.mark.parametrize("direction", ["input", "output"])
    def test_stream_vs_nonstream_same_decision(self, profile, direction):
        api_ctx = DLPRequestContext(
            channel=CHANNEL_API, streaming=False, source_type=SourceType.DIRECT_API
        )
        web_stream_ctx = DLPRequestContext(
            channel=CHANNEL_WEBCHAT, streaming=True, source_type=SourceType.WEBCHAT
        )
        a = _engine_actions(_DIVERSE_MATCHES, profile, direction, api_ctx)
        b = _engine_actions(_DIVERSE_MATCHES, profile, direction, web_stream_ctx)
        assert a == b
