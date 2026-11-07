"""FIX133 A2/A3 — the turn queue: a cap on the turns generating at once,
a visible waiting line above it, and a per-caller share of the slots.

Every chat turn from every client — the web page and the desktop through
chat-edge, the desktop's own turns, API keys — passes ``POST /v1/chat/
completions``. This is the one place a cap covers all of them, so this is
where the queue lives. The cap is on the box's CPU, not on a worker: the
line is shared across the gunicorn workers (and later across machines)
through Redis.

Shape:

* ``max_active`` turns may generate at once (``TURN_QUEUE_MAX_ACTIVE``; 0 =
  ``sizing.default_max_active_turns()``, the CPU count).
* Above that a turn WAITS, first come first served, and the caller is told
  where it stands: a streaming request gets ``{"type":"queued","position":
  N,...}`` frames (the response opens at once), a non-streaming request
  simply waits (bounded). Never a silent wait.
* One identity (a portal user behind chat-edge, an API key, a service
  caller) may hold at most ``max_per_identity`` slots (``TURN_QUEUE_MAX_
  PER_IDENTITY``; 0 = max(1, max_active // 6)). A waiting turn of an
  identity at its share is skipped over until one of its slots frees, so
  one looping client cannot take the whole line.
* Only when the line itself is longer than ``max_waiting`` (0 = 4 x
  max_active) is a turn refused: 503 ``queue_full`` + Retry-After. A turn
  that waited ``max_wait_seconds`` without a slot ends as ``queue_timeout``.
* A follow-up call of a turn already admitted (a tool round, keyed by the
  caller's X-Request-Id) re-enters without waiting: a turn queues once.
* Background purposes (a title, a memory extraction) never queue: they are
  short, bounded elsewhere, and would otherwise sit behind users.

Two stores hold the line: Redis (one Lua script does the whole admission
atomically, so every worker and every instance sees the same line) and a
per-process memory store, used when Redis is unreachable — the cap then
holds per worker, which is still a cap, and never a silent failure. The
in-memory store is also what the unit tests drive; the Redis script is
proven against it (test_fix133_turn_queue.py, with fakeredis).
"""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import structlog

from app.core import sizing

logger = structlog.get_logger(__name__)

# A turn's slot outlives its last renewal by this much: how long a crashed
# worker's slots stay taken before the line moves on without them.
ACTIVE_TTL_S_DEFAULT = 45.0
# A waiting ticket nobody polls any more (its worker died) is dropped after
# max_wait + this.
WAIT_STALE_GRACE_S = 30.0

PURPOSES_THAT_NEVER_QUEUE = frozenset({"title", "memory_extraction", "memory", "summary", "compaction", "state_extraction"})


class QueueFull(Exception):
    """The waiting line is longer than max_waiting; nothing was queued."""

    def __init__(self, waiting: int, max_waiting: int, retry_after_s: int) -> None:
        super().__init__(f"turn queue full ({waiting} waiting, limit {max_waiting})")
        self.waiting = waiting
        self.max_waiting = max_waiting
        self.retry_after_s = retry_after_s


class QueueTimeout(Exception):
    """Waited max_wait_seconds and never got a slot."""

    def __init__(self, waited_s: float, position: int) -> None:
        super().__init__(f"turn queue timeout after {waited_s:.0f}s at position {position}")
        self.waited_s = waited_s
        self.position = position


@dataclass(frozen=True)
class QueueStatus:
    granted: bool
    position: int  # 1-based place in the line; 0 once granted
    ahead: int  # tickets ahead of this one
    active: int
    waiting: int
    max_active: int
    estimate_s: int | None  # None when no turn has finished yet

    def frame(self) -> dict[str, Any]:
        """The wire shape of the ``queued`` SSE frame."""
        out: dict[str, Any] = {
            "type": "queued",
            "position": self.position,
            "ahead": self.ahead,
            "active": self.active,
            "waiting": self.waiting,
            "max_active": self.max_active,
        }
        if self.estimate_s is not None:
            out["estimate_s"] = self.estimate_s
        return out


