"""FIX129 A3/A4 — the ops watcher: the alerts the backend cannot raise about itself.

Runs as the ``ops-watch`` compose service (same image as the backend,
``python -m app.ops.watch``) with the Docker socket mounted read-only.
Every ``OPS_WATCH_INTERVAL`` seconds it checks:

* **containers** of the compose project: unhealthy, restarting, dead, or
  exited while meant to be running → one alert per container, repeated at
  most every ``OPS_WATCH_COOLDOWN`` seconds, and a "recovered" note;
* **backend readiness** from the outside (``GET /ready`` with a short
  timeout) — during the 2026-09-22 wedge the backend could not answer its
  own probe, so the alert about it must come from another process;
* **disk** on the paths in ``OPS_WATCH_DISK_PATHS`` (the Docker data root
  is mounted read-only at /host/docker) above ``OPS_WATCH_DISK_PCT``;
* **memory** of the Docker VM (``/proc/meminfo``) above ``OPS_WATCH_MEM_PCT``.

Alerts go through ``app.services.alert_channels`` (ntfy / email / webhook).
The decision functions are pure so they are unit-tested with fake Docker
JSON; only ``main`` touches the socket.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.services.alert_channels import Notification, configured_channels, deliver


@dataclass(frozen=True)
class Finding:
    key: str          # stable id, e.g. "container:ai-gateway-backend-1"
    title: str
    message: str
    severity: str = "critical"


# ---------------------------------------------------------------------------
# Pure decision functions
# ---------------------------------------------------------------------------

_BAD_STATES = {"restarting", "dead"}


def classify_containers(items: list[dict], *, ignore_exited: tuple[str, ...] = ()) -> list[Finding]:
    """Findings for the ``GET /containers/json?all=1`` items of one project.

    A container is a finding when Docker reports it unhealthy, restarting,
    dead, or exited (unless its name matches an ``ignore_exited`` prefix —
    one-shot jobs). Compose one-off ``run`` containers are skipped.
    """
    out: list[Finding] = []
    for c in items:
        names = c.get("Names") or []
        name = (names[0] if names else c.get("Id", "")[:12]).lstrip("/")
        labels = c.get("Labels") or {}
        if labels.get("com.docker.compose.oneoff") == "True":
            continue
        state = str(c.get("State", "")).lower()
        status = str(c.get("Status", ""))
        service = labels.get("com.docker.compose.service", name)
        if "(unhealthy)" in status:
            out.append(Finding(
                key=f"container:{name}",
                title=f"{service} is unhealthy",
                message=f"{name}: {status}",
            ))
        elif state in _BAD_STATES:
            out.append(Finding(
                key=f"container:{name}",
                title=f"{service} is {state}",
                message=f"{name}: {status}",
            ))
        elif state == "exited" and not any(name.startswith(p) for p in ignore_exited):
            out.append(Finding(
                key=f"container:{name}",
                title=f"{service} has exited",
                message=f"{name}: {status}",
            ))
    return out


def classify_disk(usage: dict[str, tuple[int, int]], *, max_pct: float) -> list[Finding]:
    """``usage`` = {path: (total_bytes, free_bytes)}."""
    out: list[Finding] = []
    for path, (total, free) in usage.items():
        if total <= 0:
            continue
        pct = 100.0 * (total - free) / total
        if pct >= max_pct:
            out.append(Finding(
                key=f"disk:{path}",
                title=f"disk {path} is {pct:.0f} % full",
                message=f"{free / 2**30:.1f} GiB free of {total / 2**30:.1f} GiB (limit {max_pct:.0f} %)",
            ))
    return out


def classify_memory(total_kb: int, available_kb: int, *, max_pct: float) -> list[Finding]:
    if total_kb <= 0:
        return []
    pct = 100.0 * (total_kb - available_kb) / total_kb
    if pct < max_pct:
        return []
    return [Finding(
        key="memory",
        title=f"memory is {pct:.0f} % used",
        message=f"{available_kb / 2**20:.2f} GiB available of {total_kb / 2**20:.2f} GiB (limit {max_pct:.0f} %)",
    )]


def classify_pg_activity(name: str, rows: list[dict], *, stuck_after_s: float) -> list[Finding]:
    """``rows`` = pg_stat_activity rows (pid, state, application_name,
    wait_event_type, xact_age_s, query) for sessions idle in transaction
    or waiting on a lock. One finding per database when the oldest is past
    ``stuck_after_s`` — the 2026-09-22 wedge, seen from outside the backend.

    FIX130 E4 — the finding (= the alert) names pid, state, application,
    wait and age; never the query, which holds whatever the application
    bound into it. ``tick`` logs the query head (``pg_stuck_transaction``)."""
    worst = _oldest(rows)
    if worst is None or float(worst.get("xact_age_s") or 0.0) < stuck_after_s:
        return []
    age = float(worst.get("xact_age_s") or 0.0)
    return [Finding(
        key=f"pg:{name}:stuck",
        title=f"transaction stuck {age:.0f} s on {name}",
        message=(
            f"pid {worst.get('pid')} {worst.get('state')} "
            f"({worst.get('application_name') or 'no application_name'}, wait {worst.get('wait_event_type') or '-'}) "
            f"for {age:.0f} s (limit {stuck_after_s:.0f} s) — {len(rows)} session(s) idle/blocked"
        ),
    )]


def _oldest(rows: list[dict]) -> dict | None:
    worst = None
    for r in rows:
        age = float(r.get("xact_age_s") or 0.0)
        if worst is None or age > float(worst.get("xact_age_s") or 0.0):
            worst = r
    return worst


def stuck_log_sessions(rows: list[dict], limit: int = 5) -> list[dict]:
    """The sessions as the ``pg_stuck_transaction`` log line records them —
    the one place the query head is kept (oldest first, JSON-safe)."""
    ordered = sorted(rows, key=lambda r: float(r.get("xact_age_s") or 0.0), reverse=True)
    return [
        {
            "pid": r.get("pid"),
            "state": r.get("state"),
            "application_name": r.get("application_name"),
            "wait_event_type": r.get("wait_event_type"),
            "xact_age_s": round(float(r.get("xact_age_s") or 0.0), 1),
            "query": str(r.get("query") or "")[:120],
        }
        for r in ordered[:limit]
    ]


def classify_turns(
    ok: int, failed: int, *, window_min: int, min_sample: int, max_error_pct: float
) -> list[Finding]:
    """Turn-level truth from the CHAT database (assistant rows completed /
    error). The gateway's request_logs never sees a turn that fails before
    a provider is reached (found on the test stack: 12 turns, every model
    dead, 0 rows), so "no successful turn" and "error rate" are judged here,
    from outside the backend, on what the user actually got."""
    total = ok + failed
    out: list[Finding] = []
    if total >= 1 and ok == 0:
        out.append(Finding(
            key="turns:none_succeeded",
            title=f"no successful turn in {window_min} min",
            message=f"{total} turn(s) attempted in the last {window_min} min, none completed ({failed} failed)",
        ))
    if total >= min_sample:
        pct = 100.0 * failed / total
        if pct > max_error_pct:
            out.append(Finding(
                key="turns:error_rate",
                title=f"{pct:.0f} % of turns failed ({window_min} min)",
                message=f"{failed} of {total} turns failed in the last {window_min} min (limit {max_error_pct:g} %)",
                severity="critical" if pct >= 2 * max_error_pct else "warning",
            ))
    return out


def classify_ready(status_code: int | None, body: dict | None, error: str | None) -> list[Finding]:
    if status_code == 200:
        return []
    if status_code is None:
        return [Finding(
            key="backend:ready",
            title="backend does not answer /ready",
            message=f"no answer from the readiness probe: {error or 'unknown'}",
        )]
    failures = ", ".join((body or {}).get("failures") or []) or "unknown"
    return [Finding(
        key="backend:ready",
        title="backend is not ready",
        message=f"/ready answered {status_code}: {failures}",
    )]


class AlertState:
    """Which findings are open, and when each was last announced."""

    def __init__(self, *, cooldown_s: float, now=time.time) -> None:
        self._cooldown = cooldown_s
        self._now = now
        self._last_sent: dict[str, float] = {}
        self._open: dict[str, Finding] = {}

    # Persisted across restarts (and between `once` runs) so a restart of the
    # watcher neither re-announces every open finding nor forgets what was
    # open — the "recovered" note needs to know what it is recovering from.
    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        self._last_sent = {k: float(v) for k, v in (data.get("last_sent") or {}).items()}
        self._open = {
            k: Finding(key=k, title=v.get("title", k), message=v.get("message", ""), severity=v.get("severity", "critical"))
            for k, v in (data.get("open") or {}).items()
        }

    def save(self, path: str) -> None:
        data = {
            "last_sent": self._last_sent,
            "open": {k: {"title": f.title, "message": f.message, "severity": f.severity} for k, f in self._open.items()},
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError as exc:
            _log("state_save_failed", error=str(exc)[:120])

    def step(self, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        """Return (to_announce, recovered) for this tick."""
        now = self._now()
        current = {f.key: f for f in findings}
        announce: list[Finding] = []
        for key, f in current.items():
            last = self._last_sent.get(key)
            if last is None or now - last >= self._cooldown:
                announce.append(f)
                self._last_sent[key] = now
        recovered = [f for key, f in self._open.items() if key not in current]
        for f in recovered:
            self._last_sent.pop(f.key, None)
        self._open = current
        return announce, recovered


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def docker_containers(project: str, sock: str) -> list[dict]:
    filters = quote(json.dumps({"label": [f"com.docker.compose.project={project}"]}))
    transport = httpx.HTTPTransport(uds=sock)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        r = client.get(f"http://docker/containers/json?all=1&filters={filters}")
        r.raise_for_status()
        return r.json()


def disk_usage(paths: list[str]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for p in paths:
        try:
            u = shutil.disk_usage(p)
            out[p] = (u.total, u.free)
        except OSError:
            continue
    return out


def meminfo() -> tuple[int, int]:
    total = avail = 0
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
    except OSError:
        pass
    return total, avail


_PG_ACTIVITY_SQL = """
    SELECT pid, state, coalesce(application_name, '') AS application_name,
           coalesce(wait_event_type, '') AS wait_event_type,
           extract(epoch FROM (now() - xact_start)) AS xact_age_s,
           left(coalesce(query, ''), 120) AS query
    FROM pg_stat_activity
    WHERE datname = current_database() AND backend_type = 'client backend'
      AND pid <> pg_backend_pid() AND xact_start IS NOT NULL
      AND (state = 'idle in transaction' OR wait_event_type = 'Lock')
    ORDER BY xact_start LIMIT 20
