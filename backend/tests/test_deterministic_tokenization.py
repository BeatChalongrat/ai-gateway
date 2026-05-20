"""Cross-turn tokenization tests.

The DLP transformer emits a single token shape — counter-form ``<rN>``
— via ``_allocate_token``. Cross-turn determinism (same PII value in
turn 2 keeps the same ``<rN>`` it got in turn 1) is now driven by the
caller passing a ``vault_reverse_map`` (value → prior token) and a
matching ``start_counters`` primer past the highest vault index. The
legacy HMAC-based ``conversation_key`` mode is gone — its helper
functions remain in :mod:`app.services.dlp.transformer` for backwards
compat but no longer affect the emitted token shape.

These tests pin the new contract: counter format, vault-driven
cross-turn determinism, and outbound-vs-inbound non-collision.
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
from app.services.dlp.transformer import DLPTransformer


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


def _mask_one(
    transformer: DLPTransformer,
    text: str,
    *,
    vault_reverse_map: dict[str, str] | None = None,
    start_counters: dict[str, int] | None = None,
):
    """Mask the first email in ``text`` and return (masked, span_map, counters)."""
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    assert match, f"no email in test fixture: {text!r}"
    actions = [_email_action(text, match.start(), match.end())]
    return transformer.mask_text(
        text,
        actions,
        start_counters=start_counters,
        vault_reverse_map=vault_reverse_map,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_counter_format_emitted():
    """Default path mints ``<r0>``, ``<r1>``, … (counter form only)."""
    transformer = DLPTransformer()
    masked, span_map, _counters = _mask_one(transformer, "john@acme.com here")
    assert "<r0>" in masked
    assert span_map.get("<r0>") == "john@acme.com"
    # No HMAC-style suffix should appear anywhere in the output.
    assert not re.search(r"<r[A-Za-z][A-Za-z0-9_\-]+>", masked)


def test_same_value_same_token_within_call():
    """The same value masked twice in one call dedups to one token."""
    transformer = DLPTransformer()
    text = "ping alice@x.com then alice@x.com again"
    actions = [
        _email_action(text, text.index("alice@x.com"), text.index("alice@x.com") + 11),
        _email_action(
            text,
            text.rindex("alice@x.com"),
            text.rindex("alice@x.com") + 11,
        ),
    ]
    masked, span_map, _ = transformer.mask_text(text, actions)
    # Both occurrences collapse to ``<r0>`` — span_map has one binding.
    assert span_map == {"<r0>": "alice@x.com"}
    assert masked.count("<r0>") == 2


def test_vault_reverse_map_reuses_prior_token():
    """A vault hit reuses the prior turn's token instead of minting a new one."""
    transformer = DLPTransformer()
    # Vault has alice@x.com bound to ``<r0>`` from an earlier turn.
    vault = {"alice@x.com": "<r0>"}
    masked, span_map, _ = _mask_one(
        transformer,
        "อีเมล alice@x.com",
        vault_reverse_map=vault,
        start_counters={"__global__": 1},
    )
    assert "<r0>" in masked
    assert span_map.get("<r0>") == "alice@x.com"


def test_new_value_gets_next_counter_after_vault():
    """A value not in the vault gets the next free counter past the vault max."""
    transformer = DLPTransformer()
    # Vault owns <r0>: alice, <r1>: bob; new value carol must get <r2>.
    vault = {"alice@x.com": "<r0>", "bob@y.com": "<r1>"}
    masked, span_map, _ = _mask_one(
        transformer,
        "ping carol@z.com",
        vault_reverse_map=vault,
        start_counters={"__global__": 2},
    )
    assert "<r2>" in masked
    assert span_map.get("<r2>") == "carol@z.com"
    # The vault entries are NOT in this turn's span_map — only tokens
    # actually emitted from this call's text are bound.
    assert "<r0>" not in span_map
    assert "<r1>" not in span_map


def test_outbound_value_gets_different_token():
    """Inbound and outbound calls in one request must mint different tokens
    for different values — start_counters threads the high-water mark so
    the outbound pass can't collide with an inbound binding."""
    transformer = DLPTransformer()
    inbound_text = "ping john@acme.com"
    inbound_masked, inbound_map, counters = _mask_one(transformer, inbound_text)
    inbound_token = next(iter(inbound_map))
    assert inbound_token == "<r0>"

    outbound_text = "contact fake@evil.com"
    outbound_masked, outbound_map, _ = _mask_one(
        transformer,
        outbound_text,
        start_counters=counters,
    )
    outbound_token = next(iter(outbound_map))
    assert inbound_token != outbound_token
    assert outbound_token == "<r1>"

    # Restoring with only the inbound map leaves the outbound token in
    # place — the user never sees the hallucinated value.
    restored = transformer.restore(
        f"contact {outbound_token} immediately", inbound_map
    )
    assert outbound_token in restored
    assert "fake@evil.com" not in restored


def test_multiturn_round_trip_via_vault():
    """End-to-end: turn 1 mints ``<r0>``; turn 2 reuses ``<r0>`` for the
    same value via ``vault_reverse_map`` and restores correctly."""
    transformer = DLPTransformer()

    turn1, map1, _ = _mask_one(transformer, "อีเมล john@acme.com")
    token1 = next(iter(map1))
    assert token1 == "<r0>"

    # Build the vault state as the orchestrator would (token → value,
    # then inverted to value → token for the next-turn mask call).
    vault_state = dict(map1)
    vault_reverse = {value: token for token, value in vault_state.items()}

    turn2_text = "john@acme.com อีกครั้ง"
    turn2, map2, _ = _mask_one(
        transformer,
        turn2_text,
        vault_reverse_map=vault_reverse,
        start_counters={"__global__": 1},
    )
    token2 = next(iter(map2))
    assert token2 == token1, (
        f"Same value should reuse the prior token across turns: "
        f"got {token2!r} vs {token1!r}"
    )

    # Restore round-trips both turns.
    assert transformer.restore(turn1, map1) == "อีเมล john@acme.com"
    assert transformer.restore(turn2, map2) == turn2_text
