"""FIX130 B1 — thinking a self-hosted box returns inside ``content`` is split
out of the answer (think_splitter module doc).

The shapes are the ones measured straight from the sranai box in FIX130
step 1 (the raw-shape capture of the FIX130 probes):

* qwen3.5-9b / qwen3.6-35b-a3b — implicit-open: ``Thinking Process: …</think>\\n\\n5``;
* Pathumma-…-think — explicit: ``<think>…</think>\\n\\nanswer``;
* ``reasoning_content`` is never populated by that box.

The real adapter over a fake transport: test_fix130_qwen_stream_adapter.py.
"""

from __future__ import annotations

import pytest

from app.domain.providers import ProviderResponse
from app.domain.routing import ModelCandidate
from app.services.providers import model_registry
from app.services.providers.reasoning_strategies import (
    STRATEGY_ENABLE_THINKING,
    STRATEGY_NONE,
    STRATEGY_REASONING_CONTENT,
)
from app.services.providers.think_splitter import (
    ThinkSplitter,
    in_band_thinking_is_implicit,
    split_response,
)


def _run(splitter: ThinkSplitter, pieces: list[str], finish: str | None = "stop", tools: bool = False):
    reasoning, answer = "", ""
    for p in pieces:
        r, a = splitter.feed(p)
        reasoning += r
        answer += a
    r, a = splitter.finish(finish, saw_tool_calls=tools)
    return reasoning + r, answer + a


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------

QWEN_PIECES = [
    "Thinking", " Process", ":", "\n\n", "1.  **Analyze the Request:** 2+3", "\n", "</think>", "\n\n", "5",
]


def test_implicit_open_qwen_shape_goes_to_reasoning():
    reasoning, answer = _run(ThinkSplitter(implicit_open=True), QWEN_PIECES)
    assert answer == "5"
    assert reasoning.startswith("Thinking Process:")
    assert "</think>" not in reasoning + answer


@pytest.mark.parametrize("cut", range(1, len("</think>")))
def test_closing_tag_split_across_chunks(cut):
    close = "</think>"
    pieces = ["Here's a thinking process: 2+3=5.✅\n", close[:cut], close[cut:] + "\n\n2 + 3 = 5"]
    reasoning, answer = _run(ThinkSplitter(implicit_open=True), pieces)
    assert answer == "2 + 3 = 5"
    assert reasoning == "Here's a thinking process: 2+3=5.✅\n"


@pytest.mark.parametrize("implicit", [True, False])
@pytest.mark.parametrize("cut", range(1, len("<think>")))
def test_explicit_opener_split_across_chunks(implicit, cut):
    opener = "<think>"
    pieces = ["\n" + opener[:cut], opener[cut:] + "\nokay, 2+3", "</th", "ink>\n\n5 ครับ"]
    reasoning, answer = _run(ThinkSplitter(implicit_open=implicit), pieces)
    assert answer == "5 ครับ"
    assert reasoning == "\nokay, 2+3"


def test_literal_think_tag_mid_answer_is_not_eaten():
    """An ordinary answer that talks about the tag keeps it, byte-identical."""
    text = "Wrap the reasoning like this:\n```xml\n<think>plan</think>\n```\nthen answer."
    pieces = [text[i:i + 7] for i in range(0, len(text), 7)]
    reasoning, answer = _run(ThinkSplitter(), pieces)
    assert reasoning == ""
    assert answer == text


def test_answer_starting_with_angle_bracket_is_not_held_or_eaten():
    reasoning, answer = _run(ThinkSplitter(), ["<", "div>hi</div>"])
    assert (reasoning, answer) == ("", "<div>hi</div>")


def test_normal_model_passes_each_fragment_through_immediately():
    s = ThinkSplitter()
    assert s.feed("Hello") == ("", "Hello")
    assert s.feed(" world") == ("", " world")
    assert s.finish("stop") == ("", "")


