"""FIX130 — thinking that arrives INSIDE ``content``, split out of the answer.

A self-hosted vLLM box that runs without a reasoning parser returns the
model's thinking in ``delta.content``; ``reasoning_content`` is never
populated. Measured on the sranai box (FIX130 step 1, raw samples in
``scripts/probes/FIX130/results/ans``), two shapes:

* explicit — ``<think>…</think>answer`` (Pathumma-…-qwen3-8b-think): the
  model writes the opener itself;
* implicit-open — ``…thinking…</think>\\n\\nanswer`` (qwen3.5-9b,
  qwen3.6-35b-a3b): the chat template opens the block in the PROMPT, so
  the completion starts inside it and only the closer is ever streamed.

Before this module the gateway split reasoning from the answer by field
name only, so both shapes reached the user as the answer: 55,798
characters of "Thinking Process:" billed as a completed qwen3.5-9b turn
(NIGHT2 N2), and qwen3.6 quoting ``<language_rules>`` verbatim.

The rule, deliberately narrow so an ordinary answer is never touched:

* explicit form, every OpenAI-compatible stream: ``<think>`` counts only
  as the FIRST non-whitespace text of the completion. A literal
  ``<think>`` anywhere later (a code block, an answer about XML) is
  content and passes through byte-identical;
* implicit-open form, only when the caller says the row can produce it
  (:func:`in_band_thinking_is_implicit`): the completion is thinking
  until the first ``</think>``. If the stream stops NATURALLY
  (``finish_reason == "stop"``, no tool call) without a closer, the model
  never was in a think block — the held text is re-emitted as the answer
  rather than dropped. A completion cut off by the length limit while
  still thinking stays reasoning: that turn has no answer, which is the
  truth (the output-limit path reports it).

Tags split across chunks work: a trailing partial tag (``"</thi"``) is
held until the next fragment decides it. The hold is at most
``len("</think>") - 1`` characters and only while a tag could still be
forming, so a normal answer is not delayed.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.providers import ProviderResponse

OPEN_TAG = "<think>"
CLOSE_TAG = "</think>"

_START, _THINKING, _ANSWER = "start", "thinking", "answer"


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of ``text`` that is a proper prefix of ``tag``."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-n:]):
            return n
    return 0


class ThinkSplitter:
    """Per-completion state machine: content text in, (reasoning, answer) out."""

    def __init__(self, *, implicit_open: bool = False) -> None:
        self._implicit = implicit_open
        self._state = _START
        self._pending = ""
        # Implicit mode only: everything routed to reasoning without an
        # explicit opener, kept so a natural stop without ``</think>`` can
        # hand it back as the answer.
        self._implicit_thought: list[str] | None = None
        self._strip_answer_lead = False
        self._finished = False

    @property
    def state(self) -> str:
        return self._state

    def upstream_separates_reasoning(self) -> None:
        """The provider streamed a ``reasoning_content`` field itself.

        A box running a reasoning parser keeps ``content`` for the answer,
        so the implicit-open assumption must not hold the answer back.
        Only switches while nothing has been classified yet.
        """
        if self._state == _START:
            self._implicit = False

    def feed(self, text: str) -> tuple[str, str]:
        """Consume one content fragment; return ``(reasoning, answer)`` to emit now."""
        if not text:
            return "", ""
        reasoning: list[str] = []
        answer: list[str] = []
        buf = self._pending + text
        self._pending = ""
        while buf:
            if self._state == _START:
                lead = buf.lstrip()
                if not lead or (len(lead) < len(OPEN_TAG) and OPEN_TAG.startswith(lead)):
                    # Only whitespace, or a still-forming opener: undecided.
                    self._pending = buf
                    buf = ""
                elif lead.startswith(OPEN_TAG):
                    self._state = _THINKING
                    buf = lead[len(OPEN_TAG):]
                elif self._implicit:
                    self._state = _THINKING
                    self._implicit_thought = []
                    buf = lead
                else:
                    self._state = _ANSWER
            elif self._state == _THINKING:
                idx = buf.find(CLOSE_TAG)
                if idx >= 0:
                    self._emit_thought(reasoning, buf[:idx])
                    self._state = _ANSWER
                    self._implicit_thought = None
                    self._strip_answer_lead = True
                    buf = buf[idx + len(CLOSE_TAG):]
                else:
                    hold = _partial_suffix(buf, CLOSE_TAG)
                    self._emit_thought(reasoning, buf[: len(buf) - hold])
                    self._pending = buf[len(buf) - hold:]
                    buf = ""
            else:  # _ANSWER
                if self._strip_answer_lead:
                    buf = buf.lstrip()
                    if not buf:
                        break
                    self._strip_answer_lead = False
                answer.append(buf)
                buf = ""
        return "".join(reasoning), "".join(answer)

    def _emit_thought(self, out: list[str], text: str) -> None:
        if not text:
            return
        out.append(text)
        if self._implicit_thought is not None:
            self._implicit_thought.append(text)

    def finish(self, finish_reason: str | None, *, saw_tool_calls: bool = False) -> tuple[str, str]:
        """End of the completion: flush the hold; decide an unclosed implicit block."""
        if self._finished:
            return "", ""
        self._finished = True
        pending, self._pending = self._pending, ""
        if self._state == _START:
            # Whitespace or a never-completed "<thi": not a think block.
            return "", pending
        if self._state == _THINKING:
            reasoning = pending
            if (
                self._implicit_thought is not None
                and finish_reason == "stop"
                and not saw_tool_calls
            ):
                # Natural end with no closer: the model was answering all
                # along. It streamed as thinking; it is also the answer.
                self._implicit_thought.append(pending)
                return reasoning, "".join(self._implicit_thought).strip()
            return reasoning, ""
        return "", ""


def in_band_thinking_is_implicit(provider_name: str, model: str, body: dict | None = None) -> bool:
    """True when a completion from this row may open its think block in the prompt.

    Only a self-hosted box (the operator's vLLM / SGLang / llama.cpp) whose
    row is a parse-only or enable-thinking reasoning row: that is where the
    chat template opens ``<think>`` itself and — when the server runs no
    reasoning parser — the completion starts inside the block (measured on
    the sranai box for qwen3.5-9b and qwen3.6-35b-a3b). Never when this very
    request turned thinking off with the chat-template switch (the template
    then closes the block in the prompt and the completion is all answer).
    Cloud providers keep the explicit rule only.
    """
    from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS
    from app.services.providers.model_registry import get_candidate
    from app.services.providers.reasoning_strategies import (
        STRATEGIES_MAY_THINK_IN_BAND,
    )

    if provider_name not in SELF_HOSTED_PROVIDERS:
        return False
    kwargs = (body or {}).get("chat_template_kwargs")
    if isinstance(kwargs, dict) and kwargs.get("enable_thinking") is False:
        return False
    candidate = get_candidate(model)
    return bool(
        candidate is not None
        and candidate.provider == provider_name
        and candidate.supports_reasoning
        and candidate.reasoning_strategy in STRATEGIES_MAY_THINK_IN_BAND
    )


def split_response(response: "ProviderResponse", *, implicit_open: bool) -> "ProviderResponse":
    """The non-streaming form: the same rule applied to a whole ``content``."""
    content = response.content
    if not isinstance(content, str) or not content:
        return response
    splitter = ThinkSplitter(implicit_open=implicit_open and not response.reasoning_content)
    r1, a1 = splitter.feed(content)
    r2, a2 = splitter.finish(response.finish_reason, saw_tool_calls=bool(response.tool_calls))
    reasoning, answer = r1 + r2, a1 + a2
    if not reasoning and answer == content:
        return response
    merged = (response.reasoning_content or "") + reasoning
    return dataclasses.replace(response, content=answer, reasoning_content=merged or None)
