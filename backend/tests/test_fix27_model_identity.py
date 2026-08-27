"""FIX27 Part 2 — the gateway states the resolved model's identity."""

from __future__ import annotations

from app.schemas.common import Message
from app.services.model_identity import IDENTITY_MARKER, identity_line, inject_model_identity


def test_appends_to_the_end_of_the_first_system_message():
    msgs = [Message(role="system", content="You are helpful."), Message(role="user", content="hi")]
    out = inject_model_identity(msgs, "gpt-5-mini", "openai")
    assert out[0].content.startswith("You are helpful.\n\n" + IDENTITY_MARKER)
    assert "`gpt-5-mini`" in out[0].content and "provider: openai" in out[0].content
    assert out[1] is msgs[1]
    # never mutates the input
    assert msgs[0].content == "You are helpful."


def test_inserts_a_system_message_when_there_is_none():
    out = inject_model_identity([Message(role="user", content="hi")], "claude-sonnet-5", "anthropic")
    assert out[0].role == "system" and out[0].content == identity_line("claude-sonnet-5", "anthropic")
    assert len(out) == 2


def test_fallback_attempt_replaces_the_previous_identity():
    first = inject_model_identity([Message(role="system", content="base")], "gpt-5.6-terra", "openai")
    second = inject_model_identity(first, "gemini-2.5-pro", "google")
    assert second[0].content.count(IDENTITY_MARKER) == 1
    assert "gpt-5.6-terra" not in second[0].content
    assert "`gemini-2.5-pro`" in second[0].content
    assert second[0].content.startswith("base\n\n")


def test_empty_model_is_a_no_op():
    msgs = [Message(role="user", content="hi")]
    assert inject_model_identity(msgs, "", "openai") is msgs
