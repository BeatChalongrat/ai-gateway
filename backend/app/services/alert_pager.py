"""FIX131 D — the pager: one incident = one page (+ one "recovered").

Before FIX131 every sender decided for itself: the backend's alert rules
(cooldown = the rule window), the ops watcher (its own 10-minute cooldown,
per finding) and the backup job. NIGHT3 counted four pushes for one stuck
transaction, a p5 page for a planned reload and two pages for one failed
turn. Now the backend rules and the ops watcher both hand their findings
to this pager as :class:`Signal` s, and the pager decides — with state
shared through Redis (the backend's Redis, db 0; ops-watch reaches it on the
same compose network), falling back to this process's memory when Redis
does not answer (logged; worst case each sender pages once).

The model (PagerDuty's dedup key / Alertmanager's group, reduced to what
this system needs):

* A **signal** belongs to an incident ``group`` (``alert_catalog``) and is
  reported by a ``source`` (``rule:<id>``, ``ops-watch:<finding key>``).
  Each sighting is valid ``ttl_s``; a sender that stops reporting drops out.
* The **incident** opens at its first sighting. It pages once
  (``page``) when its severity is above ``none``, it has lasted
  ``debounce_s`` (container / readiness findings: 90 s, so a normal restart
  heals first) and no maintenance window is open. After that: silence
  (``grouped``), except a new page on **escalation** (a higher severity than
  the one paged) or a **reminder** after ``alert_reminder_seconds`` (urgent,
  4 h) / ``alert_reminder_high_seconds`` (high, 24 h).
* It **closes** when every source has cleared it (or its sightings expired):
  one "recovered" note at low priority if it was paged, a log line if not
  (healed within the debounce, held by maintenance, log-only).
* A **maintenance window** (``python -m app.ops.maintenance start 15 "deploy"``,
  a Redis key with a TTL) holds every page: findings are logged only. An
  incident still open when the window ends pages normally, saying it began
  during maintenance.
* A signal whose sender already paged by itself (the backup container's
  transition page) is recorded as paged (``notified_elsewhere``), so the
  incident is not paged twice but still gets its reminder and "recovered".

Every decision is logged (``pager_decision``); a repeat of the same
non-paging decision for an open incident (grouped, hold, maintenance,
log_only) is not.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import structlog

from app.services import alert_catalog
from app.services.alert_catalog import CRITICAL, HIGH, LOW, NONE, rank

logger = structlog.get_logger(__name__)

INCIDENT_PREFIX = "sran:alert:incident:"
LOCK_PREFIX = "sran:alert:lock:"
MAINTENANCE_KEY = "sran:ops:maintenance"
# An incident record outlives any sensible incident; Redis runs allkeys-lru,
# so a TTL also keeps an orphan from living forever.
_RECORD_TTL_S = 14 * 24 * 3600

PAGE_ACTIONS = ("page", "escalate", "remind")


@dataclass(frozen=True)
class Signal:
    group: str
    source: str
    severity: str            # critical | warning | none (alert_catalog)
    title: str               # what is wrong for users
    body: str                # what to do / where to look
    debounce_s: float = 0.0
    ttl_s: float = 300.0
    details: dict = field(default_factory=dict)
    notified_elsewhere: bool = False


@dataclass
class Decision:
    action: str   # page | escalate | remind | grouped | hold | maintenance | log_only | recovered | expired | closed_unpaged | open
    group: str
    severity: str = NONE
    reason: str = ""
    delivered: dict[str, bool] | None = None

    @property
    def paged(self) -> bool:
        return self.action in PAGE_ACTIONS


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

class MemoryStore:
    """This process's memory (tests, and the fallback when Redis is down).
    ``snapshot`` / ``restore`` let ops-watch keep it across restarts."""

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._records: dict[str, dict] = {}
        self._maintenance: tuple[dict, float] | None = None
        self._lock = threading.RLock()

    @contextlib.contextmanager
    def lock(self, group: str) -> Iterator[bool]:
        with self._lock:
            yield True

    def load(self, group: str) -> dict | None:
        rec = self._records.get(group)
        return json.loads(json.dumps(rec)) if rec is not None else None

    def save(self, group: str, rec: dict) -> None:
        self._records[group] = json.loads(json.dumps(rec))

    def delete(self, group: str) -> bool:
        return self._records.pop(group, None) is not None

    def groups(self) -> list[str]:
        return list(self._records)

    def maintenance(self) -> dict | None:
        if self._maintenance is None:
            return None
        rec, until = self._maintenance
        if self._now() >= until:
            self._maintenance = None
            return None
        return dict(rec)

    def set_maintenance(self, rec: dict, ttl_s: float) -> None:
        self._maintenance = (dict(rec), self._now() + ttl_s)

    def clear_maintenance(self) -> bool:
        had = self.maintenance() is not None
        self._maintenance = None
        return had

    def snapshot(self) -> dict:
        return {"records": json.loads(json.dumps(self._records))}

    def restore(self, data: dict) -> None:
        recs = (data or {}).get("records") or {}
        if isinstance(recs, dict):
            self._records = {str(k): v for k, v in recs.items() if isinstance(v, dict)}


class RedisStore:
    """The shared store: one JSON record per open incident, a short lock per
    group so two senders never decide at the same time, and the maintenance
    window as a key with a TTL."""

    def __init__(self, url: str | None = None, *, client: Any = None) -> None:
        self._url = url
        self._client = client

    def _r(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                self._url, socket_timeout=1.0, socket_connect_timeout=1.0, decode_responses=True
            )
        return self._client

    @contextlib.contextmanager
    def lock(self, group: str) -> Iterator[bool]:
        r = self._r()
        key, token = LOCK_PREFIX + group, uuid.uuid4().hex
        deadline = time.monotonic() + 3.0
        got = False
        while True:
            if r.set(key, token, nx=True, px=10_000):
                got = True
                break
            if time.monotonic() >= deadline:
                break  # a duplicate page beats a lost one
            time.sleep(0.05)
        try:
            yield got
        finally:
            if got:
                with contextlib.suppress(Exception):
                    if r.get(key) == token:
                        r.delete(key)

    def load(self, group: str) -> dict | None:
        raw = self._r().get(INCIDENT_PREFIX + group)
        if not raw:
            return None
        try:
            rec = json.loads(raw)
        except ValueError:
            return None
        return rec if isinstance(rec, dict) else None

    def save(self, group: str, rec: dict) -> None:
        self._r().set(INCIDENT_PREFIX + group, json.dumps(rec, ensure_ascii=False), ex=_RECORD_TTL_S)

    def delete(self, group: str) -> bool:
        return bool(self._r().delete(INCIDENT_PREFIX + group))

    def groups(self) -> list[str]:
        return [k[len(INCIDENT_PREFIX):] for k in self._r().scan_iter(match=INCIDENT_PREFIX + "*", count=100)]

    def maintenance(self) -> dict | None:
        raw = self._r().get(MAINTENANCE_KEY)
        if not raw:
            return None
        try:
            rec = json.loads(raw)
        except ValueError:
            return {"reason": "maintenance"}
        if isinstance(rec, dict):
            with contextlib.suppress(Exception):
                ttl = self._r().ttl(MAINTENANCE_KEY)
                if isinstance(ttl, int) and ttl > 0:
                    rec["remaining_s"] = ttl
            return rec
        return {"reason": "maintenance"}

    def set_maintenance(self, rec: dict, ttl_s: float) -> None:
        self._r().set(MAINTENANCE_KEY, json.dumps(rec, ensure_ascii=False), ex=max(1, int(ttl_s)))

    def clear_maintenance(self) -> bool:
        return bool(self._r().delete(MAINTENANCE_KEY))


class FallbackStore:
    """Redis while it answers; this process's memory while it does not.
    After a failure Redis is skipped for ``retry_after_s`` so a dead Redis
    costs one timeout a minute, not one per call."""

    def __init__(self, primary: Any, secondary: MemoryStore, *, retry_after_s: float = 60.0,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.primary = primary
        self.secondary = secondary
        self._retry_after = retry_after_s
        self._now = now
        self._down_until = 0.0

    def _use_primary(self) -> bool:
        return self._now() >= self._down_until

    def _failed(self, op: str, exc: Exception) -> None:
        self._down_until = self._now() + self._retry_after
        logger.warning("pager_store_fallback", op=op, error=f"{type(exc).__name__}: {str(exc)[:120]}",
                       note="Redis did not answer; deciding from this process's memory for a minute")

    def _call(self, op: str, *args):
        if self._use_primary():
            try:
                return getattr(self.primary, op)(*args)
            except Exception as exc:  # noqa: BLE001 — the pager never dies on its store
                self._failed(op, exc)
        return getattr(self.secondary, op)(*args)

    @contextlib.contextmanager
    def lock(self, group: str) -> Iterator[bool]:
        if self._use_primary():
            cm = self.primary.lock(group)
            try:
                got = cm.__enter__()
            except Exception as exc:  # noqa: BLE001
                self._failed("lock", exc)
            else:
                try:
                    yield got
                finally:
                    with contextlib.suppress(Exception):
                        cm.__exit__(None, None, None)
                return
        with self.secondary.lock(group) as got:
            yield got

    def load(self, group: str) -> dict | None:
        return self._call("load", group)

    def save(self, group: str, rec: dict) -> None:
        self._call("save", group, rec)

    def delete(self, group: str) -> bool:
        return self._call("delete", group)

    def groups(self) -> list[str]:
        return self._call("groups")

    def maintenance(self) -> dict | None:
        return self._call("maintenance")

    def set_maintenance(self, rec: dict, ttl_s: float) -> None:
        self._call("set_maintenance", rec, ttl_s)

    def clear_maintenance(self) -> bool:
        return self._call("clear_maintenance")

    def snapshot(self) -> dict:
        return self.secondary.snapshot()

    def restore(self, data: dict) -> None:
        self.secondary.restore(data)


# ---------------------------------------------------------------------------
# The pager
# ---------------------------------------------------------------------------

def _dur(seconds: float) -> str:
    return alert_catalog._dur(seconds)


def _default_send(n):
    from app.services.alert_channels import deliver

    return deliver(n)


def _default_log(event: str, **kv) -> None:
    logger.info(event, **kv)


class Pager:
    def __init__(self, store: Any, *, send: Callable | None = None, now: Callable[[], float] = time.time,
                 log: Callable[..., None] | None = None, reminder_s: float | None = None,
                 reminder_high_s: float | None = None, sender: str = "backend") -> None:
        from app.core.config import settings

        self.store = store
        self._send = send or _default_send
        self._now = now
        self._log = log or _default_log
        self._sender = sender
        self._reminder = {
            CRITICAL: float(reminder_s if reminder_s is not None else getattr(settings, "alert_reminder_seconds", 14400.0)),
            HIGH: float(reminder_high_s if reminder_high_s is not None
                        else getattr(settings, "alert_reminder_high_seconds", 86400.0)),
        }

    # -- helpers -------------------------------------------------------------

    def maintenance(self) -> dict | None:
        try:
            return self.store.maintenance()
        except Exception:  # noqa: BLE001
            return None

    def _reminder_after(self, severity: str) -> float:
        return self._reminder.get(severity, self._reminder[HIGH])

    @staticmethod
    def _live_sources(rec: dict, now: float) -> dict[str, float]:
        return {s: float(exp) for s, exp in (rec.get("sources") or {}).items() if float(exp) > now}

    def _notification(self, rec: dict, action: str, now: float):
        from app.services.alert_channels import Notification

        title, body = rec["title"], rec["body"]
        if action == "remind":
            title = f"Still: {title}"
            body = f"Open for {_dur(now - rec['opened_at'])}. {body}"
        elif action == "escalate":
            body = f"{body} (Worse than first reported.)"
        if rec.get("maintenance_reason") and action == "page":
            body = (f"{body} (It began during maintenance — {rec['maintenance_reason']} — "
                    "and is still open after the window ended.)")
        return Notification(
            title=title, message=body, severity=rec["severity"], source=self._sender,
            tags=("incident",), details={"incident": rec["group"]},
        )

    def _deliver(self, n) -> dict[str, bool]:
        try:
            return self._send(n) or {}
        except Exception as exc:  # noqa: BLE001 — delivery never raises
            self._log("pager_delivery_failed", error=f"{type(exc).__name__}: {str(exc)[:120]}")
            return {}

    # -- API -----------------------------------------------------------------

    def fire(self, sig: Signal) -> Decision:
        """Report that ``sig`` is happening now; page if the incident calls for it."""
        now = self._now()
        note = None
        with self.store.lock(sig.group):
            rec = self.store.load(sig.group)
            new = rec is None
            if new:
                rec = {"group": sig.group, "opened_at": now, "sources": {}, "severity": NONE,
                       "title": sig.title, "body": sig.body, "notified_severity": None,
                       "notified_at": None, "pages": 0, "last_action": None}
            rec["sources"] = self._live_sources(rec, now)
            rec["sources"][sig.source] = now + float(sig.ttl_s)
            if rank(sig.severity) >= rank(rec["severity"]):
                rec.update(severity=sig.severity, title=sig.title, body=sig.body)
            maintenance = self.maintenance()
            notified = rec.get("notified_severity")

            if sig.notified_elsewhere and notified is None and rank(sig.severity) > rank(NONE):
                rec.update(notified_severity=sig.severity, notified_at=now)
                action, reason = "grouped", f"already paged by {sig.source}"
            elif rank(rec["severity"]) <= rank(NONE):
                action, reason = "log_only", "below the paging threshold"
            elif notified is None and now - float(rec["opened_at"]) < float(sig.debounce_s):
                action = "hold"
                reason = f"waiting {_dur(sig.debounce_s)} before paging (open {_dur(now - rec['opened_at'])})"
            elif maintenance is not None:
                action = "maintenance"
                reason = f"maintenance window: {maintenance.get('reason') or 'maintenance'}"
                rec["maintenance_reason"] = maintenance.get("reason") or "maintenance"
            elif notified is None:
                action, reason = "page", "new incident"
            elif rank(rec["severity"]) > rank(notified):
                action, reason = "escalate", f"{notified} → {rec['severity']}"
            elif now - float(rec.get("notified_at") or now) >= self._reminder_after(rec["severity"]):
                action, reason = "remind", f"still open after {_dur(now - rec['opened_at'])}"
            else:
                action, reason = "grouped", "already paged for this incident"

            prev_notified = (rec.get("notified_severity"), rec.get("notified_at"))
            if action in PAGE_ACTIONS:
                note = self._notification(rec, action, now)
                rec.update(notified_severity=rec["severity"], notified_at=now, pages=int(rec.get("pages") or 0) + 1)
            # A repeat of the same non-paging decision is not logged again
            # (every sender re-reports an open incident every 10–30 s).
            quiet = action in ("grouped", "hold", "maintenance", "log_only") and rec.get("last_action") == action
            rec["last_action"] = action
            self.store.save(sig.group, rec)

        decision = Decision(action=action, group=sig.group, severity=rec["severity"], reason=reason)
        if note is not None:
            decision.delivered = self._deliver(note)
            if decision.delivered and not any(decision.delivered.values()):
                # Every channel failed: not paged — try again on the next sighting.
                with self.store.lock(sig.group):
                    cur = self.store.load(sig.group)
                    if cur is not None and cur.get("notified_at") == now:
                        cur["notified_severity"], cur["notified_at"] = prev_notified
                        cur["pages"] = max(0, int(cur.get("pages") or 1) - 1)
                        self.store.save(sig.group, cur)
        if not quiet:
            self._log("pager_decision", action=action, group=sig.group, source=sig.source,
                      severity=rec["severity"], reason=reason, title=rec["title"],
                      **({"delivered": decision.delivered} if decision.delivered is not None else {}))
        return decision

    def clear(self, group: str, source: str) -> Decision | None:
        """``source`` no longer sees ``group``. Closes the incident when no
        other source still does."""
        now = self._now()
        if self.store.load(group) is None:
            return None  # the common case (nothing open): no lock taken
        with self.store.lock(group):
            rec = self.store.load(group)
            if rec is None:
                return None
            if source not in (rec.get("sources") or {}):
                # Only a sender that reported the incident can clear it (the
                # backend's gateway-level rule must not close what ops-watch
                # sees in the chat database); expired sightings close in sweep().
                return Decision(action="open", group=group, severity=rec.get("severity", NONE),
                                reason=f"{source} did not report it")
            live = self._live_sources(rec, now)
            live.pop(source, None)
            if live:
                rec["sources"] = live
                self.store.save(group, rec)
                return Decision(action="open", group=group, severity=rec.get("severity", NONE),
                                reason=f"still seen by {', '.join(sorted(live))}")
            self.store.delete(group)
        return self._closed(rec, now, expired=False)

    def sweep(self) -> list[Decision]:
        """Close incidents whose every sighting expired (their senders stopped
        reporting them)."""
        now = self._now()
        out: list[Decision] = []
        try:
            groups = self.store.groups()
        except Exception:  # noqa: BLE001
            return out
        for group in groups:
            with self.store.lock(group):
                rec = self.store.load(group)
                if rec is None:
                    continue
                live = self._live_sources(rec, now)
                if live:
                    continue
                self.store.delete(group)
            out.append(self._closed(rec, now, expired=True))
        return out

    def _closed(self, rec: dict, now: float, *, expired: bool) -> Decision:
        from app.services.alert_channels import Notification

        lasted = _dur(now - float(rec.get("opened_at") or now))
        if rec.get("notified_severity") is None or rank(rec.get("notified_severity")) <= rank(NONE):
            why = ("healed before the debounce ended" if rec.get("last_action") == "hold"
                   else "held by maintenance" if rec.get("last_action") == "maintenance"
                   else "log only")
            self._log("pager_decision", action="closed_unpaged", group=rec["group"], lasted=lasted,
                      reason=why, title=rec.get("title"))
            return Decision(action="closed_unpaged", group=rec["group"], severity=rec.get("severity", NONE), reason=why)
        if expired:
            title = f"No longer reported: {rec['title']}"
            body = (f"The check that raised it stopped reporting it after {lasted} — it cleared, "
                    "or the check itself stopped running (is ops-watch / the backend up?).")
            action = "expired"
        else:
            title = f"Recovered: {rec['title']}"
            body = f"Back to normal after {lasted}."
            action = "recovered"
        n = Notification(title=title, message=body, severity=LOW, source=self._sender,
                         tags=("incident", "white_check_mark"), details={"incident": rec["group"]})
        delivered = self._deliver(n)
        self._log("pager_decision", action=action, group=rec["group"], lasted=lasted,
                  title=rec.get("title"), delivered=delivered)
        return Decision(action=action, group=rec["group"], severity=LOW, reason=f"lasted {lasted}",
                        delivered=delivered)


# ---------------------------------------------------------------------------
# Process-wide store
# ---------------------------------------------------------------------------

_store: Any = None
_store_lock = threading.Lock()


def default_store() -> Any:
    """Redis (settings.redis_url) with this process's memory as the fallback."""
    global _store
    with _store_lock:
        if _store is None:
            from app.core.config import settings

            _store = FallbackStore(RedisStore(settings.redis_url), MemoryStore())
        return _store


def get_pager(**kw) -> Pager:
    return Pager(default_store(), **kw)


# ---------------------------------------------------------------------------
# Backend alert rules
# ---------------------------------------------------------------------------

def signal_for_alert(alert, *, rule_id: str | None, rule_name: str = "", ttl_s: float = 120.0) -> Signal:
    """The pager signal for a backend rule's ``Alert`` (alert_evaluator)."""
    from app.core.config import settings

    d = alert.details or {}
    metric = str(d.get("metric") or alert.alert_type.value)
    sessions = d.get("sessions") or []
    group, severity, title, body = alert_catalog.rule_signal_parts(
        metric, str(rule_id or d.get("rule_id") or metric), rule_name or metric,
        float(d.get("metric_value") or 0.0), float(d.get("threshold") or 0.0),
        int(d.get("window_minutes") or 0),
        min_attempts=int(getattr(settings, "alert_page_min_attempts", 3)),
        oldest=max(sessions, key=lambda s: float(s.get("xact_age_s") or 0.0)) if sessions else None,
        fallback_message=alert.message,
    )
    return Signal(group=group, source=f"rule:{rule_id or metric}", severity=severity,
                  title=title, body=body, ttl_s=ttl_s)
