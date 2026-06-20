"""Phase 2A.1 — Smart Numeric authoritative-safety hardening tests.

Verifies (all V2-enabled simulated in-process; .env NEVER touched):
- per-message masking (post-transformer), no cross-message context/dedup;
- shadow vs enabled analyzer-exception semantics;
- direction scoping; pseudonym income force-mask; precision precedence;
- explicit action precedence; oversized fail-closed.
"""

from __future__ import annotations

import pytest

import app.core.config as cfg
from app.domain.dlp import DetectionResult, EnforcementAction
from app.schemas.common import Message
from app.services.dlp.detectors.registry import get_default_registry
import app.services.dlp.policy_engine as PE
from app.services.dlp.policy_engine import (
    DLPPolicyEngine,
    DLPRequestContext,
    ScanDirection,
    SourceType,
    most_protective_action,
)
from app.services.dlp.numeric_context import NumericTextSegment
from app.services.dlp.transformer import DLPTransformer, MaskBindingError
from app.services.policy.profiles import DEFAULT_PROFILE

_REG = get_default_registry(ner_enabled=False)
_ENG = DLPPolicyEngine()
_TX = DLPTransformer()
LUHN = "4111111111111111"


@pytest.fixture
def v2_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", True)
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", False)


def _mask_multi(msgs, scan_system=False):
    M = [Message(role=r, content=c) for r, c in msgs]
    det = _REG.scan_messages(M, direction="input", scan_system_messages=scan_system)
    segs = [
        NumericTextSegment(message_index=i, role=r, text=c, source_type="direct_api")
        for i, (r, c) in enumerate(msgs)
        if (scan_system or r != "system")
    ]
    dec = _ENG.evaluate_decision(
        detection_result=det, profile=DEFAULT_PROFILE, segments=segs,
        context=DLPRequestContext(source_type=SourceType.DIRECT_API, direction=ScanDirection.INPUT),
    )
    out, _, _ = _TX.mask_messages(M, dec.match_actions)
    return [m.content for m in out]


# ---------------------------------------------------------------------------
# Multi-message Cases A–F (post-transformer)
# ---------------------------------------------------------------------------


class TestMultiMessage:
    def test_A_otp_second_message_redacted(self, v2_on):
        out = _mask_multi([("user", "สวัสดี"), ("user", "OTP 123456")])
        assert out[0] == "สวัสดี"
        assert "123456" not in out[1] and "[REDACTED]" in out[1]

    def test_B_salary_msg1_only(self, v2_on):
        out = _mask_multi([("user", "เงินเดือน 50000"), ("user", "จำนวนสินค้า 50000")])
        assert "50000" not in out[0]            # masked (force_mask beats income bypass)
        assert out[1] == "จำนวนสินค้า 50000"     # quantity untouched

    def test_C_weak_phone_suppressed_no_bleed(self, v2_on):
        out = _mask_multi([("user", "คำนวณ 028001234 + 5"), ("user", "โทร 028001234")])
        assert out[0] == "คำนวณ 028001234 + 5"   # msg1 weak phone suppressed
        assert "028001234" not in out[1]          # msg2 phone masked

    def test_D_no_cross_message_confidential_system_excluded(self, v2_on):
        out = _mask_multi([("system", "เงินเดือน"), ("user", "50000")], scan_system=False)
        assert out == ["เงินเดือน", "50000"]      # no cross-message; 50000 not masked

    def test_E_duplicate_value_independent(self, v2_on):
        out = _mask_multi([("user", "OTP 123456"), ("user", "OTP 123456")])
        assert out[0] == "OTP [REDACTED]" and out[1] == "OTP [REDACTED]"

    def test_F_internal_vs_public(self, v2_on):
        out = _mask_multi([
            ("user", "งบประมาณภายในบริษัท 5000000"),
            ("user", "งบประมาณแผ่นดิน 5000000"),
        ])
        assert "5000000" not in out[0]            # internal masked
        assert out[1] == "งบประมาณแผ่นดิน 5000000"  # public allowed


# ---------------------------------------------------------------------------
# Shadow vs enabled analyzer-exception purity
# ---------------------------------------------------------------------------


class _Boom:
    def analyze(self, *a, **k):
        raise RuntimeError("analyzer boom")


def _decide(text, *, profile=DEFAULT_PROFILE, direction="input"):
    dr = DetectionResult(matches=_REG.scan_text(text), scanned_direction=direction)
    return _ENG.evaluate_decision(detection_result=dr, profile=profile, text=text)


class TestExceptionPurity:
    def test_shadow_exception_keeps_legacy(self, monkeypatch):
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", False)
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", True)
        # Legacy decision for a card → MASK; analyzer boom must NOT change it.
        legacy = _decide(f"card {LUHN}").overall_action
        monkeypatch.setattr(PE, "numeric_context_analyzer", _Boom())
        after = _decide(f"card {LUHN}").overall_action
        assert after == legacy  # shadow exception → legacy unchanged

    def test_enabled_exception_with_digits_fail_closed(self, monkeypatch):
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", True)
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", False)
        monkeypatch.setattr(PE, "numeric_context_analyzer", _Boom())
        # Numeric content + V2 failure → BLOCK (no raw pass-through).
        assert _decide("my secret code is 123456").overall_action == EnforcementAction.BLOCK

    def test_enabled_exception_no_digits_uses_legacy(self, monkeypatch):
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", True)
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", False)
        monkeypatch.setattr(PE, "numeric_context_analyzer", _Boom())
        # No digits → legacy decision is safe (ALLOW for plain text).
        assert _decide("hello world no numbers").overall_action == EnforcementAction.ALLOW


