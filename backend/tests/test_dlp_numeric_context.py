"""Phase 2A — Smart Numeric Masking tests.

Exercises the NumericContextAnalyzer through the shared DLPPolicyEngine
V2 path (the same path all 4 chat channels converge on), asserting:
- general numbers ALLOW; strong identifiers MASK even with math context;
- OTP/PIN/numeric-password REDACT (never restore); confidential MASK;
- public statistics not masked;
- conflict precedence (most-protective wins);
- channel parity (V2 decision identical across API/WebChat × stream/non);
- shadow purity (V2 off → decision == legacy, zero change);
- no raw numeric value ever lands in policy_v2 metadata.
"""

from __future__ import annotations

import json

import pytest

import app.core.config as cfg
from app.domain.dlp import DetectionResult, EnforcementAction
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.policy_engine import (
    CHANNEL_API,
    CHANNEL_WEBCHAT,
    POLICY_V2_METADATA_KEY,
    DLPPolicyEngine,
    DLPRequestContext,
    ScanDirection,
    SourceType,
)
from app.services.policy.profiles import DEFAULT_PROFILE

_REGISTRY = get_default_registry(ner_enabled=False)
_ENGINE = DLPPolicyEngine()

LUHN_CARD = "4111111111111111"  # standard Luhn-valid Visa test number


def valid_thai_id() -> str:
    base = [1, 1, 0, 1, 7, 0, 0, 2, 3, 0, 7, 0]
    w = [13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2]
    check = (11 - sum(d * wi for d, wi in zip(base, w)) % 11) % 10
    return "".join(map(str, base)) + str(check)


@pytest.fixture
def v2_on(monkeypatch):
    """Make V2 the authoritative decision for direct assertion (does NOT
    touch .env; only this test process's settings object)."""
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", True)
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", True)


def _decide(text, *, direction="input", context=None, profile=DEFAULT_PROFILE):
    matches = _REGISTRY.scan_text(text)
    dr = DetectionResult(matches=matches, scanned_direction=direction)
    return _ENGINE.evaluate_decision(
        detection_result=dr, profile=profile, context=context, text=text
    )


def _classes(decision):
    return {
        (ma.match.metadata or {}).get(POLICY_V2_METADATA_KEY, {}).get("numeric_class")
        for ma in decision.match_actions
    }


# ---------------------------------------------------------------------------
# General numeric → ALLOW
# ---------------------------------------------------------------------------


class TestGeneralNumeric:
    @pytest.mark.parametrize("text", [
        "123 + 456",
        "1234567890123 + 2",          # 13 digits, fails ID checksum
        "ปี พ.ศ. 2568",
        "ประชากร 70 ล้านคน",
        "จำนวนสินค้า 500 ชิ้น",
        "ระยะทาง 120 กิโลเมตร",
        "อัตราเติบโต 25%",
        "เลขหน้า 123",
        "สมการ 987654321 คูณ 2",
    ])
    def test_general_allows(self, v2_on, text):
        assert _decide(text).overall_action == EnforcementAction.ALLOW


# ---------------------------------------------------------------------------
# Strong identifiers → MASK (math never relaxes)
# ---------------------------------------------------------------------------


class TestStrongIdentifiers:
    def test_luhn_card_with_multiply_masks(self, v2_on):
        d = _decide(f"บัตร {LUHN_CARD} คูณ 1")
        assert d.overall_action == EnforcementAction.MASK
        assert "STRONG_IDENTIFIER" in _classes(d)

    def test_thai_id_with_calc_masks(self, v2_on):
        d = _decide(f"คำนวณ {valid_thai_id()} + 1")
        assert d.overall_action == EnforcementAction.MASK

    def test_phone_keyword_masks(self, v2_on):
        assert _decide("โทร 0812345678").overall_action == EnforcementAction.MASK

    def test_bank_account_keyword_masks(self, v2_on):
        assert _decide("เลขบัญชี 1234567890").overall_action == EnforcementAction.MASK

    def test_promptpay_masks(self, v2_on):
        assert _decide("พร้อมเพย์ 0812345678").overall_action == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# Weak keyword-free phone false positive
# ---------------------------------------------------------------------------


class TestWeakPhone:
    def test_keyword_free_landline_in_math_suppressed(self, v2_on):
        # 9-digit 02-prefixed landline shape, no phone keyword, math context.
        assert _decide("คำนวณ 028001234 + 5").overall_action == EnforcementAction.ALLOW

    def test_same_number_with_phone_keyword_masks(self, v2_on):
        assert _decide("โทร 028001234").overall_action == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# Confidential numeric → MASK; public → ALLOW
# ---------------------------------------------------------------------------


class TestConfidential:
    @pytest.mark.parametrize("text", [
        "เงินเดือน 50000",
        "โบนัสของฉัน 30000",
        "งบประมาณภายในบริษัท 5000000 บาท",
        "ยอดขายบริษัทเรา 100 ล้านบาท",
    ])
    def test_confidential_masks(self, v2_on, text):
        d = _decide(text)
        assert d.overall_action == EnforcementAction.MASK
        assert "CONFIDENTIAL_NUMERIC" in _classes(d)

    @pytest.mark.parametrize("text", [
        "งบประมาณแผ่นดิน 3700000 ล้านบาท",
        "รายได้ Apple ตามรายงานประจำปี 80000 ล้าน",
        "งบประมาณ 5000000 บาท",   # ambiguous, no internal/private evidence → ALLOW (conservative)
    ])
    def test_public_or_ambiguous_allows(self, v2_on, text):
        assert _decide(text).overall_action == EnforcementAction.ALLOW


