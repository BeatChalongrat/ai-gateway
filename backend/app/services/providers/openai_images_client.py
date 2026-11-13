"""OpenAI image generation/edit client with configured keys and bounded references.

Default: GPT-Image-2.6 Flare (public docs verified 2026-11-12). GPT Image 1
and Sora 2 are retired; legacy normalization remains for compatible deployments
and historical tests. Provider-reported token breakdowns feed a standard-rate
cost estimate; cache/contract discounts and missing usage need reconciliation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.services.provider_key_service import ProviderKeyService
from app.services.providers.base_urls import effective_base_url
from app.services.providers.error_handler import call_with_retry
from app.services.providers.http_client import get_provider_client

OPENAI_BASE_URL = "https://api.openai.com/v1"

# Models accepted without an operator destination override. Account access is separate.
IMAGE_MODELS = ("gpt-image-2.6-flare", "gpt-image-1-mini")
DEFAULT_IMAGE_MODEL = "gpt-image-2.6-flare"
logger = structlog.get_logger(__name__)


def _is_gpt_image(model: str) -> bool:
    return model.startswith("gpt-image") or model == "chatgpt-image-latest"


# Per-image USD.
_DALLE3_COSTS = {
    ("1024x1024", "standard"): 0.040,
    ("1024x1024", "hd"): 0.080,
    ("1024x1792", "standard"): 0.080,
    ("1024x1792", "hd"): 0.120,
    ("1792x1024", "standard"): 0.080,
    ("1792x1024", "hd"): 0.120,
}
_DALLE2_COSTS = {"1024x1024": 0.020, "512x512": 0.018, "256x256": 0.016}
# gpt-image-1 is token-priced; these are APPROXIMATE per-image USD by
# (size, quality) from OpenAI's published rates. Real cost varies with the
# prompt's token count; the response ``usage`` (captured in raw) has the
# exact tokens if you need to reconcile later.
_GPT_IMAGE_COSTS = {
    ("1024x1024", "low"): 0.011,
    ("1024x1024", "medium"): 0.042,
    ("1024x1024", "high"): 0.167,
    ("1024x1536", "low"): 0.016,
    ("1024x1536", "medium"): 0.063,
    ("1024x1536", "high"): 0.250,
    ("1536x1024", "low"): 0.016,
    ("1536x1024", "medium"): 0.063,
    ("1536x1024", "high"): 0.250,
}


def normalize_quality(model: str, quality: str) -> str:
    """Map the request's quality into the value the model accepts."""
    if _is_gpt_image(model):
        if quality in ("auto", "low", "medium", "high"):
            return quality
        return {"standard": "medium", "hd": "high"}.get(quality, "auto")
    # dall-e-3
    if quality in ("standard", "hd"):
        return quality
    return {"low": "standard", "medium": "standard", "high": "hd"}.get(
        quality, "standard"
    )


def image_cost_usd(model: str, size: str, quality: str, n: int) -> float:
    """Per-request image cost. Unknown size/quality falls back to a sane base
    rate (don't under-bill), never 0."""
    if _is_gpt_image(model):
        q = normalize_quality(model, quality)
        per = _GPT_IMAGE_COSTS.get((size, q), _GPT_IMAGE_COSTS[("1024x1024", "medium")])
    elif model == "dall-e-2":
        per = _DALLE2_COSTS.get(size, _DALLE2_COSTS["1024x1024"])
    else:  # dall-e-3
        q = normalize_quality(model, quality)
        per = _DALLE3_COSTS.get((size, q), _DALLE3_COSTS[("1024x1024", "standard")])
    return round(per * max(1, n), 6)


@dataclass
class ImageResult:
    created: int
    data: list[dict]  # [{url|b64_json, revised_prompt?}]
    model: str
    cost_usd: float
    raw: dict = field(default_factory=dict)


def usage_cost_usd(model: str, usage: dict, fallback: float) -> float:
    """Token-based estimate where the provider reports a complete breakdown.

    Published standard rates (USD per million), verified 2026-11-12.
    Cache/batch/contract discounts are not assumed; usage.cost_usd is an estimate.
    Legacy/custom providers without a breakdown retain the output-only estimate.
    """
    rates = {"gpt-image-2.6-flare": (5, 8, 30), "gpt-image-1": (5, 10, 40), "gpt-image-1-mini": (2, 2.5, 8)}
    details = usage.get("input_tokens_details", {}) if isinstance(usage, dict) else {}
    if model not in rates or not isinstance(details, dict):
        return fallback
    counts = (details.get("text_tokens"), details.get("image_tokens"), usage.get("output_tokens"))
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in counts):
        return fallback
    return round(sum(n * rate for n, rate in zip(counts, rates[model])) / 1_000_000, 6)


