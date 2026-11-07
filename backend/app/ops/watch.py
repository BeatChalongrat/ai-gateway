"""FIX129 A3/A4 — the ops watcher: the alerts the backend cannot raise about itself.

Runs as the ``ops-watch`` compose service (same image as the backend,
``python -m app.ops.watch``) with the Docker socket mounted read-only.
Every ``OPS_WATCH_INTERVAL`` seconds it checks:

* **containers** of the compose project: unhealthy, restarting, dead, or
  exited while meant to be running;
* **backend readiness** from the outside (``GET /ready`` with a short
  timeout) — during the 2026-09-22 wedge the backend could not answer its
  own probe, so the alert about it must come from another process;
* **disk** on the paths in ``OPS_WATCH_DISK_PATHS`` (the Docker data root
  is mounted read-only at /host/docker) above ``OPS_WATCH_DISK_PCT``;
* **memory** of the Docker VM (``/proc/meminfo``) above ``OPS_WATCH_MEM_PCT``;
* **transactions** idle / lock-blocked on both databases, and the **turns**
  users actually got (chat database);
* FIX131: **turns stuck "streaming"** longer than ``OPS_WATCH_TURN_STUCK_MIN``
  (the orphaned-turn sweep should have closed them) and **backups**: no
  proven backup newer than ``OPS_WATCH_BACKUP_MAX_AGE_H`` (26 h), the last
  run failed, or its off-site copy failed — read from the backup folder
  (``/backups``, read-only), because the backup container cannot report its
  own death;
* FIX134: the **smoke suite** (``scripts/smoke.sh``, daily): its result file
  (``OPS_WATCH_SMOKE_FILE``, default ``/backups/smoke_last.json``) is red, or
  older / missing for more than ``OPS_WATCH_SMOKE_MAX_AGE_H`` (26 h).

FIX131 D — findings no longer page by themselves. Each one is a signal to
the pager (``app.services.alert_pager``), shared with the backend's alert
rules through Redis: related findings map to one incident
(``app.services.alert_catalog``), severity is graded by impact on users,
container / readiness findings must last ``ALERT_DEBOUNCE_SECONDS`` (90 s)
before they page, a maintenance window holds pages, and a closed incident
sends one "recovered". The decision functions are pure so they are
unit-tested with fake Docker JSON; only ``main`` touches the socket.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.services import alert_catalog as cat
from app.services.alert_channels import Notification, configured_channels, deliver  # noqa: F401 — tests patch watch.deliver
from app.services.alert_pager import Decision, Pager, Signal, default_store


@dataclass(frozen=True)
class Finding:
    key: str          # stable id of what this watcher sees, e.g. "container:ai-gateway-backend-1"
    title: str        # what is wrong, for users
    message: str      # what to do / where to look
    severity: str = cat.CRITICAL
    # FIX131 — the incident it belongs to (alert_catalog; "" = its own key),
    # how long it must last before it pages, and whether its sender already
    # paged (the backup container's transition page).
    group: str = ""
    debounce_s: float = 0.0
    notified_elsewhere: bool = False

    @property
    def incident(self) -> str:
        return self.group or self.key


# ---------------------------------------------------------------------------
# Pure decision functions
# ---------------------------------------------------------------------------

_BAD_STATES = {"restarting", "dead"}


def classify_containers(items: list[dict], *, ignore_exited: tuple[str, ...] = (),
                        debounce_s: float = 0.0) -> list[Finding]:
    """Findings for the ``GET /containers/json?all=1`` items of one project.

    A container is a finding when Docker reports it unhealthy, restarting,
    dead, or exited (unless its name matches an ``ignore_exited`` prefix —
    one-shot jobs). Compose one-off ``run`` containers are skipped. The
    incident is the compose service (``service:<name>``); a user-facing
    service is critical, anything else degraded.
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
            what = "is unhealthy"
        elif state in _BAD_STATES:
            what = f"is {state}"
        elif state == "exited" and not any(name.startswith(p) for p in ignore_exited):
            what = "has exited"
        else:
            continue
        severity, title, body = cat.container_text(service, what, name, status)
        out.append(Finding(key=f"container:{name}", title=title, message=body, severity=severity,
                           group=cat.service_group(service), debounce_s=debounce_s))
    return out


def classify_disk(usage: dict[str, tuple[int, int]], *, max_pct: float) -> list[Finding]:
    """``usage`` = {path: (total_bytes, free_bytes)}."""
    out: list[Finding] = []
    for path, (total, free) in usage.items():
        if total <= 0:
            continue
        pct = 100.0 * (total - free) / total
        if pct >= max_pct:
            severity, title, body = cat.disk_text(path, pct, free / 2**30, total / 2**30, max_pct)
            out.append(Finding(key=f"disk:{path}", title=title, message=body, severity=severity))
    return out


