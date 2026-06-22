"""Tests for the MEDIA_GEN DLP profile (Sprint 1).

Verifies the new profile passes identity-class PII through while hard
BLOCKing secrets/credentials/financial, and that adding it does NOT
change DEFAULT profile behavior (identity still MASKs).
"""

from app.domain.dlp import (
    DetectionMatch,
    DetectionResult,
    EnforcementAction,
    SensitivityLevel,
)
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import (
    DEFAULT_PROFILE,
    MEDIA_GEN_PROFILE,
    get_profile,
)


def _match(category: str, severity: SensitivityLevel = SensitivityLevel.HIGH):
    return DetectionMatch(
        detector_name=category,
        entity_type=category,
        category=category,
        start=0,
        end=1,
        matched_text="x",
        severity=severity,
    )


def _result(*categories: str) -> DetectionResult:
    return DetectionResult(
        matches=[_match(c) for c in categories], scanned_direction="input"
    )


def test_media_gen_profile_registered():
    assert get_profile("media_gen") is MEDIA_GEN_PROFILE


def test_media_gen_category_actions():
    ca = MEDIA_GEN_PROFILE.category_actions
    # Identity-class → PASS (ALLOW)
    assert ca["pii"] == EnforcementAction.ALLOW
    assert ca["identity"] == EnforcementAction.ALLOW
    assert ca["network"] == EnforcementAction.ALLOW
    # Secret-class → BLOCK
    assert ca["credential"] == EnforcementAction.BLOCK
    assert ca["secret"] == EnforcementAction.BLOCK
    assert ca["financial"] == EnforcementAction.BLOCK


def test_media_gen_passes_identity_blocks_secret():
    """An email (pii) passes; an api key (credential) blocks; overall BLOCK."""
    ev = PolicyEvaluator()
    decision = ev.evaluate(_result("pii", "credential"), MEDIA_GEN_PROFILE)
    by_cat = {ma.match.category: ma.action for ma in decision.match_actions}
    assert by_cat["pii"] == EnforcementAction.ALLOW
    assert by_cat["credential"] == EnforcementAction.BLOCK
    assert decision.is_blocked is True  # overall = max(actions) = BLOCK


def test_media_gen_critical_identity_still_passes():
    """A CRITICAL identity finding (e.g. national id) must still PASS —
    the explicit ALLOW beats severity_threshold_block."""
    ev = PolicyEvaluator()
    result = DetectionResult(
        matches=[_match("identity", SensitivityLevel.CRITICAL)],
        scanned_direction="input",
    )
    decision = ev.evaluate(result, MEDIA_GEN_PROFILE)
    assert decision.match_actions[0].action == EnforcementAction.ALLOW
    assert decision.is_blocked is False


def test_default_profile_unchanged_still_masks_identity():
    """Adding MEDIA_GEN must not alter DEFAULT: identity/pii/credential MASK."""
    ev = PolicyEvaluator()
    decision = ev.evaluate(_result("pii", "identity", "credential"), DEFAULT_PROFILE)
    by_cat = {ma.match.category: ma.action for ma in decision.match_actions}
    assert by_cat["pii"] == EnforcementAction.MASK
    assert by_cat["identity"] == EnforcementAction.MASK
    assert by_cat["credential"] == EnforcementAction.MASK
    assert decision.is_blocked is False
