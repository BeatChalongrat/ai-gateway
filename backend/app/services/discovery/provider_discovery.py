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

# Sentinel default for "the provider's /models payload didn't say".
# discovery_service compares against this to decide whether litellm
# pricing may fill in a real context size — keep the two in sync by
# importing THIS constant, never re-typing the number.
DEFAULT_MAX_CONTEXT_TOKENS = 128000

# Self-hosted boxes (llama.cpp / vLLM class) rarely report context via
# /models and rarely serve 128k — default them conservatively instead
# of the cloud sentinel. Admins can correct per model via
# PATCH /admin/models/{id}.
SELF_HOSTED_DEFAULT_CONTEXT_TOKENS = 32768


@dataclass
class DiscoveredModel:
    provider: str
    model: str
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS
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
    # Non-chat families that auto-sync otherwise leaks into the chat
    # catalog: audio transcription + audio/realtime (voice) APIs, legacy
    # completion/base models, and dedicated image models. NOTE:
    # ``turbo-instruct`` (NOT bare ``instruct``) so instruction-tuned CHAT
    # models like ``llama-3.3-70b-instruct`` / ``mistral-…-instruct`` are
    # NOT filtered. ``gpt-image`` matches ``chatgpt-image-latest`` too.
    "transcribe", "turbo-instruct", "davinci", "babbage", "gpt-image",
    "audio", "realtime",
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
# Why a discovery came back empty
# ---------------------------------------------------------------------------

# Adapters swallow their own errors by contract — they return ``[]`` and
# never raise. That contract is right for the sweep (one dead provider
# must not fail the run) but it left the orchestrator, the admin API and
# therefore the operator with nothing to look at but "0 models": a key
# that validates green, an endpoint that is set, and an empty catalog,
# with the reason living only in a container log line.
#
# Each adapter now records a SHORT, secret-free reason here, keyed by
# provider. ``discovery_service`` clears the slot before an attempt and
# reads it after, so a reason from an earlier attempt can never be
# reported against a later one. Reasons are built from status codes and
# exception TYPES, never from raw upstream text — nothing that could
# carry a credential ever enters this dict.
#
# The second element says whether a DIFFERENT key could plausibly change
# the outcome. Only an auth rejection qualifies: a box that never
# answered, or answered with a catalog, said nothing about the
# credential, and retrying the same dead endpoint once per key would
# just multiply the wait.
_failure_reasons: dict[str, tuple[str, bool]] = {}


def record_discovery_failure(
    provider: str, reason: str, *, key_specific: bool = False
) -> None:
    """Record why ``provider`` produced no models on this attempt."""
    _failure_reasons[provider] = (reason[:200], key_specific)


def clear_discovery_failure(provider: str) -> None:
    """Drop any recorded reason for ``provider`` (call before an attempt)."""
    _failure_reasons.pop(provider, None)


def pop_discovery_failure(provider: str) -> tuple[str, bool] | None:
    """Read and clear ``(reason, key_specific)`` recorded for ``provider``."""
    return _failure_reasons.pop(provider, None)


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
        record_discovery_failure(provider, "no API key configured")
        return []

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        # 401/403 named explicitly: on a provider holding several keys
        # this is the difference between "the box is down" and "the key
        # we tried is not the box's key" — the distinction the operator
        # needs and the one a bare status code hides.
        key_specific = status in (401, 403)
        reason = (
            f"the endpoint rejected the key (HTTP {status})"
            if key_specific
            else f"the endpoint returned HTTP {status}"
        )
        logger.warning("discovery_provider_failed", provider=provider, error=str(exc)[:200])
        record_discovery_failure(provider, reason, key_specific=key_specific)
        return []
    except httpx.TimeoutException as exc:
        logger.warning("discovery_provider_failed", provider=provider, error=str(exc)[:200])
        record_discovery_failure(
            provider,
            f"the endpoint did not answer within {int(_PROVIDER_TIMEOUT_SECONDS)}s",
        )
        return []
    except httpx.RequestError as exc:
        logger.warning("discovery_provider_failed", provider=provider, error=str(exc)[:200])
        record_discovery_failure(provider, "could not connect to the endpoint")
        return []
    except Exception as exc:
        logger.warning(
            "discovery_provider_failed",
            provider=provider,
            error=str(exc)[:200],
        )
        # Type name only — a JSON decode error carries body text.
        record_discovery_failure(
            provider, f"unreadable response from the endpoint ({type(exc).__name__})"
        )
        return []

    if not isinstance(payload, dict):
        logger.warning("discovery_unexpected_shape", provider=provider)
        record_discovery_failure(
            provider, "the endpoint's response was not an OpenAI-style model list"
        )
        return []
    raw_items = payload.get("data") or payload.get("models") or []
    if not isinstance(raw_items, list):
        record_discovery_failure(
            provider, "the endpoint's response was not an OpenAI-style model list"
        )
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
                    or DEFAULT_MAX_CONTEXT_TOKENS
                ),
                supports_reasoning=supports_reasoning,
                reasoning_strategy=reasoning_strategy,
                raw=item,
            )
        )
    if not results:
        # A 200 with nothing usable is its own diagnosis, and a very
        # different one from a 401: either the box serves an empty list,
        # or everything it serves was filtered out as non-chat.
        record_discovery_failure(
            provider,
            "the endpoint listed no chat models"
            if raw_items
            else "the endpoint returned an empty model list",
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
            context = int(item.get("context_length") or DEFAULT_MAX_CONTEXT_TOKENS)
        except (TypeError, ValueError):
            context = DEFAULT_MAX_CONTEXT_TOKENS
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


async def discover_sranai(api_key: str | None) -> list[DiscoveredModel]:
    """Self-hosted Sran AI box — endpoint is operator-configured.

    The URL comes from the DB-first resolver (admin UI setting → env
    override) at call time, so the daily sweep and the add-key trigger
    both follow whatever the admin last saved. No endpoint yet → skip
    quietly (the box simply isn't wired up).
    """
    from app.services.providers.base_urls import resolve_base_url

    base = resolve_base_url("sranai", "")
    if not base:
        logger.info("discovery_skipped_no_endpoint", provider="sranai")
        record_discovery_failure("sranai", "endpoint not configured")
        return []

    models = await _discover_openai_compatible(
        provider="sranai",
        url=f"{base}/models",
        api_key=api_key,
    )
    for m in models:
        # The payload said nothing about context → use the self-hosted
        # default rather than the 128k cloud sentinel. (A box that
        # genuinely reports 128000 via context_length keeps it — the
        # raw check distinguishes "reported" from "defaulted".)
        if m.max_context_tokens == DEFAULT_MAX_CONTEXT_TOKENS and not (
            m.raw.get("context_length") or m.raw.get("max_tokens")
        ):
            m.max_context_tokens = SELF_HOSTED_DEFAULT_CONTEXT_TOKENS
        # cost stays 0.0 — the box is self-hosted, per-token price is
        # genuinely zero (and 0.0 maps to the neutral "standard" tier).
    return models


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
    "sranai": discover_sranai,
}