def classify_memory(total_kb: int, available_kb: int, *, max_pct: float) -> list[Finding]:
    if total_kb <= 0:
        return []
    pct = 100.0 * (total_kb - available_kb) / total_kb
    if pct < max_pct:
        return []
    severity, title, body = cat.memory_text(pct, available_kb / 2**20, total_kb / 2**20, max_pct)
    return [Finding(key="memory", title=title, message=body, severity=severity)]


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
    who = (
        f"pid {worst.get('pid')} {worst.get('state')} "
        f"({worst.get('application_name') or 'no application_name'}, wait {worst.get('wait_event_type') or '-'}) "
        f"for {age:.0f} s — {len(rows)} session(s) idle/blocked"
    )
    severity, title, body = cat.stuck_tx_text(name, age, stuck_after_s, who, what=cat.stuck_state(worst))
    return [Finding(key=f"pg:{name}:stuck", title=title, message=body, severity=severity,
                    group=cat.stuck_group(name))]


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
    ok: int, failed: int, *, window_min: int, min_sample: int, max_error_pct: float,
    min_attempts: int = 3,
) -> list[Finding]:
    """Turn-level truth from the CHAT database (assistant rows completed /
    error). The gateway's request_logs never sees a turn that fails before
    a provider is reached (found on the test stack: 12 turns, every model
    dead, 0 rows), so "no successful turn" and "error rate" are judged here,
    from outside the backend, on what the user actually got.

    FIX131 — "no successful turn" is critical only with at least
    ``min_attempts`` attempts; one failed turn in a quiet window is a
    log-only finding (severity none). The error rate is degraded (warning)
    and, like it, needs ``min_attempts`` failed turns."""
    total = ok + failed
    out: list[Finding] = []
    if total >= 1 and ok == 0:
        severity, title, body = cat.turns_none_text(window_min, total, failed, min_attempts)
        out.append(Finding(key="turns:none_succeeded", title=title, message=body, severity=severity,
                           group=cat.GROUP_TURNS_NONE))
    # FIX131 — the rate also needs ``min_attempts`` failed turns: one failed
    # turn in ten (10 %) is a model outcome, not an incident to page for.
    if total >= min_sample and failed >= min_attempts:
        pct = 100.0 * failed / total
        if pct > max_error_pct:
            severity, title, body = cat.turns_error_rate_text(window_min, pct, failed, total, max_error_pct)
            out.append(Finding(key="turns:error_rate", title=title, message=body, severity=severity,
                               group=cat.GROUP_TURNS_ERROR_RATE))
    return out


def classify_stuck_turns(count: int, oldest_s: float, *, after_min: int) -> list[Finding]:
    """FIX131 — assistant replies still ``streaming`` after ``after_min``
    minutes: turns whose writer died. Another lane's sweep closes orphaned
    turns; this catches the sweep failing."""
    if count <= 0:
        return []
    severity, title, body = cat.turns_stuck_text(count, oldest_s, after_min)
    return [Finding(key="turns:stuck", title=title, message=body, severity=severity,
                    group=cat.GROUP_TURNS_STUCK)]


def container_restarting(items: list[dict], service: str = "backend") -> bool:
    """FIX133 D — is the service's container (re)starting right now? Docker
    says ``restarting``, or its healthcheck is still ``starting``, or it came
    up less than three minutes ago. A deploy or an autoheal restart looks
    like this; a hard outage does not."""
    for c in items:
        labels = c.get("Labels") or {}
        if labels.get("com.docker.compose.service") != service:
            continue
        if labels.get("com.docker.compose.oneoff") == "True":
            continue
        state = str(c.get("State", "")).lower()
        status = str(c.get("Status", ""))
        if state == "restarting" or "health: starting" in status:
            return True
        if state == "running" and re.match(r"^Up (Less than a second|\d+ seconds?|1 minute|2 minutes)\b", status):
            return True
    return False


def classify_ready(status_code: int | None, body: dict | None, error: str | None, *,
                   debounce_s: float = 0.0, restart_debounce_s: float | None = None,
                   restarting: bool = False) -> list[Finding]:
    """The backend's /ready from outside. No answer or a 503 = the backend
    incident (``service:backend``, critical after the debounce) — except a
    503 whose only reason is ``stuck_transaction``: that is the stuck
    transaction's incident (``db:gateway:stuck``), already reported by the
    backend's rule and the pg_stat_activity watch.

    FIX133 D — two speeds: a hard outage (the container is not restarting)
    pages after ``debounce_s``; a container that is restarting or just came
    up gets ``restart_debounce_s`` (longer) to come back before it pages.
    """
    if status_code == 200:
        return []
    if restarting and restart_debounce_s is not None:
        debounce_s = restart_debounce_s
    if status_code is None:
        severity, title, text = cat.ready_down_text(error or "unknown")
        return [Finding(key="backend:ready", title=title, message=text, severity=severity,
                        group=cat.service_group("backend"), debounce_s=debounce_s)]
    failures = [str(f) for f in ((body or {}).get("failures") or [])]
    if failures and all(f == "stuck_transaction" for f in failures):
        severity, title, text = cat.ready_stuck_text(status_code)
        return [Finding(key="backend:ready", title=title, message=text, severity=severity,
                        group=cat.stuck_group("gateway"))]
    severity, title, text = cat.ready_not_ready_text(status_code, failures)
    return [Finding(key="backend:ready", title=title, message=text, severity=severity,
                    group=cat.service_group("backend"), debounce_s=debounce_s)]


