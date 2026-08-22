"""Provider abstraction for the AI Sensitive Data Reviewer.

Pure functions only — no DB access, no singletons. The callers
(admin_ai_review route + realtime hook) own state.

Four supported targets:

- ``anthropic``  → native /v1/messages format
- ``openai``     → OpenAI /v1/chat/completions
- ``openrouter`` → OpenAI-compatible /v1/chat/completions
- ``custom``     → operator-supplied endpoint, assumed OpenAI-compatible

``resolve_review_key`` is the fallback chain: a key stored specifically for
AI review → the selected provider's key pool via ``ProviderKeyService`` (the
same one chat dispatch draws from) → None. ``call_review_api`` is a single
POST; callers pick up exceptions for the failure path.
"""

from __future__ import annotations

import base64
import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.provider_key_service import resolve_provider_key_async
from app.models.ai_review import AiReviewSettingsORM


# ---------------------------------------------------------------------------
# Preset catalog
# ---------------------------------------------------------------------------


PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-haiku-4-5-20251001",
        "format": "anthropic",
        # Published Haiku 4.5 pricing — keep this list honest by
        # writing the source on each line so reviewers know what to
        # update when Anthropic ships new tiers.
        "cost_per_1k_input": 0.00025,
        "cost_per_1k_output": 0.00125,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "format": "openai",
        "cost_per_1k_input": 0.00015,
        "cost_per_1k_output": 0.0006,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "anthropic/claude-haiku-4",
        "format": "openai",
        "cost_per_1k_input": 0.00025,
        "cost_per_1k_output": 0.00125,
    },
    "custom": {
        # base_url overridden by settings.review_endpoint.
        "base_url": None,
        "default_model": "",
        "format": "openai",
        # No reliable pricing data for third-party hosts; scan runs
        # will record 0 cost rather than a guess.
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
    },
}


ANTHROPIC_VERSION = "2023-06-01"
SCAN_MAX_TOKENS = 2000
SCAN_READ_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Key encoding (matches provider_key_service._encode/_decode)
# ---------------------------------------------------------------------------


def encode_key(raw: str) -> str:
    return base64.b64encode(raw.encode()).decode()


def decode_key(encoded: str) -> str:
    return base64.b64decode(encoded.encode()).decode()


async def resolve_review_key(row: AiReviewSettingsORM) -> str | None:
    """Return the plaintext API key to use for AI review.

    Priority:
      1. ``row.review_api_key_enc`` — a key set specifically for AI review,
         which stays the most specific answer and wins
      2. the provider's key pool, resolved through ``ProviderKeyService``
         exactly as chat dispatch resolves it

    Tier 2 used to read ``settings.<provider>_api_key`` directly. That made
    AI review a second live source: an admin rotating the Anthropic key in
    the admin UI would move chat onto the new key while every review scan
    kept using the old environment value, with nothing on screen saying so.
    """
    if row.review_api_key_enc:
        try:
            return decode_key(row.review_api_key_enc)
        except Exception:
            return None

    provider = row.review_provider or "anthropic"
    if provider in ("anthropic", "openai", "openrouter"):
        return await resolve_provider_key_async(provider)
    # custom provider: must have an explicit key stored.
    return None


def resolve_provider_meta(row: AiReviewSettingsORM) -> dict[str, Any]:
    """Return a dict (provider_name, preset, base_url, model) for a scan."""
    provider = row.review_provider or "anthropic"
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["anthropic"])
    base_url = preset["base_url"] or row.review_endpoint
    model = row.review_model or preset["default_model"]
    return {
        "provider": provider,
        "preset": preset,
        "base_url": base_url,
        "model": model,
    }


# ---------------------------------------------------------------------------
# Egress trust boundary (Phase 1 blocker B1)
# ---------------------------------------------------------------------------
#
# The reviewer payload is post-DLP, pre-restore: raw secrets/credentials
# are masked/blocked, but non-secret PII the detectors MISSED may be
# present by design. So the destination host must be internal or
# explicitly allowlisted before that payload may egress.

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_INTERNAL_SUFFIXES = (".internal", ".local", ".svc", ".cluster.local")


def _host_of(base_url: str | None) -> str:
    if not base_url:
        return ""
    try:
        return (urlparse(base_url).hostname or "").lower()
    except Exception:
        return ""


