"""P1-1 — Thai length handling in the query classifier.

`len(text.split())` counted a whole Thai sentence as ONE word, so every Thai
length threshold was dead and long Thai prompts looked trivial. The classifier
now scores Thai-dominant text by CHARACTER count; English keeps its word-based
path unchanged.
"""

from __future__ import annotations

from app.services.query_classifier import classify_query_complexity, QueryComplexity

S, M, C = QueryComplexity.SIMPLE, QueryComplexity.MODERATE, QueryComplexity.COMPLEX


def _c(text):
    return classify_query_complexity([{"role": "user", "content": text}])


# "แมว" (cat) — a 3-char Thai word that is NOT in any signal list, so these
# exercise the LENGTH path in isolation.
def _thai(n_words):
    return "แมว" * n_words


# --- Thai length path ------------------------------------------------------

def test_short_thai_greeting_is_simple():
    assert _c("ไง") == S
    assert _c("สวัสดีครับ") == S


def test_thai_with_signal_is_not_simple():
    # Has "วิเคราะห์" → at least MODERATE regardless of length.
    assert _c("วิเคราะห์โค้ดนี้อย่างละเอียดมากๆ") != S


def test_long_thai_paragraph_no_signal_is_complex():
    # 270 chars, no signal → length pushes it to COMPLEX (was SIMPLE before).
    assert _c(_thai(90)) == C


def test_medium_thai_no_signal_is_moderate():
    assert _c(_thai(50)) == M     # 150 chars → MODERATE
    assert _c(_thai(34)) == M     # 102 chars → MODERATE


def test_short_thai_no_signal_is_simple():
    assert _c(_thai(3)) == S      # 9 chars → SIMPLE (< 30)


# --- English regression (must be unchanged) --------------------------------

def test_english_single_signal_still_moderate():
    assert _c("analyze this data") == M


def test_english_long_still_complex():
    assert _c(" ".join(["word"] * 120)) == C


def test_english_short_still_simple():
    assert _c("hi") == S


def test_english_midlength_no_signal_still_moderate():
    text = ("kindly compose a polite message about the upcoming meeting agenda "
            "for everyone joining us later from the regional partner teams soon")
    assert _c(text) == M


# --- Mixed language --------------------------------------------------------

def test_mixed_majority_english_uses_word_path():
    # Mostly English (< 30% Thai) + a complex signal → EN word path → MODERATE.
    assert _c("please analyze the dataset นะ") == M


def test_mixed_majority_thai_uses_char_path():
    # Mostly Thai + a short EN token → char path; 150+ chars → MODERATE.
    assert _c("note " + _thai(50)) == M
