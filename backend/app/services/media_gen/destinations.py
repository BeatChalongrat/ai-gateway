"""Shared media destination order + fallback rule (FIX67).

One place decides, for a media type (``image`` / ``video``), the ORDERED list
of destinations to try and which of them are usable right now. Both the
synchronous image path and the asynchronous video job loop consume this — they
keep their own runners (an image call returns bytes now; a video job polls for
minutes), but the *order source*, the *usable filter*, and the *≤2-tier /
never-upgrade / cross-provider* rule live here so there is one answer, not three.

The order is admin configuration (``system_settings``), never a hardcoded model
name. The day a real second backend gets a key, the operator edits one row; no
code changes.

Rule, borrowed verbatim from the chat ``CURATED_FALLBACKS`` (``model_tiers.py``):

* **at most 2 tiers total** — a primary and one fallback, so a single failure
  cannot fan out into a wide rescue sweep;
* **each tier a different provider** — a fallback on the same dead account is no
  fallback;
* **never a costlier tier** — the fallback must not silently escalate spend
  (enforced at config time by the admin validator, see ``admin_media_order``).

``mock`` is a TEST-ONLY provider. It is usable only when ``media_allow_mock`` is
explicitly on, and never appears in a default order — see :func:`is_mock_provider`
and the guard the admin validator + the leak test enforce.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.system_setting import SystemSettingORM

# system_settings keys.
ORDER_KEYS: dict[str, str] = {
    "image": "media_order:image",
    "video": "media_order:video",
}
ALLOW_MOCK_KEY = "media_allow_mock"

# Providers that must never serve a real user unless mock is explicitly enabled.
MOCK_PROVIDERS: frozenset[str] = frozenset({"mock"})

# Rule: primary + at most one fallback.
MAX_TIERS = 2


def is_mock_provider(provider: str) -> bool:
    return provider in MOCK_PROVIDERS


@dataclass(frozen=True)
class Destination:
    provider: str
    model: str

    def label(self) -> str:
        """What the user sees under the artifact ("made by …"). The model id is
        the meaningful half; the provider disambiguates only when needed."""
        return self.model


def parse_order(raw: str | None) -> list[Destination]:
    """Parse ``"openai:gpt-image-1, mock:mock-image-1"`` → destinations.

    Tolerant: blank entries dropped, whitespace trimmed, an entry without a
    ``provider:model`` shape is skipped rather than raising (the admin
    validator is where a bad value is rejected loudly; resolution never
    500s a request over a malformed setting).
    """
    out: list[Destination] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        provider, _, model = chunk.partition(":")
        provider, model = provider.strip(), model.strip()
        if provider and model:
            out.append(Destination(provider=provider, model=model))
    return out


async def _get_setting(session: AsyncSession, key: str) -> str | None:
    row = await session.get(SystemSettingORM, key)
    return row.value if row is not None else None


async def allow_mock(session: AsyncSession) -> bool:
    """Whether the ``mock`` provider may serve a request on this deployment.

    Default OFF. A mock artifact handed to a real user unaware is worse than no
    artifact (the ``mock-chat-1`` incident), so mock is opt-in and loud.
    """
    v = (await _get_setting(session, ALLOW_MOCK_KEY) or "").strip().lower()
    return v in ("true", "1", "yes", "on")


async def resolve_order(session: AsyncSession, media_type: str) -> list[Destination]:
    """The admin-configured destination order for a media type (may be empty)."""
    key = ORDER_KEYS.get(media_type)
    if key is None:
        return []
    return parse_order(await _get_setting(session, key))


# --- sync variants (public_models is a sync route) -------------------------


def _get_setting_sync(session: Session, key: str) -> str | None:
    row = session.get(SystemSettingORM, key)
    return row.value if row is not None else None


def allow_mock_sync(session: Session) -> bool:
    v = (_get_setting_sync(session, ALLOW_MOCK_KEY) or "").strip().lower()
    return v in ("true", "1", "yes", "on")


def resolve_order_sync(session: Session, media_type: str) -> list[Destination]:
    key = ORDER_KEYS.get(media_type)
    if key is None:
        return []
    return parse_order(_get_setting_sync(session, key))


def usable_destinations(
    order: list[Destination],
    *,
    valid_providers: set[str],
    mock_enabled: bool,
    registered_providers: set[str] | None = None,
) -> list[Destination]:
    """Filter an order to the destinations that can actually run right now, then
    cap at :data:`MAX_TIERS`.

    A destination is usable when its provider is registered (an adapter/backend
    exists for it) AND either the provider holds a valid key, or the provider is
    ``mock`` and mock is explicitly enabled. The first-different-provider rule is
    applied so the two tiers never share a provider.
    """
    seen_providers: set[str] = set()
    out: list[Destination] = []
    for d in order:
        if registered_providers is not None and d.provider not in registered_providers:
            continue
        if is_mock_provider(d.provider):
            if not mock_enabled:
                continue
        elif d.provider not in valid_providers:
            continue
        if d.provider in seen_providers:
            # cross-provider rule: a second tier on the same provider is not a
            # fallback. Skip rather than count it toward the cap.
            continue
        seen_providers.add(d.provider)
        out.append(d)
        if len(out) >= MAX_TIERS:
            break
    return out
