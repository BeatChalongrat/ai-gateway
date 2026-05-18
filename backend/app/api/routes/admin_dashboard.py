"""Operational dashboard endpoints.

Four aggregations over ``request_logs`` used by the admin dashboard:

- GET /admin/dashboard/overview?period=24h|7d|30d
    KPIs (request count, success rate, avg latency, spend) + delta vs.
    the previous period of the same length + platform resources.

- GET /admin/dashboard/volume?period=24h|7d|30d
    Time-bucketed stacked series keyed by the dashboard's top-tier
    providers (openai / anthropic / google / other). Hourly for 24h,
    daily for 7d / 30d.

- GET /admin/dashboard/top-models?period=24h|7d|30d&limit=5
    Top-N models by request count within the period.

- GET /admin/dashboard/recent-requests?limit=10
    Most recent N request_log rows (for the live feed).

Handlers are sync ``def`` so FastAPI runs them in the threadpool —
consistent with ``admin.py::dashboard_stats`` and avoids the asyncio
pool starvation issue flagged there.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM

router = APIRouter(prefix="/dashboard")

Period = Literal["24h", "7d", "30d"]

# Providers called out explicitly in the stacked chart. Anything else
# rolls up into "other".
_CHART_PROVIDERS = ("openai", "anthropic", "google")


def _period_bounds(period: str) -> tuple[datetime, datetime, datetime]:
    """Return (current_start, current_end=now, previous_start).

    ``previous_start = current_start - (current_end - current_start)``.
    """
    now = datetime.now(timezone.utc)
    if period == "7d":
        delta = timedelta(days=7)
    elif period == "30d":
        delta = timedelta(days=30)
    else:  # default 24h
        delta = timedelta(hours=24)
    current_start = now - delta
    previous_start = current_start - delta
    return current_start, now, previous_start


def _bucketize(period: str) -> tuple[str, int]:
    """Return (granularity, bucket_count) for the chart axis."""
    if period == "7d":
        return "day", 7
    if period == "30d":
        return "day", 30
    return "hour", 24


def _pct_delta(current: float, previous: float) -> tuple[float | None, str]:
    """Compute percent change + direction label.

    Returns (delta_pct, direction). ``delta_pct`` is ``None`` when the
    previous window has no data — the UI uses that to render "No prior
    data" instead of a misleading "▼ 100%" chip.
    """
    if previous == 0:
        return None, "flat"
    pct = (current - previous) / previous * 100.0
    if abs(pct) < 0.05:
        return 0.0, "flat"
    return round(pct, 1), ("up" if pct > 0 else "down")


def _fetch_period_series(
    session: Session,
    start: datetime,
    end: datetime,
    bucket_count: int,
    granularity: str,
) -> dict:
    """Per-bucket KPI series used for sparklines on the overview cards.

    Returns a dict with four arrays of length ``bucket_count``:
    requests, success_rate (%), avg_latency (ms), spend (USD).
    """
    rows = session.execute(
        sa.select(
            RequestLogORM.created_at,
            RequestLogORM.status,
            RequestLogORM.latency_ms,
            RequestLogORM.estimated_cost_usd,
        )
        .where(RequestLogORM.created_at >= start)
        .where(RequestLogORM.created_at < end)
    ).all()

    req = [0] * bucket_count
    succ = [0] * bucket_count
    lat_sum = [0.0] * bucket_count
    spend = [0.0] * bucket_count

    def _idx(ts: datetime) -> int:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if granularity == "hour":
            return max(0, min(bucket_count - 1, int((ts - start).total_seconds() // 3600)))
        return max(0, min(bucket_count - 1, int((ts - start).total_seconds() // 86400)))

    for ts, status, lat, cost in rows:
        i = _idx(ts)
        req[i] += 1
        if status == "success":
            succ[i] += 1
        lat_sum[i] += float(lat or 0)
        spend[i] += float(cost or 0)

    return {
        "requests_series": req,
        "success_series": [
            round((succ[i] / req[i] * 100.0) if req[i] else 0.0, 2)
            for i in range(bucket_count)
        ],
        "latency_series": [
            int(round(lat_sum[i] / req[i])) if req[i] else 0
            for i in range(bucket_count)
        ],
        "spend_series": [round(s, 4) for s in spend],
    }


def _fetch_period_kpis(
    session: Session, start: datetime, end: datetime
) -> dict:
    """Aggregate KPIs for a single window. Returns zeros on empty tables."""
    stmt = (
        sa.select(
            sa.func.count().label("total"),
            sa.func.sum(sa.case((RequestLogORM.status == "success", 1), else_=0)).label(
                "success"
            ),
            sa.func.coalesce(sa.func.avg(RequestLogORM.latency_ms), 0.0).label(
                "avg_latency"
            ),
            sa.func.coalesce(
                sa.func.sum(RequestLogORM.estimated_cost_usd), 0.0
            ).label("spend"),
        )
        .where(RequestLogORM.created_at >= start)
        .where(RequestLogORM.created_at < end)
    )
    row = session.execute(stmt).one()
    total = int(row.total or 0)
    success = int(row.success or 0)
    return {
        "total": total,
        "success": success,
        "success_rate": (success / total * 100.0) if total else 0.0,
        "avg_latency": float(row.avg_latency or 0.0),
        "spend": float(row.spend or 0.0),
    }


@router.get("/overview")
def dashboard_overview(
    period: Period = Query("24h"),
    session: Session = Depends(get_db_session),
):
    """KPI cards + resource counts + period delta vs prior window."""
    current_start, end, previous_start = _period_bounds(period)
    granularity, bucket_count = _bucketize(period)

    current = _fetch_period_kpis(session, current_start, end)
    previous = _fetch_period_kpis(session, previous_start, current_start)
    series = _fetch_period_series(session, current_start, end, bucket_count, granularity)

    req_delta, req_dir = _pct_delta(current["total"], previous["total"])
    rate_delta, rate_dir = _pct_delta(current["success_rate"], previous["success_rate"])
    # Lower latency is better; we flip the direction label so the UI can
    # colour "down" as green for latency.
    lat_delta, lat_dir = _pct_delta(current["avg_latency"], previous["avg_latency"])
    spend_delta, spend_dir = _pct_delta(current["spend"], previous["spend"])

    # If either the current or the prior window has no traffic, the
    # per-metric deltas are all meaningless — a "-100%" next to a 0 value
    # is just noise. The UI renders "No prior data" when delta_pct is
    # null, which is the honest thing to show here.
    if current["total"] == 0 or previous["total"] == 0:
        req_delta = rate_delta = lat_delta = spend_delta = None
        req_dir = rate_dir = lat_dir = spend_dir = "flat"

    # Resources snapshot — same logic as /admin/dashboard but here to
    # keep the operational dashboard self-contained.
    tenants = session.execute(
        sa.select(sa.func.count()).select_from(TenantORM).where(
            TenantORM.status == "active"
        )
    ).scalar() or 0
    projects = session.execute(
        sa.select(sa.func.count()).select_from(ProjectORM)
    ).scalar() or 0

    from app.services.providers.model_registry import get_all_candidates

    # THB display layer — additive to ``spend_usd``. Uses the cached
    # currency rate (or the settings fallback) so the endpoint stays
    # sync. The series is converted bucket-by-bucket with the same
    # rate so percentage deltas match across currencies.
    from app.services.currency_service import currency_service
    spend_thb_value = currency_service.convert(current["spend"])
    spend_thb_series = [
        round(currency_service.convert(s), 4) for s in series["spend_series"]
    ]

    return {
        "period": period,
        "requests": {
            "value": current["total"],
            "delta_pct": req_delta,
            "direction": req_dir,
        },
        "success_rate": {
            "value": round(current["success_rate"], 2),
            "delta_pct": rate_delta,
            "direction": rate_dir,
        },
        "avg_latency_ms": {
            "value": int(round(current["avg_latency"])),
            "delta_pct": lat_delta,
            "direction": lat_dir,
        },
        "spend_usd": {
            "value": round(current["spend"], 4),
            "delta_pct": spend_delta,
            "direction": spend_dir,
        },
        "spend_thb": {
            "value": spend_thb_value,
            "delta_pct": spend_delta,
            "direction": spend_dir,
        },
        "resources": {
            "tenants": int(tenants),
            "projects": int(projects),
            "models": len(get_all_candidates()),
        },
        **series,
        "spend_thb_series": spend_thb_series,
    }


@router.get("/volume")
def dashboard_volume(
    period: Period = Query("24h"),
    session: Session = Depends(get_db_session),
):
    """Stacked time series by provider for the chart."""
    current_start, end, _ = _period_bounds(period)
    granularity, bucket_count = _bucketize(period)

    rows = session.execute(
        sa.select(
            RequestLogORM.created_at,
            RequestLogORM.provider,
        )
        .where(RequestLogORM.created_at >= current_start)
        .where(RequestLogORM.created_at < end)
    ).all()

    # Bucket counts per (bucket_index, provider-group)
    counts: dict[int, dict[str, int]] = defaultdict(
        lambda: {"openai": 0, "anthropic": 0, "google": 0, "other": 0}
    )

    def _bucket_idx(ts: datetime) -> int:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if granularity == "hour":
            diff_hours = int((ts - current_start).total_seconds() // 3600)
            return max(0, min(bucket_count - 1, diff_hours))
        diff_days = int((ts - current_start).total_seconds() // 86400)
        return max(0, min(bucket_count - 1, diff_days))

    for ts, provider in rows:
        idx = _bucket_idx(ts)
        key = provider if provider in _CHART_PROVIDERS else "other"
        counts[idx][key] += 1

    # Materialize every bucket so the chart has no gaps
    def _label_for(idx: int) -> str:
        if granularity == "hour":
            ts = current_start + timedelta(hours=idx)
            return ts.strftime("%H:00")
        ts = current_start + timedelta(days=idx)
        return ts.strftime("%m-%d")

    buckets = []
    for i in range(bucket_count):
        c = counts.get(i) or {"openai": 0, "anthropic": 0, "google": 0, "other": 0}
        buckets.append({"label": _label_for(i), **c})

    return {
        "period": period,
        "granularity": granularity,
        "buckets": buckets,
        "providers": ["openai", "anthropic", "google", "other"],
    }


@router.get("/top-models")
def dashboard_top_models(
    period: Period = Query("24h"),
    limit: int = Query(5, ge=1, le=50),
    session: Session = Depends(get_db_session),
):
    """Top-N models by request count in the period."""
    current_start, end, _ = _period_bounds(period)

    rows = session.execute(
        sa.select(
            RequestLogORM.model,
            RequestLogORM.provider,
            sa.func.count().label("c"),
        )
        .where(RequestLogORM.created_at >= current_start)
        .where(RequestLogORM.created_at < end)
        .group_by(RequestLogORM.model, RequestLogORM.provider)
        .order_by(sa.desc("c"))
        .limit(limit)
    ).all()

    total = session.execute(
        sa.select(sa.func.count())
        .select_from(RequestLogORM)
        .where(RequestLogORM.created_at >= current_start)
        .where(RequestLogORM.created_at < end)
    ).scalar() or 0

    items = [
        {"model": r.model, "provider": r.provider, "count": int(r.c)}
        for r in rows
    ]
    return {"period": period, "total": int(total), "items": items}


@router.get("/fallback-stats")
def dashboard_fallback_stats(
    period_hours: int = Query(24, ge=1, le=720),
    session: Session = Depends(get_db_session),
):
    """Aggregate Phase-2 fallback usage over the last ``period_hours``.

    Surfaces four numbers operators care about during an upstream
    incident:

      * ``total_requests``       — overall request volume in the window
      * ``fallback_triggered``   — how many rows have ``fallback_used = true``
      * ``fallback_success_rate``— of those, the share that ended in ``status = success``
      * ``by_provider``          — per *original* provider, count of failures and
                                   where the fallback chain steered the rescue
      * ``by_tier``              — per-tier fallback rate (uses the model_tiers registry)

    The window only ever looks at the partitioned ``request_logs``
    table — archive rows are excluded so the dashboard stays fast.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=period_hours)

    total_requests = int(
        session.execute(
            sa.select(sa.func.count())
            .select_from(RequestLogORM)
            .where(RequestLogORM.created_at >= start)
            .where(RequestLogORM.created_at < end)
        ).scalar()
        or 0
    )

    # Pull only the rows where fallback fired — usually a small subset
    # of the window so we can hydrate full ORM rows without paying for
    # everything.
    fallback_rows = (
        session.execute(
            sa.select(RequestLogORM)
            .where(RequestLogORM.created_at >= start)
            .where(RequestLogORM.created_at < end)
            .where(RequestLogORM.fallback_used == True)  # noqa: E712
        )
        .scalars()
        .all()
    )

    triggered = len(fallback_rows)
    successful = sum(1 for r in fallback_rows if r.status == "success")
    success_rate = (successful / triggered) if triggered else 0.0

    # by_provider: keyed on the *original* provider so operators see
    # "openai had 40 failures → 35 rescued by anthropic, 5 by google".
    by_provider: dict[str, dict] = {}
    for r in fallback_rows:
        op = r.original_provider or r.provider or "unknown"
        bucket = by_provider.setdefault(
            op, {"failures": 0, "fallback_to": defaultdict(int)}
        )
        bucket["failures"] += 1
        if r.status == "success" and r.provider:
            bucket["fallback_to"][r.provider] += 1
    by_provider_out = {
        provider: {
            "failures": v["failures"],
            "fallback_to": dict(v["fallback_to"]),
        }
        for provider, v in by_provider.items()
    }

    # by_tier: per-tier fallback rate. The previous implementation
    # pulled every row in the window and rolled up in Python, which
    # was O(N) data transfer and hit the request from the dashboard
    # path. Push the rollup into Postgres via GROUP BY so the result
    # set is bounded by (distinct models × 2 fallback states) — a few
    # dozen rows at most. Postgres uses HashAggregate over the index
    # on (created_at) for the predicate; the partition pruner trims
    # the partitions outside the window before the scan.
    from app.services.providers.model_tiers import get_tier

    tier_total: dict[str, int] = defaultdict(int)
    tier_fallback: dict[str, int] = defaultdict(int)
    period_models = session.execute(
        sa.select(
            RequestLogORM.model,
            RequestLogORM.fallback_used,
            sa.func.count().label("cnt"),
        )
        .where(RequestLogORM.created_at >= start)
        .where(RequestLogORM.created_at < end)
        .group_by(RequestLogORM.model, RequestLogORM.fallback_used)
    ).all()
    for model, fb_used, cnt in period_models:
        tier = get_tier(model or "")
        if tier is None:
            continue
        tier_total[tier] += int(cnt or 0)
        if fb_used:
            tier_fallback[tier] += int(cnt or 0)
    by_tier_out = {
        tier: {
            "fallback_rate": round(
                tier_fallback[tier] / tier_total[tier], 4
            )
        }
        for tier in tier_total
        if tier_total[tier] > 0
    }

    return {
        "period_hours": period_hours,
        "total_requests": total_requests,
        "fallback_triggered": triggered,
        "fallback_success_rate": round(success_rate, 4),
        "by_provider": by_provider_out,
        "by_tier": by_tier_out,
    }


