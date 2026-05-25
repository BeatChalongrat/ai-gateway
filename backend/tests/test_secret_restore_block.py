"""Secret-restore safety net.

Two layers guard against secrets round-tripping through the outbound
restore step back to the user:

  Layer 1 — Policy default. ``DEFAULT_PROFILE.category_actions_output``
            sets ``credential`` and ``secret`` to ``MASK`` (NOT
            ``RESTORE``). See ``policy/profiles.py``.
  Layer 2 — Hard-coded exclusion. ``_compute_restore_allowed_tokens``
            skips any match whose category is in
            ``_NEVER_RESTORE_CATEGORIES`` regardless of the active
            policy profile. See ``orchestrator.py``.

These tests pin both layers. A regression in either alone would still
be caught by the other; both at once would mean a future change can
leak secrets to the user.
"""

from __future__ import annotations

import pytest

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    EnforcementDecision,
    MatchAction,
    SensitivityLevel,
)
from app.domain.policy import PolicyProfile
from app.services.orchestrator import (
    GatewayOrchestrator,
    _NEVER_RESTORE_CATEGORIES,
)
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE


# ---------------------------------------------------------------------------
# Test 1 — DEFAULT_PROFILE outbound never restores secret-class categories
# ---------------------------------------------------------------------------


def test_default_profile_blocks_secret_restore():
    out = DEFAULT_PROFILE.category_actions_output
    assert out is not None
    assert out.get("credential") != EnforcementAction.RESTORE, (
        "DEFAULT_PROFILE.category_actions_output[credential] must not be "
        "RESTORE — leaks any echoed <r0> token back to the user as the "
        "original API key. Use MASK so orphan-redact strips it instead."
    )
    assert out.get("secret") != EnforcementAction.RESTORE, (
        "DEFAULT_PROFILE.category_actions_output[secret] must not be RESTORE."
    )


# ---------------------------------------------------------------------------
# Test 2 — STRICT_PROFILE never restores secret-class categories either
# ---------------------------------------------------------------------------


def test_strict_profile_blocks_secret_restore():
    # STRICT has ``category_actions_output = None`` so the evaluator
    # falls back to the inbound map (which is BLOCK for credential /
    # secret). BLOCK ≠ RESTORE → safe. Pin both possible code paths.
    out = STRICT_PROFILE.category_actions_output
    inbound = STRICT_PROFILE.category_actions

    def _resolved(category: str) -> EnforcementAction | None:
        if out is not None and category in out:
            return out[category]
        return inbound.get(category)

    assert _resolved("credential") != EnforcementAction.RESTORE
    assert _resolved("secret") != EnforcementAction.RESTORE


# ---------------------------------------------------------------------------
# Test 3 — Hard-coded exclusion overrides a misconfigured profile
# ---------------------------------------------------------------------------


def _make_action(category: str, matched_text: str = "x") -> MatchAction:
    return MatchAction(
        match=DetectionMatch(
            detector_name="api_key_secret",
            entity_type="anthropic_api_key",
            category=category,
            start=0,
            end=len(matched_text),
            matched_text=matched_text,
            severity=SensitivityLevel.CRITICAL,
        ),
        action=EnforcementAction.MASK,
        reason_code="test",
        reason_message="test",
    )


def test_hard_coded_exclusion_overrides_profile():
    """Even a custom profile that explicitly RESTOREs credentials
    must not produce credential tokens in the allow-list — the Layer
    2 exclusion bypasses the profile entirely.
    """
    # Misconfigured profile — RESTORE on credential. Could only arrive
    # via a manual DB edit / future regression.
    misconfigured = PolicyProfile(
        profile_id="rogue",
        name="Rogue",
        category_actions={"credential": EnforcementAction.MASK},
        category_actions_output={"credential": EnforcementAction.RESTORE},
    )
    span_map = {"<r0>": "sk-test-abc123"}
    enforcement = EnforcementDecision(
        match_actions=[_make_action("credential", "sk-test-abc123")],
        policy_profile_id="rogue",
    )

    allowed = GatewayOrchestrator._compute_restore_allowed_tokens(
        misconfigured, span_map, enforcement
    )

    # Hard-coded exclusion fires → token is NOT in allow-list → restore
    # leaves it as ``<r0>`` → orphan-redact strips it later.
    assert "<r0>" not in allowed
    assert allowed == set()
    # Sanity: the constant itself contains the two categories.
    assert "credential" in _NEVER_RESTORE_CATEGORIES
    assert "secret" in _NEVER_RESTORE_CATEGORIES


