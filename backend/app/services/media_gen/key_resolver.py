"""Per-request Replicate key resolution (Sprint 2B.5).

``ReplicateVideoAdapter`` calls this on every provider request instead of
reading ``REPLICATE_API_TOKEN`` directly, so a key added through the admin
``/providers`` UI activates the adapter with NO restart.

Resolution priority (delegated to ``ProviderKeyService.get_active_key_async``):

    1. ``provider_keys`` table — active + valid (UI Add-Key flow)
    2. ``providers.api_key_encrypted`` — legacy single key
    3. ``REPLICATE_API_TOKEN`` env — fallback (via ``_ENV_KEYS``)

The resolved key is returned to the caller and is NEVER logged, printed, or
cached in this module.
"""

from __future__ import annotations

from typing import Optional


async def _resolve_key_for(provider_name: str) -> Optional[str]:
    """Resolve a provider's active key via ProviderKeyService (UI → legacy →
    env). Fails CLOSED: any DB / lookup error returns ``None`` so the adapter
    raises ``ProviderKeyMissing`` before any provider call. Errors are
    swallowed deliberately — they must never reach a log line that could carry
    a key fragment.
    """
    from app.core.db import get_async_session_factory
    from app.services.provider_key_service import ProviderKeyService

    try:
        factory = get_async_session_factory()
        async with factory() as session:
            return await ProviderKeyService().get_active_key_async(
                session, provider_name
            )
    except Exception:  # noqa: BLE001 — fail closed, never leak details
        return None


async def resolve_replicate_key() -> Optional[str]:
    """Resolve the active Replicate API key, or ``None`` if none configured."""
    return await _resolve_key_for("replicate")


async def resolve_openai_key() -> Optional[str]:
    """Resolve the active OpenAI API key (reused for OpenAI TTS). Sprint 4."""
    return await _resolve_key_for("openai")


async def resolve_elevenlabs_key() -> Optional[str]:
    """Resolve the active ElevenLabs API key. Sprint 4."""
    return await _resolve_key_for("elevenlabs")
