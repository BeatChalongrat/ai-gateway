"""Deterministic HMAC tokenization tests.

The DLP transformer supports two token modes:

- **Counter** (legacy, ``conversation_key=None``) — sequential
  ``[EMAIL_0]``, ``[EMAIL_1]``. Counter resets per request.
- **Deterministic** (``conversation_key=bytes``) — HMAC-derived
  opaque suffix. Same value + same conversation always produce the
  same token; different conversations produce different tokens.

These tests pin the deterministic-mode contract and verify that the
counter mode remains untouched (backward compat).
"""

from __future__ import annotations

import re

import pytest

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.services.dlp.transformer import (
    DLPTransformer,
    _make_conversation_key,
    _make_deterministic_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _email_action(text: str, start: int, end: int) -> MatchAction:
    """Build a single MASK MatchAction for an email span."""
    return MatchAction(
        match=DetectionMatch(
            detector_name="email",
            entity_type="email_address",
            category="pii",
            start=start,
            end=end,
            matched_text=text[start:end],
            severity=SensitivityLevel.MEDIUM,
        ),
        action=EnforcementAction.MASK,
        reason_code="pii_mask_policy",
        reason_message="test mask",
    )


def _mask_one(transformer: DLPTransformer, text: str, key: bytes | None):
    """Mask a single email in ``text`` and return (masked_text, span_map)."""
    # Pull every "*@*.*" run out of the text as a MatchAction. Keeping
    # the helper small — the real detector pipeline is exercised in
    # the integration suite; this file pins transformer semantics.
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    assert match, f"no email in test fixture: {text!r}"
    actions = [_email_action(text, match.start(), match.end())]
    masked, span_map, _counters = transformer.mask_text(
        text, actions, conversation_key=key
    )
    return masked, span_map


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_same_value_same_token():
    """Same value in the same conversation → identical token."""
    key = _make_conversation_key("secret", "conv-123")
    t1 = _make_deterministic_token("EMAIL", "john@acme.com", key)
    t2 = _make_deterministic_token("EMAIL", "john@acme.com", key)
    assert t1 == t2


def test_different_values_different_tokens():
    """Different PII values in one conversation → different tokens."""
    key = _make_conversation_key("secret", "conv-123")
    t1 = _make_deterministic_token("EMAIL", "a@b.com", key)
    t2 = _make_deterministic_token("EMAIL", "c@d.com", key)
    assert t1 != t2


def test_different_conversations_different_tokens():
    """Cross-conversation isolation — same value, different convs → different tokens."""
    key_a = _make_conversation_key("secret", "conv-A")
    key_b = _make_conversation_key("secret", "conv-B")
    t_a = _make_deterministic_token("EMAIL", "john@acme.com", key_a)
    t_b = _make_deterministic_token("EMAIL", "john@acme.com", key_b)
    assert t_a != t_b


def test_multiturn_same_token_restored():
    """Turn 1 + turn 2 of the same conversation mint the same token
    for the same value, and ``restore()`` swaps it back to the
    original on both turns."""
    transformer = DLPTransformer()
    key = _make_conversation_key("secret", "conv-mt")
    turn1, map1 = _mask_one(transformer, "อีเมล john@acme.com", key)
    turn2, map2 = _mask_one(transformer, "john@acme.com อีกครั้ง", key)

    token1 = next(iter(map1))
    token2 = next(iter(map2))
    assert token1 == token2, (
        f"Same value should produce same token across turns: "
        f"got {token1!r} vs {token2!r}"
    )

    assert transformer.restore(turn1, map1) == "อีเมล john@acme.com"
    assert transformer.restore(turn2, map2) == "john@acme.com อีกครั้ง"


def test_outbound_hallucinated_pii_gets_different_token():
    """Inbound value and a different outbound value share the same
    conversation_key but produce different tokens — so a hallucinated
    PII is never accidentally restored to the user."""
    transformer = DLPTransformer()
    key = _make_conversation_key("secret", "conv-out")
    _, inbound_map = _mask_one(transformer, "john@acme.com", key)
    _, outbound_map = _mask_one(transformer, "fake@evil.com", key)
    inbound_token = next(iter(inbound_map))
    outbound_token = next(iter(outbound_map))
    assert inbound_token != outbound_token

    # Crucially: restore() with only the inbound map leaves the
    # outbound token literally in place — the user never sees the
    # hallucinated value.
    masked = f"contact {outbound_token} immediately"
    restored = transformer.restore(masked, inbound_map)
    assert outbound_token in restored
    assert "fake@evil.com" not in restored


def test_legacy_counter_mode_when_no_key():
    """conversation_key=None → counter-format tokens, identical to
    the pre-2026-05 behaviour all existing tests rely on."""
    transformer = DLPTransformer()
    masked, span_map = _mask_one(transformer, "john@acme.com here", key=None)
    assert "[EMAIL_0]" in masked, (
        f"legacy mode must emit counter tokens, got: {masked!r}"
    )
    assert span_map.get("[EMAIL_0]") == "john@acme.com"


def test_token_format_is_opaque():
    """The deterministic suffix is base64url and reveals nothing
    about the input — none of the original characters, words, or
    domain parts should appear."""
    key = _make_conversation_key("secret", "conv-opaque")
    token = _make_deterministic_token("EMAIL", "john@acme.com", key)
    assert "john" not in token
    assert "acme" not in token
    assert "@" not in token
    # 8-char base64url body (no padding) inside an [EMAIL_…] wrapper.
    assert re.match(r"^\[EMAIL_[A-Za-z0-9_-]{8}\]$", token), (
        f"unexpected token shape: {token!r}"
    )
