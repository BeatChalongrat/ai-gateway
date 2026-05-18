"""Persists DLP ``span_map`` across turns of the same conversation.

Single-turn behaviour: each request creates a fresh ``span_map`` on
the orchestrator stack frame; the AI's response is restored against
that map and the map dies with the request. No cross-turn carry-over.

When the AI echoes a placeholder from a previous turn — for example,
the user pasted ``john@acme.com`` in turn 1, the gateway masked it to
``[EMAIL_0]``, the AI remembered the token, and now in turn 3 the AI
writes "I'll send your reply to [EMAIL_0]" — the new turn's
``span_map`` doesn't carry that key, so ``transformer.restore`` leaves
the literal token in the user-facing text and logs an orphan warning.

This vault closes the gap by writing the span map to Redis keyed on
``conversation_id`` with a 2-hour TTL. ``restore`` accepts a
``vault_resolver`` callback that the orchestrator wires to
``ConversationVault.resolve``; orphans surviving the local span_map
get a second chance against the cross-turn vault.

Security model unchanged from the pre-async version. May 2026
scalability fix: the transport is now ``redis.asyncio`` via the shared
pool. ``resolve`` remains callable from sync contexts because
``transformer.restore`` is itself sync — the orchestrator passes a
sync lambda into it. The async variants are exposed for callers that
already have ``await`` available.
"""

from __future__ import annotations

import inspect
from typing import Any, Iterable

import structlog

from app.services._redis_pool import get_async_redis, run_sync


VAULT_TTL_SECONDS = 7200  # 2 hours of inactivity

_KEY_PREFIX = "dlp_vault"

logger = structlog.get_logger(__name__)


async def _await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class ConversationVault:
    """Redis-backed cross-turn span_map store."""

    def __init__(self, redis_client: Any = None) -> None:
        self._fake_client = redis_client
        self._async_client: Any | None = None
        # Public attribute for legacy tests that introspect the
        # injected fake client directly (e.g. ``vault._redis.ttl(...)``
        # against fakeredis). When no fake is supplied, this stays
        # None — production code calls ``_client()`` instead.
        self._redis = redis_client

    def _client(self) -> Any:
        if self._fake_client is not None:
            return self._fake_client
        if self._async_client is None:
            self._async_client = get_async_redis()
        return self._async_client

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"{_KEY_PREFIX}:{conversation_id}"

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def extend_async(
        self,
        conversation_id: str,
        span_map: dict[str, str],
    ) -> None:
        if not conversation_id or not span_map:
            return
        try:
            key = self._key(conversation_id)
            client = self._client()
            pipe = client.pipeline()
            pipe.hset(key, mapping=dict(span_map))
            pipe.expire(key, VAULT_TTL_SECONDS)
            result = pipe.execute()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning(
                "conversation_vault_extend_failed",
                conversation_id=conversation_id,
                error=str(exc)[:200],
            )

    async def resolve_async(
        self,
        conversation_id: str,
        tokens: Iterable[str],
    ) -> dict[str, str]:
        token_list = [t for t in tokens if t]
        if not conversation_id or not token_list:
            return {}
        try:
            client = self._client()
            key = self._key(conversation_id)
            values = await _await(client.hmget(key, token_list))
        except Exception as exc:
            logger.warning(
                "conversation_vault_resolve_failed",
                conversation_id=conversation_id,
                error=str(exc)[:200],
            )
            return {}
        out: dict[str, str] = {}
        for token, value in zip(token_list, values):
            if value is not None:
                out[token] = value
        return out

    async def delete_async(self, conversation_id: str) -> None:
        if not conversation_id:
            return
        try:
            client = self._client()
            await _await(client.delete(self._key(conversation_id)))
        except Exception as exc:
            logger.warning(
                "conversation_vault_delete_failed",
                conversation_id=conversation_id,
                error=str(exc)[:200],
            )

    async def get_all_tokens_async(
        self, conversation_id: str
    ) -> dict[str, str]:
        if not conversation_id:
            return {}
        try:
            client = self._client()
            return dict(
                await _await(client.hgetall(self._key(conversation_id))) or {}
            )
        except Exception as exc:
            logger.warning(
                "conversation_vault_get_all_failed",
                conversation_id=conversation_id,
                error=str(exc)[:200],
            )
            return {}

    # ------------------------------------------------------------------
    # Sync API — called from transformer.restore (sync) via a closure
    # in the orchestrator. Bridged to the async client via run_sync.
    # ------------------------------------------------------------------

    def extend(
        self, conversation_id: str, span_map: dict[str, str]
    ) -> None:
        if self._fake_client is not None:
            self._extend_via_fake(conversation_id, span_map)
            return
        run_sync(self.extend_async(conversation_id, span_map))

    def resolve(
        self, conversation_id: str, tokens: Iterable[str]
    ) -> dict[str, str]:
        if self._fake_client is not None:
            return self._resolve_via_fake(conversation_id, tokens)
        return run_sync(self.resolve_async(conversation_id, tokens))

    def delete(self, conversation_id: str) -> None:
        if self._fake_client is not None:
            self._delete_via_fake(conversation_id)
            return
        run_sync(self.delete_async(conversation_id))

    def get_all_tokens(self, conversation_id: str) -> dict[str, str]:
        if self._fake_client is not None:
            return self._get_all_tokens_via_fake(conversation_id)
        return run_sync(self.get_all_tokens_async(conversation_id))

    # ------------------------------------------------------------------
    # Fake-client paths — preserve verbatim sync behaviour for tests
    # ------------------------------------------------------------------

    def _extend_via_fake(
        self, conversation_id: str, span_map: dict[str, str]
    ) -> None:
        if not conversation_id or not span_map:
            return
        try:
            key = self._key(conversation_id)
            pipe = self._fake_client.pipeline()
            pipe.hset(key, mapping=dict(span_map))
            pipe.expire(key, VAULT_TTL_SECONDS)
            pipe.execute()
        except Exception as exc:
            logger.warning(
                "conversation_vault_extend_failed",
                conversation_id=conversation_id,
                error=str(exc)[:200],
            )

    def _resolve_via_fake(
        self, conversation_id: str, tokens: Iterable[str]
    ) -> dict[str, str]:
        token_list = [t for t in tokens if t]
        if not conversation_id or not token_list:
            return {}
        try:
            key = self._key(conversation_id)
            values = self._fake_client.hmget(key, token_list)
        except Exception:
            return {}
        out: dict[str, str] = {}
        for token, value in zip(token_list, values):
            if value is not None:
                out[token] = value
        return out

    def _delete_via_fake(self, conversation_id: str) -> None:
        if not conversation_id:
            return
        try:
            self._fake_client.delete(self._key(conversation_id))
        except Exception:
            pass

    def _get_all_tokens_via_fake(self, conversation_id: str) -> dict[str, str]:
        if not conversation_id:
            return {}
        try:
            return dict(
                self._fake_client.hgetall(self._key(conversation_id)) or {}
            )
        except Exception:
            return {}


# Module-level singleton — same construction pattern as ``CacheService``.
conversation_vault = ConversationVault()
