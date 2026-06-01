"""Group A / A2 — resolve-time placeholder filter + auto-invalidate on 401.

Part 1: ``_resolve_key_candidates`` must drop a placeholder row stored in
``provider_keys`` (not just the env fallback) so the pool only yields keys
that look real.

Part 2: when a provider rejects a key with 401/403 mid-dispatch, the key's
row is flipped to invalid and the dispatcher rotates to the next key in the
pool within the same request — bounded by the pool size so an all-401 pool
can't spin.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.exceptions import ProviderError
from app.domain.providers import ProviderResponse
from app.repositories.provider_key_repository import ProviderKeyRepository
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.provider_key_service import (
    ProviderKeyService,
    _encode,
    clear_keys_cache,
)


@dataclass
class _Route:
    provider_name: str
    model_name: str


# --------------------------------------------------------------------------
# Part 1 — resolve filter
# --------------------------------------------------------------------------


class TestResolveFiltersPlaceholderRows:
    def test_pool_with_valid_and_placeholder_returns_only_valid(
        self, db_session
    ):
        clear_keys_cache()
        repo = ProviderKeyRepository(db_session)
        real = "sk-proj-" + "a" * 60
        placeholder = "sk-ant-api03-your-key-here-xxxx"
        repo.create("openai", "real", _encode(real))
        repo.create("openai", "placeholder", _encode(placeholder))
        db_session.flush()

        svc = ProviderKeyService()
        candidates = svc._resolve_key_candidates(db_session, "openai")

        assert candidates == [real]


# --------------------------------------------------------------------------
# Part 2a — invalidate_key_by_value flips the row
# --------------------------------------------------------------------------


class TestInvalidateKeyByValue:
    def test_matching_active_key_is_marked_invalid(self, db_session):
        clear_keys_cache()
        repo = ProviderKeyRepository(db_session)
        key = "sk-proj-" + "b" * 60
        created = repo.create("openai", "k1", _encode(key))
        db_session.flush()

        svc = ProviderKeyService()
        invalidated_id = svc.invalidate_key_by_value(db_session, "openai", key)

        assert invalidated_id == created.id
        row = repo.get_by_id(created.id)
        assert row.is_active is False
        assert row.last_test_status == "invalid"

    def test_no_match_returns_none(self, db_session):
        clear_keys_cache()
        svc = ProviderKeyService()
        assert (
            svc.invalidate_key_by_value(db_session, "openai", "sk-unknown-xyz")
            is None
        )


# --------------------------------------------------------------------------
# Part 2b — dispatcher key rotation on 401
# --------------------------------------------------------------------------


def _auth_error(provider: str = "openai") -> ProviderError:
    return ProviderError(
        f"Provider '{provider}' rejected credentials (401).",
        details={"provider": provider, "status": 401},
    )


@pytest.mark.asyncio
async def test_dispatch_rotates_to_next_key_on_401(monkeypatch):
    dispatcher = ProviderDispatcher()
    invalidated: list[tuple[str, str]] = []

    # First key 401s, second key succeeds.
    keys_seen: list[str] = []

    async def _fake_dispatch(self, *, route, payload, messages, api_key=None):
        keys_seen.append(api_key)
        if api_key == "bad-key":
            raise _auth_error()
        return ProviderResponse(content="ok", model=route.model_name)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _fake_dispatch)

    def _resolver(provider):
        return "good-key"

    def _invalidate(provider, key):
        invalidated.append((provider, key))

    resp = await dispatcher._dispatch_one(
        route=_Route("openai", "gpt-4o"),
        provider="openai",
        model="gpt-4o",
        payload=None,
        messages=[],
        first_key="bad-key",
        key_resolver=_resolver,
        invalidate_key=_invalidate,
    )

    assert resp.content == "ok"
    assert keys_seen == ["bad-key", "good-key"]
    assert invalidated == [("openai", "bad-key")]


@pytest.mark.asyncio
async def test_dispatch_bounded_when_all_keys_401(monkeypatch):
    dispatcher = ProviderDispatcher()
    invalidated: list[str] = []
    call_count = 0

    async def _fake_dispatch(self, *, route, payload, messages, api_key=None):
        nonlocal call_count
        call_count += 1
        raise _auth_error()

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _fake_dispatch)

    # Resolver hands out a fresh key once, then the pool is empty.
    pool = ["key-2"]

    def _resolver(provider):
        return pool.pop(0) if pool else None

    def _invalidate(provider, key):
        invalidated.append(key)

    with pytest.raises(ProviderError):
        await dispatcher._dispatch_one(
            route=_Route("openai", "gpt-4o"),
            provider="openai",
            model="gpt-4o",
            payload=None,
            messages=[],
            first_key="key-1",
            key_resolver=_resolver,
            invalidate_key=_invalidate,
        )

    # Tried key-1 then key-2, then resolver dry → stop. No infinite loop.
    assert call_count == 2
    assert invalidated == ["key-1", "key-2"]


@pytest.mark.asyncio
async def test_non_auth_error_does_not_rotate(monkeypatch):
    dispatcher = ProviderDispatcher()
    invalidated: list[str] = []

    async def _fake_dispatch(self, *, route, payload, messages, api_key=None):
        raise ProviderError("500 boom", details={"provider": "openai", "status": 500})

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _fake_dispatch)

    with pytest.raises(ProviderError):
        await dispatcher._dispatch_one(
            route=_Route("openai", "gpt-4o"),
            provider="openai",
            model="gpt-4o",
            payload=None,
            messages=[],
            first_key="key-1",
            key_resolver=lambda p: "key-2",
            invalidate_key=lambda p, k: invalidated.append(k),
        )

    assert invalidated == [], "non-auth error must not invalidate or rotate"
