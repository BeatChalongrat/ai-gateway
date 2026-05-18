"""Tests for Fix 3 — direction-aware policy enforcement.

The default profile previously masked every sensitive category in both
directions, so an AI's reply quoting a Stripe test card or a fictional
email was rewritten to ``[CREDIT_CARD_0]`` / ``[EMAIL_0]`` before
reaching the user. ``PolicyProfile.category_actions_output`` lets each
profile narrow (or override) the outbound rules — the default now masks
only credentials and secrets outbound, letting reference-data answers
flow through.

These tests pin the contract end-to-end at the ``PolicyEvaluator``
boundary so a change in either ``profiles.py`` or ``evaluator.py`` that
breaks the direction split is caught immediately.
"""

from __future__ import annotations

import pytest

from app.domain.dlp import (
    DetectionMatch,
    DetectionResult,
    EnforcementAction,
    SensitivityLevel,
)
from app.domain.policy import PolicyProfile
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE


def _match(category: str, detector: str = "_test", severity: SensitivityLevel = SensitivityLevel.MEDIUM) -> DetectionMatch:
    return DetectionMatch(
        detector_name=detector,
        entity_type=detector,
        category=category,
        start=0,
        end=1,
        matched_text="x",
        severity=severity,
    )


def _result(direction: str, *matches: DetectionMatch) -> DetectionResult:
    return DetectionResult(
        matches=list(matches),
        scanned_direction=direction,
        scan_target="messages" if direction == "input" else "response_content",
    )


# ---------------------------------------------------------------------------
# DEFAULT profile — direction-aware behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category,detector,expected_action",
    [
        # Outbound PII / financial / identity now MASK under default —
        # an LLM that hallucinates a fake email/phone/card/passport
        # must not deliver the raw value to the user. Tightened in
        # the May 2026 DLP coverage pass.
        ("pii",       "email",       EnforcementAction.RESTORE),
        ("pii",       "phone",       EnforcementAction.RESTORE),
        ("financial", "credit_card", EnforcementAction.RESTORE),
        ("financial", "iban",        EnforcementAction.RESTORE),
        ("identity",  "passport",    EnforcementAction.RESTORE),
        # Network kept at ALLOW — AI legitimately answers about
        # RFC-1918 ranges, example IPs, etc.
        ("network",   "ip_address",  EnforcementAction.ALLOW),
    ],
)
def test_default_profile_outbound_reference_categories(
    category: str, detector: str, expected_action: EnforcementAction
) -> None:
    """Outbound action map per category (default profile, post-fix)."""
    evaluator = PolicyEvaluator()
    decision = evaluator.evaluate(
        _result("output", _match(category, detector)), DEFAULT_PROFILE
    )
    assert decision.overall_action == expected_action, (
        f"outbound {category} ({detector}) expected {expected_action.name} "
        f"under DEFAULT_PROFILE, got {decision.overall_action.name}"
    )
    assert decision.requires_transformation is (
        expected_action == EnforcementAction.MASK
    )


@pytest.mark.parametrize(
    "category,detector",
    [
        ("credential", "api_key_secret"),
        ("credential", "db_connection_string"),
        ("secret", "bearer_token"),
        ("secret", "private_key"),
        ("secret", "password_field"),
    ],
)
def test_default_profile_outbound_credentials_and_secrets_restore(category: str, detector: str) -> None:
    """Outbound credential / secret → MASK under default. AI must never leak these."""
    evaluator = PolicyEvaluator()
    decision = evaluator.evaluate(
        _result("output", _match(category, detector)), DEFAULT_PROFILE
    )
    assert decision.overall_action == EnforcementAction.RESTORE, (
        f"outbound {category} ({detector}) must be RESTORE under DEFAULT_PROFILE"
    )
    assert decision.requires_transformation is False