def classify_review_host(base_url: str | None) -> str:
    """Classify a reviewer endpoint host as internal / trusted / external.

    - ``internal``: localhost, private/loopback/link-local IP, a single-
      label hostname (docker service name like ``ner-service``), or an
      ``*.internal/.local/.svc/.cluster.local`` suffix.
    - ``trusted``: host listed in ``settings.ai_review_allowed_hosts``.
    - ``external``: anything else (including an unparseable URL).
    """
    host = _host_of(base_url)
    if not host:
        return "external"
    if host in _LOCAL_HOSTS:
        return "internal"
    if any(host.endswith(s) for s in _INTERNAL_SUFFIXES):
        return "internal"
    if "." not in host:
        return "internal"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return "internal"
    except ValueError:
        pass
    allowed = {
        h.strip().lower()
        for h in (settings.ai_review_allowed_hosts or "").split(",")
        if h.strip()
    }
    if host in allowed:
        return "trusted"
    return "external"


# Built-in first-party LLM vendor providers. Their endpoints are fixed
# preset hosts baked into the product (PROVIDER_PRESETS), NOT operator-
# arbitrary — so they are trusted by default. The B1 risk the gate
# addresses is the ``custom`` provider pointing at an arbitrary host.
_FIRST_PARTY_PROVIDERS = frozenset({"anthropic", "openai", "openrouter"})


def review_egress_allowed(
    base_url: str | None, provider: str | None = None
) -> tuple[bool, str, str]:
    """Return (allowed, host_class, host) for a reviewer endpoint.

    Gated by ``ai_review_require_trusted_endpoint`` (default True):
    - First-party preset providers (anthropic/openai/openrouter) use
      fixed vendor hosts → ``trusted_preset`` → allowed.
    - ``custom`` provider: internal/allowlisted host → allowed; any other
      external host → blocked unless ``ai_review_allow_external`` is true.
    When the gate is disabled, everything is allowed (legacy behavior).
    """
    host = _host_of(base_url)
    if not getattr(settings, "ai_review_require_trusted_endpoint", True):
        return True, "gate_disabled", host
    if provider in _FIRST_PARTY_PROVIDERS:
        return True, "trusted_preset", host
    host_class = classify_review_host(base_url)
    if host_class in ("internal", "trusted"):
        return True, host_class, host
    if getattr(settings, "ai_review_allow_external", False):
        return True, host_class, host
    return False, host_class, host


