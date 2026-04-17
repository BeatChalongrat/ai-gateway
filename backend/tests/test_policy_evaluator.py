"""Tests for P3-05: Policy decision model and evaluator scaffold.

Verifies:
- PolicyProfile dataclass
- PolicyEvaluator per-match decision logic
- Severity threshold blocking
- Category-action mapping
- Fallback to ALLOW for unconfigured categories
- Reason codes and messages
- Empty detection result
- Built-in profiles
- get_profile() lookup with fallback
- EnforcementDecision overall_action escalation (via P3-01 computed property)
"""

import pytest

from app.domain.dlp import (
    DetectionMatch,
    DetectionResult,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.domain.policy import PolicyProfile
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import (
    DEFAULT_PROFILE,
    STRICT_PROFILE,
    get_profile,
)


# ---------------------------------------------------------------------------
# Match factory helpers
# ---------------------------------------------------------------------------


def _match(
    entity_type: str = "email_address",
    category: str = "pii",
    severity: SensitivityLevel = SensitivityLevel.MEDIUM,
) -> DetectionMatch:
    return DetectionMatch(
        detector_name="test",
        entity_type=entity_type,
        category=category,
        start=0,
        end=10,
        matched_text="x" * 10,
        severity=severity,
    )


def _result(*matches: DetectionMatch) -> DetectionResult:
    return DetectionResult(matches=list(matches))


# ---------------------------------------------------------------------------
# PolicyProfile dataclass
# ---------------------------------------------------------------------------


class TestPolicyProfile:
    def test_fields(self):
        p = DEFAULT_PROFILE
        assert p.profile_id == "default"
        assert p.name == "Default"
        assert isinstance(p.category_actions, dict)
        assert isinstance(p.scan_responses, bool)

    def test_frozen(self):
        with pytest.raises(AttributeError):
            DEFAULT_PROFILE.name = "Modified"  # type: ignore[misc]

    def test_custom_profile(self):
        p = PolicyProfile(
            profile_id="custom",
            name="Custom",
            category_actions={"pii": EnforcementAction.REDACT},
            severity_threshold_block=SensitivityLevel.HIGH,
            scan_responses=False,
        )
        assert p.category_actions["pii"] == EnforcementAction.REDACT
        assert p.severity_threshold_block == SensitivityLevel.HIGH
        assert p.scan_responses is False


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------


class TestBuiltInProfiles:
    def test_default_masks_pii(self):
        assert DEFAULT_PROFILE.category_actions.get("pii") == EnforcementAction.MASK

    def test_default_masks_secrets(self):
        # Default profile masks (does not block) the legacy "secret" category
        # so API-key-in-prompt mistakes don't hard-fail customer requests.
        assert DEFAULT_PROFILE.category_actions.get("secret") == EnforcementAction.MASK

    def test_default_no_severity_threshold(self):
        assert DEFAULT_PROFILE.severity_threshold_block is None

    def test_strict_blocks_financial(self):
        assert STRICT_PROFILE.category_actions.get("financial") == EnforcementAction.BLOCK

    def test_strict_has_severity_threshold(self):
        assert STRICT_PROFILE.severity_threshold_block == SensitivityLevel.CRITICAL

    def test_strict_scans_responses(self):
        assert STRICT_PROFILE.scan_responses is True


# ---------------------------------------------------------------------------
# get_profile() lookup
# ---------------------------------------------------------------------------


class TestGetProfile:
    def test_returns_default(self):
        p = get_profile("default")
        assert p is DEFAULT_PROFILE

    def test_returns_strict(self):
        p = get_profile("strict")
        assert p is STRICT_PROFILE

    def test_unknown_falls_back_to_default(self):
        p = get_profile("nonexistent-id")
        assert p is DEFAULT_PROFILE

    def test_empty_string_falls_back(self):
        p = get_profile("")
        assert p is DEFAULT_PROFILE


# ---------------------------------------------------------------------------
# PolicyEvaluator — empty input
# ---------------------------------------------------------------------------


class TestEvaluatorEmpty:
    evaluator = PolicyEvaluator()

    def test_no_findings_returns_allow(self):
        result = DetectionResult()
        decision = self.evaluator.evaluate(result, DEFAULT_PROFILE)
        assert decision.overall_action == EnforcementAction.ALLOW
        assert decision.match_actions == []
        assert decision.policy_profile_id == "default"
        assert len(decision.reasons) > 0

    def test_no_findings_not_blocked(self):
        result = DetectionResult()
        decision = self.evaluator.evaluate(result, DEFAULT_PROFILE)
        assert decision.is_blocked is False
        assert decision.requires_transformation is False


# ---------------------------------------------------------------------------
# PolicyEvaluator — category action mapping
# ---------------------------------------------------------------------------


class TestEvaluatorCategoryMapping:
    evaluator = PolicyEvaluator()

    def test_pii_masked_by_default_profile(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="pii")),
            DEFAULT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.MASK
        assert len(decision.match_actions) == 1
        assert decision.match_actions[0].action == EnforcementAction.MASK

    def test_secret_masked_by_default_profile(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="secret", severity=SensitivityLevel.CRITICAL)),
            DEFAULT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.MASK
        assert decision.is_blocked is False

    def test_secret_blocked_by_strict_profile(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="secret", severity=SensitivityLevel.CRITICAL)),
            STRICT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.BLOCK
        assert decision.is_blocked is True

    def test_unknown_category_allowed(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="unknown_cat")),
            DEFAULT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.ALLOW
        assert decision.match_actions[0].reason_code == "no_matching_rule"

    def test_financial_blocked_by_strict_profile(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="financial", severity=SensitivityLevel.CRITICAL)),
            STRICT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.BLOCK

    def test_custom_redact_action(self):
        profile = PolicyProfile(
            profile_id="redact-pii",
            name="Redact PII",
            category_actions={"pii": EnforcementAction.REDACT},
        )
        decision = self.evaluator.evaluate(
            _result(_match(category="pii")),
            profile,
        )
        assert decision.overall_action == EnforcementAction.REDACT
        assert decision.requires_transformation is True