"""


def pg_activity_rows(dsn: str) -> list[dict]:
    """Read pg_stat_activity through psycopg with short timeouts; the
    connection sets its own statement_timeout so it can never be the
    thing that is stuck."""
    import psycopg

    with psycopg.connect(
        dsn, connect_timeout=5, application_name="ops-watch",
        options="-c statement_timeout=5000 -c lock_timeout=2000",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(_PG_ACTIVITY_SQL)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


_TURNS_SQL = """
    SELECT count(*) FILTER (WHERE status = 'completed') AS ok,
           count(*) FILTER (WHERE status = 'error')     AS failed
    FROM chat.messages_masked
    WHERE role = 'assistant' AND created_at > now() - make_interval(mins => %s)
"""


def turn_counts(dsn: str, window_min: int) -> tuple[int, int]:
    """(completed, error) assistant rows in the window, from the chat DB."""
    import psycopg

    with psycopg.connect(
        dsn, connect_timeout=5, application_name="ops-watch",
        options="-c statement_timeout=5000 -c lock_timeout=2000",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(_TURNS_SQL, (int(window_min),))
            ok, failed = cur.fetchone()
            return int(ok or 0), int(failed or 0)


def parse_pg_dsns(spec: str) -> list[tuple[str, str]]:
    """``OPS_WATCH_PG_DSNS`` = ``name=dsn;name=dsn`` (';' — DSNs carry ',')."""
    out: list[tuple[str, str]] = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, dsn = part.split("=", 1)
        if name.strip() and dsn.strip():
            out.append((name.strip(), dsn.strip()))
    return out


def probe_ready(url: str) -> tuple[int | None, dict | None, str | None]:
    try:
        r = httpx.get(url, timeout=6.0)
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body, None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {str(exc)[:120]}"


def _log(msg: str, **kv) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "msg": msg, **kv},
                     ensure_ascii=False, default=str), flush=True)


def tick(state: AlertState, cfg: dict) -> None:
    findings: list[Finding] = []
    try:
        findings += classify_containers(
            docker_containers(cfg["project"], cfg["sock"]),
            ignore_exited=cfg["ignore_exited"],
        )
    except Exception as exc:  # noqa: BLE001
        _log("docker_query_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")
    if cfg["backend_url"]:
        findings += classify_ready(*probe_ready(cfg["backend_url"].rstrip("/") + "/ready"))
    findings += classify_disk(disk_usage(cfg["disk_paths"]), max_pct=cfg["disk_pct"])
    findings += classify_memory(*meminfo(), max_pct=cfg["mem_pct"])
    for name, dsn in cfg["pg_dsns"]:
        try:
            rows = pg_activity_rows(dsn)
            stuck = classify_pg_activity(name, rows, stuck_after_s=cfg["pg_stuck_s"])
            if stuck:
                # The query head goes to this log only, never into the alert.
                _log("pg_stuck_transaction", db=name, stuck_after_s=cfg["pg_stuck_s"],
                     sessions=stuck_log_sessions(rows))
            findings += stuck
        except Exception as exc:  # noqa: BLE001 — the DSN, never the password, is named by its label
            _log("pg_activity_query_failed", db=name, error=f"{type(exc).__name__}: {str(exc)[:120]}")
    chat_dsn = dict(cfg["pg_dsns"]).get("chat")
    if chat_dsn:
        try:
            ok, failed = turn_counts(chat_dsn, cfg["turn_window_min"])
            findings += classify_turns(
                ok, failed, window_min=cfg["turn_window_min"],
                min_sample=cfg["turn_min_sample"], max_error_pct=cfg["turn_error_pct"],
            )
        except Exception as exc:  # noqa: BLE001
            _log("turn_query_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")

    announce, recovered = state.step(findings)
    for f in announce:
        _log("alert", key=f.key, title=f.title, message=f.message)
        deliver(Notification(
            title=f.title, message=f.message, severity=f.severity,
            source="ops-watch", tags=("ops",), details={"key": f.key},
        ))
    for f in recovered:
        _log("recovered", key=f.key, title=f.title)
        deliver(Notification(
            title=f"recovered: {f.title}", message=f"{f.key} is back to normal",
            severity="info", source="ops-watch", tags=("ops", "white_check_mark"),
        ))
    _log("tick", findings=len(findings), open=[f.key for f in findings])


def main() -> int:
    cfg = {
        "project": _env("OPS_WATCH_PROJECT", "ai-gateway"),
        "sock": _env("OPS_WATCH_DOCKER_SOCK", "/var/run/docker.sock"),
        "interval": float(_env("OPS_WATCH_INTERVAL", "30")),
        "cooldown": float(_env("OPS_WATCH_COOLDOWN", "600")),
        "backend_url": _env("OPS_WATCH_BACKEND_URL", "http://backend:8080"),
        "disk_paths": [p for p in _env("OPS_WATCH_DISK_PATHS", "/host/docker").split(",") if p],
        "disk_pct": float(_env("OPS_WATCH_DISK_PCT", "90")),
        "mem_pct": float(_env("OPS_WATCH_MEM_PCT", "90")),
        "ignore_exited": tuple(p for p in _env("OPS_WATCH_IGNORE_EXITED", "").split(",") if p),
        # name=dsn;name=dsn — databases whose pg_stat_activity is watched for
        # a transaction idle / lock-blocked past OPS_WATCH_PG_STUCK_S. The
        # backend samples its own (gateway) database; this is for the chat
        # database, and for the gateway database when the backend cannot.
        "pg_dsns": parse_pg_dsns(_env("OPS_WATCH_PG_DSNS", "")),
        "pg_stuck_s": float(_env("OPS_WATCH_PG_STUCK_S", "60")),
        # Turn-level rules on the chat database (the "chat" DSN above).
        "turn_window_min": int(_env("OPS_WATCH_TURN_WINDOW_MIN", "5")),
        "turn_min_sample": int(_env("OPS_WATCH_TURN_MIN_SAMPLE", "10")),
        "turn_error_pct": float(_env("OPS_WATCH_TURN_ERROR_PCT", "5")),
    }
    _log("ops_watch_start", channels=configured_channels(),
         **{k: (v if k != "pg_dsns" else [n for n, _ in v]) for k, v in cfg.items() if k != "sock"})
    if not configured_channels():
        _log("ops_watch_warning", note="no alert channel configured (ALERT_NTFY_URL / ALERT_EMAIL_TO / ALERT_WEBHOOK_URL) — findings are logged only")
    state = AlertState(cooldown_s=cfg["cooldown"])
    state_file = _env("OPS_WATCH_STATE_FILE", "")
    if state_file:
        state.load(state_file)

    def _tick() -> None:
        tick(state, cfg)
        if state_file:
            state.save(state_file)

    if len(sys.argv) > 1 and sys.argv[1] == "once":
        _tick()
        return 0
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001 — the watcher never dies on one tick
            _log("tick_failed", error=f"{type(exc).__name__}: {str(exc)[:160]}")
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    raise SystemExit(main())
