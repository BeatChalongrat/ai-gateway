"""Bare-name substring masking (DLPTransformer._mask_bare_substrings).

Second-pass expansion that runs after the main detector-driven mask
under ``DLP_PSEUDONYM_MODE=true``. Splits multi-word PERSON entries
into individual words and rescans messages for standalone occurrences,
binding each to the SAME pseudonym so multi-turn references to a
person's first or last name resolve to one entity at the LLM.
"""

from __future__ import annotations

import pytest

from app.schemas.common import Message
from app.services.dlp.transformer import DLPTransformer


@pytest.fixture
def pseudonym_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_pseudonym_mode", True, raising=False)
    yield


@pytest.fixture
def pseudonym_off(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "dlp_pseudonym_mode", False, raising=False)
    yield


# ---------------------------------------------------------------------------
# Test 1: bare first name picked up
# ---------------------------------------------------------------------------


def test_bare_first_name_masked(pseudonym_on):
    span_map = {"<<PERSON_A>>": "สมชาย ใจดี"}
    reverse_map: dict[str, str] = {}
    msgs = [Message(role="user", content="สมชาย เคยสั่งซื้อกี่ครั้ง?")]

    result = DLPTransformer()._mask_bare_substrings(
        msgs, span_map, reverse_map
    )

    assert result[0].content == "<<PERSON_A>> เคยสั่งซื้อกี่ครั้ง?"


# ---------------------------------------------------------------------------
# Test 2: bare last name picked up via honorific soft-boundary
# ---------------------------------------------------------------------------


def test_bare_last_name_masked(pseudonym_on):
    span_map = {"<<PERSON_A>>": "สมชาย ใจดี"}
    reverse_map: dict[str, str] = {}
    msgs = [Message(role="user", content="คุณใจดี โทรมาถาม")]

    result = DLPTransformer()._mask_bare_substrings(
        msgs, span_map, reverse_map
    )

    # Honorific-aware soft boundary: ``คุณ`` directly before ``ใจดี``
    # is treated as a valid left boundary, so the surname binds.
    assert "<<PERSON_A>>" in result[0].content
    assert "ใจดี" not in result[0].content


# ---------------------------------------------------------------------------
# Test 3: ambiguous first name skipped
# ---------------------------------------------------------------------------


def test_ambiguous_first_name_skipped(pseudonym_on):
    span_map = {
        "<<PERSON_A>>": "สมชาย ใจดี",
        "<<PERSON_B>>": "สมชาย รักดี",
    }
    reverse_map: dict[str, str] = {}
    msgs = [Message(role="user", content="สมชาย โทรมา")]

    result = DLPTransformer()._mask_bare_substrings(
        msgs, span_map, reverse_map
    )

    # ``สมชาย`` appears in both PERSON entries → ambiguous → no replacement.
    assert result[0].content == "สมชาย โทรมา"


# ---------------------------------------------------------------------------
# Test 4: short name skipped
# ---------------------------------------------------------------------------


def test_short_name_skipped(pseudonym_on):
    # ``กุล`` has 2 non-combining Thai letters (ก, ล) — under the
    # < 3-letter FP guard.
    span_map = {"<<PERSON_A>>": "กุล สมบัติ"}
    reverse_map: dict[str, str] = {}
    msgs = [Message(role="user", content="กุล โทรมา")]

    result = DLPTransformer()._mask_bare_substrings(
        msgs, span_map, reverse_map
    )

    assert result[0].content == "กุล โทรมา"


# ---------------------------------------------------------------------------
# Test 5: non-PERSON category never decomposed
# ---------------------------------------------------------------------------


def test_non_person_category_skipped(pseudonym_on):
    span_map = {"<<EMAIL_1>>": "somchai@gmail.com"}
    reverse_map: dict[str, str] = {}
    msgs = [Message(role="user", content="somchai ส่ง email มา")]

    result = DLPTransformer()._mask_bare_substrings(
        msgs, span_map, reverse_map
    )

    # Word ``somchai`` is the local-part of an EMAIL binding, NOT a
    # PERSON name. The bare-name pass must not touch it.
    assert "somchai" in result[0].content
    assert "<<EMAIL_1>>" not in result[0].content


# ---------------------------------------------------------------------------
# Test 6: full mask_messages flow — turn 1 binds, turn 2 reuses
# ---------------------------------------------------------------------------


def test_multi_turn_bare_name(pseudonym_on):
    """End-to-end through ``mask_messages``: detector catches the full
    name in turn 1, bare-name pass extends to turn 2 in the same call.

    Mirrors the real-LLM E2E Case 3 failure mode (turn 3 ``สมชาย``
    reaching the LLM as cleartext because bare-single-word doesn't
    fit the regex's 2-word requirement).
    """
    from app.domain.dlp import (
        DetectionMatch,
        EnforcementAction,
        MatchAction,
        SensitivityLevel,
    )

    turn1 = "ลูกค้า สมชาย ใจดี สั่งของ"
    turn2 = "สมชาย จ่ายเงินยัง?"
    msgs = [
        Message(role="user", content=turn1),
        Message(role="user", content=turn2),
    ]

    # Build a match_action equivalent to what thai_name_no_prefix would
    # emit for turn 1's "สมชาย ใจดี" span.
    start = turn1.index("สมชาย ใจดี")
    end = start + len("สมชาย ใจดี")
    action = MatchAction(
        match=DetectionMatch(
            detector_name="thai_name_no_prefix",
            entity_type="thai_name",
            category="pii",
            start=start,
            end=end,
            matched_text="สมชาย ใจดี",
            severity=SensitivityLevel.MEDIUM,
        ),
        action=EnforcementAction.MASK,
        reason_code="pii_mask_policy",
        reason_message="pii mask policy",
    )

    new_msgs, span_map, _ = DLPTransformer().mask_messages(msgs, [action])

    # Turn 1: full name → pseudonym.
    assert "<<PERSON_A>>" in new_msgs[0].content
    assert "สมชาย ใจดี" not in new_msgs[0].content
    # Turn 2: bare first name → SAME pseudonym (bare-name pass).
    assert new_msgs[1].content == "<<PERSON_A>> จ่ายเงินยัง?"
    assert span_map == {"<<PERSON_A>>": "สมชาย ใจดี"}


# ---------------------------------------------------------------------------
# Test 7: bare-name pass is gated on DLP_PSEUDONYM_MODE
# ---------------------------------------------------------------------------


def test_only_in_pseudonym_mode(pseudonym_off):
    # Legacy mode never emits ``<<PERSON_*>>``, so realistically this
    # span_map can't exist — but the gate must still bail out cleanly
    # for any caller that hand-constructs one (e.g. test fixtures).
    span_map = {"<r0>": "สมชาย ใจดี"}
    reverse_map: dict[str, str] = {}
    msgs = [Message(role="user", content="สมชาย โทรมา")]

    result = DLPTransformer()._mask_bare_substrings(
        msgs, span_map, reverse_map
    )

    # Flag off → no substitution attempted.
    assert result[0].content == "สมชาย โทรมา"
    assert reverse_map == {}
