"""Model-to-provider registry with capability metadata.

Maps model names to providers and tracks capabilities for routing
decisions (streaming support, cost tier, context length).
"""

from __future__ import annotations

from app.domain.routing import ModelCandidate

# Full model registry with capability metadata
_CANDIDATES: list[ModelCandidate] = [
    ModelCandidate(provider="openai", model="gpt-4o", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="openai", model="gpt-4o-mini", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="openai", model="gpt-4-turbo", cost_tier="premium", latency_tier="slow", supports_streaming=True),
    ModelCandidate(provider="openai", model="gpt-3.5-turbo", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    # Anthropic streaming is implemented in anthropic_adapter.stream_chat
    # (see _process_sse_stream state machine). Flipping these flags
    # without the adapter method would route streaming requests into
    # AnthropicAdapter → NotImplementedError from BaseProviderAdapter.
    ModelCandidate(provider="anthropic", model="claude-3-5-sonnet-20241022", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="anthropic", model="claude-3-opus-20240229", cost_tier="premium", latency_tier="slow", supports_streaming=True),
    ModelCandidate(provider="anthropic", model="claude-3-haiku-20240307", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    # Minimax (OpenAI-compatible)
    ModelCandidate(provider="minimax", model="abab6.5s-chat", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="minimax", model="abab5.5-chat", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    # Qwen / Alibaba DashScope (OpenAI-compatible)
    ModelCandidate(provider="qwen", model="qwen-turbo", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="qwen", model="qwen-plus", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="qwen", model="qwen-max", cost_tier="premium", latency_tier="slow", supports_streaming=True),
    # KIMI / Moonshot (OpenAI-compatible)
    ModelCandidate(provider="kimi", model="moonshot-v1-8k", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="kimi", model="moonshot-v1-32k", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="kimi", model="moonshot-v1-128k", cost_tier="premium", latency_tier="slow", supports_streaming=True),
    # Z.AI / 01.AI Yi (OpenAI-compatible)
    ModelCandidate(provider="zai", model="yi-large", cost_tier="premium", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="zai", model="yi-medium", cost_tier="standard", latency_tier="fast", supports_streaming=True),
    # OpenRouter aggregator — popular meta-models passed through as-is.
    ModelCandidate(provider="openrouter", model="openrouter/auto", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="openrouter", model="anthropic/claude-3.5-sonnet", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="openrouter", model="openai/gpt-4o", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="openrouter", model="meta-llama/llama-3.1-405b", cost_tier="premium", latency_tier="slow", supports_streaming=True),
    ModelCandidate(provider="openrouter", model="google/gemini-pro-1.5", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    # Google AI (Gemini) — native adapter
    ModelCandidate(provider="google", model="gemini-2.0-flash-exp", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="google", model="gemini-1.5-pro", cost_tier="premium", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="google", model="gemini-1.5-flash", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="google", model="gemini-1.5-flash-8b", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    # Groq (OpenAI-compatible; all on LPUs — latency tier "fast")
    ModelCandidate(provider="groq", model="llama-3.3-70b-versatile", cost_tier="standard", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="groq", model="llama-3.1-8b-instant", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="groq", model="mixtral-8x7b-32768", cost_tier="standard", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="groq", model="gemma2-9b-it", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="groq", model="deepseek-r1-distill-llama-70b", cost_tier="standard", latency_tier="fast", supports_streaming=True),
    ModelCandidate(provider="groq", model="qwen-2.5-32b", cost_tier="standard", latency_tier="fast", supports_streaming=True),
    # DeepSeek (OpenAI-compatible)
    ModelCandidate(provider="deepseek", model="deepseek-chat", cost_tier="budget", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="deepseek", model="deepseek-coder", cost_tier="budget", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="deepseek", model="deepseek-reasoner", cost_tier="standard", latency_tier="slow", supports_streaming=True),
    # xAI / Grok (OpenAI-compatible)
    ModelCandidate(provider="xai", model="grok-2-latest", cost_tier="standard", latency_tier="standard", supports_streaming=True),
    ModelCandidate(provider="xai", model="grok-2-mini", cost_tier="budget", latency_tier="fast", supports_streaming=True),
    # Mistral (pending — registered so UI exposes key slot; chat() raises 501)
    ModelCandidate(provider="mistral", model="mistral-large-latest", cost_tier="premium", latency_tier="standard", supports_streaming=False),
    ModelCandidate(provider="mistral", model="mistral-small-latest", cost_tier="budget", latency_tier="fast", supports_streaming=False),
    ModelCandidate(provider="mistral", model="codestral-latest", cost_tier="standard", latency_tier="standard", supports_streaming=False),
    ModelCandidate(provider="mistral", model="mistral-embed", cost_tier="budget", latency_tier="fast", supports_streaming=False),
    # Cohere (pending)
    ModelCandidate(provider="cohere", model="command-r-plus-08-2024", cost_tier="premium", latency_tier="standard", supports_streaming=False),
    ModelCandidate(provider="cohere", model="command-r-08-2024", cost_tier="standard", latency_tier="standard", supports_streaming=False),
    ModelCandidate(provider="cohere", model="command", cost_tier="standard", latency_tier="standard", supports_streaming=False),
    ModelCandidate(provider="cohere", model="command-light", cost_tier="budget", latency_tier="fast", supports_streaming=False),
    # Perplexity (pending)
    ModelCandidate(provider="perplexity", model="llama-3.1-sonar-large-128k-online", cost_tier="premium", latency_tier="standard", supports_streaming=False),
    ModelCandidate(provider="perplexity", model="llama-3.1-sonar-small-128k-online", cost_tier="standard", latency_tier="fast", supports_streaming=False),
    ModelCandidate(provider="perplexity", model="llama-3.1-sonar-huge-128k-online", cost_tier="premium", latency_tier="slow", supports_streaming=False),
]

_MODEL_MAP: dict[str, ModelCandidate] = {c.model: c for c in _CANDIDATES}

# Prefix → provider fallback (for models not in the explicit registry)
_PREFIX_MAP: list[tuple[str, str]] = [
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("claude-", "anthropic"),
    ("openai/", "openrouter"),
    ("anthropic/", "openrouter"),
    ("meta-llama/", "openrouter"),
    ("google/", "openrouter"),
    ("mistralai/", "openrouter"),
    ("abab", "minimax"),
    ("qwen-", "qwen"),
    ("moonshot-", "kimi"),
    ("yi-", "zai"),
    ("gemini-", "google"),
    ("grok-", "xai"),
    ("deepseek-", "deepseek"),
    ("codestral-", "mistral"),
    ("mistral-", "mistral"),
]


def resolve_provider(model_name: str) -> str | None:
    """Resolve a model name to a provider identifier."""
    if model_name in _MODEL_MAP:
        return _MODEL_MAP[model_name].provider

    for prefix, provider in _PREFIX_MAP:
        if model_name.startswith(prefix):
            return provider

    return None


def get_candidate(model_name: str) -> ModelCandidate | None:
    """Look up a model's full candidate metadata."""
    return _MODEL_MAP.get(model_name)


def get_all_candidates() -> list[ModelCandidate]:
    """Return all known model candidates."""
    return list(_CANDIDATES)


def list_models() -> list[dict[str, str]]:
    """Return a list of known models with their providers."""
    return [
        {"model": c.model, "provider": c.provider}
        for c in _CANDIDATES
    ]


def filter_candidates(
    candidates: list[ModelCandidate],
    *,
    requires_streaming: bool = False,
    allowed_providers: frozenset[str] | None = None,
    blocked_models: frozenset[str] | None = None,
) -> list[ModelCandidate]:
    """Filter candidates by capability requirements and constraints."""
    result = []
    for c in candidates:
        if requires_streaming and not c.supports_streaming:
            continue
        if allowed_providers and c.provider not in allowed_providers:
            continue
        if blocked_models and c.model in blocked_models:
            continue
        result.append(c)
    return result