def estimate_cost(
    provider: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Compute a per-run USD cost from the preset pricing table.

    ``custom`` provider always returns 0.0 (no reliable pricing).
    """
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        return 0.0
    cost = (
        (prompt_tokens / 1000.0) * preset["cost_per_1k_input"]
        + (completion_tokens / 1000.0) * preset["cost_per_1k_output"]
    )
    return round(cost, 8)


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------


class ReviewApiError(RuntimeError):
    """Raised when the upstream review LLM call fails."""


async def call_review_api(
    row: AiReviewSettingsORM,
    *,
    system_text: str,
    user_text: str,
) -> dict[str, Any]:
    """Call the configured review LLM and return a normalized response.

    Returns
    -------
    dict with:
        content: str          -- the model's text output
        prompt_tokens: int
        completion_tokens: int

    Raises
    ------
    ValueError
        When no API key is available.
    ReviewApiError
        When the upstream returns a non-2xx or the response cannot be
        parsed.
    """
    meta = resolve_provider_meta(row)
    provider = meta["provider"]
    preset = meta["preset"]
    base_url = meta["base_url"]
    model = meta["model"]

    if provider == "custom" and not base_url:
        raise ValueError(
            "Custom provider selected but review_endpoint is not set."
        )
    if provider == "custom" and not model:
        raise ValueError(
            "Custom provider selected but review_model is not set."
        )

    # B1 egress trust boundary — defense in depth. The realtime hook
    # also checks this and skips with a sanitized log, but enforce here
    # too so the manual scan + test-connection paths can never ship the
    # (missed-PII-bearing) payload to a non-allowlisted external host.
    allowed, host_class, _host = review_egress_allowed(base_url, provider)
    if not allowed:
        raise ReviewApiError(
            f"AI review egress blocked: endpoint host class '{host_class}' "
            "is not internal or allowlisted. Add the host to "
            "AI_REVIEW_ALLOWED_HOSTS or set AI_REVIEW_ALLOW_EXTERNAL=true."
        )

    api_key = await resolve_review_key(row)
    if not api_key:
        raise ValueError(
            f"No API key configured for AI review provider '{provider}'. "
            "Set a dedicated key in AI Review settings, or add a key for "
            "that provider in the admin UI under Providers."
        )

    timeout = httpx.Timeout(
        connect=5.0, read=SCAN_READ_TIMEOUT, write=5.0, pool=5.0
    )

    if preset["format"] == "anthropic":
        body = {
            "model": model,
            "max_tokens": SCAN_MAX_TOKENS,
            "system": system_text,
            "messages": [{"role": "user", "content": user_text}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
    else:
        # OpenAI chat completions shape — works for openai, openrouter,
        # and any custom OpenAI-compatible endpoint.
        body = {
            "model": model,
            "max_tokens": SCAN_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(base_url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ReviewApiError(f"Transport error: {exc}") from exc

    if resp.status_code >= 400:
        raise ReviewApiError(
            f"{provider} API error {resp.status_code}: {resp.text[:500]}"
        )

    try:
        data = resp.json()
    except Exception as exc:
        raise ReviewApiError(f"Response was not JSON: {exc}") from exc

    return _normalize_response(preset["format"], data)


def _normalize_response(fmt: str, data: dict) -> dict[str, Any]:
    """Reduce provider-specific response shapes to {content, tokens}."""
    if fmt == "anthropic":
        content = ""
        for block in data.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                content = block.get("text", "")
                break
        usage = data.get("usage", {}) or {}
        return {
            "content": content,
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
        }

    # OpenAI-compatible
    content = ""
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
    usage = data.get("usage", {}) or {}
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }


# ---------------------------------------------------------------------------
# Shared prompt builder + findings parser
# ---------------------------------------------------------------------------


SCAN_TEXT_CAP = 500


def build_ai_review_prompt(
    texts: list[str], detector_names: list[str]
) -> tuple[str, str]:
    """Return (system_prompt, user_message) for a scan run."""
    detector_list = ", ".join(sorted(detector_names)) or "(none)"

    system = (
        "You are a sensitive data analyst reviewing AI model responses. "
        "The texts below are AI provider responses AFTER the gateway's "
        "DLP masking pass but BEFORE restore. Data the DLP pipeline "
        "already caught appears as opaque placeholder tokens (e.g. "
        "'<r0>') — ignore those. \n\n"
        "Identify any sensitive, personal, financial, or confidential "
        "data that is still present in PLAINTEXT — i.e. data the AI "
        "model generated that should have been detected and masked by "
        "DLP filters but was not.\n\n"
        f"Existing detectors that already handle these categories: "
        f"{detector_list}\n\n"
        "Mark is_in_coverage=true if an existing detector would catch it. "
        "Mark is_in_coverage=false if no existing detector covers it — "
        "these represent gaps in the current DLP configuration.\n\n"
        "Respond ONLY with a valid JSON array. No explanation, no "
        "markdown. Each element: {\"request_index\": <int>, "
        "\"entity_type\": <string>, \"description\": <string>, "
        "\"sample_text\": <string, max 100 chars>, "
        "\"risk_level\": \"HIGH\"|\"MEDIUM\"|\"LOW\", "
        "\"is_in_coverage\": <bool>, "
        "\"suggested_detector\": <string|null>}\n"
        "If no sensitive data found in a message, omit it from results."
    )

    numbered = "\n".join(
        f"[{i}] {t[:SCAN_TEXT_CAP]}" for i, t in enumerate(texts)
    )
    user = (
        f"Analyze these {len(texts)} AI response messages for "
        f"sensitive data that should have been filtered but was not:\n{numbered}"
    )
    return system, user


def parse_findings_json(raw_text: str) -> list[dict]:
    """Parse the model's findings JSON, stripping ```json fences."""
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    if not cleaned:
        return []
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ReviewApiError(f"Response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ReviewApiError("Response was not a JSON array")
    return parsed
