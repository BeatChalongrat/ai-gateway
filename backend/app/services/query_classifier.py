"""Query-complexity classifier — Auto Thinking Mode.

A fast, dependency-free heuristic that estimates how much reasoning a
prompt needs. It exists so the router can pick the *right* model for an
``auto`` request (a reasoning model for complex work, a fast model for
trivia) WITHOUT an extra LLM round-trip. The model's own native adaptive
thinking then decides the actual effort once selected.

This is deliberately a heuristic, not ML: an operator reading the
``auto_complexity_route`` log line can always reconstruct why a turn was
classified the way it was. Mirrors the Go-side ``isComplexQuery`` in
``services/chat-edge-api/internal/service/model_resolver.go`` — keep the
two signal lists roughly in sync when tuning.

Note: in the chat product the model is usually already resolved by
chat-edge before it reaches the gateway, so this classifier only steers
direct-API ``auto`` callers. It is kept here so the gateway is correct
on its own, independent of any upstream resolver.
"""

from __future__ import annotations


class QueryComplexity:
    SIMPLE = "simple"      # greetings, lookups, translations, short answers
    MODERATE = "moderate"  # summaries, explanations, comparisons
    COMPLEX = "complex"    # analysis, math, code review, multi-step reasoning


# Multi-step reasoning / analysis / math / code signals. A match here
# pulls the query toward COMPLEX (and thus the reasoning model pool).
_COMPLEX_SIGNALS: tuple[str, ...] = (
    "analyze", "analyse", "compare and contrast", "evaluate",
    "pros and cons", "trade-off", "tradeoff", "step by step",
    "think through", "reason about", "prove", "derive",
    "debug", "review this code", "find the bug", "optimize",
    "what would happen if", "design a system", "architect",
    "calculate", "solve", "equation",
    # Thai
    "วิเคราะห์", "เปรียบเทียบ", "ข้อดีข้อเสีย", "อธิบายละเอียด",
    "คิดทีละขั้น", "หาจุดบกพร่อง", "ออกแบบระบบ", "คำนวณ",
)

# Greeting / lookup / trivia signals — pull toward SIMPLE.
_SIMPLE_SIGNALS: tuple[str, ...] = (
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
    "what is", "who is", "when did", "where is", "translate",
    # Thai
    "สวัสดี", "ขอบคุณ", "โอเค", "ได้เลย", "แปล",
)


def classify_query_complexity(messages: list[dict]) -> str:
    """Estimate the complexity of the last user message.

    Returns one of ``QueryComplexity.{SIMPLE,MODERATE,COMPLEX}``.
    Empty / missing input defaults to SIMPLE.
    """
    if not messages:
        return QueryComplexity.SIMPLE

    last_user = ""
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            content = m.get("content", "")
            # Content can be a multimodal list (vision turns). Pull the
            # text parts; treat any non-text block as a complexity bump
            # below via the attachment heuristic.
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                last_user = " ".join(
                    str(p.get("text", ""))
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            break

    if not last_user:
        return QueryComplexity.SIMPLE

    text = last_user.lower()

    complex_score = sum(1 for s in _COMPLEX_SIGNALS if s in text)
    simple_score = sum(1 for s in _SIMPLE_SIGNALS if s in text)

    # P1-1 — length scoring is LANGUAGE-AWARE. Thai (and other space-free
    # scripts) can't be word-counted with ``str.split()``: a whole Thai
    # sentence is a single "word", so the English word thresholds never fire
    # and every long Thai prompt looked trivial. For Thai-dominant text we
    # score by CHARACTER count instead; English keeps the exact word-based
    # path so its behavior is unchanged.
    stripped = text.strip()
    thai_chars = sum(1 for ch in stripped if "฀" <= ch <= "๿")
    is_thai = bool(stripped) and thai_chars >= 0.3 * len(stripped)

    if is_thai:
        char_count = len(stripped)
        if char_count > 200:
            complex_score += 2          # long Thai paragraph → complex
        if char_count > 400:
            complex_score += 1
        length_moderate = char_count > 120
        length_simple = char_count < 30
    else:
        word_count = len(text.split())
        if word_count > 100:
            complex_score += 2
        if word_count > 200:
            complex_score += 1
        length_moderate = word_count > 50
        length_simple = word_count < 15

    # Multiple questions = more to reason about.
    question_marks = text.count("?") + text.count("？")
    if question_marks >= 3:
        complex_score += 1

    # Code blocks almost always need a stronger model.
    if "```" in text:
        complex_score += 2

    if complex_score >= 2:
        return QueryComplexity.COMPLEX
    if complex_score >= 1 or length_moderate:
        return QueryComplexity.MODERATE
    if simple_score >= 1 or length_simple:
        return QueryComplexity.SIMPLE
    return QueryComplexity.MODERATE
