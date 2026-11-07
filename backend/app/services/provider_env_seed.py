"""First-boot seeding of provider configuration from the environment.

THE RULE, stated once, because everything here follows from it::

    a provider that has never had a key   ->  env is read, and seeds the DB
    the moment a key exists (seeded, or   ->  the database owns that provider
    typed into the admin UI)                  permanently; env is never read
                                              for it again

Editing an env var on a running system therefore does **nothing**. That is
the point, not a limitation: two live sources for the same value is what
produced every confusing bug in this area — a badge reading ``via env`` while
dispatch used a different key, a rotated UI key that chat followed and the
utility clients did not.

This is a known shape. Open WebUI ships it as ``PersistentConfig``: env is
read once, stored, and thereafter *"will no longer use the external
environment variable values"*. LiteLLM overlays its database on top of
``config.yaml`` at startup so the database wins, and its production guidance
is to keep one primary source of truth. We copy the shape.

We deliberately do NOT copy their documented wart. Open WebUI's
``ENABLE_PERSISTENT_CONFIG=False`` escape hatch leaves the UI *looking*
editable while every edit is silently discarded on restart. Nothing here can
put the UI in that state: a seeded key is an ordinary row, editable and
deletable like any other, and once it exists env can no longer contradict it.

WHY THE GATE IS A MARKER AND NOT A ROW COUNT
--------------------------------------------
The obvious rule — "seed when the provider has no key" — fails in both
directions, and the two failures pull opposite ways:

*Too eager.* An operator deletes a key on purpose, restarts, and env puts it
back. Silently undoing an operator's decision is worse than any convenience
it buys. A row count cannot tell "never had one" from "had one and removed
it"; ``delete_key`` is a soft delete, so the row is still there but is not
usable, and a usable-row count reads the same as a fresh install.

*Too shy.* The old importer (removed in ``f19a0ac0``) used "no rows at all",
and this box shows what that costs: ``anthropic`` has 1 inactive row and
``openrouter`` 3, left behind by that same importer. Under a row-count rule
those providers could never be seeded again, for the lifetime of the
database, and nothing on screen would say why.

So the gate is an explicit marker in ``system_settings``, written when a
provider first acquires a key — from this seeder or from the admin UI. It
records *that a decision was made*, which is the thing a row count cannot
express. Inactive leftovers are irrelevant to it.

A boot that finds env unset or holding a placeholder decides nothing and
writes nothing, so the ordinary "copy .env.example, boot, then fill in the
real keys" flow still gets seeded on the next restart.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

# ``system_settings`` key namespace for the ownership markers. One row per
# (kind, provider); the value is the reason, the description is for a human
# reading the table months later.
_MARKER_PREFIX = "provider_env_seed:"

KIND_KEY = "key"
KIND_BASE_URL = "base_url"

# The providers whose credentials env may seed. Deliberately explicit rather
# than derived from the adapter registry: adding a provider to this map is a
# decision about which env var names are supported, not a side effect of
# registering an adapter.
_ENV_SEED_KEYS: dict[str, str] = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "openrouter": "openrouter_api_key",
}


def marker_key(kind: str, provider_name: str) -> str:
    return f"{_MARKER_PREFIX}{kind}:{provider_name}"


def is_provider_owned(session: Session, kind: str, provider_name: str) -> bool:
    """True when this provider has already acquired a value of ``kind``.

    Once true, env is never consulted for it again.
    """
    from app.models.system_setting import SystemSettingORM

    return session.get(SystemSettingORM, marker_key(kind, provider_name)) is not None


def mark_provider_owned(
    session: Session, kind: str, provider_name: str, reason: str
) -> None:
    """Record that ``provider_name`` now owns its own ``kind`` value.

    Insert-if-absent: the FIRST reason wins, so a later UI edit does not
    rewrite the history of how the provider was originally configured. Does
    not commit — the caller owns the transaction.

    FLUSHES after inserting, and that is load-bearing rather than tidy.
    Production ``SessionLocal`` is built with ``autoflush=False``
    (app/core/db.py:96). The seed path marks a provider owned and then calls
    a writer that marks it again; without a flush in between, the pending row
    is not in the identity map, the second call re-SELECTs, finds nothing,
    and adds a SECOND row with the same primary key — an IntegrityError at
    commit that takes the whole seed down.

    The key path survives without this only by accident: ``add_key`` goes
    through ``repo.create`` → ``BaseRepository.add``, which flushes to
    populate the id. The endpoint path has no such flush, so it is the one
    that actually breaks — and depending on a repository's internal flush for
    correctness here would be a trap for whoever changes it next.
    """
    from app.models.system_setting import SystemSettingORM

    key = marker_key(kind, provider_name)
    if session.get(SystemSettingORM, key) is not None:
        return
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session.add(
        SystemSettingORM(
            key=key,
            value=reason,
            description=(
                f"{provider_name} {kind} first configured via '{reason}' at "
                f"{stamp}. The environment is no longer read for this "
                f"provider — change it in the admin UI."
            ),
        )
    )
    session.flush()


def _env_value(field: str) -> str:
    return (getattr(settings, field, None) or "").strip()


def seed_provider_keys_from_env(session: Session) -> dict[str, str]:
    """Seed ``provider_keys`` from env for providers that never had a key.

    Returns ``{provider: outcome}`` for logging. Never commits; never prints
    or returns key material — outcomes are verdicts only. This is a DLP
    product and a boot log is not a place for credentials.
    """
    # Imported here rather than at module scope: this module is imported from
    # main.py's startup path, and provider_key_service pulls in the ORM +
    # crypto stack that the schema guard has only just finished checking.
    from app.services.provider_key_service import (
        ProviderKeyService,
        _looks_like_placeholder_key,
        _usable_rows,
    )
    from app.repositories.provider_key_repository import ProviderKeyRepository

    svc = ProviderKeyService()
    repo = ProviderKeyRepository(session)
    outcomes: dict[str, str] = {}

    for provider, field in _ENV_SEED_KEYS.items():
        if is_provider_owned(session, KIND_KEY, provider):
            outcomes[provider] = "owned-by-database"
            continue

        raw = _env_value(field)
        if not raw:
            # Nothing was decided, so nothing is recorded: a deployment that
            # fills in its real key after a first look still gets seeded.
            outcomes[provider] = "env-unset"
            continue

        # The SAME placeholder test the resolver uses, imported rather than
        # re-implemented. A second copy would drift, and the failure mode of
        # drift here is seeding a value dispatch will refuse to send.
        if _looks_like_placeholder_key(raw):
            outcomes[provider] = "env-placeholder"
            continue

        if _usable_rows(repo.list_active_by_provider(provider), provider, log=False):
            # Already configured by hand. Record that, so deleting that key
            # later can never re-open the env door behind the operator.
            mark_provider_owned(session, KIND_KEY, provider, "admin-ui")
            outcomes[provider] = "already-configured"
            continue

        # Claim ownership BEFORE writing the value. ``add_key`` marks the
        # provider owned too (so a UI-added key closes the env door without
        # waiting for a boot), and the mark is insert-if-absent — so if the
        # seeder marked second, the row would read "admin-ui" for a key the
        # operator never typed. Provenance is the whole point of the row.
        mark_provider_owned(session, KIND_KEY, provider, "env-seed")
        svc.add_key(session, provider, raw, label="Seeded from environment")
        outcomes[provider] = "seeded"

    return outcomes


def seed_provider_base_urls_from_env(session: Session) -> dict[str, str]:
    """Seed ``system_settings`` base-URL rows from ``PROVIDER_BASE_URL_OVERRIDES``.

    Same rule, same marker namespace. ``base_urls`` itself still does not
    import ``settings`` — the env map is read here and written to the DB,
    so there is no env tier at resolve time, only a seed at boot.
    """
    from app.services.providers.base_urls import (
        get_stored_base_url,
        set_stored_base_url,
        validate_provider_base_url,
    )

    raw_map = (settings.provider_base_url_overrides or "").strip()
    if not raw_map:
        return {}
    try:
        parsed = json.loads(raw_map)
    except (ValueError, TypeError):
        logger.warning(
            "PROVIDER_BASE_URL_OVERRIDES is not valid JSON; no endpoint seeded"
        )
        return {"*": "env-malformed"}
    if not isinstance(parsed, dict):
        logger.warning(
            "PROVIDER_BASE_URL_OVERRIDES is not a JSON object; no endpoint seeded"
        )
        return {"*": "env-malformed"}

    outcomes: dict[str, str] = {}
    for provider, url in parsed.items():
        provider = str(provider).strip()
        url = str(url or "").strip()
        if not provider or not url:
            continue
        if is_provider_owned(session, KIND_BASE_URL, provider):
            outcomes[provider] = "owned-by-database"
            continue
        if get_stored_base_url(session, provider):
            mark_provider_owned(session, KIND_BASE_URL, provider, "admin-ui")
            outcomes[provider] = "already-configured"
            continue
        try:
            # The same SSRF/scheme validation the admin PATCH runs. A seed is
            # not a reason to accept an endpoint the UI would reject.
            clean = validate_provider_base_url(url)
        except Exception as exc:  # noqa: BLE001 — one bad entry must not stop boot
            logger.warning(
                "Skipping base-URL seed for %s: %s", provider, exc
            )
            outcomes[provider] = "env-invalid"
            continue
        # Same ordering, same reason as the key path above.
        mark_provider_owned(session, KIND_BASE_URL, provider, "env-seed")
        set_stored_base_url(session, provider, clean)
        outcomes[provider] = "seeded"

    return outcomes


def report_outcomes(
    key_outcomes: dict[str, str], url_outcomes: dict[str, str]
) -> None:
    """One line per provider, every boot.

    The too-shy failure mode is not just "nothing was seeded" — it is
    "nothing was seeded and nobody can see why". The reason is therefore
    always on the boot log, and it is a verdict, never a value.
    """
    for provider, outcome in sorted(key_outcomes.items()):
        print(f"[startup] provider key {provider}: {outcome}", flush=True)
    for provider, outcome in sorted(url_outcomes.items()):
        print(f"[startup] provider endpoint {provider}: {outcome}", flush=True)