def classify_capacity(body: dict | None, *, queue_min_waiting: int = 0, queue_oldest_s: float = 60.0,
                      saturation_pct: float = 90.0, debounce_s: float = 180.0) -> list[Finding]:
    """FIX133 D — from a 200 /ready body: the turn queue's line is not
    draining (``checks.turn_queue``: waiting >= queue_min_waiting, default
    max(4, max_active / 3), or the oldest waiter past queue_oldest_s), and
    the backend workers are saturated (``checks.container``: in-flight >=
    saturation_pct of workers x cap). Both are degraded (HIGH), both only
    after the debounce — a spike that drains is not an incident."""
    out: list[Finding] = []
    checks = (body or {}).get("checks") or {}
    tq = checks.get("turn_queue") or {}
    if tq.get("enabled") and "waiting" in tq:
        waiting = int(tq.get("waiting") or 0)
        oldest = float(tq.get("oldest_wait_s") or 0.0)
        max_active = int(tq.get("max_active") or 0)
        active = int(tq.get("active") or 0)
        threshold = queue_min_waiting if queue_min_waiting > 0 else max(4, max_active // 3)
        if waiting >= threshold or (waiting > 0 and oldest >= queue_oldest_s):
            severity, title, text = cat.queue_backlog_text(waiting, oldest, active, max_active)
            out.append(Finding(key="capacity:queue", title=title, message=text, severity=severity,
                               group=cat.GROUP_QUEUE, debounce_s=debounce_s))
    container = checks.get("container") or {}
    inflight_one = checks.get("inflight") or {}
    workers = int(container.get("workers") or 0)
    limit = int(inflight_one.get("limit") or 0)
    inflight = int(container.get("inflight") or 0)
    capacity = workers * limit
    if capacity > 0 and inflight >= capacity * saturation_pct / 100.0:
        severity, title, text = cat.saturated_text(inflight, capacity, workers)
        out.append(Finding(key="capacity:saturated", title=title, message=text, severity=severity,
                           group=cat.GROUP_SATURATED, debounce_s=debounce_s))
    return out


def _when(epoch: float | None) -> str:
    if not epoch:
        return "none"
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def classify_backup(success: dict | None, failure: dict | None, *, now: float, max_age_h: float,
                    newest_proven: tuple[float, str] | None = None,
                    offsite_max_age_h: float | None = None,
                    newest_offsite: tuple[float, str] | None = None,
                    offsite_remind: bool = False) -> list[Finding]:
    """FIX131 B — from the backup container's records (infra/backup/
    schedule.sh): ``last_success.json`` (the newest backup whose restore
    drill passed) and ``last_failure.json`` (the newest failed run).
    ``newest_proven`` = (epoch, id) of the newest proven backup directory,
    for backups written before the records existed.

    * the last run failed (a failure newer than the newest success) →
      "backups are failing" — ``notified_elsewhere`` when the backup
      container already paged that transition, so it is not paged twice;
    * otherwise no proven backup newer than ``max_age_h`` → "no fresh backup"
      (the container is stopped, the PC slept through it, the schedule died);
    * the newest success's off-site copy failed → its own incident."""
    out: list[Finding] = []
    ok_epoch: float | None = None
    ok_id = ""
    if success and isinstance(success.get("epoch"), (int, float)):
        ok_epoch, ok_id = float(success["epoch"]), str(success.get("backup_id") or "")
    if newest_proven and (ok_epoch is None or newest_proven[0] > ok_epoch):
        ok_epoch, ok_id = float(newest_proven[0]), newest_proven[1]
    newest = f"{ok_id} ({_when(ok_epoch)})" if ok_epoch else "none"

    f_epoch = float((failure or {}).get("epoch") or 0)
    if failure and f_epoch > (ok_epoch or 0):
        severity, title, body = cat.backup_failed_text(
            str(failure.get("status") or "BACKUP FAILED"), int(failure.get("attempts") or 1),
            str(failure.get("next_retry_at") or "the next scheduler tick"), newest)
        out.append(Finding(key="backup:failed", title=title, message=body, severity=severity,
                           group=cat.GROUP_BACKUP, notified_elsewhere=bool(failure.get("notified"))))
    elif ok_epoch is None or now - ok_epoch > max_age_h * 3600:
        severity, title, body = cat.backup_stale_text(None if ok_epoch is None else now - ok_epoch,
                                                      newest, max_age_h)
        out.append(Finding(key="backup:stale", title=title, message=body, severity=severity,
                           group=cat.GROUP_BACKUP))
    offsite = (success or {}).get("offsite") or {}
    if success and offsite.get("status") == "fail":
        severity, title, body = cat.backup_offsite_text(str(success.get("backup_id") or "?"))
        out.append(Finding(key="backup:offsite", title=title, message=body, severity=severity,
                           group=cat.GROUP_BACKUP_OFFSITE, notified_elsewhere=bool(offsite.get("notified"))))
    # FIX133 C — a copy that never leaves the PC, or one older than
    # offsite_max_age_h, is its own (degraded) incident. Configured = the
    # newest run says anything but "not_configured", or a proven copy exists.
    if offsite_max_age_h is not None and (success is not None or newest_offsite is not None):
        status = str(offsite.get("status") or "")
        if status == "not_configured" and newest_offsite is None:
            # The backup container said so itself (a record without the key
            # predates the off-site copy and says nothing). An opt-in
            # reminder (OPS_WATCH_OFFSITE_REMIND), quiet while the backups
            # themselves are failing or stale: that is the incident.
            if out or not offsite_remind:
                return out
            severity, title, body = cat.backup_offsite_missing_text()
            out.append(Finding(key="backup:offsite_missing", title=title, message=body, severity=severity,
                               group=cat.GROUP_BACKUP_OFFSITE_MISSING))
        elif status not in ("", "not_configured") or newest_offsite is not None:
            age = None if newest_offsite is None else now - newest_offsite[0]
            if age is None or age > offsite_max_age_h * 3600:
                severity, title, body = cat.backup_offsite_stale_text(age, newest, offsite_max_age_h)
                out.append(Finding(key="backup:offsite_stale", title=title, message=body, severity=severity,
                                   group=cat.GROUP_BACKUP_OFFSITE_STALE))
    return out


def classify_smoke(record: dict | None, *, now: float, max_age_h: float = 26.0,
                   missing_since: float | None = None) -> list[Finding]:
    """FIX134 F — the daily smoke suite's result (``smoke_last.json``, written by
    scripts/smoke-daily.ps1 into the backup folder ops-watch reads at /backups).

    * no file: quiet until it has been missing for ``max_age_h`` (``missing_since`` =
      when this watcher first saw it absent) → "has not run";
    * a result older than ``max_age_h`` (or one without a timestamp) → "has not run";
    * a fresh result with ``ok`` false → "is red", naming the failed checks.
    One incident (``smoke``) for all three, degraded (HIGH): chat may still work."""
    limit_s = max_age_h * 3600
    if record is None:
        if missing_since is None or now - missing_since <= limit_s:
            return []
        severity, title, body = cat.smoke_stale_text(now - missing_since, max_age_h)
        return [Finding(key="smoke:missing", title=title, message=body, severity=severity, group=cat.GROUP_SMOKE)]
    epoch = record.get("epoch")
    if not isinstance(epoch, (int, float)):
        severity, title, body = cat.smoke_stale_text(None, max_age_h, why="an unreadable record (no timestamp)")
        return [Finding(key="smoke:stale", title=title, message=body, severity=severity, group=cat.GROUP_SMOKE)]
    age = now - float(epoch)
    if age > limit_s:
        severity, title, body = cat.smoke_stale_text(age, max_age_h)
        return [Finding(key="smoke:stale", title=title, message=body, severity=severity, group=cat.GROUP_SMOKE)]
    if record.get("ok") is True:
        return []
    checks = record.get("checks") if isinstance(record.get("checks"), list) else []
    failed = [str(c.get("name") or "?") for c in checks if isinstance(c, dict) and c.get("status") == "FAIL"]
    # AM1b: the agent_market.* checks gate a deploy, but their failures are
    # Agent Market's own (its dashboard and channel "agent-market"), never a
    # page on the core channel.
    core_failed = [n for n in failed if not n.startswith("agent_market.")]
    if failed and not core_failed:
        return []
    failed = core_failed
    total = len(checks) or int((record.get("counts") or {}).get("fail") or 0) + int((record.get("counts") or {}).get("pass") or 0)
    if not failed:
        failed = [str(record.get("error") or "the suite did not finish")]
    severity, title, body = cat.smoke_failed_text(failed, total, str(record.get("at") or "unknown time"),
                                                  str(record.get("target") or "live"))
    return [Finding(key="smoke:failed", title=title, message=body, severity=severity, group=cat.GROUP_SMOKE)]


class AlertState:
    """The watcher's side of the pager: what it reported on the last tick
    (so a finding it no longer sees is cleared at once) and, when Redis is
    down, the pager's memory — both kept in ``OPS_WATCH_STATE_FILE`` across
    restarts, so a restart neither re-pages an open incident nor forgets it.

    ``cooldown_s`` is accepted for compatibility and ignored: FIX131 retired
    the watcher's own 10-minute re-announce; the pager re-pages only on
    escalation or after ALERT_REMINDER_SECONDS."""

    def __init__(self, *, cooldown_s: float | None = None, now=time.time, store=None,
                 ttl_s: float = 90.0, send=None, sender: str = "ops-watch") -> None:
        self._now = now
        self.store = store if store is not None else default_store()
        # AM1c — Agent Market's state passes its own store and channel.
        self.pager = Pager(self.store, send=send or (lambda n: deliver(n)), now=now, log=_log, sender=sender)
        self.ttl_s = ttl_s
        self.seen: dict[str, str] = {}

    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        seen = data.get("seen") or {}
        if isinstance(seen, dict):
            self.seen = {str(k): str(v) for k, v in seen.items()}
        if hasattr(self.store, "restore"):
            self.store.restore(data.get("pager") or {})

    def save(self, path: str) -> None:
        data = {"seen": self.seen}
        if hasattr(self.store, "snapshot"):
            data["pager"] = self.store.snapshot()
        try:
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except OSError as exc:
            _log("state_save_failed", error=str(exc)[:120])

    def step(self, findings: list[Finding]) -> list[Decision]:
        """Report this tick's findings; clear what is gone; close what expired."""
        decisions: list[Decision] = []
        current: dict[str, str] = {}
        for f in findings:
            d = self.pager.fire(Signal(
                group=f.incident, source=f"ops-watch:{f.key}", severity=f.severity,
                title=f.title, body=f.message, debounce_s=f.debounce_s, ttl_s=self.ttl_s,
                notified_elsewhere=f.notified_elsewhere,
            ))
            if d.paged:
                # The watcher's own trail of what it paged (FIX129 "alert" line).
                _log("alert", key=f.key, incident=f.incident, action=d.action, severity=d.severity, title=f.title)
            decisions.append(d)
            current[f.key] = f.incident
        for key, group in self.seen.items():
            if key not in current:
                d = self.pager.clear(group, f"ops-watch:{key}")
                if d is not None:
                    decisions.append(d)
        decisions += self.pager.sweep()
        for d in decisions:
            if d.action in ("recovered", "expired"):
                _log("recovered", incident=d.group, action=d.action, reason=d.reason)
        self.seen = current
        return decisions


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


def _pg_connect(dsn: str):
    import psycopg

    return psycopg.connect(
        dsn, connect_timeout=5, application_name="ops-watch",
        options="-c statement_timeout=5000 -c lock_timeout=2000",
    )


def pg_activity_rows(dsn: str) -> list[dict]:
    """Read pg_stat_activity through psycopg with short timeouts; the
    connection sets its own statement_timeout so it can never be the
    thing that is stuck."""
    with _pg_connect(dsn) as conn:
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
    with _pg_connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_TURNS_SQL, (int(window_min),))
            ok, failed = cur.fetchone()
            return int(ok or 0), int(failed or 0)


