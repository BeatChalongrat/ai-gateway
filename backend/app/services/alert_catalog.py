"""FIX131 D — what an alert says, and which incident it belongs to.

NIGHT3: one stuck transaction produced FOUR pushes (the backend rule, the
ops watcher's "/ready answered 503: stuck_transaction", and "transaction
stuck … on gateway" / "… on chat"); a planned backend reload paged at top
priority; one failed turn in a quiet 5 min paged twice; the rule body dumped
"metric_value: 68.707031 / threshold: 60.0". This module is the one place
that decides, for the backend rules AND the ops watcher:

* the **group** (incident key) — related signals map to the same group, so
  the pager (``alert_pager``) sends one page per incident:

  ==========================  ===============================================
  ``service:<service>``       container unhealthy / restarting / exited;
                              for the backend also "/ready does not answer"
                              and "/ready 503" for any reason but a stuck
                              transaction
  ``db:<db>:stuck``           backend rule ``stuck_transaction`` (gateway),
                              ops-watch ``pg:<db>:stuck``, and "/ready 503"
                              whose only reason is ``stuck_transaction``
  ``turns:none_succeeded``    backend rule ``no_successful_turn`` + ops-watch
  ``turns:error_rate``        backend rule ``error_rate`` + ops-watch
  ``turns:stuck``             ops-watch: replies left "streaming" > 15 min
  ``backup`` / ``backup:offsite``  ops-watch, from the backup records
  ``disk:<path>``, ``memory`` ops-watch
  ``rule:<id>``               any other (operator-defined) rule
  ==========================  ===============================================

* the **severity by impact** — ``critical`` (ntfy 5) only when users cannot
  use the system: a user-facing service down or not ready past the debounce,
  no successful turn with a meaningful number of attempts. ``warning``
  (ntfy 4) when it is degraded: error rate, stuck transaction, disk / memory
  high, backups. ``none`` = log only (one failed turn in a quiet window).

* the **words** — the title says what is wrong for users; the body says what
  still works, what to do and where to look. Raw numbers stay in the logs
  and ``alert_events``.
"""

from __future__ import annotations

CRITICAL = "critical"   # users cannot use the system — ntfy priority 5
HIGH = "warning"        # degraded — ntfy priority 4
LOW = "low"             # "recovered" notes — ntfy priority 2
NONE = "none"           # log only, never sent

_RANK = {NONE: 0, LOW: 1, "info": 2, HIGH: 3, CRITICAL: 4}


def rank(severity: str) -> int:
    return _RANK.get(severity, 2)


# Services a user needs to sign in and chat. Anything else being down is
# degraded, not an outage.
USER_FACING_SERVICES = frozenset({
    "backend", "gateway", "chat-edge-api", "web-chat",
    "postgres", "postgres-webchat", "redis", "redis-webchat",
})


def service_group(service: str) -> str:
    return f"service:{service}"


def stuck_group(db: str) -> str:
    return f"db:{db}:stuck"


GROUP_TURNS_NONE = "turns:none_succeeded"
GROUP_TURNS_ERROR_RATE = "turns:error_rate"
GROUP_TURNS_STUCK = "turns:stuck"
GROUP_BACKUP = "backup"
GROUP_BACKUP_OFFSITE = "backup:offsite"
# FIX133 C/D — the off-site copy is stale or was never set up; the turn
# queue backs up; the backend workers are saturated.
GROUP_BACKUP_OFFSITE_STALE = "backup:offsite_stale"
GROUP_BACKUP_OFFSITE_MISSING = "backup:offsite_missing"
GROUP_QUEUE = "capacity:queue"
GROUP_SATURATED = "capacity:saturated"
# FIX134 F — the daily smoke suite (scripts/smoke.sh): red, or not running.
GROUP_SMOKE = "smoke"
# The host's power: on battery, battery low, or the reading has stopped.
GROUP_POWER = "power"