@dataclass(frozen=True)
class QueueSnapshot:
    enabled: bool
    store: str
    active: int
    waiting: int
    max_active: int
    max_per_identity: int
    max_waiting: int
    oldest_wait_s: float
    avg_turn_s: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "store": self.store,
            "active": self.active,
            "waiting": self.waiting,
            "max_active": self.max_active,
            "max_per_identity": self.max_per_identity,
            "max_waiting": self.max_waiting,
            "oldest_wait_s": round(self.oldest_wait_s, 1),
            "avg_turn_s": self.avg_turn_s,
        }


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdmitResult:
    granted: bool
    position: int  # 1-based, 0 when granted, -1 when refused (full)
    active: int
    waiting: int


class TurnQueueStore(Protocol):
    name: str

    async def admit(
        self,
        *,
        now_ms: int,
        member: str,
        identity: str,
        max_active: int,
        max_per_identity: int,
        max_waiting: int,
        ttl_ms: int,
        stale_ms: int,
        force: bool,
    ) -> AdmitResult: ...

    async def release(self, member: str) -> None: ...

    async def renew(self, members: list[str], ttl_ms: int) -> None: ...

    async def stats(self, now_ms: int) -> tuple[int, int, float]: ...  # active, waiting, oldest wait s

    async def recent(self, turn_id: str, ttl_s: int) -> bool: ...

    async def avg_ms(self) -> int | None: ...

    async def record_ms(self, ms: int) -> None: ...


def _identity_of(member: str) -> str:
    return member.split("|", 1)[1] if "|" in member else ""


class MemoryTurnQueueStore:
    """The admission algorithm in Python, for one process (and the tests).

    Exactly the Redis script's rules, in the same order; the parity test
    runs both stores through the same scenarios.
    """

    name = "memory"

    def __init__(self) -> None:
        self._active: dict[str, int] = {}  # member -> expiry ms
        self._wait: list[tuple[int, str]] = []  # (enqueued ms, member), FIFO
        self._recent: dict[str, float] = {}
        self._avg_ms: int | None = None

    def _expire(self, now_ms: int, stale_ms: int) -> None:
        for m, exp in list(self._active.items()):
            if exp <= now_ms:
                del self._active[m]
        self._wait = [(t, m) for (t, m) in self._wait if t > now_ms - stale_ms]

    async def admit(
        self,
        *,
        now_ms: int,
        member: str,
        identity: str,
        max_active: int,
        max_per_identity: int,
        max_waiting: int,
        ttl_ms: int,
        stale_ms: int,
        force: bool,
    ) -> AdmitResult:
        self._expire(now_ms, stale_ms)
        if member in self._active:
            self._active[member] = now_ms + ttl_ms
            return AdmitResult(True, 0, len(self._active), len(self._wait))
        if force:
            self._wait = [(t, m) for (t, m) in self._wait if m != member]
            self._active[member] = now_ms + ttl_ms
            return AdmitResult(True, 0, len(self._active), len(self._wait))
        in_wait = any(m == member for _, m in self._wait)
        if not in_wait:
            if len(self._wait) >= max_waiting:
                return AdmitResult(False, -1, len(self._active), len(self._wait))
            self._wait.append((now_ms, member))
        counts: dict[str, int] = {}
        for m in self._active:
            u = _identity_of(m)
            counts[u] = counts.get(u, 0) + 1
        n_active = len(self._active)
        granted = False
        remaining: list[tuple[int, str]] = []
        for t, m in self._wait:
            u = _identity_of(m)
            if n_active < max_active and counts.get(u, 0) < max_per_identity:
                self._active[m] = now_ms + ttl_ms
                counts[u] = counts.get(u, 0) + 1
                n_active += 1
                if m == member:
                    granted = True
            else:
                remaining.append((t, m))
        self._wait = remaining
        if granted:
            return AdmitResult(True, 0, n_active, len(self._wait))
        rank = next((i for i, (_, m) in enumerate(self._wait) if m == member), -1)
        return AdmitResult(False, rank + 1, n_active, len(self._wait))

    async def release(self, member: str) -> None:
        self._active.pop(member, None)
        self._wait = [(t, m) for (t, m) in self._wait if m != member]

    async def renew(self, members: list[str], ttl_ms: int) -> None:
        now_ms = int(time.time() * 1000)
        for m in members:
            if m in self._active:
                self._active[m] = now_ms + ttl_ms

    async def stats(self, now_ms: int) -> tuple[int, int, float]:
        self._expire(now_ms, 10**12)
        oldest = min((t for t, _ in self._wait), default=None)
        return len(self._active), len(self._wait), ((now_ms - oldest) / 1000.0 if oldest is not None else 0.0)

    async def recent(self, turn_id: str, ttl_s: int) -> bool:
        now = time.time()
        for k, exp in list(self._recent.items()):
            if exp <= now:
                del self._recent[k]
        seen = turn_id in self._recent
        self._recent[turn_id] = now + ttl_s
        return seen

    async def avg_ms(self) -> int | None:
        return self._avg_ms

    async def record_ms(self, ms: int) -> None:
        self._avg_ms = ms if self._avg_ms is None else int(self._avg_ms * 0.8 + ms * 0.2)


