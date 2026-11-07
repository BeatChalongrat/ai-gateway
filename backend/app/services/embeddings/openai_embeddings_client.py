"""OpenAI embeddings client — the gateway-side utility endpoint (FIX 2).

chat-edge used to call OpenAI ``/v1/embeddings`` directly, which bypassed DLP
entirely (raw KB document content + RAG queries egressed unmasked). The
gateway now fronts embeddings, and THIS module is the only place the text
leaves for OpenAI — it sits BEHIND
``app.services.dlp.egress_policy.scan_embedding_text`` (credential-block +
per-tenant PII policy + audit). It is therefore classified ``raw_whitelisted``
in ``tests/test_dlp_egress_invariant.py``: it carries RAW user content by
design, gated by the whitelisted ``embeddings`` channel.

The OpenAI credential is resolved through ``ProviderKeyService`` — the same
pool chat dispatch draws from — so rotating the key in the admin UI is
followed here too. It used to read ``settings.openai_api_key`` directly,
which made this a second live source: an admin could rotate the key, chat
would follow it, and RAG would keep sending the stale environment value with
nothing on screen saying so. No usable key disables the endpoint (503).
"""

from __future__ import annotations

import httpx
import structlog

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError, ValidationError
from app.services.provider_key_service import resolve_provider_key_async

logger = structlog.get_logger(__name__)

# Column size for chat.knowledge_chunks (pgvector(1536)); pinned to the
# text-embedding-3-small default. chat-edge's EmbeddingDimensions must match.
EMBEDDING_DIMENSIONS = 1536

# Utility calls are low-frequency relative to chat; a generous per-call
# timeout covers a 50-page-doc indexing burst without holding a pooled client.
_TIMEOUT_SECONDS = 30.0


async def embeddings_enabled() -> bool:
    """Whether the gateway has a usable OpenAI credential for embeddings."""
    return bool(await resolve_provider_key_async("openai"))


async def generate_embedding(text: str, *, model: str | None = None) -> list[float]:
    """Return the embedding vector for ``text``.

    Raises :class:`ValidationError` on empty input and
    :class:`ProviderUnavailableError` when the provider is unconfigured or
    the upstream call fails. The caller MUST have run the DLP embeddings
    egress scan before invoking this (the route does).
    """
    api_key = await resolve_provider_key_async("openai")
    if not api_key:
        raise ProviderUnavailableError(
            "embeddings provider not configured — add an OpenAI key in "
            "the admin UI under Providers"
        )
    text = (text or "").strip()
    if not text:
        raise ValidationError("embedding input is empty")

    base = (settings.openai_utility_base_url or "https://api.openai.com/v1").rstrip("/")
    mdl = model or settings.openai_embedding_model

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{base}/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": mdl, "input": text},
            )
    except httpx.HTTPError as exc:
        logger.warning("embeddings_upstream_unreachable", error=str(exc))
        raise ProviderUnavailableError(
            "embeddings provider unreachable"
        ) from exc

    if resp.status_code != 200:
        logger.warning("embeddings_upstream_error", status=resp.status_code)
        raise ProviderUnavailableError(
            f"embeddings provider returned {resp.status_code}"
        )

    data = (resp.json() or {}).get("data") or []
    if not data or not data[0].get("embedding"):
        raise ProviderUnavailableError("embeddings provider returned empty data")
    return list(data[0]["embedding"])