def _dur(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s // 3600} h {(s % 3600) // 60} min"


# ---------------------------------------------------------------------------
# Containers and readiness
# ---------------------------------------------------------------------------

def container_text(service: str, what: str, name: str, status: str) -> tuple[str, str, str]:
    """(severity, title, body) for a container that is unhealthy /
    restarting / dead / has exited."""
    if service in USER_FACING_SERVICES:
        return CRITICAL, f"Chat is down: {service} {what}", (
            f"Users cannot sign in or chat while {service} is not running. "
            f"Docker says: {name}: {status}. autoheal restarts an unhealthy container by itself; "
            f"if it keeps failing, read its log: docker logs {name} --tail 100"
        )
    return HIGH, f"{service} {what}", (
        f"Chat still works; {service} is not running as it should. "
        f"Docker says: {name}: {status}. Read its log: docker logs {name} --tail 100"
    )


def ready_down_text(error: str) -> tuple[str, str, str]:
    return CRITICAL, "Chat is down: the backend does not answer", (
        "Users cannot sign in or chat: the backend's readiness probe got no answer "
        f"({error}). autoheal restarts it when its healthcheck stays red; if it keeps "
        "failing: docker logs ai-gateway-backend-1 --tail 100"
    )


def ready_not_ready_text(status_code: int, failures: list[str]) -> tuple[str, str, str]:
    why = ", ".join(failures) or "unknown"
    return CRITICAL, "Chat is down: the backend is not ready", (
        f"Users cannot chat: the backend's /ready answered {status_code} ({why}). "
        "autoheal restarts it when its healthcheck stays red. Look: "
        "curl -s http://localhost:8081/ready and docker logs ai-gateway-backend-1 --tail 100"
    )


# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------

def stuck_state(session: dict | None) -> str:
    """``idle`` / ``waiting on a lock`` / ``stuck`` (pg_activity.describe_state)."""
    if not session:
        return "stuck"
    if session.get("state") == "idle in transaction":
        return "idle"
    if "Lock" in (session.get("wait"), session.get("wait_event_type")):
        return "waiting on a lock"
    return "stuck"


def stuck_tx_text(db: str, age_s: float, limit_s: float, who: str,
                  what: str = "idle or blocked") -> tuple[str, str, str]:
    return HIGH, f"Chat may hang: a database transaction is stuck ({db}, {_dur(age_s)})", (
        f"A transaction on the {db} database has been {what} for {_dur(age_s)} "
        f"(limit {_dur(limit_s)}): {who}. Requests that need the rows it holds wait and fail. "
        "The server ends an idle transaction at 120 s; the backend's /ready turns red and "
        "autoheal restarts it. Who holds it (pid, application, query head): the "
        "pg_stuck_transaction log line — docker logs ai-gateway-ops-watch-1 | grep pg_stuck_transaction"
    )


def ready_stuck_text(status_code: int) -> tuple[str, str, str]:
    """/ready red for a stuck transaction only — the stuck transaction's
    incident, seen through the backend's readiness."""
    return HIGH, "Chat may hang: a database transaction is stuck (gateway)", (
        f"The backend's /ready answered {status_code} (stuck_transaction): a transaction on the "
        "gateway database holds rows other requests need. The server ends an idle transaction at "
        "120 s; autoheal restarts the backend when its healthcheck stays red. Who holds it: the "
        "pg_stuck_transaction log line — docker logs ai-gateway-ops-watch-1 | grep pg_stuck_transaction"
    )


# ---------------------------------------------------------------------------
# Turns (what users actually got)
# ---------------------------------------------------------------------------

def turns_none_text(window_min: int, attempts: int, failed: int, min_attempts: int,
                    unit: str = "turn") -> tuple[str, str, str]:
    severity = CRITICAL if attempts >= min_attempts else NONE
    return severity, f"Chat is failing: no reply succeeded in {window_min} min ({attempts} tries)", (
        f"{attempts} {unit}(s) attempted in the last {window_min} min, none completed ({failed} failed). "
        "Users get errors instead of answers. Look: admin → Requests (status, error_code), "
        "docker logs ai-gateway-chat-edge-api --tail 100, and the model provider keys."
    )


def turns_error_rate_text(window_min: int, pct: float, failed: int, total: int,
                          limit_pct: float, unit: str = "turn") -> tuple[str, str, str]:
    return HIGH, f"Chat is degraded: {pct:.0f} % of replies failed in {window_min} min", (
        f"{failed} of {total} {unit}s failed in the last {window_min} min (limit {limit_pct:g} %). "
        "Most users still get answers. Look: admin → Requests filtered by status for the "
        "error_code, and whether one model or provider is failing (admin → Fallback)."
    )


def turns_stuck_text(count: int, oldest_s: float, after_min: int) -> tuple[str, str, str]:
    return HIGH, f"Chat: {count} repl{'y' if count == 1 else 'ies'} stuck 'typing' for over {after_min} min", (
        f"{count} assistant repl{'y is' if count == 1 else 'ies are'} still marked streaming, the oldest "
        f"for {_dur(oldest_s)}. Users see a reply that never finishes. The orphaned-turn sweep "
        "should have closed them: docker logs ai-gateway-chat-edge-api | grep -i sweep"
    )


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------

def disk_text(path: str, pct: float, free_gib: float, total_gib: float, limit: float) -> tuple[str, str, str]:
    return HIGH, f"Server disk is {pct:.0f} % full", (
        f"{free_gib:.1f} GiB free of {total_gib:.1f} GiB on {path} (limit {limit:.0f} %). "
        "When it fills, the databases stop writing and chat fails. Look: docker system df; "
        "remove old images (docker image prune) and check the backup folder's size."
    )


def memory_text(pct: float, avail_gib: float, total_gib: float, limit: float) -> tuple[str, str, str]:
    return HIGH, f"Server memory is {pct:.0f} % used", (
        f"{avail_gib:.2f} GiB available of {total_gib:.2f} GiB (limit {limit:.0f} %). "
        "Services may be killed or slow down. Look: docker stats --no-stream"
    )


# ---------------------------------------------------------------------------
# Backups (from infra/backup's last_success.json / last_failure.json)
# ---------------------------------------------------------------------------

_BACKUP_RUN = "docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup now"


def backup_stale_text(age_s: float | None, newest: str, max_age_h: float) -> tuple[str, str, str]:
    if age_s is None:
        return HIGH, "Backups: no proven backup found", (
            "There is no backup whose restore drill passed in the backup folder. If the PC or "
            f"a database dies now, data is lost. Look: docker logs ai-gateway-backup; run one: {_BACKUP_RUN}"
        )
    return HIGH, f"Backups: no fresh backup for {_dur(age_s)}", (
        f"The newest proven backup is {newest} ({_dur(age_s)} old, limit {max_age_h:g} h). "
        "Chat works, but anything written since then would be lost with the database. "
        "The backup container may be stopped or failing: docker ps -a --filter name=backup; "
        f"docker logs ai-gateway-backup; run one: {_BACKUP_RUN}"
    )


def backup_failed_text(status: str, attempts: int, next_retry: str, newest: str) -> tuple[str, str, str]:
    what = "could not be restored in the drill" if status == "DRILL FAILED" else "did not complete"
    return HIGH, f"Backups are failing: the last backup {what}", (
        f"Attempt {attempts} failed ({status}); the next automatic try is at {next_retry}. "
        f"The newest proven backup is {newest}. Look: /backups/last_failure.json and "
        f"docker logs ai-gateway-backup; run one: {_BACKUP_RUN}"
    )


def backup_offsite_text(backup_id: str) -> tuple[str, str, str]:
    return HIGH, "Off-site backup copy failed (the local backup is fine)", (
        f"Backup {backup_id} was written and proven here, but its copy to the off-site bucket "
        "failed, so a dead PC would lose it. Look: offsite.json in that backup folder and "
        "docker logs ai-gateway-backup; check the bucket keys (docs/ops/backups.md, Off-site copy)."
    )


def backup_offsite_stale_text(age_s: float | None, newest: str, max_age_h: float) -> tuple[str, str, str]:
    """FIX133 C — the newest proven off-site copy is older than max_age_h."""
    if age_s is None:
        how = "no proven off-site copy exists yet"
    else:
        how = f"the newest proven off-site copy is {age_s / 3600:.0f} h old"
    return HIGH, "Off-site backup copy is stale", (
        f"{how} (limit {max_age_h:.0f} h; newest local backup {newest}). A dead PC would lose "
        "everything since then. Look: docker logs ai-gateway-backup, offsite.json in the newest "
        "backup folder, and whether the off-site folder / bucket is reachable "
        "(docs/ops/backups.md, Off-site copy)."
    )


def backup_offsite_missing_text() -> tuple[str, str, str]:
    """FIX133 C — backups exist, but nothing leaves this PC."""
    return LOW, "No off-site backup copy is configured", (
        "Backups are written and proven on this PC only. Set BACKUP_OFFSITE_REPOSITORY (a synced "
        "folder or a bucket) and BACKUP_OFFSITE_PASSWORD in .env, then run "
        "`backup offsite-check` (docs/ops/backups.md, Off-site copy)."
    )


def queue_backlog_text(waiting: int, oldest_s: float, active: int, max_active: int) -> tuple[str, str, str]:
    """FIX133 D — the turn queue has a line that is not draining."""
    return HIGH, f"Chat is slow: {waiting} turns are waiting in the queue", (
        f"{active} of {max_active} generation slots are busy and {waiting} turns wait, the oldest "
        f"for {oldest_s:.0f} s; users see \"waiting in queue\". If this lasts, the box is at its "
        "ceiling: docs/ops/scaling.md (scale on queue depth, not CPU). Look: "
        "curl -s http://localhost:8081/ready | jq .checks.turn_queue"
    )


def saturated_text(inflight: int, capacity: int, workers: int) -> tuple[str, str, str]:
    """FIX133 D — every backend worker is near its admission cap."""
    return HIGH, "Backend workers are saturated", (
        f"{inflight} chat requests are in flight across {workers} workers (cap {capacity}); the next "
        "ones are refused with 503 server_busy. Either a stuck request holds slots (see "
        "checks.inflight.stuck) or the box needs more workers / a second machine "
        "(docs/ops/scaling.md). Look: curl -s http://localhost:8081/ready | jq .checks.container"
    )


# ---------------------------------------------------------------------------
# The smoke suite (FIX134 F: scripts/smoke.sh, daily via scripts/smoke-daily.ps1,
# result in <BACKUP_DIR>/smoke_last.json)
# ---------------------------------------------------------------------------

_SMOKE_RUN = "SMOKE_TARGET=live bash scripts/smoke.sh"


def smoke_failed_text(failed: list[str], total: int, at: str, target: str = "live") -> tuple[str, str, str]:
    names = ", ".join(failed[:8]) + (f" (+{len(failed) - 8} more)" if len(failed) > 8 else "")
    return HIGH, f"Smoke suite is red: {len(failed)} of {total} checks failed", (
        f"The daily smoke run of {at} on {target} failed: {names or 'see the result file'}. "
        "An entry point users rely on (a page, sign-in, the API, a background job, the desktop) "
        "is not answering as it should. Look: smoke_last.json in the backup folder (each check's "
        f"detail) and scripts/smoke/README.md; re-run: {_SMOKE_RUN}"
    )


def smoke_stale_text(age_s: float | None, max_age_h: float, why: str = "") -> tuple[str, str, str]:
    """The suite has not produced a result within ``max_age_h`` — the scheduled task is off,
    failing before it writes, or the file is unreadable (``why``)."""
    how = f"the newest smoke result is {age_s / 3600:.0f} h old" if age_s is not None else f"no smoke result found for {why or 'unknown reasons'}"
    return HIGH, "Smoke suite has not run", (
        f"{how} (limit {max_age_h:.0f} h). Nothing is checking the entry points daily, so a "
        "broken sign-in or page would go unnoticed. The scheduled task \"SRAN smoke daily\" "
        "(06:30, scripts/smoke-daily.ps1) may be off or failing before it writes smoke_last.json. "
        f"Look: schtasks /Query /TN \"SRAN smoke daily\" and smoke_last.log in the backup folder; run: {_SMOKE_RUN}"
    )


# ---------------------------------------------------------------------------
# The host's power (the host is a laptop: scripts/ops/power-watch.ps1 writes
# <BACKUP_DIR>/power_last.json once a minute; the watcher's container cannot
# see the battery itself)
# ---------------------------------------------------------------------------

_POWER_TASK = "the task \"SRAN power watch\" (scripts/ops/power-watch.ps1)"


def _time_left(minutes: float | None) -> str:
    return f", about {minutes:.0f} min at this rate" if minutes is not None and minutes >= 0 else ""


def power_on_battery_text(pct: float | None, minutes: float | None, page_pct: float,
                          page_minutes: float) -> tuple[str, str, str]:
    """The host runs on its battery and there is still time: said once, not a 3 a.m. page."""
    charge = f"{pct:.0f}% left" if pct is not None else "charge unknown"
    return HIGH, f"The host is on battery: {charge}", (
        f"The machine every service runs on is not on mains power ({charge}{_time_left(minutes)}). "
        "Everything still works. When the battery is empty the machine stops, and with it the chat, "
        "the gateway, the backups and this pager — nobody would be told. Plug it in. "
        f"A second, louder page comes at {page_pct:.0f}% or {page_minutes:.0f} min left, whichever is first."
    )


def power_low_text(pct: float | None, minutes: float | None) -> tuple[str, str, str]:
    """On battery and past the threshold: plug in while there is still time."""
    charge = f"{pct:.0f}%" if pct is not None else "low"
    return CRITICAL, f"Battery at {charge} — plug the host in now", (
        f"The machine every service runs on is on battery at {charge}{_time_left(minutes)}. "
        "When it is empty everything stops at once — the chat, the gateway, the backups — and this "
        "pager stops with it: this is the last warning there can be. Plug the machine in now."
    )


def power_stale_text(age_s: float, max_age_s: float) -> tuple[str, str, str]:
    """A reading was there and has stopped coming: the watcher can no longer see the host's power."""
    return HIGH, "Power reading has stopped", (
        f"The newest reading of the host's power is {_dur(age_s)} old (limit {_dur(max_age_s)}). "
        "If the machine went on battery now, nobody would be told. "
        f"{_POWER_TASK[0].upper() + _POWER_TASK[1:]} writes it once a minute and may have stopped. "
        "Look: schtasks /Query /TN \"SRAN power watch\"; start it again: "
        "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\ops\\power-register-task.ps1"
    )


# ---------------------------------------------------------------------------
# Backend alert rules → (group, severity, title, body)
# ---------------------------------------------------------------------------

_RULE_GROUPS = {
    "no_successful_turn": GROUP_TURNS_NONE,
    "error_rate": GROUP_TURNS_ERROR_RATE,
    # The backend's pg_stat_activity sampler watches its own (gateway) DB.
    "stuck_transaction": stuck_group("gateway"),
}


def group_for_rule(metric: str, rule_id: str) -> str:
    return _RULE_GROUPS.get(metric, f"rule:{rule_id}")


def rule_signal_parts(metric: str, rule_id: str, rule_name: str, value: float, threshold: float,
                      window_min: int, *, min_attempts: int, oldest: dict | None = None,
                      fallback_message: str = "") -> tuple[str, str, str, str]:
    group = group_for_rule(metric, rule_id)
    if metric == "no_successful_turn":
        n = int(value)
        sev, title, body = turns_none_text(window_min, n, n, min_attempts, unit="request")
        return group, sev, title, body
    if metric == "error_rate":
        sev, title, body = HIGH, f"Chat is degraded: {value:.0f} % of requests failed in {window_min} min", (
            f"{value:.1f} % of gateway requests failed in the last {window_min} min "
            f"(limit {threshold:g} %). Most users still get answers. Look: admin → Requests "
            "filtered by status for the error_code, and admin → Fallback."
        )
        return group, sev, title, body
    if metric == "stuck_transaction":
        who = "unknown session"
        if oldest:
            who = (f"pid {oldest.get('pid')}, {oldest.get('state') or 'stuck'}, "
                   f"app {oldest.get('application_name') or 'unknown'}")
        sev, title, body = stuck_tx_text("gateway", value, threshold, who, what=stuck_state(oldest))
        return group, sev, title, body
    # An operator-defined rule: degraded at most — a DLP spike, a budget or a
    # fallback spike is worth a look, not a 3 a.m. phone call.
    return group, HIGH, rule_name[:100], (
        fallback_message or f"{rule_name}: {metric} reached {value:g} (limit {threshold:g})"
    ) + " — admin → Alerts → events for the history."
