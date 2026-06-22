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


async def resolve_replicate_key() -> Optional[str]:
    """Resolve the active Replicate API key, or ``None`` if none configured.

    Fails CLOSED: any DB / lookup error returns ``None`` so the adapter
    raises ``ProviderKeyMissing`` before making a provider call rather than
    dispatching with a bad/absent credential. The exception details are
    swallowed deliberately — they must never reach a log line that could
    carry a key fragment.
    """
    from app.core.db import get_async_session_factory
    from app.services.provider_key_service import ProviderKeyService

    try:
        factory = get_async_session_factory()
        async with factory() as session:
            return await ProviderKeyService().get_active_key_async(
                session, "replicate"
            )
    except Exception:  # noqa: BLE001 — fail closed, never leak details
        return None
