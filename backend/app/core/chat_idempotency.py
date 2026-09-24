"""FIX133 F2 — ``Idempotency-Key`` on ``POST /v1/chat/completions``.

An automated client retries. Without a key, every retry of a chat completion
was a NEW turn and a NEW charge (the backend minted its own request id and
nothing tied two attempts together). With ``Idempotency-Key: <client id>``
(8–64 chars of ``[A-Za-z0-9._-]``, the same shape chat-edge's X-Request-Id
uses) a retry is answered from the first attempt:

* the first attempt claims the key (per tenant + API key) with the body's
  hash and runs;
* a second attempt while the first still runs → 409 ``idempotency_in_progress``;
* the same key with a different body → 422 ``idempotency_key_reused``;
* after a NON-streaming attempt finished → its status and body again, with
  ``Idempotent-Replayed: true``;
* after a STREAMING attempt finished → 409 ``idempotent_stream_done``: the
  turn already ran and was charged once; a stream is not stored to replay,
  and it is never generated a second time.

Keys live in Redis for 24 h (``idem:chat:<tenant>:<sha256(key id | key)>``);
a claim is one ``SET NX``. An attempt that fails with an exception releases
its claim so the retry can run. When Redis is unreachable the key is not
honoured (the request runs; logged once a minute) — the same fail-open the
rate limiter chose. A memory store drives the unit tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

KEY_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
TTL_S = 24 * 3600
MAX_STORED_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class Claim:
    state: str  # new | replay | in_progress | mismatch | stream_done
    status: int | None = None
    body: Any = None


class IdempotencyStore(Protocol):
    name: str

    async def claim(self, key: str, record: str) -> str | None: ...  # None = claimed; else existing record

    async def put(self, key: str, record: str) -> None: ...

    async def delete(self, key: str) -> None: ...


class MemoryIdempotencyStore:
    name = "memory"

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, str]] = {}

    def _expire(self) -> None:
        now = time.time()
        for k, (exp, _) in list(self._d.items()):
            if exp <= now:
                del self._d[k]

    async def claim(self, key: str, record: str) -> str | None:
        self._expire()
        if key in self._d:
            return self._d[key][1]
        self._d[key] = (time.time() + TTL_S, record)
        return None

    async def put(self, key: str, record: str) -> None:
        self._d[key] = (time.time() + TTL_S, record)

    async def delete(self, key: str) -> None:
        self._d.pop(key, None)


class RedisIdempotencyStore:
    name = "redis"

    def __init__(self, client: Any) -> None:
        self._r = client

    async def claim(self, key: str, record: str) -> str | None:
        created = await self._r.set(key, record, ex=TTL_S, nx=True)
        if created:
            return None
        raw = await self._r.get(key)
        if raw is None:  # expired between the two calls: try once more
            created = await self._r.set(key, record, ex=TTL_S, nx=True)
            if created:
                return None
            raw = await self._r.get(key)
        return raw.decode("utf-8") if isinstance(raw, bytes) else raw

    async def put(self, key: str, record: str) -> None:
        await self._r.set(key, record, ex=TTL_S)

    async def delete(self, key: str) -> None:
        await self._r.delete(key)


def body_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


class ChatIdempotency:
    def __init__(self, store: IdempotencyStore) -> None:
        self.store = store
        self._failed_at = 0.0

    @staticmethod
    def valid_key(raw: str | None) -> str | None:
        raw = (raw or "").strip()
        return raw if KEY_RE.match(raw) else None

    @staticmethod
    def redis_key(tenant_id: str, key_id: str, idem_key: str) -> str:
        digest = hashlib.sha256(f"{key_id}|{idem_key}".encode("utf-8")).hexdigest()
        return f"idem:chat:{tenant_id}:{digest}"

    def _trouble(self, exc: BaseException) -> None:
        now = time.monotonic()
        if now - self._failed_at > 60:
            self._failed_at = now
            logger.warning("chat_idempotency_store_unavailable", store=self.store.name,
                           err=f"{type(exc).__name__}: {str(exc)[:120]}")

    async def claim(self, tenant_id: str, key_id: str, idem_key: str, payload_json: str) -> Claim:
        key = self.redis_key(tenant_id, key_id, idem_key)
        record = json.dumps({"state": "in_progress", "hash": body_hash(payload_json), "at": time.time()})
        try:
            existing = await self.store.claim(key, record)
        except Exception as exc:  # noqa: BLE001 — fail open
            self._trouble(exc)
            return Claim("new")
        if existing is None:
            return Claim("new")
        try:
            rec = json.loads(existing)
        except (TypeError, ValueError):
            return Claim("new")
        if rec.get("hash") != body_hash(payload_json):
            return Claim("mismatch")
        state = rec.get("state")
        if state == "in_progress":
            return Claim("in_progress")
        if state == "stream_done":
            return Claim("stream_done")
        if state == "done":
            return Claim("replay", status=int(rec.get("status") or 200), body=rec.get("body"))
        return Claim("new")

    async def complete(self, tenant_id: str, key_id: str, idem_key: str, payload_json: str,
                       status: int, body: Any) -> None:
        key = self.redis_key(tenant_id, key_id, idem_key)
        try:
            encoded = json.dumps(body)
            if len(encoded) > MAX_STORED_BODY_BYTES:
                # too big to replay: the retry is refused as done, never re-run
                record = json.dumps({"state": "stream_done", "hash": body_hash(payload_json), "at": time.time()})
            else:
                record = json.dumps({"state": "done", "hash": body_hash(payload_json), "status": status,
                                     "body": body, "at": time.time()})
            await self.store.put(key, record)
        except Exception as exc:  # noqa: BLE001
            self._trouble(exc)

    async def stream_done(self, tenant_id: str, key_id: str, idem_key: str, payload_json: str) -> None:
        key = self.redis_key(tenant_id, key_id, idem_key)
        try:
            await self.store.put(key, json.dumps({"state": "stream_done", "hash": body_hash(payload_json),
                                                  "at": time.time()}))
        except Exception as exc:  # noqa: BLE001
            self._trouble(exc)

    async def release(self, tenant_id: str, key_id: str, idem_key: str) -> None:
        try:
            await self.store.delete(self.redis_key(tenant_id, key_id, idem_key))
        except Exception as exc:  # noqa: BLE001
            self._trouble(exc)


_instance: ChatIdempotency | None = None


def chat_idempotency() -> ChatIdempotency:
    """The process's instance: Redis (the shared async pool), else memory."""
    global _instance
    if _instance is None:
        try:
            from app.services._redis_pool import get_async_redis

            _instance = ChatIdempotency(RedisIdempotencyStore(get_async_redis()))
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat_idempotency_redis_unavailable_using_memory", err=str(exc)[:120])
            _instance = ChatIdempotency(MemoryIdempotencyStore())
    return _instance


def configure(instance: ChatIdempotency | None) -> None:
    global _instance
    _instance = instance