# ---------------------------------------------------------------------------
# Test 4 — End-to-end (transformer-level): LLM-echoed secret stripped
# ---------------------------------------------------------------------------


def test_secret_not_in_output_e2e():
    """Full mask → echo → restore → redact pipeline.

    Inbound: ``api_key = 'sk-test-abc123'`` → masked to ``<r0>``.
    LLM echoes ``<r0>`` verbatim (the new strong prompt makes this
    realistic).
    Outbound restore: hard-coded exclusion blocks the credential
    token → ``<r0>`` is NOT restored to ``sk-test-abc123``.
    Orphan-redact: ``<r0>`` is not in the allowed restore set, so
    after restore it survives — the post-restore final pass strips it.
    """
    from app.services.dlp.transformer import DLPTransformer, redact_orphan_tokens

    transformer = DLPTransformer()

    # Build the "LLM echoed the masked token" text directly — equivalent
    # to what GPT-4o-mini produced in the round-2 E2E Case 4 trace.
    span_map = {"<r0>": "sk-test-abc123"}
    enforcement = EnforcementDecision(
        match_actions=[_make_action("credential", "sk-test-abc123")],
        policy_profile_id=DEFAULT_PROFILE.profile_id,
    )

    allowed = GatewayOrchestrator._compute_restore_allowed_tokens(
        DEFAULT_PROFILE, span_map, enforcement
    )

    llm_reply = "Here is your code: api_key = '<r0>' — replace with env var."
    restored = transformer.restore(
        llm_reply, span_map, allowed_tokens=allowed
    )
    # Secret value never appears.
    assert "sk-test-abc123" not in restored
    # Token survived restore (because allow-list is empty).
    assert "<r0>" in restored

    # Final orphan-redact pass — the orchestrator filters span_map
    # via ``_strip_never_restore_tokens`` so the credential token
    # falls into the orphan branch and is deleted. Mirrors what
    # ``orchestrator.handle_chat_completion`` does in section P3.
    filtered_map = GatewayOrchestrator._strip_never_restore_tokens(
        span_map, enforcement,
    )
    final = redact_orphan_tokens(restored, filtered_map)
    assert "<r0>" not in final, (
        "Layer 2 must ensure credential tokens are stripped by "
        "orphan-redact even though they were in span_map."
    )
    assert "sk-test-abc123" not in final


# ---------------------------------------------------------------------------
# Test 5 — PII still restores normally (the exclusion is targeted)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 6 — Streaming path also blocks secret restore
# ---------------------------------------------------------------------------


def test_streaming_secret_not_restored():
    """Simulate the streaming orphan-redact step: even when the LLM
    echoes ``<r0>`` for a credential, the secret never reaches the
    wire because ``public_chat.py`` now uses the same
    ``_strip_never_restore_tokens`` filter as the non-stream P3.
    """
    from app.services.dlp.transformer import DLPTransformer, redact_orphan_tokens

    span_map = {"<r0>": "sk-test-streamed"}
    enforcement = EnforcementDecision(
        match_actions=[_make_action("credential", "sk-test-streamed")],
        policy_profile_id=DEFAULT_PROFILE.profile_id,
    )

    # Streaming allow-list — same orchestrator helper the streaming
    # route now calls.
    allowed = GatewayOrchestrator._compute_restore_allowed_tokens(
        DEFAULT_PROFILE, span_map, enforcement
    )
    # Layer 2 → credential excluded from allow-list.
    assert allowed == set()

    # Restore in the stream emit path.
    chunk = "Here's the code: api_key = '<r0>' — use env var."
    restored = DLPTransformer().restore(
        chunk, span_map, allowed_tokens=allowed
    )
    # Secret never appears post-restore.
    assert "sk-test-streamed" not in restored
    # Token survives restore (not allowed).
    assert "<r0>" in restored

    # Per-chunk orphan-redact — uses the FILTERED span_map.
    redact_map = GatewayOrchestrator._strip_never_restore_tokens(
        span_map, enforcement,
    )
    on_wire = redact_orphan_tokens(restored, redact_map)
    assert "<r0>" not in on_wire, (
        "streaming orphan-redact must strip credential tokens with "
        "the filtered span_map (Layer 2)"
    )
    assert "sk-test-streamed" not in on_wire