# ---------------------------------------------------------------------------
# Secret numeric → REDACT (never restore); unlabelled → ALLOW
# ---------------------------------------------------------------------------


class TestSecretNumeric:
    @pytest.mark.parametrize("text", [
        "OTP 123456",
        "PIN 1234",
        "รหัสผ่าน 1234",
        "verification code 998877",
    ])
    def test_secret_redacts(self, v2_on, text):
        d = _decide(text)
        assert d.overall_action == EnforcementAction.REDACT
        assert "SECRET_NUMERIC" in _classes(d)

    def test_math_does_not_override_secret(self, v2_on):
        assert _decide("คำนวณ OTP 123456 + 1").overall_action == EnforcementAction.REDACT

    @pytest.mark.parametrize("text", ["เลข 1234", "จำนวนสินค้า 1234"])
    def test_unlabelled_short_number_allows(self, v2_on, text):
        assert _decide(text).overall_action == EnforcementAction.ALLOW


# ---------------------------------------------------------------------------
# Conflict precedence — most protective wins
# ---------------------------------------------------------------------------


class TestConflicts:
    def test_secret_plus_math_secret_wins(self, v2_on):
        assert _decide("OTP 123456 + 100").overall_action == EnforcementAction.REDACT

    def test_card_plus_math_card_wins(self, v2_on):
        assert _decide(f"{LUHN_CARD} x 2").overall_action == EnforcementAction.MASK

    def test_thai_id_plus_unit_id_wins(self, v2_on):
        assert _decide(f"{valid_thai_id()} จำนวน 5 ชิ้น").overall_action == EnforcementAction.MASK

    def test_confidential_plus_percent_confidential_wins(self, v2_on):
        d = _decide("เงินเดือน 50000 เพิ่ม 25%")
        assert d.overall_action == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# Channel parity
# ---------------------------------------------------------------------------


class TestChannelParity:
    @pytest.mark.parametrize("text", [
        "เงินเดือน 50000", "OTP 123456", f"บัตร {LUHN_CARD}", "123 + 456",
    ])
    def test_decision_identical_across_channels(self, v2_on, text):
        api_ns = DLPRequestContext(channel=CHANNEL_API, streaming=False, source_type=SourceType.DIRECT_API)
        api_s = DLPRequestContext(channel=CHANNEL_API, streaming=True, source_type=SourceType.DIRECT_API)
        web_ns = DLPRequestContext(channel=CHANNEL_WEBCHAT, streaming=False, source_type=SourceType.WEBCHAT)
        web_s = DLPRequestContext(channel=CHANNEL_WEBCHAT, streaming=True, source_type=SourceType.WEBCHAT)
        actions = {
            _decide(text, context=c).overall_action
            for c in (api_ns, api_s, web_ns, web_s)
        }
        classes = [
            tuple(sorted(str(x) for x in _classes(_decide(text, context=c))))
            for c in (api_ns, api_s, web_ns, web_s)
        ]
        assert len(actions) == 1, f"action differs across channels: {actions}"
        assert len(set(classes)) == 1, f"class differs across channels: {classes}"


# ---------------------------------------------------------------------------
# Shadow purity — V2 off → no change
# ---------------------------------------------------------------------------


class TestShadowPurity:
    @pytest.mark.parametrize("text", [
        "เงินเดือน 50000", "OTP 123456", "คำนวณ 028001234 + 5", "123 + 456",
    ])
    def test_shadow_decision_equals_legacy(self, monkeypatch, text):
        # Default flags: shadow on, V2 NOT authoritative.
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", False)
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", True)
        matches = _REGISTRY.scan_text(text)
        # Legacy decision = classifier + evaluator, no numeric layer.
        from app.services.dlp.classifier import SensitivityClassifier
        from app.services.policy.evaluator import PolicyEvaluator
        legacy_matches = SensitivityClassifier().classify(list(matches))
        legacy = PolicyEvaluator().evaluate(
            DetectionResult(matches=legacy_matches, scanned_direction="input"), DEFAULT_PROFILE
        )
        shadow = _decide(text)  # V2 disabled → returns legacy decision
        assert shadow.overall_action == legacy.overall_action


# ---------------------------------------------------------------------------
# Privacy — no raw numeric values in metadata
# ---------------------------------------------------------------------------


class TestNoRawValues:
    @pytest.mark.parametrize("text,value", [
        ("เงินเดือน 50000", "50000"),
        ("OTP 123456", "123456"),
        ("งบประมาณภายในบริษัท 5000000 บาท", "5000000"),
    ])
    def test_metadata_has_no_numeric_value(self, v2_on, text, value):
        d = _decide(text)
        for ma in d.match_actions:
            meta = (ma.match.metadata or {}).get(POLICY_V2_METADATA_KEY, {})
            assert value not in json.dumps(meta, ensure_ascii=False), (
                f"raw value {value} leaked into policy_v2 metadata: {meta}"
            )
