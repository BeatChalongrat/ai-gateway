"""Per-provider model discovery adapters.

Every adapter is an ``async`` function ``discover_{provider}(api_key)``
that returns ``list[DiscoveredModel]``. The contract:

- Network errors, bad JSON, missing key, schema drift → empty list +
  warning log. Never raise.
- 15-second timeout per provider.
- Capability flags (``supports_tools``, ``supports_vision``,
  ``is_chat``) are inferred from the model name when the API doesn't
  surface them.
- ``DiscoveredModel.raw`` carries the original JSON entry for debug
  observability.

OpenRouter is the odd-one-out: its model list endpoint returns
pricing inline, so we extract ``cost_per_input_token`` /
``cost_per_output_token`` directly and the orchestrator must NOT
overwrite those values with litellm data.

Providers that don't expose a model list API return a static list
sourced from the prior hardcoded registry — discovery still
"succeeds" so pricing enrichment from litellm runs against them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import structlog


logger = structlog.get_logger(__name__)


_PROVIDER_TIMEOUT_SECONDS = 15.0


@dataclass
class DiscoveredModel:
    provider: str
    model: str
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    max_context_tokens: int = 128000
    cost_per_input_token: float = 0.0
    cost_per_output_token: float = 0.0
    # ``True`` when the cost came directly from the provider API
    # (currently only OpenRouter). The orchestrator uses this flag to
    # decide whether litellm pricing should overwrite or skip.
    cost_from_provider: bool = False
    # Reasoning capability inferred from the model id. Discovery only
    # PROMOTES a row to reasoning (never demotes) so seed/manual
    # reasoning rows are never clobbered by an inference miss.
    supports_reasoning: bool = False
    reasoning_strategy: str = "none"
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Capability inference
# ---------------------------------------------------------------------------

_VISION_KEYWORDS = (
    "vision", "vl", "gpt-4o", "claude-3", "gemini",
    "llava", "pixtral", "qvq",
)
_TOOL_KEYWORDS = (
    "gpt-4", "gpt-3.5", "claude", "gemini", "mistral",
    "command-r", "qwen", "deepseek-chat",
)
_NON_CHAT_KEYWORDS = (
    "embed", "moderat", "whisper", "tts", "dall-e",
    "text-similarity", "search",
)


def _infer_capability(model_id: str, raw: dict) -> dict:
    """Infer capability flags from a model id + raw API entry.

    The keyword lists are deliberately broad — false positives here
    cost a misleading capability flag (admin can override), false
    negatives cost a registered chat model getting filtered out.
    """
    name = model_id.lower()
    supports_vision = any(kw in name for kw in _VISION_KEYWORDS)
    supports_tools = any(kw in name for kw in _TOOL_KEYWORDS)
    is_chat = not any(kw in name for kw in _NON_CHAT_KEYWORDS)
    # Respect explicit provider-side hints when present (OpenRouter,
    # some Anthropic-style payloads). The inferred value remains a
    # fallback.
    if isinstance(raw, dict):
        if "supports_function_calling" in raw:
            supports_tools = bool(raw["supports_function_calling"])
        if "supports_vision" in raw:
            supports_vision = bool(raw["supports_vision"])
    return {
        "supports_vision": supports_vision,
        "supports_tools": supports_tools,
        "is_chat": is_chat,
    }


def _infer_reasoning(provider: str, model_id: str) -> tuple[bool, str]:
    """Detect reasoning capability from a model id.

    OpenAI o-series (the ``o1`` / ``o3`` / ``o4`` families) reason via
    the Responses-style ``reasoning_effort`` knob. Gated to the
    ``openai`` provider so a same-prefixed model on another provider
    isn't mis-tagged with an OpenAI-specific strategy.

    Other providers' reasoning models (DeepSeek-R1, Groq R1-distill,
    Claude 4, Gemini 2.5) are tagged via seed/manual rows today, and the
    persist step only PROMOTES to reasoning — so returning ``(False,
    "none")`` here never demotes them.

    Returns ``(supports_reasoning, reasoning_strategy)``.
    """
    name = model_id.lower()
    if provider == "openai" and (
        name.startswith("o1") or name.startswith("o3") or name.startswith("o4")
    ):
        return True, "openai_responses_reasoning"
    return False, "none"


# ---------------------------------------------------------------------------
# Internal helper — OpenAI-compatible /v1/models style
# ---------------------------------------------------------------------------

async def _discover_openai_compatible(
    *,
    provider: str,
    url: str,
    api_key: str | None,
    require_auth: bool = True,
) -> list[DiscoveredModel]:
    """Fetch ``GET {url}`` with an optional ``Bearer`` header.

    Expects an OpenAI-style ``{"data": [{"id": "..."}, ...]}`` shape
    and filters to chat models via ``_infer_capability``. All errors
    return ``[]``.
    """
    if require_auth and not api_key:
        logger.info("discovery_skipped_no_api_key", provider=provider)
        return []

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning(
            "discovery_provider_failed",
            provider=provider,
            error=str(exc)[:200],
        )
        return []

    if not isinstance(payload, dict):
        logger.warning("discovery_unexpected_shape", provider=provider)
        return []
    raw_items = payload.get("data") or payload.get("models") or []
    if not isinstance(raw_items, list):
        return []

    results: list[DiscoveredModel] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name") or item.get("model")
        if not isinstance(model_id, str) or not model_id:
            continue
        caps = _infer_capability(model_id, item)
        if not caps["is_chat"]:
            continue
        supports_reasoning, reasoning_strategy = _infer_reasoning(provider, model_id)
        results.append(
            DiscoveredModel(
                provider=provider,
                model=model_id,
                supports_streaming=True,
                supports_tools=caps["supports_tools"],
                supports_vision=caps["supports_vision"],
                max_context_tokens=int(
                    item.get("context_length")
                    or item.get("max_tokens")
                    or 128000
                ),
                supports_reasoning=supports_reasoning,
                reasoning_strategy=reasoning_strategy,
                raw=item,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Provider-specific discovery entry points
# ---------------------------------------------------------------------------

async def discover_openai(api_key: str | None) -> list[DiscoveredModel]:
    return await _discover_openai_compatible(
        provider="openai",
        url="https://api.openai.com/v1/models",
        api_key=api_key,
    )


async def discover_groq(api_key: str | None) -> list[DiscoveredModel]:
    return await _discover_openai_compatible(
        provider="groq",
        url="https://api.groq.com/openai/v1/models",
        api_key=api_key,
    )


async def discover_mistral(api_key: str | None) -> list[DiscoveredModel]:
    return await _discover_openai_compatible(
        provider="mistral",
        url="https://api.mistral.ai/v1/models",
        api_key=api_key,
    )


async def discover_cohere(api_key: str | None) -> list[DiscoveredModel]:
    # Cohere's /v1/models response uses ``{"models": [{"name": "...", ...}]}``
    # — handled by the helper via its ``"models"`` fallback.
    return await _discover_openai_compatible(
        provider="cohere",
        url="https://api.cohere.com/v1/models",
        api_key=api_key,
    )


async def discover_deepseek(api_key: str | None) -> list[DiscoveredModel]:
    return await _discover_openai_compatible(
        provider="deepseek",
        url="https://api.deepseek.com/models",
        api_key=api_key,
    )


async def discover_perplexity(api_key: str | None) -> list[DiscoveredModel]:
    return await _discover_openai_compatible(
        provider="perplexity",
        url="https://api.perplexity.ai/models",
        api_key=api_key,
    )


async def discover_openrouter(api_key: str | None) -> list[DiscoveredModel]:
    """OpenRouter ships pricing inline → extract per-token cost direct.

    No auth required. Response shape:

      {"data": [{"id": "anthropic/claude-3.5-sonnet",
                 "context_length": 200000,
                 "pricing": {"prompt": "0.000003",
                             "completion": "0.000015"}, ...}]}

    Pricing values are STRINGS containing $/token (yes, strings) —
    coerced to float with a defensive fallback to 0.0.
    """
    url = "https://openrouter.ai/api/v1/models"
    try:
        async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning(
            "discovery_provider_failed",
            provider="openrouter",
            error=str(exc)[:200],
        )
        return []

    raw_items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return []

    results: list[DiscoveredModel] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        caps = _infer_capability(model_id, item)
        if not caps["is_chat"]:
            continue
        pricing = item.get("pricing") or {}
        if not isinstance(pricing, dict):
            pricing = {}
        try:
            cost_in = float(pricing.get("prompt") or 0.0)
        except (TypeError, ValueError):
            cost_in = 0.0
        try:
            cost_out = float(pricing.get("completion") or 0.0)
        except (TypeError, ValueError):
            cost_out = 0.0
        try:
            context = int(item.get("context_length") or 128000)
        except (TypeError, ValueError):
            context = 128000
        results.append(
            DiscoveredModel(
                provider="openrouter",
                model=model_id,
                supports_streaming=True,
                supports_tools=caps["supports_tools"],
                supports_vision=caps["supports_vision"],
                max_context_tokens=context,
                cost_per_input_token=cost_in,
                cost_per_output_token=cost_out,
                cost_from_provider=cost_in > 0.0 or cost_out > 0.0,
                raw=item,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Static-list providers (no model list API)
# ---------------------------------------------------------------------------

# Keep these aligned with the registry seed so a sync against a provider
# without a listing API still yields the expected set of candidates.
# Pricing is left at 0.0 and enriched by litellm in the orchestrator.

_STATIC_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-3-5-sonnet-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
    ],
    "kimi": [
        "moonshot-v1-8k",
        "moonshot-v1-32k",
        "moonshot-v1-128k",
    ],
    "minimax": [
        "abab6.5s-chat",
        "abab5.5-chat",
    ],
    "qwen": [
        "qwen-turbo",
        "qwen-plus",
        "qwen-max",
    ],
    "xai": [
        "grok-2-latest",
        "grok-2-mini",
    ],
    "zai": [
        "yi-large",
        "yi-medium",
    ],
    "google": [
        "gemini-2.0-flash-exp",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
    ],
}


def _discover_static(provider: str) -> list[DiscoveredModel]:
    models = _STATIC_MODELS.get(provider, [])
    results: list[DiscoveredModel] = []
    for model_id in models:
        caps = _infer_capability(model_id, {})
        results.append(
            DiscoveredModel(
                provider=provider,
                model=model_id,
                supports_streaming=True,
                supports_tools=caps["supports_tools"],
                supports_vision=caps["supports_vision"],
                raw={"source": "static"},
            )
        )
    return results


async def discover_anthropic(api_key: str | None) -> list[DiscoveredModel]:
    return _discover_static("anthropic")


async def discover_kimi(api_key: str | None) -> list[DiscoveredModel]:
    return _discover_static("kimi")


async def discover_minimax(api_key: str | None) -> list[DiscoveredModel]:
    return _discover_static("minimax")


async def discover_qwen(api_key: str | None) -> list[DiscoveredModel]:
    return _discover_static("qwen")


async def discover_xai(api_key: str | None) -> list[DiscoveredModel]:
    return _discover_static("xai")


async def discover_zai(api_key: str | None) -> list[DiscoveredModel]:
    return _discover_static("zai")


async def discover_google(api_key: str | None) -> list[DiscoveredModel]:
    # Google's generativelanguage.googleapis.com /v1/models endpoint
    # requires an API key. Use the static list so the seed catalog
    # stays visible whenever the key is absent — the discovery sync
    # would otherwise soft-deactivate every google row.
    return _discover_static("google")


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

# ``ModelDiscoveryService.sync_all`` iterates this map. Keep keys in
# sync with the provider identifiers used by the routing service.
PROVIDER_DISCOVERY = {
    "openai": discover_openai,
    "anthropic": discover_anthropic,
    "google": discover_google,
    "groq": discover_groq,
    "mistral": discover_mistral,
    "cohere": discover_cohere,
    "deepseek": discover_deepseek,
    "perplexity": discover_perplexity,
    "openrouter": discover_openrouter,
    "kimi": discover_kimi,
    "minimax": discover_minimax,
    "qwen": discover_qwen,
    "xai": discover_xai,
    "zai": discover_zai,
}
