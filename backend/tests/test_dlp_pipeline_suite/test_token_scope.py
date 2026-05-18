"""Token scope: each mask() call produces an independent span_map.

The counter starts at zero per call, so ``[EMAIL_0]`` always refers
to "the first email in THIS request". Cross-request restore must be a
no-op for tokens that weren't minted in this request's span_map —
otherwise an old conversation's PII could leak into a new one.
"""

from __future__ import annotations

from ..test_dlp_detectors._helpers import mask, restore


def test_independent_counters():
    _, sm_a, _ = mask("Contact alice@example.com")
    _, sm_b, _ = mask("Contact bob@example.com")
    _, sm_c, _ = mask("ส่งอีเมลไปที่ somchai@example.co.th")

    assert sm_a["[EMAIL_0]"] == "alice@example.com"
    assert sm_b["[EMAIL_0]"] == "bob@example.com"
    assert sm_c["[EMAIL_0]"] == "somchai@example.co.th"


def test_span_maps_are_independent_objects():
    _, sm_a, _ = mask("a@a.com")
    _, sm_b, _ = mask("b@b.com")
    assert sm_a is not sm_b
    # Mutating one must not affect the other.
    sm_a["[CANARY]"] = "leak"
    assert "[CANARY]" not in sm_b


def test_cross_request_restore_is_lossy():
    """Restoring A's masked text with B's span_map yields B's email,
    not A's. The token name collides but the binding is per-request,
    so the wrong span_map mints the wrong original — proof that
    cross-conversation restore is unsafe without conversation_key
    isolation.
    """
    masked_a, sm_a, _ = mask("Contact alice@example.com")
    masked_b, sm_b, _ = mask("Contact bob@example.com")

    # masked_a == masked_b == "Contact [EMAIL_0]"
    assert masked_a == masked_b

    # Restoring A with B's span_map gives bob@..., not alice@...
    wrong = restore(masked_a, sm_b)
    assert "bob@example.com" in wrong
    assert "alice@example.com" not in wrong