# One script, one round trip, one atomic decision for every worker.
_ADMIT_LUA = """
local active, wait = KEYS[1], KEYS[2]
local now = tonumber(ARGV[1])
local me, ident = ARGV[2], ARGV[3]
local maxA, maxU, maxW = tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
local ttl, stale = tonumber(ARGV[7]), tonumber(ARGV[8])
local force = ARGV[9] == '1'
redis.call('ZREMRANGEBYSCORE', active, '-inf', now)
redis.call('ZREMRANGEBYSCORE', wait, '-inf', now - stale)
if redis.call('ZSCORE', active, me) then
  redis.call('ZADD', active, now + ttl, me)
  return {1, 0, redis.call('ZCARD', active), redis.call('ZCARD', wait)}
end
if force then
  redis.call('ZREM', wait, me)
  redis.call('ZADD', active, now + ttl, me)
  return {1, 0, redis.call('ZCARD', active), redis.call('ZCARD', wait)}
end
if not redis.call('ZSCORE', wait, me) then
  if redis.call('ZCARD', wait) >= maxW then
    return {0, -1, redis.call('ZCARD', active), redis.call('ZCARD', wait)}
  end
  redis.call('ZADD', wait, now, me)
end
local counts = {}
local members = redis.call('ZRANGE', active, 0, -1)
for _, m in ipairs(members) do
  local u = string.match(m, '|(.*)$') or ''
  counts[u] = (counts[u] or 0) + 1
end
local nActive = #members
local granted = false
for _, m in ipairs(redis.call('ZRANGE', wait, 0, -1)) do
  if nActive >= maxA then break end
  local u = string.match(m, '|(.*)$') or ''
  if (counts[u] or 0) < maxU then
    redis.call('ZREM', wait, m)
    redis.call('ZADD', active, now + ttl, m)
    counts[u] = (counts[u] or 0) + 1
    nActive = nActive + 1
    if m == me then granted = true end
  end
end
if granted then
  return {1, 0, nActive, redis.call('ZCARD', wait)}
end
local rank = redis.call('ZRANK', wait, me)
if rank == false or rank == nil then rank = -1 end
return {0, rank + 1, nActive, redis.call('ZCARD', wait)}
"""


class RedisTurnQueueStore:
    name = "redis"

    def __init__(self, client: Any, prefix: str = "sran:turnq") -> None:
        self._r = client
        self._active = f"{prefix}:active"
        self._wait = f"{prefix}:wait"
        self._recent_prefix = f"{prefix}:recent:"
        self._avg = f"{prefix}:avg_ms"
        self._script = client.register_script(_ADMIT_LUA)

    async def admit(
        self,
        *,
        now_ms: int,
        member: str,
        identity: str,
        max_active: int,
        max_per_identity: int,
        max_waiting: int,
        ttl_ms: int,
        stale_ms: int,
        force: bool,
    ) -> AdmitResult:
        res = await self._script(
            keys=[self._active, self._wait],
            args=[now_ms, member, identity, max_active, max_per_identity, max_waiting, ttl_ms, stale_ms, "1" if force else "0"],
        )
        granted, position, active, waiting = (int(x) for x in res)
        return AdmitResult(bool(granted), position, active, waiting)

    async def release(self, member: str) -> None:
        pipe = self._r.pipeline()
        pipe.zrem(self._active, member)
        pipe.zrem(self._wait, member)
        await pipe.execute()

    async def renew(self, members: list[str], ttl_ms: int) -> None:
        if not members:
            return
        exp = int(time.time() * 1000) + ttl_ms
        pipe = self._r.pipeline()
        for m in members:
            pipe.zadd(self._active, {m: exp}, xx=True)
        await pipe.execute()

    async def stats(self, now_ms: int) -> tuple[int, int, float]:
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(self._active, "-inf", now_ms)
        pipe.zcard(self._active)
        pipe.zcard(self._wait)
        pipe.zrange(self._wait, 0, 0, withscores=True)
        _, active, waiting, head = await pipe.execute()
        oldest = 0.0
        if head:
            oldest = max(0.0, (now_ms - float(head[0][1])) / 1000.0)
        return int(active), int(waiting), oldest

    async def recent(self, turn_id: str, ttl_s: int) -> bool:
        # SET NX: True when this call created it (= not seen before).
        created = await self._r.set(self._recent_prefix + turn_id, "1", ex=ttl_s, nx=True)
        if created:
            return False
        await self._r.expire(self._recent_prefix + turn_id, ttl_s)
        return True

    async def avg_ms(self) -> int | None:
        raw = await self._r.get(self._avg)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def record_ms(self, ms: int) -> None:
        cur = await self.avg_ms()
        new = ms if cur is None else int(cur * 0.8 + ms * 0.2)
        await self._r.set(self._avg, new, ex=86400)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


