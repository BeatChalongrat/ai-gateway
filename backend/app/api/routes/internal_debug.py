"""Debug-only tracemalloc endpoints — DISABLED unless ENABLE_TRACEMALLOC=true.

Safety contract:
  * The router is only INCLUDED in the app when ``ENABLE_TRACEMALLOC=true``
    (see app/main.py). When disabled the paths simply don't exist (404).
  * Even when included, every endpoint requires the ``X-Internal-Service-
    Secret`` header to match ``settings.internal_service_secret`` (the same
    trusted-internal signal the rate limiter uses). No secret configured ->
    all endpoints 403.
  * Responses contain ONLY allocation metadata: size, count, and code
    ``file:line`` tracebacks. They NEVER include request/response payloads,
    user message contents, headers, or secrets — tracemalloc records
    allocation call sites, not the allocated values.
  * Snapshots/diffs are also written to ``TRACEMALLOC_SNAPSHOT_DIR``
    (default /tmp/tracemalloc) as local text files for offline inspection.

Intended use: run the backend with ENABLE_TRACEMALLOC=true and a SINGLE
gunicorn worker (GUNICORN_WORKERS=1) so every request + every snapshot call
hits the same process, then drive the load profile and diff snapshots.
"""

from __future__ import annotations

import os
import tracemalloc

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter()

# label -> tracemalloc.Snapshot, process-local (per worker).
_SNAPSHOTS: dict[str, "tracemalloc.Snapshot"] = {}


def _enabled() -> bool:
    return os.environ.get("ENABLE_TRACEMALLOC", "").lower() in ("1", "true", "yes")


def _top_n() -> int:
    try:
        return max(1, int(os.environ.get("TRACEMALLOC_TOP_N", "30")))
    except ValueError:
        return 30


def _snapshot_dir() -> str:
    d = os.environ.get("TRACEMALLOC_SNAPSHOT_DIR", "/tmp/tracemalloc")
    os.makedirs(d, exist_ok=True)
    return d


def _auth_or_reject(secret: str | None):
    """Return None when authorized, else a JSONResponse to return."""
    if not _enabled():
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    configured = (settings.internal_service_secret or "").strip()
    if not configured:
        return JSONResponse(
            status_code=403,
            content={"detail": "INTERNAL_SERVICE_SECRET not configured"},
        )
    import hmac

    if not secret or not hmac.compare_digest(secret.strip(), configured):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return None


def _format_top(stats, n: int) -> list[dict]:
    out = []
    for stat in stats[:n]:
        frame = stat.traceback[0] if stat.traceback else None
        out.append(
            {
                "size_bytes": getattr(stat, "size", 0),
                "size_diff_bytes": getattr(stat, "size_diff", 0),
                "count": getattr(stat, "count", 0),
                "count_diff": getattr(stat, "count_diff", 0),
                # file:line ONLY — never the allocated value.
                "traceback": [str(f) for f in (stat.traceback or [])][-6:],
                "top_frame": f"{frame.filename}:{frame.lineno}" if frame else None,
            }
        )
    return out


@router.post("/internal/debug/tracemalloc/start")
def tm_start(
    nframe: int = Query(default=15, ge=1, le=50),
    x_internal_service_secret: str | None = Header(default=None),
):
    rej = _auth_or_reject(x_internal_service_secret)
    if rej is not None:
        return rej
    if not tracemalloc.is_tracing():
        tracemalloc.start(nframe)
    return {"tracing": tracemalloc.is_tracing(), "nframe": nframe}


@router.post("/internal/debug/tracemalloc/snapshot")
def tm_snapshot(
    label: str = Query(...),
    x_internal_service_secret: str | None = Header(default=None),
):
    rej = _auth_or_reject(x_internal_service_secret)
    if rej is not None:
        return rej
    if not tracemalloc.is_tracing():
        return JSONResponse(status_code=409, content={"detail": "not tracing"})
    snap = tracemalloc.take_snapshot()
    _SNAPSHOTS[label] = snap
    cur, peak = tracemalloc.get_traced_memory()
    top = _format_top(snap.statistics("lineno"), _top_n())
    # Write a text summary file (code locations only).
    path = os.path.join(_snapshot_dir(), f"snapshot_{label}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# tracemalloc snapshot label={label}\n")
        fh.write(f"# traced_current={cur} traced_peak={peak}\n\n")
        for i, s in enumerate(snap.statistics("lineno")[: _top_n()]):
            fh.write(f"{i+1:>3}. {s.size/1024:.1f} KiB  count={s.count}  {s}\n")
    return {
        "label": label,
        "traced_current_bytes": cur,
        "traced_peak_bytes": peak,
        "labels": sorted(_SNAPSHOTS),
        "top": top,
        "file": path,
    }


@router.post("/internal/debug/tracemalloc/diff")
def tm_diff(
    from_label: str = Query(..., alias="from"),
    to_label: str = Query(..., alias="to"),
    key: str = Query(default="lineno"),
    x_internal_service_secret: str | None = Header(default=None),
):
    rej = _auth_or_reject(x_internal_service_secret)
    if rej is not None:
        return rej
    if from_label not in _SNAPSHOTS or to_label not in _SNAPSHOTS:
        return JSONResponse(
            status_code=404,
            content={"detail": "unknown label", "have": sorted(_SNAPSHOTS)},
        )
    key = key if key in ("lineno", "traceback") else "lineno"
    stats = _SNAPSHOTS[to_label].compare_to(_SNAPSHOTS[from_label], key)
    top = _format_top(stats, _top_n())
    total_diff = sum(s.size_diff for s in stats)
    path = os.path.join(_snapshot_dir(), f"diff_{from_label}__to__{to_label}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# tracemalloc diff {from_label} -> {to_label} (key={key})\n")
        fh.write(f"# total_size_diff_bytes={total_diff}\n\n")
        for i, s in enumerate(stats[: _top_n()]):
            fh.write(
                f"{i+1:>3}. {s.size_diff/1024:+.1f} KiB  "
                f"(count {s.count_diff:+d})  {s}\n"
            )
    return {
        "from": from_label,
        "to": to_label,
        "key": key,
        "total_size_diff_bytes": total_diff,
        "top": top,
        "file": path,
    }


@router.post("/internal/debug/tracemalloc/stop")
def tm_stop(x_internal_service_secret: str | None = Header(default=None)):
    rej = _auth_or_reject(x_internal_service_secret)
    if rej is not None:
        return rej
    was = tracemalloc.is_tracing()
    if was:
        tracemalloc.stop()
    _SNAPSHOTS.clear()
    return {"was_tracing": was, "tracing": tracemalloc.is_tracing()}