@pytest.mark.parametrize(
    "category,detector",
    [
        ("pii", "email"),
        ("financial", "credit_card"),
        ("network", "ip_address"),
        ("identity", "passport"),
        ("credential", "api_key_secret"),
        ("secret", "bearer_token"),
    ],
)
def test_default_profile_inbound_unchanged(category: str, detector: str) -> None:
    """Inbound behavior is untouched by Fix 3 — every category still MASKs."""
    evaluator = PolicyEvaluator()
    decision = evaluator.evaluate(
        _result("input", _match(category, detector)), DEFAULT_PROFILE
    )
    assert decision.overall_action == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# STRICT profile — backward compatibility
# ---------------------------------------------------------------------------


def test_strict_profile_has_no_output_override() -> None:
    """STRICT keeps category_actions_output=None so outbound falls back to the
    inbound dict — i.e. its pre-Fix-3 behavior is preserved verbatim."""
    assert STRICT_PROFILE.category_actions_output is None


@pytest.mark.parametrize(
    "category,detector,expected",
    [
        ("pii", "email", EnforcementAction.MASK),
        ("financial", "credit_card", EnforcementAction.BLOCK),
        ("network", "ip_address", EnforcementAction.BLOCK),
        ("identity", "passport", EnforcementAction.BLOCK),
        ("credential", "api_key_secret", EnforcementAction.BLOCK),
        ("secret", "bearer_token", EnforcementAction.BLOCK),
    ],
)
def test_strict_profile_outbound_falls_back_to_inbound_rules(
    category: str, detector: str, expected: EnforcementAction
) -> None:
    """When category_actions_output is None, outbound uses category_actions."""
    evaluator = PolicyEvaluator()
    decision = evaluator.evaluate(
        _result("output", _match(category, detector, severity=SensitivityLevel.MEDIUM)),
        STRICT_PROFILE,
    )
    assert decision.overall_action == expected


# ---------------------------------------------------------------------------
# Custom profile — explicit override semantics
# ---------------------------------------------------------------------------


def test_custom_profile_with_empty_output_dict_means_allow_everything_outbound() -> None:
    """An empty (but non-None) category_actions_output means "no rules" →
    every category falls through to the ALLOW fallback in _decide."""
    profile = PolicyProfile(
        profile_id="lax-output",
        name="Lax Output",
        category_actions={"pii": EnforcementAction.MASK},
        category_actions_output={},  # explicit empty — different from None
    )
    evaluator = PolicyEvaluator()
    decision = evaluator.evaluate(
        _result("output", _match("pii", "email")), profile
    )
    assert decision.overall_action == EnforcementAction.ALLOW


def test_inbound_unaffected_by_output_override() -> None:
    """Setting category_actions_output must NOT change inbound decisions."""
    profile = PolicyProfile(
        profile_id="split",
        name="Split",
        category_actions={"pii": EnforcementAction.MASK},
        category_actions_output={"pii": EnforcementAction.ALLOW},
    )
    evaluator = PolicyEvaluator()
    inbound = evaluator.evaluate(
        _result("input", _match("pii", "email")), profile
    )
    outbound = evaluator.evaluate(
        _result("output", _match("pii", "email")), profile
    )
    assert inbound.overall_action == EnforcementAction.MASK
    assert outbound.overall_action == EnforcementAction.ALLOW


def test_explicit_category_action_wins_over_severity_threshold() -> None:
    """Threshold check runs before category lookup, regardless of direction."""
    profile = PolicyProfile(
        profile_id="threshold-only",
        name="Threshold Only",
        category_actions={"pii": EnforcementAction.MASK},
        category_actions_output={"pii": EnforcementAction.ALLOW},
        severity_threshold_block=SensitivityLevel.HIGH,
    )
    evaluator = PolicyEvaluator()
    crit_match = _match("pii", "email", severity=SensitivityLevel.CRITICAL)

    inbound = evaluator.evaluate(_result("input", crit_match), profile)
    outbound = evaluator.evaluate(_result("output", crit_match), profile)

    assert inbound.overall_action == EnforcementAction.MASK
    assert outbound.overall_action == EnforcementAction.ALLOW