@dataclass
class QueueConfig:
    enabled: bool = True
    max_active: int = 0
    max_per_identity: int = 0
    max_waiting: int = 0
    max_wait_s: float = 120.0
    poll_s: float = 0.5
    frame_every_s: float = 5.0  # a `queued` frame at least this often while waiting
    active_ttl_s: float = ACTIVE_TTL_S_DEFAULT
    recent_turn_s: int = 30
    retry_after_s: int = 10

    def resolved(self) -> "QueueConfig":
        max_active = self.max_active if self.max_active > 0 else sizing.default_max_active_turns()
        max_per = self.max_per_identity if self.max_per_identity > 0 else max(1, max_active // 6)
        max_waiting = self.max_waiting if self.max_waiting > 0 else max_active * 4
        return QueueConfig(
            enabled=self.enabled,
            max_active=max_active,
            max_per_identity=min(max_per, max_active),
            max_waiting=max_waiting,
            max_wait_s=self.max_wait_s,
            poll_s=self.poll_s,
            frame_every_s=self.frame_every_s,
            active_ttl_s=self.active_ttl_s,
            recent_turn_s=self.recent_turn_s,
            retry_after_s=self.retry_after_s,
        )


class Ticket:
    """One turn's place in the line, then its slot. ``release()`` is idempotent."""

    def __init__(self, queue: "TurnQueue", member: str, identity: str, turn_id: str | None) -> None:
        self._queue = queue
        self.member = member
        self.identity = identity
        self.turn_id = turn_id
        self.enqueued_at = time.monotonic()
        self.granted_at: float | None = None
        self.waited_s: float = 0.0
        self._released = False

    @property
    def granted(self) -> bool:
        return self.granted_at is not None

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._queue._release(self)

    def release_nowait(self) -> None:
        """Release from a non-async finaliser: schedules the release."""
        if self._released:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.release())