# FIX131 — replies still "streaming" after N minutes. Only recent rows (the
# lookback): turns orphaned long ago are history for the sweep, not a page.
# idx_messages_created (created_at) serves the range.
_STUCK_TURNS_SQL = """
    SELECT count(*) AS n,
           coalesce(max(extract(epoch FROM (now() - created_at))), 0) AS oldest_s
    FROM chat.messages_masked
    WHERE role = 'assistant' AND status = 'streaming'
      AND created_at < now() - make_interval(mins => %s)
      AND created_at > now() - make_interval(hours => %s)
"""


def stuck_turns(dsn: str, after_min: int, lookback_h: int) -> tuple[int, float]:
    """(count, oldest age in s) of assistant rows streaming past ``after_min``."""
    with _pg_connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_STUCK_TURNS_SQL, (int(after_min), int(lookback_h)))
            n, oldest = cur.fetchone()
            return int(n or 0), float(oldest or 0.0)


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def backup_records(root: str) -> tuple[dict | None, dict | None, tuple[float, str] | None]:
    """(last_success.json, last_failure.json, newest proven directory) of the
    backup folder. The directory scan covers backups made before the records
    existed (FIX129/FIX130 images)."""
    success = _read_json(os.path.join(root, "last_success.json"))
    failure = _read_json(os.path.join(root, "last_failure.json"))
    newest: tuple[float, str] | None = None
    try:
        names = sorted((n for n in os.listdir(root) if len(n) == 15 and n[8] == "_" and n.replace("_", "").isdigit()),
                       reverse=True)
    except OSError:
        names = []
    for name in names:
        drill = _read_json(os.path.join(root, name, "drill.json"))
        manifest = _read_json(os.path.join(root, name, "manifest.json"))
        if not drill or drill.get("pass") is not True or not manifest:
            continue
        try:
            at = datetime.strptime(str(manifest.get("created_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        newest = (at.timestamp(), name)
        break
    return success, failure, newest


def newest_offsite_copy(root: str) -> tuple[float, str] | None:
    """FIX133 C — (epoch, backup id) of the newest run whose off-site copy was
    proven (``<run>/offsite.json``: pass == true), from the run directories."""
    try:
        names = sorted((n for n in os.listdir(root) if len(n) == 15 and n[8] == "_" and n.replace("_", "").isdigit()),
                       reverse=True)
    except OSError:
        return None
    for name in names:
        rec = _read_json(os.path.join(root, name, "offsite.json"))
        if not rec or rec.get("pass") is not True:
            continue
        try:
            at = datetime.strptime(str(rec.get("at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return (at.timestamp(), name)
    return None


def smoke_record(path: str) -> dict | None:
    """FIX134 F — ``smoke_last.json`` (None when absent or unreadable)."""
    return _read_json(path)


def smoke_findings(cfg: dict) -> list[Finding]:
    """FIX134 F — the smoke rule's I/O: read the record, remember in ``cfg`` when the file
    was first seen missing (so "has not run" fires after the limit, not at once), classify."""
    smoke_file = cfg.get("smoke_file")
    if not smoke_file:
        return []
    now = time.time()
    record = smoke_record(smoke_file)
    if record is None:
        cfg.setdefault("_smoke_missing_since", now)
    else:
        cfg.pop("_smoke_missing_since", None)
    return classify_smoke(record, now=now, max_age_h=float(cfg.get("smoke_max_age_h", 26)),
                          missing_since=cfg.get("_smoke_missing_since"))


def classify_power(record: dict | None, *, now: float, max_age_s: float = 600.0, page_pct: float = 40.0,
                   page_minutes: float = 60.0, debounce_s: float = 120.0) -> list[Finding]:
    """The host's power. The host is a laptop, and this watcher's container cannot see a battery:
    scripts/ops/power-watch.ps1 (a task of the host) writes ``power_last.json`` beside the backups once
    a minute, and this reads it.

    * no reading at all: quiet — the task is not installed on this host;
    * a reading of a machine with no battery: quiet;
    * a reading older than ``max_age_s``: "the reading has stopped" (degraded) — the watcher would not
      see the machine go on battery;
    * on battery: said (degraded) once it has lasted ``debounce_s``, so that carrying the machine to
      another socket pages nobody;
    * on battery with the charge at or under ``page_pct``, or the time left at or under
      ``page_minutes``: critical, at once — while there is still time to plug in.

    One incident (``power``): the pager says "on battery" once and pages again when it turns critical.
    When the battery is empty this watcher stops with the machine; there is no page after that one."""
    if not record:
        return []
    epoch = record.get("epoch")
    age = now - float(epoch) if isinstance(epoch, (int, float)) and not isinstance(epoch, bool) else float("inf")
    if age > max_age_s:
        severity, title, body = cat.power_stale_text(age if age != float("inf") else max_age_s, max_age_s)
        return [Finding(key="power:stale", title=title, message=body, severity=severity, group=cat.GROUP_POWER,
                        debounce_s=max(debounce_s, 300.0))]
    if record.get("battery") is False or not record.get("on_battery"):
        return []

    def number(v) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None

    pct, minutes = number(record.get("charge_pct")), number(record.get("minutes_left"))
    if (pct is not None and pct <= page_pct) or (minutes is not None and minutes <= page_minutes):
        severity, title, body = cat.power_low_text(pct, minutes)
        return [Finding(key="power:low", title=title, message=body, severity=severity, group=cat.GROUP_POWER)]
    severity, title, body = cat.power_on_battery_text(pct, minutes, page_pct, page_minutes)
    return [Finding(key="power:on_battery", title=title, message=body, severity=severity, group=cat.GROUP_POWER,
                    debounce_s=debounce_s)]


def power_findings(cfg: dict) -> list[Finding]:
    """The power rule's I/O: read the reading the host's task wrote, classify."""
    power_file = cfg.get("power_file")
    if not power_file:
        return []
    return classify_power(_read_json(power_file), now=time.time(), max_age_s=float(cfg.get("power_max_age_s", 600)),
                          page_pct=float(cfg.get("power_page_pct", 40)), page_minutes=float(cfg.get("power_page_min", 60)),
                          debounce_s=float(cfg.get("power_debounce_s", 120)))


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


def collect(cfg: dict) -> list[Finding]:
    """Every check of one tick → its findings (I/O here; decisions are pure)."""
    from app.core.config import settings

    debounce = float(cfg.get("debounce_s", getattr(settings, "alert_debounce_seconds", 90.0)))
    findings: list[Finding] = []
    items: list[dict] = []
    try:
        items = docker_containers(cfg["project"], cfg["sock"])
        findings += classify_containers(items, ignore_exited=cfg["ignore_exited"], debounce_s=debounce)
    except Exception as exc:  # noqa: BLE001
        _log("docker_query_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")
    if cfg["backend_url"]:
        # FIX133 D — two speeds: a hard outage pages after ready_debounce_s
        # (~60 s with the tick), a restart gets restart_debounce_s.
        code, body, err = probe_ready(cfg["backend_url"].rstrip("/") + "/ready")
        findings += classify_ready(code, body, err,
                                   debounce_s=float(cfg.get("ready_debounce_s", debounce)),
                                   restart_debounce_s=float(cfg.get("restart_debounce_s", debounce)),
                                   restarting=container_restarting(items, "backend"))
        if code == 200 and body:
            findings += classify_capacity(body, queue_min_waiting=int(cfg.get("queue_min_waiting", 0)),
                                          queue_oldest_s=float(cfg.get("queue_oldest_s", 60.0)),
                                          saturation_pct=float(cfg.get("saturation_pct", 90.0)),
                                          debounce_s=float(cfg.get("capacity_debounce_s", 180.0)))
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
                min_attempts=int(cfg.get("min_attempts", getattr(settings, "alert_page_min_attempts", 3))),
            )
        except Exception as exc:  # noqa: BLE001
            _log("turn_query_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")
        if cfg.get("turn_stuck_min"):
            try:
                n, oldest = stuck_turns(chat_dsn, int(cfg["turn_stuck_min"]), int(cfg.get("turn_stuck_lookback_h", 24)))
                findings += classify_stuck_turns(n, oldest, after_min=int(cfg["turn_stuck_min"]))
            except Exception as exc:  # noqa: BLE001
                _log("stuck_turn_query_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")
    if cfg.get("backup_dir"):
        if os.path.isdir(cfg["backup_dir"]):
            success, failure, newest = backup_records(cfg["backup_dir"])
            findings += classify_backup(success, failure, now=time.time(),
                                        max_age_h=float(cfg.get("backup_max_age_h", 26)), newest_proven=newest,
                                        offsite_max_age_h=float(cfg.get("offsite_max_age_h", 48)),
                                        newest_offsite=newest_offsite_copy(cfg["backup_dir"]),
                                        offsite_remind=bool(cfg.get("offsite_remind", False)))
        else:
            _log("backup_dir_missing", path=cfg["backup_dir"],
                 note="the backup folder is not mounted — no-fresh-backup alerts are off")
    findings += smoke_findings(cfg)
    findings += power_findings(cfg)
    return findings


def tick(state: AlertState, cfg: dict) -> list[Decision]:
    findings = collect(cfg)
    decisions = state.step(findings)
    _log("tick", findings=len(findings), open=[f.key for f in findings],
         paged=[d.group for d in decisions if d.paged])
    return decisions


def log_email_channel_state() -> str | None:
    """FIX134 C3 — one line at start when ALERT_EMAIL_TO is set but this
    process cannot send mail (EMAIL_DELIVERY=disabled, or no Resend key):
    the email channel is off, so the pager uses the other channels and
    never schedules an email retry. Returns the reason, None when on."""
    from app.core.config import settings
    from app.services.alert_channels import email_channel_off_reason

    if not (settings.alert_email_to or "").strip():
        return None
    reason = email_channel_off_reason()
    if reason:
        _log("alert_email_channel_off", reason=reason,
             note="ALERT_EMAIL_TO is set but mail cannot leave this process (EMAIL_DELIVERY / RESEND_API_KEY): "
                  "the email channel is off; alerts go to the other configured channels")
    return reason


def main() -> int:
    from app.core.config import settings

    interval = float(_env("OPS_WATCH_INTERVAL", "30"))
    cfg = {
        "project": _env("OPS_WATCH_PROJECT", "ai-gateway"),
        "sock": _env("OPS_WATCH_DOCKER_SOCK", "/var/run/docker.sock"),
        "interval": interval,
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
        # FIX131 — replies stuck "streaming", and backup freshness.
        "turn_stuck_min": int(_env("OPS_WATCH_TURN_STUCK_MIN", "15")),
        "turn_stuck_lookback_h": int(_env("OPS_WATCH_TURN_STUCK_LOOKBACK_H", "24")),
        "backup_dir": _env("OPS_WATCH_BACKUP_DIR", ""),
        "backup_max_age_h": float(_env("OPS_WATCH_BACKUP_MAX_AGE_H", "26")),
        # FIX133 C/D — the off-site copy's freshness, the two paging speeds
        # (hard outage vs a restart), the queue and saturation rules.
        "offsite_max_age_h": float(_env("OPS_WATCH_OFFSITE_MAX_AGE_H", "48")),
        "offsite_remind": _env("OPS_WATCH_OFFSITE_REMIND", "") in ("1", "true", "yes"),
        "ready_debounce_s": float(_env("OPS_WATCH_READY_DEBOUNCE_S", "45")),
        "restart_debounce_s": float(_env("OPS_WATCH_RESTART_DEBOUNCE_S", "150")),
        "queue_min_waiting": int(_env("OPS_WATCH_QUEUE_MIN_WAITING", "0")),
        "queue_oldest_s": float(_env("OPS_WATCH_QUEUE_OLDEST_S", "60")),
        "saturation_pct": float(_env("OPS_WATCH_SATURATION_PCT", "90")),
        "capacity_debounce_s": float(_env("OPS_WATCH_CAPACITY_DEBOUNCE_S", "180")),
        # FIX134 F — the daily smoke suite's result, beside the backups by default
        # (scripts/smoke-daily.ps1 writes <BACKUP_DIR>/smoke_last.json); "" = off.
        "smoke_file": _env("OPS_WATCH_SMOKE_FILE",
                           os.path.join(_env("OPS_WATCH_BACKUP_DIR", ""), "smoke_last.json") if _env("OPS_WATCH_BACKUP_DIR", "") else ""),
        "smoke_max_age_h": float(_env("OPS_WATCH_SMOKE_MAX_AGE_H", "26")),
        # The host's power, beside the backups too (scripts/ops/power-watch.ps1 writes
        # <BACKUP_DIR>/power_last.json once a minute); "" = off. On battery it is said after
        # POWER_DEBOUNCE_S; at POWER_PAGE_PCT % or POWER_PAGE_MIN minutes left it pages.
        "power_file": _env("OPS_WATCH_POWER_FILE",
                           os.path.join(_env("OPS_WATCH_BACKUP_DIR", ""), "power_last.json") if _env("OPS_WATCH_BACKUP_DIR", "") else ""),
        "power_page_pct": float(_env("OPS_WATCH_POWER_PAGE_PCT", "40")),
        "power_page_min": float(_env("OPS_WATCH_POWER_PAGE_MIN", "60")),
        "power_max_age_s": float(_env("OPS_WATCH_POWER_MAX_AGE_S", "600")),
        "power_debounce_s": float(_env("OPS_WATCH_POWER_DEBOUNCE_S", "120")),
        # FIX131 — the pager's knobs (settings: ALERT_*).
        "debounce_s": float(getattr(settings, "alert_debounce_seconds", 90.0)),
        "min_attempts": int(getattr(settings, "alert_page_min_attempts", 3)),
    }
    state = AlertState(ttl_s=3 * interval)
    store_ok = "memory"
    try:
        state.store.primary._r().ping()
        store_ok = "redis"
    except Exception as exc:  # noqa: BLE001
        _log("pager_store_warning", error=f"{type(exc).__name__}: {str(exc)[:120]}",
             note="Redis did not answer: incidents are kept in this process (and the state file) until it does")
    _log("ops_watch_start", channels=configured_channels(), pager_store=store_ok,
         **{k: (v if k != "pg_dsns" else [n for n, _ in v]) for k, v in cfg.items() if k != "sock"})
    log_email_channel_state()
    if os.environ.get("OPS_WATCH_COOLDOWN"):
        _log("ops_watch_note", note="OPS_WATCH_COOLDOWN is retired (FIX131): the pager re-pages on escalation "
                                    "or after ALERT_REMINDER_SECONDS / ALERT_REMINDER_HIGH_SECONDS")
    if not configured_channels():
        _log("ops_watch_warning", note="no alert channel configured (ALERT_NTFY_URL / ALERT_EMAIL_TO / ALERT_WEBHOOK_URL) — findings are logged only")
    state_file = _env("OPS_WATCH_STATE_FILE", "")
    if state_file:
        state.load(state_file)

    # AM1c — Agent Market's alerts (Prometheus, channel="agent-market") page
    # its own topic through a pager of their own; never the core channels.
    from app.ops import agent_market_alerts as ama
    from app.services.alert_channels import is_test_environment

    prom_url = _env("OPS_WATCH_PROMETHEUS_URL", "")
    agent_topic = (getattr(settings, "alert_test_ntfy_url", "") if is_test_environment()
                   else getattr(settings, "agent_market_ntfy_url", "")) or ""
    agent = None
    if prom_url and agent_topic.strip():
        agent = ama.agent_state(ttl_s=3 * interval)
        if state_file:
            agent.load(state_file + ".agent-market")
    _log("agent_market_pager", on=agent is not None, prometheus=prom_url or None,
         note=None if agent is not None else "AGENT_MARKET_NTFY_URL or OPS_WATCH_PROMETHEUS_URL is not set: "
                                             "Agent Market alerts are on the dashboard only")

    def _tick() -> None:
        try:
            tick(state, cfg)
            if state_file:
                state.save(state_file)
        finally:
            if agent is not None:
                # AM24: with the smoke's own record, whose agent checks the core pager leaves to this one
                ama.agent_tick(agent, prom_url, smoke_record(cfg["smoke_file"]) if cfg.get("smoke_file") else None)
                if state_file:
                    agent.save(state_file + ".agent-market")

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
