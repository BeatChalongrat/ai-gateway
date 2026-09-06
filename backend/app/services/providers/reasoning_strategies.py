"""Reasoning-strategy names — the one place they are spelled.

``model_configs.reasoning_strategy`` says HOW a row's thinking is
obtained, and three layers read it: the discovery inference that writes
it, the adapters that decide what to put on the wire, and the admin API
that lets an operator correct it. Before this module the strings were
retyped in each of them (plus migrations 045/049), which is how
``openai_compatible_reasoning`` ended up meaning two different things
depending on who was reading.

A leaf module on purpose — no imports of its own — so an adapter can
read a strategy name without importing the discovery package, the same
way ``providers.cost_tiers`` broke the registry/discovery cycle.

The four values:

``none``
    Not a reasoning row.

``openai_responses_reasoning``
    OpenAI o-series / gpt-5 over the Responses API. The native OpenAI
    adapter owns this one end to end.

``gemini_thinking_level``
    Gemini 2.5+ via ``thinkingConfig``. The Google adapter owns it.

``openai_compatible_reasoning``
    An OpenAI-compatible provider that ACCEPTS a request-side
    ``reasoning_effort`` knob and returns thinking. Seeded for DeepSeek
    and Groq, whose APIs are known to take it.

``openai_compatible_reasoning_content``
    The model streams ``delta.reasoning_content`` and the gateway reads
    it — but nothing extra is sent upstream. This is the honest
    description of a self-hosted box: vLLM / SGLang / llama.cpp emit
    reasoning on their own, and many reject request fields they do not
    recognise, so guessing a knob onto one risks turning a working
    reasoning model into a 400.

``openai_compatible_enable_thinking``
    Same as ``..._reasoning_content`` on the way IN by default (thinking
    on, nothing sent), but the box ALSO honours the Qwen/vLLM chat-template
    off-switch ``chat_template_kwargs={"enable_thinking": false}``. Set
    ONLY on a row whose endpoint has been MEASURED to accept it and to
    actually stop thinking (FIX72 measured gateway.9arm.co serving
    qwen3.8-27b-fp8: with the switch, completion dropped 59→31 tokens,
    ``reasoning_content`` disappeared, and the tool call still fired). The
    adapter sends the switch only when ``reasoning.mode == "off"``; every
    other mode is byte-identical to ``..._reasoning_content``. Never set
    this by guessing a model name — an unrecognised field is a 400.

Everything downstream of the adapters (the auto-enable in the
orchestrator, ``show_summary``, the SSE analysis bracket) keys off
``supports_reasoning``, never off the strategy — so a parse-only row
still shows its thinking. The strategy decides only what goes OUT.
"""

from __future__ import annotations

STRATEGY_NONE = "none"
STRATEGY_OPENAI_RESPONSES = "openai_responses_reasoning"
STRATEGY_GEMINI_THINKING = "gemini_thinking_level"
STRATEGY_OPENAI_COMPATIBLE = "openai_compatible_reasoning"
STRATEGY_REASONING_CONTENT = "openai_compatible_reasoning_content"
STRATEGY_ENABLE_THINKING = "openai_compatible_enable_thinking"

# Every value the admin API will accept for ``reasoning_strategy``.
# Ordered as the admin UI lists them.
KNOWN_STRATEGIES = (
    STRATEGY_NONE,
    STRATEGY_REASONING_CONTENT,
    STRATEGY_ENABLE_THINKING,
    STRATEGY_OPENAI_COMPATIBLE,
    STRATEGY_OPENAI_RESPONSES,
    STRATEGY_GEMINI_THINKING,
)

# Strategies whose provider takes a request-side effort knob. The
# OpenAI-compatible adapter sends ``reasoning_effort`` for these and
# stays silent for the rest.
STRATEGIES_WITH_EFFORT_KNOB = frozenset({STRATEGY_OPENAI_COMPATIBLE})

# Strategies whose box honours the Qwen/vLLM chat-template off-switch.
# The OpenAI-compatible adapter sends
# ``chat_template_kwargs={"enable_thinking": false}`` for these ONLY when
# ``reasoning.mode == "off"`` — never on any other mode, and never for a
# strategy not listed here (an unknown field 400s a self-hosted box).
STRATEGIES_WITH_ENABLE_THINKING_OFF = frozenset({STRATEGY_ENABLE_THINKING})