# ---------------------------------------------------------------------------
# Direction scoping
# ---------------------------------------------------------------------------


def _decide_dir(text, direction, source_type=SourceType.DIRECT_API):
    dr = DetectionResult(matches=_REG.scan_text(text), scanned_direction=direction)
    return _ENG.evaluate_decision(
        detection_result=dr, profile=DEFAULT_PROFILE, text=text,
        context=DLPRequestContext(source_type=source_type, direction=ScanDirection.coerce(direction)),
    )


class TestDirectionScope:
    def test_confidential_inbound_masks(self, v2_on):
        assert _decide_dir("เงินเดือนของฉัน 50000", "input").overall_action == EnforcementAction.MASK

    def test_confidential_outbound_not_supplemented(self, v2_on):
        # Outbound: no confidential supplemental → ALLOW (no income detector here).
        assert _decide_dir("ยอดขายบริษัทเรา 100 ล้าน", "output").overall_action == EnforcementAction.ALLOW

    def test_secret_outbound_redacts(self, v2_on):
        assert _decide_dir("OTP 123456", "output").overall_action == EnforcementAction.REDACT

    def test_weak_phone_not_suppressed_outbound(self, v2_on):
        # Outbound weak-landline must NOT be suppressed (stays masked).
        assert _decide_dir("คำนวณ 028001234 + 5", "output").overall_action == EnforcementAction.MASK

    def test_weak_phone_suppressed_inbound(self, v2_on):
        assert _decide_dir("คำนวณ 028001234 + 5", "input").overall_action == EnforcementAction.ALLOW

    def test_tool_args_full_treatment(self, v2_on):
        d = _decide_dir("เงินเดือน 50000", "output", source_type=SourceType.TOOL_ARGUMENT)
        assert d.overall_action == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# Pseudonym income force-mask
# ---------------------------------------------------------------------------


class TestPseudonymForceMask:
    def test_enabled_masks_income_despite_bypass(self, v2_on, monkeypatch):
        monkeypatch.setattr(cfg.settings, "dlp_pseudonym_mode", True)
        out = _mask_multi([("user", "เงินเดือน 50000")])
        assert "50000" not in out[0]  # masked despite income bypass

    def test_shadow_keeps_legacy_pseudonym(self, monkeypatch):
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", False)
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", True)
        monkeypatch.setattr(cfg.settings, "dlp_pseudonym_mode", True)
        out = _mask_multi([("user", "เงินเดือน 50000")])
        # Legacy + pseudonym bypass → value survives (unchanged behavior).
        assert out[0] == "เงินเดือน 50000"


# ---------------------------------------------------------------------------
# Precision tuning
# ---------------------------------------------------------------------------


class TestPrecision:
    @pytest.mark.parametrize("text,expect", [
        ("เงินเดือนของฉัน 50000", EnforcementAction.MASK),
        ("อัตราเงินเดือนราชการที่ประกาศอย่างเป็นทางการ 50000", EnforcementAction.ALLOW),
        ("โบนัสของฉัน 30000", EnforcementAction.MASK),
        ("โบนัสประจำปีที่บริษัทประกาศต่อสาธารณะ 30000", EnforcementAction.ALLOW),
        ("งบประมาณภายในของรัฐบาลที่เผยแพร่แล้ว 5000000", EnforcementAction.ALLOW),
        ("เงินเดือนของฉันถูกเผยแพร่ 50000", EnforcementAction.MASK),  # personal ownership beats public
    ])
    def test_public_private_precedence(self, v2_on, text, expect):
        assert _decide(text).overall_action == expect

    def test_nearest_number_binding(self, v2_on):
        # Label far (>window) from the number → not bound → ALLOW.
        far = "เงินเดือน " + ("ก" * 60) + " 50000"
        assert _decide(far).overall_action == EnforcementAction.ALLOW


# ---------------------------------------------------------------------------
# Explicit action precedence
# ---------------------------------------------------------------------------


class TestActionPrecedence:
    def test_ranking(self):
        A = EnforcementAction
        assert most_protective_action([A.ALLOW, A.MASK]) == A.MASK
        assert most_protective_action([A.MASK, A.REDACT]) == A.REDACT
        assert most_protective_action([A.REDACT, A.BLOCK]) == A.BLOCK
        assert most_protective_action([A.ALLOW, A.RESTORE]) == A.RESTORE
        assert most_protective_action([]) == A.ALLOW

    def test_unknown_action_fails_closed(self):
        assert most_protective_action(["weird_action"]) == EnforcementAction.BLOCK


# ---------------------------------------------------------------------------
# Oversized input
# ---------------------------------------------------------------------------


class TestOversized:
    def test_enabled_oversize_fails_closed(self, v2_on):
        # Segment far over the ceiling with a secret at the end → must NOT
        # silently pass; authoritative mode fails closed (BLOCK).
        big = ("a " * 110_000) + " OTP 123456"  # > 200k chars
        dr = DetectionResult(matches=_REG.scan_text(big), scanned_direction="input")
        seg = [NumericTextSegment(message_index=0, role="user", text=big, source_type="direct_api")]
        dec = _ENG.evaluate_decision(
            detection_result=dr, profile=DEFAULT_PROFILE, segments=seg,
            context=DLPRequestContext(source_type=SourceType.DIRECT_API, direction=ScanDirection.INPUT),
        )
        assert dec.overall_action == EnforcementAction.BLOCK