def test_implicit_cut_by_length_while_thinking_has_no_answer():
    """NIGHT2 N2: 55,798 chars of thinking, max_tokens, never a </think> —
    that was billed and shown as the ANSWER. It is reasoning; the answer is empty."""
    reasoning, answer = _run(
        ThinkSplitter(implicit_open=True),
        ["Thinking Process:\n", '> "5" -> "5" -> ' * 50],
        finish="length",
    )
    assert answer == ""
    assert reasoning.startswith("Thinking Process:")


def test_implicit_natural_stop_without_closer_is_the_answer():
    """The row never entered a think block (e.g. the box stopped opening one):
    the text is handed back as the answer rather than dropped."""
    reasoning, answer = _run(ThinkSplitter(implicit_open=True), ["2 + 3 = ", "5 ครับ"], finish="stop")
    assert answer == "2 + 3 = 5 ครับ"


def test_implicit_unclosed_before_a_tool_call_is_not_re_emitted():
    _, answer = _run(ThinkSplitter(implicit_open=True), ["I should search."], finish="tool_calls", tools=True)
    assert answer == ""


def test_explicit_unclosed_stays_reasoning():
    reasoning, answer = _run(ThinkSplitter(), ["<think>still going"], finish="stop")
    assert (reasoning, answer) == ("still going", "")


def test_upstream_reasoning_field_disables_the_implicit_hold():
    """A box with a reasoning parser keeps content for the answer."""
    s = ThinkSplitter(implicit_open=True)
    s.upstream_separates_reasoning()
    assert s.feed("5") == ("", "5")


# ---------------------------------------------------------------------------
# Which rows get the implicit rule
# ---------------------------------------------------------------------------

def _cand(provider, model, *, reasoning=True, strategy=STRATEGY_REASONING_CONTENT):
    return ModelCandidate(
        provider=provider, model=model, supports_reasoning=reasoning,
        reasoning_strategy=strategy, default_reasoning_mode="auto",
    )


@pytest.mark.parametrize(
    "provider,cand,body,expected",
    [
        ("sranai", _cand("sranai", "qwen3.5-9b"), {}, True),
        ("sranai", _cand("sranai", "qwen3.8-27b-fp8", strategy=STRATEGY_ENABLE_THINKING), {}, True),
        # The request itself switched thinking off: the template closed the block.
        ("sranai", _cand("sranai", "qwen3.8-27b-fp8", strategy=STRATEGY_ENABLE_THINKING),
         {"chat_template_kwargs": {"enable_thinking": False}}, False),
        ("sranai", _cand("sranai", "Typhoon-S", reasoning=False, strategy=STRATEGY_NONE), {}, False),
        ("deepseek", _cand("deepseek", "deepseek-reasoner"), {}, False),
        ("sranai", None, {}, False),
    ],
)
def test_implicit_rule_is_only_for_self_hosted_reasoning_rows(monkeypatch, provider, cand, body, expected):
    monkeypatch.setattr(model_registry, "get_candidate", lambda m: cand)
    assert in_band_thinking_is_implicit(provider, "m", body) is expected


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------

def _resp(content, reasoning=None, finish="stop"):
    return ProviderResponse(content=content, model="m", finish_reason=finish, reasoning_content=reasoning)


def test_non_stream_implicit_split():
    out = split_response(_resp("The user asks 2+3.\n</think>\n\n2 บวก 3 เท่ากับ 5 ครับ"), implicit_open=True)
    assert out.content == "2 บวก 3 เท่ากับ 5 ครับ"
    assert out.reasoning_content == "The user asks 2+3.\n"


def test_non_stream_explicit_split_on_any_row():
    out = split_response(_resp("<think>hmm</think>\n\nanswer"), implicit_open=False)
    assert (out.content, out.reasoning_content) == ("answer", "hmm")


def test_non_stream_normal_answer_is_the_same_object():
    r = _resp("plain <think> talk")
    assert split_response(r, implicit_open=False) is r