# ---------------------------------------------------------------------------
# PolicyEvaluator — severity threshold
# ---------------------------------------------------------------------------


class TestEvaluatorSeverityThreshold:
    evaluator = PolicyEvaluator()

    def test_critical_finding_blocked_by_strict_threshold(self):
        """Strict profile blocks CRITICAL regardless of category action."""
        decision = self.evaluator.evaluate(
            _result(_match(category="pii", severity=SensitivityLevel.CRITICAL)),
            STRICT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.BLOCK
        assert decision.match_actions[0].reason_code == "severity_threshold"

    def test_high_finding_not_blocked_by_critical_threshold(self):
        """HIGH severity is below CRITICAL threshold → uses category rule."""
        decision = self.evaluator.evaluate(
            _result(_match(category="pii", severity=SensitivityLevel.HIGH)),
            STRICT_PROFILE,
        )
        # Falls through to category rule: pii → MASK
        assert decision.overall_action == EnforcementAction.MASK

    def test_no_threshold_means_no_severity_block(self):
        """Default profile has no threshold → CRITICAL pii is masked, not blocked."""
        decision = self.evaluator.evaluate(
            _result(_match(category="pii", severity=SensitivityLevel.CRITICAL)),
            DEFAULT_PROFILE,
        )
        # Category rule: pii → MASK (threshold is None, so severity check skipped)
        assert decision.overall_action == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# PolicyEvaluator — escalation and mixed findings
# ---------------------------------------------------------------------------


class TestEvaluatorEscalation:
    evaluator = PolicyEvaluator()

    def test_mixed_pii_and_secret_overall_masked_in_default(self):
        """Email (pii→mask) + AWS key (secret→mask) → overall MASK."""
        decision = self.evaluator.evaluate(
            _result(
                _match(category="pii"),
                _match(category="secret", severity=SensitivityLevel.CRITICAL),
            ),
            DEFAULT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.MASK
        assert decision.is_blocked is False
        actions = {ma.match.category: ma.action for ma in decision.match_actions}
        assert actions["pii"] == EnforcementAction.MASK
        assert actions["secret"] == EnforcementAction.MASK

    def test_mixed_pii_and_secret_overall_blocked_in_strict(self):
        """Strict still blocks on secret."""
        from app.services.policy.profiles import STRICT_PROFILE

        decision = self.evaluator.evaluate(
            _result(
                _match(category="pii"),
                _match(category="secret", severity=SensitivityLevel.CRITICAL),
            ),
            STRICT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.BLOCK
        assert decision.is_blocked is True

    def test_multiple_pii_all_masked(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="pii"), _match(category="pii")),
            DEFAULT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.MASK
        assert all(ma.action == EnforcementAction.MASK for ma in decision.match_actions)

    def test_allow_plus_mask_overall_mask(self):
        decision = self.evaluator.evaluate(
            _result(
                _match(category="unknown_cat"),  # → ALLOW
                _match(category="pii"),           # → MASK
            ),
            DEFAULT_PROFILE,
        )
        assert decision.overall_action == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# PolicyEvaluator — reason codes and messages
# ---------------------------------------------------------------------------


class TestEvaluatorReasons:
    evaluator = PolicyEvaluator()

    def test_category_action_has_reason_code(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="pii")),
            DEFAULT_PROFILE,
        )
        ma = decision.match_actions[0]
        assert ma.reason_code == "pii_mask_policy"
        assert "mask" in ma.reason_message.lower()
        assert "Default" in ma.reason_message

    def test_severity_threshold_has_reason_code(self):
        decision = self.evaluator.evaluate(
            _result(_match(severity=SensitivityLevel.CRITICAL)),
            STRICT_PROFILE,
        )
        ma = decision.match_actions[0]
        assert ma.reason_code == "severity_threshold"
        assert "CRITICAL" in ma.reason_message

    def test_fallback_has_reason_code(self):
        decision = self.evaluator.evaluate(
            _result(_match(category="other")),
            DEFAULT_PROFILE,
        )
        ma = decision.match_actions[0]
        assert ma.reason_code == "no_matching_rule"

    def test_aggregated_reasons_deduplicated(self):
        """Two pii matches should not produce duplicate reason messages."""
        decision = self.evaluator.evaluate(
            _result(_match(category="pii"), _match(category="pii")),
            DEFAULT_PROFILE,
        )
        # Both have the same reason message; should appear only once
        assert len(decision.reasons) == 1

    def test_mixed_reasons_both_present(self):
        decision = self.evaluator.evaluate(
            _result(
                _match(category="pii"),
                _match(category="secret", severity=SensitivityLevel.CRITICAL),
            ),
            DEFAULT_PROFILE,
        )
        assert len(decision.reasons) == 2
