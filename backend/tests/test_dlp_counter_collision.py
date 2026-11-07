"""Regression tests for the inbound→outbound mask-index collision bug.

Before Fix 1, ``mask_text`` on the outbound path started its counters
at 0 with no knowledge of the inbound counters. An AI-invented email
in the response would be masked as ``<r0>`` — the same token already
bound to the user's inbound email in ``span_map`` — so the subsequent
``restore`` would splice the user's own PII into the position where
the provider wrote a different value.

Fix: ``mask_messages`` now returns a third element ``counters``, and
``mask_text`` accepts ``start_counters`` so outbound continues the
index sequence. Tokens produced on the outbound pass are therefore
disjoint from inbound tokens by construction — ``restore`` leaves
them alone, and the user sees the mask placeholder (policy behavior)
rather than the wrong original value.

Under the neutral ``<rN>`` token shape the counter is a single global
slot (``__global__``) — there's no category in the token itself, so
two distinct detectors can no longer start at independent zeros and
emit the same ``<r0>`` for different values.
"""

from __future__ import annotations

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.schemas.common import Message
from app.services.dlp.transformer import DLPTransformer


def _email_match_action(text: str, start: int, end: int) -> MatchAction:
    """Build a MatchAction that the transformer will honor as an email MASK."""
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
        reason_code="test",
        reason_message="test email mask",
    )


def test_mask_messages_returns_counters() -> None:
    """mask_messages is a 3-tuple: (messages, span_map, counters).

    Under the neutral ``<rN>`` shape the counter is a single global
    slot (``__global__``) so any sequence of detections advances the
    same index regardless of category.
    """
    transformer = DLPTransformer()
    content = "send to alice@co.com and bob@co.com"
    msg = Message(role="user", content=content)
    actions = [
        _email_match_action(content, content.index("alice"), content.index("alice") + len("alice@co.com")),
        _email_match_action(content, content.index("bob"), content.index("bob") + len("bob@co.com")),
    ]

    masked_msgs, span_map, counters = transformer.mask_messages([msg], actions)

    assert "<r0>" in masked_msgs[0].content
    assert "<r1>" in masked_msgs[0].content
    assert span_map["<r0>"] == "alice@co.com"
    assert span_map["<r1>"] == "bob@co.com"
    # Counter has advanced past the last allocated index — next mint would be <r2>.
    assert counters["__global__"] == 2


def test_mask_text_accepts_start_counters_and_advances_them() -> None:
    """mask_text continues the index sequence when given start_counters."""
    transformer = DLPTransformer()
    text = "also cc carol@x.com on this"
    actions = [
        _email_match_action(
            text,
            text.index("carol"),
            text.index("carol") + len("carol@x.com"),
        ),
    ]

    out_text, out_span_map, out_counters = transformer.mask_text(
        text, actions, start_counters={"__global__": 2}
    )

    # Continues from index 2, not 0.
    assert "<r2>" in out_text
    assert "<r0>" not in out_text
    assert out_span_map == {"<r2>": "carol@x.com"}
    assert out_counters["__global__"] == 3


def test_no_counter_collision_between_inbound_and_outbound() -> None:
    """End-to-end: inbound <r0>, outbound new email must NOT be <r0>.

    Critically, after restore, the user's original email must NOT appear in
    the position where the AI generated a different email.
    """
    transformer = DLPTransformer()

    # ── Inbound ──────────────────────────────────────────────────────────
    user_text = "send to alice@co.com please"
    inbound_actions = [
        _email_match_action(
            user_text,
            user_text.index("alice"),
            user_text.index("alice") + len("alice@co.com"),
        ),
    ]
    _masked_msgs, span_map, inbound_counters = transformer.mask_messages(
        [Message(role="user", content=user_text)], inbound_actions
    )
    assert span_map == {"<r0>": "alice@co.com"}
    assert inbound_counters["__global__"] == 1

    # ── Outbound (AI invents a new email) ─────────────────────────────────
    ai_reply = "I'll also copy bob@other.com on this"
    outbound_actions = [
        _email_match_action(
            ai_reply,
            ai_reply.index("bob"),
            ai_reply.index("bob") + len("bob@other.com"),
        ),
    ]
    outbound_text, _out_span_map, _out_counters = transformer.mask_text(
        ai_reply, outbound_actions, start_counters=inbound_counters
    )
    assert "<r1>" in outbound_text
    assert "<r0>" not in outbound_text  # load-bearing: no collision

    # ── Restore (uses inbound span_map; outbound token has no binding) ───
    restored = transformer.restore(outbound_text, span_map)
    # alice must NOT have leaked into the position where the AI wrote bob.
    assert "alice@co.com" not in restored
    # The outbound-only token survives restore — it is intentionally not in
    # span_map and is expected to reach the user as a placeholder.
    assert "<r1>" in restored


def test_empty_start_counters_is_equivalent_to_no_arg() -> None:
    """Passing start_counters=None or omitting it starts at 0 as before."""
    transformer = DLPTransformer()
    text = "mail bob@z.com"
    actions = [
        _email_match_action(
            text, text.index("bob"), text.index("bob") + len("bob@z.com")
        ),
    ]

    a_text, _, a_counters = transformer.mask_text(text, actions)
    b_text, _, b_counters = transformer.mask_text(text, actions, start_counters=None)
    c_text, _, c_counters = transformer.mask_text(text, actions, start_counters={})

    assert a_text == b_text == c_text
    assert "<r0>" in a_text
    assert a_counters == b_counters == c_counters == {"__global__": 1}
