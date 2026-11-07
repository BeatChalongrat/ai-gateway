"""FIX35 Part C step 2 — rank models by what THIS tenant actually got.

OpenRouter ranks "which models the community actually spends on over the
past 7 days". We have no community; we have something better for an
enterprise deployment: the tenant's own ``request_logs``. Same 7-day window,
scoped to the tenant, grouped by (task_category, model):

    volume        requests served (spend-weighted like OpenRouter's signal)
    success rate  status = success / all
    latency       p50-ish via avg latency_ms (cheap; good enough to order)
    cost          avg estimated_cost_usd per request

Score (0..1, higher = better), then the router sorts candidates by it:

    0.45 * success_rate + 0.25 * volume_share + 0.20 * speed + 0.10 * thrift

where speed / thrift are 1 - normalised rank within the group.

Cold start: a (task, model) cell with fewer than ``cold_start_min_samples``
(task_taxonomy.json, default 20) requests is NOT trusted — it contributes
nothing and the router falls back to the taxonomy's static ``default_rank``
for that task, then to cost. Cells fill in as traffic accrues, so real data
replaces the static list gradually, per task, per tenant.

Hot path: routing must be fast, so this module never queries per request.
``TenantRanking`` is materialised once per tenant per ``TTL`` (5 min) with a
single aggregate query, cached in-process; a refresh that fails keeps the
previous snapshot (or an empty one) and logs — the router treats "no data"
as "use defaults", never as an error.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

TTL_SECONDS = 300.0
WINDOW_DAYS = 7


@dataclass
class Cell:
    requests: int
    successes: int
    avg_latency_ms: float
    avg_cost_usd: float

    @property
    def success_rate(self) -> float:
        return self.successes / self.requests if self.requests else 0.0


@dataclass
class TenantRanking:
    tenant_id: str
    # (task_category or "*", model) -> Cell
    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)
    fetched_at: float = 0.0
    min_samples: int = 20

    def score(self, group_id: str, model: str) -> float | None:
        """0..1 or None when this tenant has too little data for
        (rank group, model). Falls back to the group-agnostic "*" cell when
        the per-group cell is thin but the model has overall history."""
        s, _n = self.score_with_samples(group_id, model)
        return s

    def score_with_samples(self, group_id: str, model: str) -> tuple[float | None, int]:
        """(score or None, samples) — FIX36 A2 feeds the prior/live blend
        w = n/(n+K), so the caller needs the sample count, not just a
        trusted/untrusted verdict. ``score`` is None below min_samples in
        BOTH the group and the "*" fallback; ``samples`` is the group cell's
        raw count either way (0 when the model has never been seen)."""
        group = self._group(group_id)
        cell = group.get(model)
        raw_n = cell.requests if cell is not None else 0
        if cell is None or cell.requests < self.min_samples:
            star = self._group("*")
            scell = star.get(model)
            if scell is None or scell.requests < self.min_samples:
                return None, raw_n
            return self._score_in(star, model, scell), raw_n if raw_n else scell.requests
        return self._score_in(group, model, cell), cell.requests

    def _group(self, task: str) -> dict[str, Cell]:
        return {m: c for (t, m), c in self.cells.items() if t == task}

    def _score_in(self, group: dict[str, Cell], model: str, cell: Cell) -> float:
        trusted = {m: c for m, c in group.items() if c.requests >= self.min_samples}
        if model not in trusted:
            trusted[model] = cell
        total = sum(c.requests for c in trusted.values()) or 1
        volume_share = cell.requests / total
        lat_sorted = sorted(trusted.values(), key=lambda c: c.avg_latency_ms)
        cost_sorted = sorted(trusted.values(), key=lambda c: c.avg_cost_usd)
        n = max(len(trusted) - 1, 1)
        speed = 1.0 - lat_sorted.index(cell) / n if len(trusted) > 1 else 1.0
        thrift = 1.0 - cost_sorted.index(cell) / n if len(trusted) > 1 else 1.0
        return round(0.45 * cell.success_rate + 0.25 * volume_share + 0.20 * speed + 0.10 * thrift, 4)


_cache: dict[str, TenantRanking] = {}
_lock = threading.Lock()


def _empty(tenant_id: str, min_samples: int) -> TenantRanking:
    return TenantRanking(tenant_id=tenant_id, fetched_at=time.monotonic(), min_samples=min_samples)


def _query(session, tenant_id: str) -> list[tuple[Any, ...]]:
    """One aggregate over the tenant's 7-day window. Sync session (the
    orchestrator passes an AsyncSession on the hot path, so callers use
    ``refresh_sync`` with a fresh SessionLocal — same pattern as
    routing_settings._read)."""
    from sqlalchemy import case, func, select

    from app.models.request_log import RequestLogORM

    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    ok = case((RequestLogORM.status == "success", 1), else_=0)
    stmt = (
        select(
            RequestLogORM.task_category,
            RequestLogORM.model,
            func.count().label("n"),
            func.sum(ok).label("ok"),
            func.avg(RequestLogORM.latency_ms).label("lat"),
            func.avg(RequestLogORM.estimated_cost_usd).label("cost"),
        )
        .where(RequestLogORM.tenant_id == tenant_id, RequestLogORM.created_at >= since)
        .group_by(RequestLogORM.task_category, RequestLogORM.model)
    )
    return list(session.execute(stmt).all())


def build_ranking(
    tenant_id: str,
    rows: list[tuple[Any, ...]],
    min_samples: int,
    group_of: "dict[str, str] | None" = None,
) -> TenantRanking:
    """FIX36 A1 — cells aggregate by RANK GROUP, not by the 32 fine tasks.

    32 tasks × ~20 models × 20 samples would need ~12,800 requests before the
    table said anything; 7 groups fill 4–5× faster. ``group_of`` maps a
    fine ``task_category`` to its group (from the taxonomy asset); an
    unmapped / NULL category lands in the task-agnostic "*" star cell only.
    """
    if group_of is None:
        try:
            from app.services.auto_router.taxonomy import load_taxonomy

            group_of = {t: spec.get("group", "") for t, spec in load_taxonomy()["tasks"].items()}
        except Exception:  # noqa: BLE001 — a broken asset must not break ranking
            group_of = {}
    r = _empty(tenant_id, min_samples)
    star: dict[str, Cell] = {}
    grouped: dict[tuple[str, str], Cell] = {}
    for task, model, n, ok, lat, cost in rows:
        if not model:
            continue
        n = int(n or 0)
        cell = Cell(requests=n, successes=int(ok or 0), avg_latency_ms=float(lat or 0.0), avg_cost_usd=float(cost or 0.0))
        group = group_of.get(task or "", "") or "*"
        key = (group, model)
        agg2 = grouped.get(key)
        if agg2 is None:
            grouped[key] = Cell(cell.requests, cell.successes, cell.avg_latency_ms * n, cell.avg_cost_usd * n)
        else:
            agg2.requests += n
            agg2.successes += cell.successes
            agg2.avg_latency_ms += cell.avg_latency_ms * n
            agg2.avg_cost_usd += cell.avg_cost_usd * n
        agg = star.get(model)
        if agg is None:
            star[model] = Cell(cell.requests, cell.successes, cell.avg_latency_ms * n, cell.avg_cost_usd * n)
        else:
            agg.requests += n
            agg.successes += cell.successes
            agg.avg_latency_ms += cell.avg_latency_ms * n
            agg.avg_cost_usd += cell.avg_cost_usd * n
    for (group, model), agg in grouped.items():
        if group != "*" and agg.requests:
            r.cells[(group, model)] = Cell(agg.requests, agg.successes, agg.avg_latency_ms / agg.requests, agg.avg_cost_usd / agg.requests)
    for model, agg in star.items():
        if agg.requests:
            r.cells[("*", model)] = Cell(agg.requests, agg.successes, agg.avg_latency_ms / agg.requests, agg.avg_cost_usd / agg.requests)
    return r


def get_ranking(tenant_id: str, *, min_samples: int, session_factory=None) -> TenantRanking:
    """Cached per tenant for TTL_SECONDS. Never raises; never queries when
    the cache is fresh; on a failed refresh keeps the previous snapshot."""
    now = time.monotonic()
    with _lock:
        cached = _cache.get(tenant_id)
        if cached is not None and now - cached.fetched_at < TTL_SECONDS:
            return cached
    if session_factory is None:
        try:
            from app.core.db import SessionLocal as session_factory  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            return cached or _empty(tenant_id, min_samples)
    try:
        with session_factory() as s:
            rows = _query(s, tenant_id)
        fresh = build_ranking(tenant_id, rows, min_samples)
    except Exception as exc:  # noqa: BLE001 — ranking down ⇒ defaults, never an error
        log.warning("auto_ranking_refresh_failed", tenant_id=tenant_id, error=str(exc)[:200])
        fresh = cached or _empty(tenant_id, min_samples)
        fresh.fetched_at = now
    with _lock:
        _cache[tenant_id] = fresh
    return fresh


def _ranking_stmt(tenant_id: str):
    from sqlalchemy import case, func, select

    from app.models.request_log import RequestLogORM

    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    ok = case((RequestLogORM.status == "success", 1), else_=0)
    return (
        select(
            RequestLogORM.task_category,
            RequestLogORM.model,
            func.count().label("n"),
            func.sum(ok).label("ok"),
            func.avg(RequestLogORM.latency_ms).label("lat"),
            func.avg(RequestLogORM.estimated_cost_usd).label("cost"),
        )
        .where(RequestLogORM.tenant_id == tenant_id, RequestLogORM.created_at >= since)
        .group_by(RequestLogORM.task_category, RequestLogORM.model)
    )


async def get_ranking_async(tenant_id: str, session, *, min_samples: int) -> TenantRanking:
    """Same cache as :func:`get_ranking`, refreshed through the REQUEST's own
    AsyncSession — no second connection, so a single-connection test
    database (sqlite StaticPool) is never wedged and production never pays
    for an extra pool checkout on the hot path. Never raises."""
    now = time.monotonic()
    with _lock:
        cached = _cache.get(tenant_id)
        if cached is not None and now - cached.fetched_at < TTL_SECONDS:
            return cached
    try:
        res = await session.execute(_ranking_stmt(tenant_id))
        fresh = build_ranking(tenant_id, list(res.all()), min_samples)
    except Exception as exc:  # noqa: BLE001 — ranking down ⇒ defaults, never an error
        log.warning("auto_ranking_refresh_failed", tenant_id=tenant_id, error=str(exc)[:200])
        fresh = cached or _empty(tenant_id, min_samples)
        fresh.fetched_at = now
    with _lock:
        _cache[tenant_id] = fresh
    return fresh


def clear_cache() -> None:
    with _lock:
        _cache.clear()
