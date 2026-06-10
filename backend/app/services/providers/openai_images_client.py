"""OpenAI Images API client (DALL-E 3 / DALL-E 2) — a STANDALONE client, NOT
a chat adapter.

The chat ``BaseProviderAdapter`` is messages/streaming-shaped and the
``provider_dispatcher`` calls ``adapter.chat()`` only, so a one-shot
prompt -> image operation doesn't fit it. This client reuses the SAME infra
the chat adapters use — the shared pooled async httpx client, base-URL
override, provider-key resolution (round-robin + cache), and retry/error
mapping — without pretending an image request is a chat turn.

Image models are intentionally NOT registered in the chat routing candidate
pool: their per-token cost is 0, so the auto-router would otherwise rank them
"cheapest" and route a chat request to dall-e-3.

Endpoint: POST {base}/images/generations
Docs: https://platform.openai.com/docs/api-reference/images/create

Note: DALL-E ``url`` results expire (~1h). Phase A returns the URL directly;
persistent storage is a later phase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.services.provider_key_service import ProviderKeyService
from app.services.providers.base_urls import resolve_base_url
from app.services.providers.error_handler import call_with_retry
from app.services.providers.http_client import get_provider_client

OPENAI_BASE_URL = "https://api.openai.com/v1"

# Image models surfaced in /v1/models (decoupled from chat routing).
IMAGE_MODELS = ("dall-e-3", "dall-e-2")
DEFAULT_IMAGE_MODEL = "dall-e-3"

# Per-image USD. DALL-E 3 keyed by (size, quality); DALL-E 2 by size.
_DALLE3_COSTS = {
    ("1024x1024", "standard"): 0.040,
    ("1024x1024", "hd"): 0.080,
    ("1024x1792", "standard"): 0.080,
    ("1024x1792", "hd"): 0.120,
    ("1792x1024", "standard"): 0.080,
    ("1792x1024", "hd"): 0.120,
}
_DALLE2_COSTS = {"1024x1024": 0.020, "512x512": 0.018, "256x256": 0.016}


def image_cost_usd(model: str, size: str, quality: str, n: int) -> float:
    """Per-request image cost. Unknown size/quality falls back to the base
    1024x1024 standard rate for that model rather than 0 (don't under-bill)."""
    if model == "dall-e-2":
        per = _DALLE2_COSTS.get(size, _DALLE2_COSTS["1024x1024"])
    else:
        per = _DALLE3_COSTS.get((size, quality), _DALLE3_COSTS[("1024x1024", "standard")])
    return round(per * max(1, n), 6)


@dataclass
class ImageResult:
    created: int
    data: list[dict]  # [{url|b64_json, revised_prompt}]
    model: str
    cost_usd: float
    raw: dict = field(default_factory=dict)


async def generate_image(
    session: AsyncSession,
    *,
    model: str,
    prompt: str,
    n: int = 1,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "vivid",
    response_format: str = "url",
) -> ImageResult:
    """Call POST /images/generations. ``prompt`` is the already-DLP-masked
    text. Returns the provider payload + the computed cost. Provider/HTTP
    errors are raised as the gateway's domain exceptions (via
    ``call_with_retry``) for the central handler to map."""
    key = await ProviderKeyService().get_active_key_async(session, "openai")
    if not key:
        raise ProviderUnavailableError(
            "No API key configured for provider 'openai'.",
            details={"provider": "openai"},
        )

    n_eff = 1 if model == "dall-e-3" else max(1, n)  # dall-e-3 only supports n=1
    body: dict = {
        "model": model,
        "prompt": prompt,
        "n": n_eff,
        "size": size,
    }
    if model == "dall-e-3":
        body["quality"] = quality
    # NOTE: the current OpenAI images API rejects `response_format` and `style`
    # for dall-e-3 ("unknown_parameter") and returns a URL by default. We keep
    # those request fields (for cost calc / forward-compat) but do NOT forward
    # them. Only model/prompt/n/size/quality are sent.
    _ = (response_format, style)

    client = await get_provider_client(
        base_url=resolve_base_url("openai", OPENAI_BASE_URL),
        timeout_connect=settings.provider_timeout_connect,
        timeout_read=settings.provider_timeout_read,
    )
    headers = {"Authorization": f"Bearer {key}"}
    resp = await call_with_retry(
        lambda: client.post("/images/generations", json=body, headers=headers),
        provider_name="openai",
        max_retries=settings.provider_max_retries,
    )
    data = resp.json()
    return ImageResult(
        created=int(data.get("created", time.time())),
        data=list(data.get("data", [])),
        model=model,
        cost_usd=image_cost_usd(model, size, quality, n_eff),
        raw=data,
    )