async def generate_image(
    session: AsyncSession,
    *,
    model: str,
    prompt: str,
    request_id: str | None = None,
    n: int = 1,
    size: str = "1024x1024",
    quality: str = "auto",
    style: str = "vivid",
    response_format: str = "url",
    source_images: list[str] | None = None,
) -> ImageResult:
    """POST /images/generations. ``prompt`` is the already-DLP-masked text.
    Provider/HTTP errors are raised as the gateway's domain exceptions (via
    ``call_with_retry``) for the central handler to map."""
    files = None
    endpoint = "/images/generations"
    if source_images:
        from app.services.media_gen.image_inputs import validate_image_inputs
        if not _is_gpt_image(model):
            raise ProviderUnavailableError("Reference-image editing requires a GPT Image destination")
        files = [("image[]", item) for item in validate_image_inputs(source_images)]
        endpoint = "/images/edits"
    key = await ProviderKeyService().get_active_key_async(session, "openai")
    if not key:
        raise ProviderUnavailableError(
            "No API key configured for provider 'openai'.",
            details={"provider": "openai"},
        )

    n_eff = 1 if model == "dall-e-3" else max(1, n)  # dall-e-3 only supports n=1
    body: dict = {"model": model, "prompt": prompt, "n": n_eff, "size": size}
    # quality: gpt-image-1 (auto/low/medium/high) and dall-e-3 (standard/hd)
    # take it; dall-e-2 has no quality. NEVER send response_format or style —
    # the current OpenAI images API rejects both ("unknown_parameter").
    if _is_gpt_image(model) or model == "dall-e-3":
        body["quality"] = normalize_quality(model, quality)
    _ = (response_format, style)  # accepted for forward-compat, not forwarded

    client = await get_provider_client(
        base_url=effective_base_url("openai", OPENAI_BASE_URL, key),
        timeout_connect=settings.provider_timeout_connect,
        timeout_read=settings.provider_timeout_read,
        provider_name="openai",
    )
    headers = {"Authorization": f"Bearer {key}"}
    request_body = {"json": body}
    if files:
        # The pooled chat client defaults to application/json. Obtain a public
        # httpx multipart encoder's boundary and override that default explicitly.
        multipart = httpx.Request("POST", "https://image-request.invalid", data={k: str(v) for k, v in body.items()}, files=files)
        headers["Content-Type"] = multipart.headers["Content-Type"]
        request_body = {"content": multipart.read()}
    timeout = httpx.Timeout(
        connect=settings.provider_timeout_connect,
        read=settings.openai_image_timeout_seconds,
        write=10.0,
        pool=5.0,
    )
    t0 = time.perf_counter()
    logger.info(
        "backend_openai_image_started",
        request_id=request_id,
        provider="openai",
        model=model,
        endpoint=endpoint,
        timeout_ms=int(settings.openai_image_timeout_seconds * 1000),
    )
    try:
        resp = await call_with_retry(
            lambda: client.post(
                endpoint,
                **request_body,
                headers=headers,
                timeout=timeout,
            ),
            provider_name="openai",
            max_retries=settings.provider_max_retries,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        event = (
            "backend_openai_image_timeout"
            if isinstance(exc, httpx.TimeoutException)
            or getattr(exc, "code", "") == "provider_timeout"
            else "backend_openai_image_failed"
        )
        logger.warning(
            event,
            request_id=request_id,
            provider="openai",
            model=model,
            endpoint=endpoint,
            elapsed_ms=elapsed_ms,
            timeout_ms=int(settings.openai_image_timeout_seconds * 1000),
            error_code=getattr(exc, "code", type(exc).__name__),
            error_message=str(exc)[:200],
        )
        raise
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    data = resp.json()
    logger.info(
        "backend_openai_image_succeeded",
        request_id=request_id,
        provider="openai",
        model=model,
        endpoint=endpoint,
        status_code=resp.status_code,
        elapsed_ms=elapsed_ms,
        timeout_ms=int(settings.openai_image_timeout_seconds * 1000),
    )
    return ImageResult(
        created=int(data.get("created", time.time())),
        data=list(data.get("data", [])),
        model=model,
        cost_usd=usage_cost_usd(model, data.get("usage", {}), image_cost_usd(model, size, quality, n_eff)),
        raw=data,
    )