class TurnQueue:
    def __init__(self, store: TurnQueueStore, cfg: QueueConfig, *, clock: Callable[[], float] | None = None) -> None:
        self.cfg = cfg.resolved()
        self.store = store
        self._held: dict[str, Ticket] = {}
        self._renew_task: asyncio.Task | None = None
        self._clock = clock or time.time
        self._store_failed_at: float = 0.0

    # -- admission ------------------------------------------------------------

    @staticmethod
    def member_for(identity: str) -> str:
        ident = (identity or "anonymous").replace("|", "_")
        return f"{secrets.token_hex(8)}|{ident}"

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    async def _admit_once(self, ticket: Ticket, *, force: bool) -> AdmitResult:
        return await self.store.admit(
            now_ms=self._now_ms(),
            member=ticket.member,
            identity=ticket.identity,
            max_active=self.cfg.max_active,
            max_per_identity=self.cfg.max_per_identity,
            max_waiting=self.cfg.max_waiting,
            ttl_ms=int(self.cfg.active_ttl_s * 1000),
            stale_ms=int((self.cfg.max_wait_s + WAIT_STALE_GRACE_S) * 1000),
            force=force,
        )

    async def _estimate_s(self, res: AdmitResult) -> int | None:
        avg = await self.store.avg_ms()
        if not avg:
            return None
        ahead = max(0, res.position - 1)
        return int(math.ceil(((ahead + 1) / max(1, self.cfg.max_active)) * avg / 1000.0))

    async def _status(self, res: AdmitResult) -> QueueStatus:
        return QueueStatus(
            granted=res.granted,
            position=max(0, res.position),
            ahead=max(0, res.position - 1),
            active=res.active,
            waiting=res.waiting,
            max_active=self.cfg.max_active,
            estimate_s=None if res.granted else await self._estimate_s(res),
        )

    def _mark_held(self, ticket: Ticket) -> None:
        ticket.granted_at = time.monotonic()
        ticket.waited_s = ticket.granted_at - ticket.enqueued_at
        self._held[ticket.member] = ticket
        self._ensure_renewer()

    async def try_admit(self, identity: str, turn_id: str | None) -> tuple[Ticket, QueueStatus]:
        """One admission attempt. The ticket is either granted or waiting
        (poll with ``wait``); raises QueueFull when the line is too long.

        A follow-up call of a turn admitted within recent_turn_s is admitted
        at once (a turn queues once).
        """
        ticket = Ticket(self, self.member_for(identity), identity, turn_id)
        force = False
        if turn_id:
            try:
                force = await self.store.recent(turn_id, self.cfg.recent_turn_s)
            except Exception as exc:  # noqa: BLE001 — the store is optional here
                self._store_trouble(exc)
        try:
            res = await self._admit_once(ticket, force=force)
        except Exception as exc:  # noqa: BLE001 — Redis down: admit, say so once a minute
            self._store_trouble(exc)
            res = AdmitResult(True, 0, 0, 0)
        if res.position == -1:
            raise QueueFull(res.waiting, self.cfg.max_waiting, self.cfg.retry_after_s)
        if res.granted:
            self._mark_held(ticket)
        return ticket, await self._status(res)

    async def wait(
        self,
        ticket: Ticket,
        on_status: Callable[[QueueStatus], Awaitable[None] | None] | None = None,
    ) -> QueueStatus:
        """Poll until the ticket holds a slot; reports every change of place
        (and at least every frame_every_s) through on_status. Raises
        QueueTimeout after max_wait_s; a cancelled wait leaves the line."""
        deadline = time.monotonic() + self.cfg.max_wait_s
        last_reported: tuple[int, int] | None = None
        last_report_at = 0.0
        last_res: AdmitResult | None = None
        try:
            while True:
                try:
                    res = await self._admit_once(ticket, force=False)
                except Exception as exc:  # noqa: BLE001
                    self._store_trouble(exc)
                    res = AdmitResult(True, 0, 0, 0)
                last_res = res
                if res.position == -1:
                    # The line filled while we were in it (stale-purged and
                    # re-added past the cap): treat as refused.
                    raise QueueFull(res.waiting, self.cfg.max_waiting, self.cfg.retry_after_s)
                if res.granted:
                    self._mark_held(ticket)
                    st = await self._status(res)
                    if on_status is not None:
                        await _maybe_await(on_status(st))
                    return st
                now = time.monotonic()
                key = (res.position, res.active)
                if on_status is not None and (key != last_reported or now - last_report_at >= self.cfg.frame_every_s):
                    await _maybe_await(on_status(await self._status(res)))
                    last_reported, last_report_at = key, now
                if now >= deadline:
                    raise QueueTimeout(now - ticket.enqueued_at, res.position)
                await asyncio.sleep(self.cfg.poll_s)
        except (QueueTimeout, QueueFull, asyncio.CancelledError, BaseException):
            if not ticket.granted:
                try:
                    await asyncio.shield(self.store.release(ticket.member))
                except Exception:  # noqa: BLE001
                    pass
            raise

    async def _release(self, ticket: Ticket) -> None:
        self._held.pop(ticket.member, None)
        try:
            await self.store.release(ticket.member)
            if ticket.granted_at is not None:
                await self.store.record_ms(int((time.monotonic() - ticket.granted_at) * 1000))
        except Exception as exc:  # noqa: BLE001
            self._store_trouble(exc)

    # -- renewal ------------------------------------------------------------------

    def _ensure_renewer(self) -> None:
        if self._renew_task is None or self._renew_task.done():
            try:
                self._renew_task = asyncio.get_running_loop().create_task(self._renew_loop())
            except RuntimeError:
                self._renew_task = None

    async def _renew_loop(self) -> None:
        every = max(1.0, self.cfg.active_ttl_s / 3.0)
        try:
            while self._held:
                await asyncio.sleep(every)
                members = list(self._held)
                if not members:
                    break
                try:
                    await self.store.renew(members, int(self.cfg.active_ttl_s * 1000))
                except Exception as exc:  # noqa: BLE001
                    self._store_trouble(exc)
        finally:
            self._renew_task = None

    # -- observability ------------------------------------------------------------

    def _store_trouble(self, exc: BaseException) -> None:
        now = time.monotonic()
        if now - self._store_failed_at > 60:
            self._store_failed_at = now
            logger.warning("turn_queue_store_unavailable", store=self.store.name, err=f"{type(exc).__name__}: {str(exc)[:120]}")

    async def snapshot(self) -> QueueSnapshot:
        try:
            active, waiting, oldest = await self.store.stats(self._now_ms())
            avg = await self.store.avg_ms()
        except Exception as exc:  # noqa: BLE001
            self._store_trouble(exc)
            active, waiting, oldest, avg = len(self._held), 0, 0.0, None
        return QueueSnapshot(
            enabled=self.cfg.enabled,
            store=self.store.name,
            active=active,
            waiting=waiting,
            max_active=self.cfg.max_active,
            max_per_identity=self.cfg.max_per_identity,
            max_waiting=self.cfg.max_waiting,
            oldest_wait_s=oldest,
            avg_turn_s=(round(avg / 1000.0, 1) if avg else None),
        )

    def held(self) -> int:
        return len(self._held)


