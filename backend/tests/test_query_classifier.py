"""Tests for the Auto-Thinking query-complexity classifier.

P0-1: `query_classifier.py` made routing decisions with ZERO test coverage.
These are CHARACTERIZATION tests — they pin the classifier's ACTUAL current
behavior (including its known weaknesses, documented inline), so future tuning
is a deliberate, visible change rather than a silent drift.

Levels: SIMPLE / MODERATE / COMPLEX. COMPLEX requires complex_score >= 2, so a
SINGLE complex signal lands MODERATE by design (documented below).
"""

from __future__ import annotations

import pytest

from app.services.query_classifier import (
    QueryComplexity,
    classify_query_complexity,
    _COMPLEX_SIGNALS,
    _SIMPLE_SIGNALS,
)

S, M, C = QueryComplexity.SIMPLE, QueryComplexity.MODERATE, QueryComplexity.COMPLEX


def _c(text):
    return classify_query_complexity([{"role": "user", "content": text}])


# ===========================================================================
# SIMPLE — greetings / trivia / short lookups
# ===========================================================================


@pytest.mark.parametrize("text", [
    "ไง", "hi", "hello", "ขอบคุณ", "ok", "555", "👍", "ดี", "จ้า", "bye",
    "วันนี้วันอะไร", "แปลคำว่า apple", "1+1 เท่ากับเท่าไหร่",
])
def test_simple_level(text):
    assert _c(text) == S


# ===========================================================================
# COMPLEX — needs >= 2 complex signals, or a code fence, or a long EN message
# ===========================================================================


def test_two_complex_signals_thai():
    assert _c("วิเคราะห์และเปรียบเทียบข้อมูลนี้") == C


def test_complex_signal_plus_code_fence():
    assert _c("debug this code:\n```python\nx = 1/0\n```") == C


def test_code_fence_alone_is_complex():
    # "```" alone contributes +2 → COMPLEX.
    assert _c("```\nSELECT * FROM users;\n```") == C


def test_long_english_message_is_complex():
    # > 100 words → +2 → COMPLEX.
    assert _c(" ".join(["word"] * 120)) == C


# ===========================================================================
# MODERATE — a SINGLE complex signal lands here (COMPLEX needs >= 2)
# ===========================================================================


def test_single_complex_signal_is_moderate_thai():
    # KNOWN BEHAVIOR: one signal → complex_score=1 → MODERATE, not COMPLEX.
    assert _c("วิเคราะห์ข้อมูลนี้") == M


def test_single_complex_signal_is_moderate_english():
    assert _c("analyze this data") == M


def test_moderate_default_midlength_no_signal():
    # 15-50 words, no signal, no SIMPLE substring → falls through to MODERATE.
    text = ("please write a short friendly note about the weather today for my "
            "friend who lives far away now and misses home very much indeed")
    assert _c(text) == M


# ===========================================================================
# Edge cases — must never crash
# ===========================================================================


@pytest.mark.parametrize("messages", [
    [], None, [{"role": "user", "content": None}],
    [{"role": "user", "content": "   "}],
    [{"role": "user"}],                       # missing content key
    ["not-a-dict", 42, None],                 # non-dict entries skipped
    [{"role": "assistant", "content": "hello"}],  # no user message
])
def test_edge_inputs_never_crash(messages):
    out = classify_query_complexity(messages)
    assert out in (S, M, C)


def test_emoji_only_is_simple():
    assert _c("👍👍👍") == S


def test_whitespace_only_is_simple():
    assert _c("   \n  \t ") == S


# ===========================================================================
# Multimodal content — KNOWN GAP: image/file blocks add NO complexity bump
# ===========================================================================


def test_multimodal_extracts_text_only_no_attachment_bump():
    # The docstring claims a non-text block is "a complexity bump"; in reality
    # only the text parts are scored and the image block is ignored. So a
    # vision turn classifies the SAME as its text alone. Documented gap
    # (Python side); the Go resolver DOES treat hasImages as complex.
    with_image = classify_query_complexity([{
        "role": "user",
        "content": [{"type": "text", "text": "วิเคราะห์"}, {"type": "image_url"}],
    }])
    text_only = _c("วิเคราะห์")
    assert with_image == text_only == M  # image did not bump it to COMPLEX


# ===========================================================================
# Substring false-positives — KNOWN ISSUE (documented, not yet fixed)
# ===========================================================================


def test_known_substring_false_positive_hi_in_this():
    # "hi" is a SIMPLE signal and is a substring of "this"/"thinking", so an
    # 18-word neutral sentence containing "this" is pulled to SIMPLE even
    # though it would otherwise default to MODERATE. Pinned here as a KNOWN
    # ISSUE so a future fix (word-boundary matching) is a deliberate change.
    with_this = _c("this is a perfectly ordinary statement of about eighteen "
                   "words written purely to exercise the moderate length path here")
    assert with_this == S  # the "hi"-in-"this" false positive wins


# ===========================================================================
# Thai language — registered signals + particles
# ===========================================================================


def test_every_thai_complex_signal_is_recognized():
    thai_complex = [s for s in _COMPLEX_SIGNALS if any("฀" <= ch <= "๿" for ch in s)]
    assert thai_complex, "expected Thai complex signals in the list"
    for sig in thai_complex:
        # A single signal → MODERATE (or COMPLEX if the phrase itself trips 2).
        assert _c(sig) in (M, C), f"Thai complex signal not recognized: {sig}"


def test_every_thai_simple_signal_is_simple():
    thai_simple = [s for s in _SIMPLE_SIGNALS if any("฀" <= ch <= "๿" for ch in s)]
    assert thai_simple
    for sig in thai_simple:
        assert _c(sig) == S, f"Thai simple signal not SIMPLE: {sig}"


@pytest.mark.parametrize("particle", ["ครับ", "ค่ะ", "นะ", "จ้า"])
def test_thai_particles_do_not_change_level(particle):
    base = _c("วิเคราะห์ข้อมูลนี้")
    withp = _c("วิเคราะห์ข้อมูลนี้" + particle)
    assert base == withp == M


def test_thai_greeting_with_particle_is_simple():
    assert _c("สวัสดีครับ") == S


# ===========================================================================
# Provider mapping (verify the auto tier the classifier feeds into)
# ===========================================================================


def test_auto_mode_provider_budgets():
    from app.services.providers.anthropic_adapter import _REASONING_BUDGET_TOKENS
    from app.services.providers.openai_adapter import _REASONING_EFFORT_MAP
    from app.services.providers.google_adapter import _GEMINI_THINKING_LEVEL_MAP

    assert _REASONING_BUDGET_TOKENS["auto"] == 4096
    assert _REASONING_EFFORT_MAP["auto"] == "medium"
    assert _GEMINI_THINKING_LEVEL_MAP["auto"] == "MEDIUM"


def test_classifier_never_raises_on_garbage():
    for bad in [None, [], [{}], [{"role": "user", "content": 12345}],
                [{"role": "user", "content": ["x", 1, None]}]]:
        assert classify_query_complexity(bad) in (S, M, C)
