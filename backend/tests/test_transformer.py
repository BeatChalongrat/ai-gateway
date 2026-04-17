"""Tests for P3-06: Text transformation engine.

Verifies:
- Single span mask and redact
- Multiple non-overlapping spans in reverse-offset order
- ALLOW and BLOCK actions leave text unchanged
- Overlapping span resolution (higher action wins)
- Adjacent (non-overlapping) spans
- Empty match_actions → text unchanged
- TransformedSpan records accuracy
- transform_messages batch processing
- Input immutability
"""

import pytest

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
    TransformationResult,
)
from app.schemas.common import Message
from app.services.dlp.transformer import (
    DEFAULT_MASK_TOKEN,
    DLPTransformer,
    MASK_TOKENS,
    mask_token_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match_at(start: int, end: int, text_slice: str) -> DetectionMatch:
    """Create a DetectionMatch at specific offsets."""
    return DetectionMatch(
        detector_name="test",
        entity_type="test_entity",
        category="pii",
        start=start,
        end=end,
        matched_text=text_slice,
        severity=SensitivityLevel.MEDIUM,
    )


def _action(
    start: int,
    end: int,
    text_slice: str,
    action: EnforcementAction = EnforcementAction.MASK,
    category: str = "pii",
) -> MatchAction:
    return MatchAction(
        match=DetectionMatch(
            detector_name="test",
            entity_type="test_entity",
            category=category,
            start=start,
            end=end,
            matched_text=text_slice,
            severity=SensitivityLevel.MEDIUM,
        ),
        action=action,
        reason_code="test",
        reason_message="test reason",
    )


transformer = DLPTransformer()


# ---------------------------------------------------------------------------
# Single span — MASK
# ---------------------------------------------------------------------------


class TestSingleMask:
    def test_email_masked(self):
        text = "contact user@example.com please"
        email = "user@example.com"
        start = text.index(email)
        end = start + len(email)
        # _action uses detector_name="test" which falls back to DEFAULT_MASK_TOKEN
        ma = _action(start, end, email, EnforcementAction.MASK)

        result = transformer.transform_text(text, [ma])

        assert result.transformed_text == f"contact {DEFAULT_MASK_TOKEN} please"
        assert result.transformations_applied == 1

    def test_span_record_correct(self):
        text = "hi user@test.com bye"
        email = "user@test.com"
        start = text.index(email)
        end = start + len(email)
        ma = _action(start, end, email, EnforcementAction.MASK)

        result = transformer.transform_text(text, [ma])
        span = result.transformed_spans[0]

        assert span.start == start
        assert span.end == end
        assert span.original_length == len(email)
        assert span.replacement_length == len(DEFAULT_MASK_TOKEN)
        assert span.action == EnforcementAction.MASK

    def test_mask_at_start_of_text(self):
        text = "secret123 is the password"
        ma = _action(0, 9, "secret123", EnforcementAction.MASK)
        result = transformer.transform_text(text, [ma])
        assert result.transformed_text.endswith(" is the password")
        assert result.transformations_applied == 1

    def test_mask_at_end_of_text(self):
        text = "password is secret123"
        start = text.index("secret123")
        ma = _action(start, start + 9, "secret123", EnforcementAction.MASK)
        result = transformer.transform_text(text, [ma])
        assert result.transformed_text.startswith("password is ")
        assert result.transformations_applied == 1


# ---------------------------------------------------------------------------
# Single span — REDACT
# ---------------------------------------------------------------------------


class TestSingleRedact:
    def test_redacted_replacement(self):
        text = "SSN is 123-45-6789 here"
        ssn = "123-45-6789"
        start = text.index(ssn)
        end = start + len(ssn)
        ma = _action(start, end, ssn, EnforcementAction.REDACT)

        result = transformer.transform_text(text, [ma])
        assert result.transformed_text == "SSN is [REDACTED] here"
        assert result.transformations_applied == 1

    def test_redact_span_record(self):
        text = "SSN: 123-45-6789"
        ssn = "123-45-6789"
        start = text.index(ssn)
        ma = _action(start, start + len(ssn), ssn, EnforcementAction.REDACT)

        result = transformer.transform_text(text, [ma])
        span = result.transformed_spans[0]
        assert span.action == EnforcementAction.REDACT
        assert span.replacement_length == len("[REDACTED]")


# ---------------------------------------------------------------------------
# ALLOW and BLOCK — no text modification
# ---------------------------------------------------------------------------


class TestNoModification:
    def test_allow_leaves_text_unchanged(self):
        text = "hello user@test.com world"
        ma = _action(6, 19, "user@test.com", EnforcementAction.ALLOW)
        result = transformer.transform_text(text, [ma])
        assert result.transformed_text == text
        assert result.transformations_applied == 0

    def test_block_leaves_text_unchanged(self):
        text = "hello user@test.com world"
        ma = _action(6, 19, "user@test.com", EnforcementAction.BLOCK)
        result = transformer.transform_text(text, [ma])
        assert result.transformed_text == text
        assert result.transformations_applied == 0

    def test_empty_actions_returns_original(self):
        text = "nothing to do here"
        result = transformer.transform_text(text, [])
        assert result.transformed_text == text
        assert result.transformations_applied == 0


# ---------------------------------------------------------------------------
# Multiple non-overlapping spans
# ---------------------------------------------------------------------------


class TestMultipleSpans:
    def test_two_masks(self):
        text = "a user@a.com b user@b.com c"
        e1 = "user@a.com"
        e2 = "user@b.com"
        s1 = text.index(e1)
        s2 = text.index(e2)

        result = transformer.transform_text(text, [
            _action(s1, s1 + len(e1), e1, EnforcementAction.MASK),
            _action(s2, s2 + len(e2), e2, EnforcementAction.MASK),
        ])

        assert result.transformations_applied == 2
        # Original emails should not appear in output
        assert "user@a.com" not in result.transformed_text
        assert "user@b.com" not in result.transformed_text
        # Surrounding text preserved
        assert result.transformed_text.startswith("a ")
        assert result.transformed_text.endswith(" c")

    def test_mask_and_redact_together(self):
        text = "email: user@test.com ssn: 123-45-6789"
        email = "user@test.com"
        ssn = "123-45-6789"
        se = text.index(email)
        ss = text.index(ssn)

        result = transformer.transform_text(text, [
            _action(se, se + len(email), email, EnforcementAction.MASK),
            _action(ss, ss + len(ssn), ssn, EnforcementAction.REDACT),
        ])

        assert result.transformations_applied == 2
        assert "user@test.com" not in result.transformed_text
        assert "[REDACTED]" in result.transformed_text

    def test_spans_in_forward_order(self):
        """TransformedSpan records should be in ascending start order."""
        text = "a user@a.com b user@b.com c"
        e1 = "user@a.com"
        e2 = "user@b.com"
        s1 = text.index(e1)
        s2 = text.index(e2)

        result = transformer.transform_text(text, [
            _action(s2, s2 + len(e2), e2, EnforcementAction.MASK),  # later span first in input
            _action(s1, s1 + len(e1), e1, EnforcementAction.MASK),
        ])

        starts = [s.start for s in result.transformed_spans]
        assert starts == sorted(starts)

    def test_adjacent_spans_not_confused(self):
        """Two spans that touch but don't overlap: [0,5) and [5,10)."""
        text = "AAAAABBBBB"
        result = transformer.transform_text(text, [
            _action(0, 5, "AAAAA", EnforcementAction.MASK),
            _action(5, 10, "BBBBB", EnforcementAction.REDACT),
        ])
        assert result.transformations_applied == 2
        assert "AAAAA" not in result.transformed_text
        assert "BBBBB" not in result.transformed_text


# ---------------------------------------------------------------------------
# Overlapping spans
# ---------------------------------------------------------------------------


class TestOverlappingSpans:
    def test_overlap_keeps_more_restrictive(self):
        """When MASK and REDACT overlap, REDACT wins (higher action value)."""
        text = "overlap1234567890end"
        # Span A: [0, 10) → MASK
        # Span B: [5, 15) → REDACT (overlaps A)
        result = transformer.transform_text(text, [
            _action(0, 10, "overlap1234", EnforcementAction.MASK),
            _action(5, 15, "1234567890e", EnforcementAction.REDACT),
        ])
        # REDACT wins, only one transformation applied
        assert result.transformations_applied == 1
        assert result.transformed_spans[0].action == EnforcementAction.REDACT

    def test_overlap_mask_vs_mask_keeps_first(self):
        """When two MASK overlap with same action, earlier start wins."""
        text = "overlap1234567890end"
        result = transformer.transform_text(text, [
            _action(0, 10, "overlap1234", EnforcementAction.MASK),
            _action(5, 15, "1234567890e", EnforcementAction.MASK),
        ])
        assert result.transformations_applied == 1
        assert result.transformed_spans[0].start == 0  # earlier span kept

    def test_fully_contained_span_resolved(self):
        """Inner span fully inside outer span: more restrictive wins."""
        text = "aaaBBBBBBaaa"
        # Outer: [0, 12) → MASK
        # Inner: [3, 9) → REDACT
        result = transformer.transform_text(text, [
            _action(0, 12, "aaaBBBBBBaaa", EnforcementAction.MASK),
            _action(3, 9, "BBBBBB", EnforcementAction.REDACT),
        ])
        assert result.transformations_applied == 1
        assert result.transformed_spans[0].action == EnforcementAction.REDACT

    def test_non_overlapping_after_overlap_resolution(self):
        """Three spans: A overlaps B, C is separate. A wins over B, C kept."""
        text = "aaaaaXXXXXbbbbbbbbbbcccccc"
        result = transformer.transform_text(text, [
            _action(0, 10, "aaaaaXXXXX", EnforcementAction.MASK),
            _action(5, 15, "XXXXXbbbbb", EnforcementAction.MASK),  # overlaps A
            _action(20, 26, "cccccc", EnforcementAction.REDACT),   # separate
        ])
        assert result.transformations_applied == 2


# ---------------------------------------------------------------------------
# transform_messages
# ---------------------------------------------------------------------------


class TestTransformMessages:
    def test_single_message(self):
        messages = [Message(role="user", content="email user@test.com here")]
        email = "user@test.com"
        start = messages[0].content.index(email)
        actions = [_action(start, start + len(email), email, EnforcementAction.MASK)]

        new_msgs, results = transformer.transform_messages(messages, actions)

        assert len(new_msgs) == 1
        assert "user@test.com" not in new_msgs[0].content
        assert new_msgs[0].role == "user"
        assert results[0].transformations_applied == 1

    def test_multiple_messages(self):
        messages = [
            Message(role="user", content="email user@a.com"),
            Message(role="assistant", content="got it"),
            Message(role="user", content="ssn 123-45-6789"),
        ]
        email = "user@a.com"
        ssn = "123-45-6789"
        se = messages[0].content.index(email)
        ss = messages[2].content.index(ssn)

        actions = [
            _action(se, se + len(email), email, EnforcementAction.MASK),
            _action(ss, ss + len(ssn), ssn, EnforcementAction.REDACT),
        ]

        new_msgs, results = transformer.transform_messages(messages, actions)

        assert len(new_msgs) == 3
        assert "user@a.com" not in new_msgs[0].content
        assert new_msgs[1].content == "got it"  # untouched
        assert "[REDACTED]" in new_msgs[2].content

    def test_original_messages_not_mutated(self):
        original_content = "email user@test.com"
        messages = [Message(role="user", content=original_content)]
        email = "user@test.com"
        start = messages[0].content.index(email)
        actions = [_action(start, start + len(email), email, EnforcementAction.MASK)]

        new_msgs, _ = transformer.transform_messages(messages, actions)

        assert messages[0].content == original_content  # original unchanged
        assert new_msgs[0].content != original_content  # new one is different
        assert new_msgs[0] is not messages[0]

    def test_empty_messages(self):
        new_msgs, results = transformer.transform_messages([], [])
        assert new_msgs == []
        assert results == []

    def test_no_matching_actions_leaves_unchanged(self):
        messages = [Message(role="user", content="clean text")]
        new_msgs, results = transformer.transform_messages(messages, [])
        assert new_msgs[0].content == "clean text"
        assert results[0].transformations_applied == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_text(self):
        result = transformer.transform_text("", [])
        assert result.transformed_text == ""
        assert result.transformations_applied == 0

    def test_entire_text_is_one_span(self):
        text = "user@test.com"
        ma = _action(0, len(text), text, EnforcementAction.REDACT)
        result = transformer.transform_text(text, [ma])
        assert result.transformed_text == "[REDACTED]"

    def test_mask_uses_descriptive_token_by_detector(self):
        """Masking emits a descriptive placeholder based on detector_name."""
        text = "my email is user@example.com here"
        email = "user@example.com"
        start = text.index(email)
        # Build a MatchAction whose detector_name maps to a known token
        ma = MatchAction(
            match=DetectionMatch(
                detector_name="email",
                entity_type="email_address",
                category="pii",
                start=start,
                end=start + len(email),
                matched_text=email,
                severity=SensitivityLevel.MEDIUM,
            ),
            action=EnforcementAction.MASK,
            reason_code="pii_mask_policy",
            reason_message="email masked",
        )
        result = transformer.transform_text(text, [ma])
        assert result.transformed_text == "my email is [EMAIL] here"

    def test_mask_unknown_detector_falls_back_to_default_token(self):
        ma = _action(0, 5, "abcde", EnforcementAction.MASK)
        # _action uses detector_name="test" (not in MASK_TOKENS)
        result = transformer.transform_text("abcde tail", [ma])
        assert result.transformed_text == f"{DEFAULT_MASK_TOKEN} tail"

    def test_mask_token_lookup_helper(self):
        assert mask_token_for("email") == "[EMAIL]"
        assert mask_token_for("ssn") == "[SSN]"
        assert mask_token_for("credit_card") == "[CREDIT_CARD]"
        assert mask_token_for("unknown_detector") == DEFAULT_MASK_TOKEN
