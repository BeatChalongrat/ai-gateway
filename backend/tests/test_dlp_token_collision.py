"""Regression tests for the mask-token namespace collision bug.

Pre-fix, ``mask_messages`` / ``mask_text`` keyed their per-detector
index counters on ``detector_name``. Two different detectors that
share a ``MASK_TOKENS`` base would each start their own counter at 0
and emit identical tokens, silently overwriting one another in
``span_map``.

Under the neutral ``<rN>`` shape the token no longer carries a
category, so the counter is a single ``__global__`` slot — two
detectors firing in sequence advance the same counter and produce
distinct tokens by construction (``<r0>``, ``<r1>``). These tests
pin that round-trip.
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


def _mask_action(
    detector_name: str,
    category: str,
    text: str,
    start: int,
    end: int,
    severity: SensitivityLevel = SensitivityLevel.CRITICAL,
) -> MatchAction:
    return MatchAction(
        match=DetectionMatch(
            detector_name=detector_name,
            entity_type=detector_name,
            category=category,
            start=start,
            end=end,
            matched_text=text[start:end],
            severity=severity,
        ),
        action=EnforcementAction.MASK,
        reason_code="collision_test",
        reason_message="collision_test",
    )


def test_api_key_and_db_conn_in_same_message_get_distinct_tokens() -> None:
    """Two detectors sharing a base mask token must not collide."""
    transformer = DLPTransformer()

    api_key = (
        "sk-ant-api03-xK9mN2pL8qR5vT3wY6uI1oE4aS7dF0jHmQbZcVnXp"
        "12345678901234"
    )
    db_url = "postgresql://admin:P@ssw0rd123@db.internal:5432/prod"
    content = f"API Key: {api_key} DB: {db_url}"

    api_start = content.index(api_key)
    db_start = content.index(db_url)

    msg = Message(role="user", content=content)
    actions = [
        _mask_action("api_key_secret", "credential", content, api_start, api_start + len(api_key)),
        _mask_action("db_connection_string", "credential", content, db_start, db_start + len(db_url)),
    ]

    masked_msgs, span_map, counters = transformer.mask_messages([msg], actions)

    # Two sequential detections — global counter advances, distinct tokens.
    assert len(span_map) == 2, f"expected 2 span_map entries, got {len(span_map)}: {list(span_map)}"

    tokens = sorted(span_map.keys())
    assert tokens == ["<r0>", "<r1>"], tokens

    # Both raw values must be recoverable from span_map — neither was
    # overwritten by the other.
    assert set(span_map.values()) == {api_key, db_url}

    assert counters == {"__global__": 2}

    # End-to-end round-trip: inbound mask → restore must return both
    # original values in their original positions.
    restored = transformer.restore(masked_msgs[0].content, span_map)
    assert api_key in restored
    assert db_url in restored
    assert "<r" not in restored


def test_counter_shared_across_mask_messages_and_mask_text() -> None:
    """The base counter continues across mask_messages → mask_text so
    outbound new findings don't reuse an inbound token.

    Pre-fix, counters were keyed on detector_name; since outbound
    invented credentials typically come from a different detector than
    the inbound one, this bug was partially masked by the detector-name
    keying. Post-fix counters are keyed on the base, so this test also
    guards against an outbound detector that shares the base collapsing
    onto an inbound index.
    """
    transformer = DLPTransformer()

    inbound_text = "API Key: sk-ant-api03-abc123abc123abc123abc123abc123abc"
    api_start = inbound_text.index("sk-ant-api03-")
    inbound_msg = Message(role="user", content=inbound_text)
    inbound_action = _mask_action(
        "api_key_secret", "credential",
        inbound_text, api_start, len(inbound_text),
    )

    _msgs, inbound_span, inbound_counters = transformer.mask_messages(
        [inbound_msg], [inbound_action]
    )
    assert "<r0>" in inbound_span
    assert inbound_counters["__global__"] == 1

    # Outbound pass — global counter continues from 1, no collision.
    outbound_text = "Hint: connect at postgresql://admin:pw@db:5432/x"
    db_url = "postgresql://admin:pw@db:5432/x"
    db_start = outbound_text.index(db_url)
    out_action = _mask_action(
        "db_connection_string", "credential",
        outbound_text, db_start, db_start + len(db_url),
    )

    _out, out_span, out_counters = transformer.mask_text(
        outbound_text, [out_action], start_counters=inbound_counters
    )

    assert list(out_span.keys()) == ["<r1>"]
    assert out_span["<r1>"] == db_url
    assert out_counters["__global__"] == 2


def test_multiple_same_detector_still_get_incremental_indices() -> None:
    """A single detector firing twice keeps the per-occurrence index
    behaviour — ``<r0>`` and ``<r1>`` on two different emails."""
    transformer = DLPTransformer()
    content = "contact alice@x.com or bob@y.com for details"
    a = content.index("alice")
    b = content.index("bob")
    actions = [
        _mask_action("email", "pii", content, a, a + len("alice@x.com")),
        _mask_action("email", "pii", content, b, b + len("bob@y.com")),
    ]
    _msgs, span_map, counters = transformer.mask_messages(
        [Message(role="user", content=content)], actions
    )
    assert sorted(span_map.keys()) == ["<r0>", "<r1>"]
    assert counters == {"__global__": 2}
