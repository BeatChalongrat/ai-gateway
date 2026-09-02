"""FIX27 Part 1 — vision (list-content) messages are masked, not passed through.

Before FIX27 ``mask_messages`` skipped every message whose ``content`` was a
list (image + text blocks): the scanners flatten such a message to its
text blocks and DO find PII in it, but the transformer left the blocks in
clear — so any turn that carried an image (web-chat attachment, desktop
attachment, computer-use screenshot) carried its text around the masking
layer. These tests pin the fix: text blocks are masked on the flattened
view's offsets, image blocks are untouched, string messages are unchanged.
"""

from __future__ import annotations

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    MatchAction,
    SensitivityLevel,
)
from app.schemas.common import Message
from app.services.dlp.transformer import (
    DLPTransformer,
    apply_replacements_to_blocks,
    flatten_list_content,
)

IMG = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


def _email(text: str, start: int, end: int, message_index: int | None = None) -> MatchAction:
    metadata = {"policy_v2": {"message_index": message_index}} if message_index is not None else {}
    return MatchAction(
        match=DetectionMatch(
            detector_name="email",
            entity_type="email_address",
            category="pii",
            start=start,
            end=end,
            matched_text=text[start:end],
            severity=SensitivityLevel.MEDIUM,
            metadata=metadata,
        ),
        action=EnforcementAction.MASK,
        reason_code="test",
        reason_message="test email mask",
    )


def _span(text: str, needle: str) -> tuple[int, int]:
    s = text.index(needle)
    return s, s + len(needle)


def test_flatten_matches_the_scanners_view() -> None:
    blocks = [IMG, {"type": "text", "text": "a"}, IMG, {"type": "text", "text": "b"}]
    assert flatten_list_content(blocks) == "a\nb"
    assert flatten_list_content([IMG]) == ""


def test_text_block_after_an_image_is_masked_and_the_image_is_untouched() -> None:
    text = "ส่งไปที่ alice@co.com ด้วย"
    msg = Message(role="user", content=[IMG, {"type": "text", "text": text}])
    s, e = _span(text, "alice@co.com")
    masked, span_map, _ = DLPTransformer().mask_messages([msg], [_email(text, s, e)])

    out = masked[0].content
    assert isinstance(out, list) and len(out) == 2
    assert out[0] == IMG, "image block must ride through byte-identical"
    assert "alice@co.com" not in out[1]["text"]
    assert "<r0>" in out[1]["text"]
    assert span_map == {"<r0>": "alice@co.com"}
    # the original message object is not mutated
    assert msg.content[1]["text"] == text


def test_offsets_index_the_flattened_view_across_two_text_blocks() -> None:
    first, second = "hello", "mail bob@co.com now"
    flat = first + "\n" + second
    msg = Message(
        role="user",
        content=[{"type": "text", "text": first}, IMG, {"type": "text", "text": second}],
    )
    s, e = _span(flat, "bob@co.com")
    masked, span_map, _ = DLPTransformer().mask_messages([msg], [_email(flat, s, e)])

    out = masked[0].content
    assert out[0]["text"] == first
    assert out[1] == IMG
    assert out[2]["text"] == "mail <r0> now"
    assert span_map == {"<r0>": "bob@co.com"}


def test_message_index_bound_finding_lands_on_its_own_list_message() -> None:
    text = "contact carol@co.com"
    s, e = _span(text, "carol@co.com")
    m0 = Message(role="user", content="unrelated first turn")
    m1 = Message(role="user", content=[IMG, {"type": "text", "text": text}])
    masked, span_map, _ = DLPTransformer().mask_messages(
        [m0, m1], [_email(text, s, e, message_index=1)]
    )
    assert masked[0].content == "unrelated first turn"
    assert masked[1].content[1]["text"] == "contact <r0>"
    assert span_map == {"<r0>": "carol@co.com"}


def test_span_straddling_the_block_joint_leaves_no_half_in_clear() -> None:
    blocks = [{"type": "text", "text": "abc@x"}, {"type": "text", "text": "y.com tail"}]
    # a (synthetic) span covering "abc@x\ny.com" — offsets 0..11 in "abc@x\ny.com tail"
    out = apply_replacements_to_blocks(blocks, [(0, 11, "<r0>", "abc@x\ny.com")])
    assert out[0]["text"] == "<r0>"
    assert out[1]["text"] == "<r0> tail"


def test_string_messages_are_unchanged_by_the_change() -> None:
    text = "send to dave@co.com"
    s, e = _span(text, "dave@co.com")
    masked, span_map, counters = DLPTransformer().mask_messages(
        [Message(role="user", content=text)], [_email(text, s, e)]
    )
    assert masked[0].content == "send to <r0>"
    assert span_map == {"<r0>": "dave@co.com"}
    assert counters


def test_list_message_without_findings_is_copied_intact() -> None:
    msg = Message(role="user", content=[IMG, {"type": "text", "text": "no pii here"}])
    masked, span_map, _ = DLPTransformer().mask_messages([msg], [])
    assert masked[0].content == msg.content
    assert span_map == {}