@router.get("/partition-health")
def dashboard_partition_health(
    session: Session = Depends(get_db_session),
):
    """Operational read-out of the ``request_logs`` partition layout.

    Surfaces:
      * Per-partition row estimate (uses ``pg_class.reltuples`` so the
        endpoint stays cheap even on a 1B-row hot table).
      * Oldest / newest monthly partition names so operators can
        eyeball the retention window at a glance.
      * A ``next_month_missing`` flag to call out when the daily
        partition_maintenance job has slipped — a true value here
        means new writes will land in ``request_logs_default`` until
        the job catches up.

    On non-Postgres engines (SQLite test env) the response is a
    structured "unsupported" stub so the admin UI can render a
    friendly message instead of a 500.
    """
    from app.jobs.partition_maintenance_job import list_partition_health

    return list_partition_health(session)


@router.get("/recent-requests")
def dashboard_recent_requests(
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_db_session),
):
    """Most recent N requests for the live feed."""
    rows = session.execute(
        sa.select(
            RequestLogORM.request_id,
            RequestLogORM.status,
            RequestLogORM.provider,
            RequestLogORM.model,
            RequestLogORM.latency_ms,
            RequestLogORM.error_code,
            RequestLogORM.created_at,
        )
        .order_by(sa.desc(RequestLogORM.created_at))
        .limit(limit)
    ).all()

    # DERIVED HTTP status code map. The request_logs table stores an
    # internal status label (e.g. "success", "blocked", "timeout") — it does
    # NOT persist the actual HTTP response code the client received. This
    # dict maps each label to a plausible HTTP code so the dashboard can
    # show a numeric status pill (200 / 403 / 502 / 504) instead of a fuzzy
    # bucket ("2XX" / "4XX" / "5XX").
    #
    # NOTE: these are DERIVED, not authoritative. A future iteration should
    # add a real ``http_status INTEGER`` column to request_logs and capture
    # the actual ``response.status_code`` from the gateway middleware. Until
    # then, any discrepancy between a request's real response code and the
    # value shown here is expected — consumers of /admin/dashboard must
    # treat ``http_status`` as best-effort.
    HTTP_STATUS_MAP = {
        "success": 200,
        "blocked": 403,
        "dlp_blocked": 403,
        "policy_blocked": 403,
        "error": 502,
        "provider_error": 502,
        "timeout": 504,
    }

    items = []
    for r in rows:
        # Map the internal status string to a rough HTTP class so the UI
        # can colour-code without reading error_code.
        status = r.status or ""
        if status == "success":
            http_class = "2xx"
        elif status in ("blocked", "dlp_blocked", "policy_blocked"):
            http_class = "4xx"
        elif status in ("error", "provider_error", "timeout"):
            http_class = "5xx"
        else:
            http_class = "unknown"

        items.append(
            {
                "request_id": r.request_id,
                "status": status,
                "http_class": http_class,
                "http_status": HTTP_STATUS_MAP.get(status),
                "provider": r.provider,
                "model": r.model,
                "latency_ms": int(r.latency_ms or 0),
                "error_code": r.error_code,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )

    return {"items": items}