async def _maybe_await(v: Any) -> None:
    if asyncio.iscoroutine(v) or isinstance(v, asyncio.Future):
        await v


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_queue: TurnQueue | None = None
_configured = False


def configure(cfg: QueueConfig, store: TurnQueueStore | None = None) -> TurnQueue | None:
    """Build the process's queue from settings (main.lifespan) or tests.

    ``store=None`` picks Redis (``_redis_pool.get_async_redis``) and falls
    back to the per-process memory store when the client cannot be built.
    Returns None (and disables the queue) when ``cfg.enabled`` is False.
    """
    global _queue, _configured
    _configured = True
    if not cfg.enabled:
        _queue = None
        logger.info("turn_queue_disabled")
        return None
    if store is None:
        try:
            from app.services._redis_pool import get_async_redis

            store = RedisTurnQueueStore(get_async_redis())
        except Exception as exc:  # noqa: BLE001
            logger.warning("turn_queue_redis_unavailable_using_memory", err=str(exc)[:120])
            store = MemoryTurnQueueStore()
    _queue = TurnQueue(store, cfg)
    logger.info(
        "turn_queue_configured",
        store=store.name,
        max_active=_queue.cfg.max_active,
        max_per_identity=_queue.cfg.max_per_identity,
        max_waiting=_queue.cfg.max_waiting,
        max_wait_s=_queue.cfg.max_wait_s,
    )
    return _queue


def turn_queue() -> TurnQueue | None:
    """The process's queue, or None when disabled / not configured."""
    return _queue


def reset_for_tests() -> None:
    global _queue, _configured
    _queue = None
    _configured = False


def config_from_settings(settings: Any) -> QueueConfig:
    return QueueConfig(
        enabled=bool(getattr(settings, "turn_queue_enabled", True)),
        max_active=int(getattr(settings, "turn_queue_max_active", 0) or 0),
        max_per_identity=int(getattr(settings, "turn_queue_max_per_identity", 0) or 0),
        max_waiting=int(getattr(settings, "turn_queue_max_waiting", 0) or 0),
        max_wait_s=float(getattr(settings, "turn_queue_max_wait_seconds", 120.0)),
        poll_s=float(getattr(settings, "turn_queue_poll_seconds", 0.5)),
        frame_every_s=float(getattr(settings, "turn_queue_frame_seconds", 5.0)),
        active_ttl_s=float(getattr(settings, "turn_queue_active_ttl_seconds", ACTIVE_TTL_S_DEFAULT)),
        recent_turn_s=int(getattr(settings, "turn_queue_recent_turn_seconds", 30)),
        retry_after_s=int(getattr(settings, "turn_queue_retry_after_seconds", 10)),
    )


def purpose_never_queues(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    purpose = str(metadata.get("purpose") or "").strip().lower()
    return purpose in PURPOSES_THAT_NEVER_QUEUE