# ---------------------------------------------------------------------------
# Test 7 — Streaming PII still restores normally (gate is targeted)
# ---------------------------------------------------------------------------


def test_streaming_pii_still_restores():
    """Identity-class PII must still round-trip on streaming. The
    secret strip is targeted at credential / secret categories only.
    """
    from app.services.dlp.transformer import DLPTransformer, redact_orphan_tokens

    span_map = {"<<PERSON_A>>": "สมชาย"}
    enforcement = EnforcementDecision(
        match_actions=[
            MatchAction(
                match=DetectionMatch(
                    detector_name="presidio_ner",
                    entity_type="person",
                    category="pii",
                    start=0,
                    end=5,
                    matched_text="สมชาย",
                    severity=SensitivityLevel.MEDIUM,
                ),
                action=EnforcementAction.MASK,
                reason_code="test",
                reason_message="test",
            )
        ],
        policy_profile_id=DEFAULT_PROFILE.profile_id,
    )

    allowed = GatewayOrchestrator._compute_restore_allowed_tokens(
        DEFAULT_PROFILE, span_map, enforcement
    )
    assert "<<PERSON_A>>" in allowed

    chunk = "ลูกค้า <<PERSON_A>> สั่งของ"
    restored = DLPTransformer().restore(
        chunk, span_map, allowed_tokens=allowed
    )
    assert "สมชาย" in restored
    assert "<<PERSON_A>>" not in restored

    # Filter keeps the PII token (only credential/secret are stripped).
    redact_map = GatewayOrchestrator._strip_never_restore_tokens(
        span_map, enforcement,
    )
    assert "<<PERSON_A>>" in redact_map
    on_wire = redact_orphan_tokens(restored, redact_map)
    # Already-restored "สมชาย" survives orphan-redact.
    assert "สมชาย" in on_wire


def test_pii_still_restores():
    """PII / identity categories must keep their RESTORE behaviour —
    only credential + secret are hard-blocked.
    """
    from app.services.dlp.transformer import DLPTransformer

    span_map = {
        "<<PERSON_A>>": "สมชาย",
        "<<EMAIL_1>>": "somchai@gmail.com",
    }
    person_action = MatchAction(
        match=DetectionMatch(
            detector_name="presidio_ner",
            entity_type="person",
            category="pii",
            start=0,
            end=5,
            matched_text="สมชาย",
            severity=SensitivityLevel.MEDIUM,
        ),
        action=EnforcementAction.MASK,
        reason_code="test",
        reason_message="test",
    )
    email_action = MatchAction(
        match=DetectionMatch(
            detector_name="email",
            entity_type="email_address",
            category="pii",
            start=0,
            end=len("somchai@gmail.com"),
            matched_text="somchai@gmail.com",
            severity=SensitivityLevel.MEDIUM,
        ),
        action=EnforcementAction.MASK,
        reason_code="test",
        reason_message="test",
    )
    enforcement = EnforcementDecision(
        match_actions=[person_action, email_action],
        policy_profile_id=DEFAULT_PROFILE.profile_id,
    )

    allowed = GatewayOrchestrator._compute_restore_allowed_tokens(
        DEFAULT_PROFILE, span_map, enforcement
    )

    # PII allow-list includes both tokens — RESTORE policy preserved.
    assert "<<PERSON_A>>" in allowed
    assert "<<EMAIL_1>>" in allowed

    # Restore actually swaps them.
    llm_reply = "ลูกค้า <<PERSON_A>> สั่งของ email <<EMAIL_1>>"
    restored = DLPTransformer().restore(
        llm_reply, span_map, allowed_tokens=allowed
    )
    assert "สมชาย" in restored
    assert "somchai@gmail.com" in restored
    assert "<<PERSON_A>>" not in restored
    assert "<<EMAIL_1>>" not in restored
