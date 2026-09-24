"""FIX133 C/D — the pager's two speeds and its new rules.

Pure: the classifiers take the same inputs ops-watch collects (Docker's
container list, the /ready body, the backup folder's records).
"""

from __future__ import annotations

import json
import os
import time

from app.ops import watch
from app.services import alert_catalog as cat


def _container(service: str, state: str, status: str) -> dict:
    return {"Names": [f"/ai-gateway-{service}-1"], "State": state, "Status": status,
            "Labels": {"com.docker.compose.service": service, "com.docker.compose.project": "ai-gateway"}}


# ── D: two speeds ────────────────────────────────────────────────────────────


def test_a_restarting_container_is_recognised():
    assert watch.container_restarting([_container("backend", "restarting", "Restarting (1) 3 seconds ago")])
    assert watch.container_restarting([_container("backend", "running", "Up 5 seconds (health: starting)")])
    assert watch.container_restarting([_container("backend", "running", "Up 40 seconds")])
    assert watch.container_restarting([_container("backend", "running", "Up 2 minutes (healthy)")])
    assert not watch.container_restarting([_container("backend", "running", "Up 3 minutes (healthy)")])
    assert not watch.container_restarting([_container("backend", "running", "Up 3 hours (healthy)")])
    assert not watch.container_restarting([_container("gateway", "restarting", "Restarting")])  # another service
    assert not watch.container_restarting([])


def test_a_hard_outage_pages_fast_and_a_restart_waits():
    down = watch.classify_ready(None, None, "connection refused", debounce_s=45, restart_debounce_s=150,
                                restarting=False)
    assert len(down) == 1 and down[0].severity == cat.CRITICAL and down[0].debounce_s == 45
    restart = watch.classify_ready(None, None, "connection refused", debounce_s=45, restart_debounce_s=150,
                                   restarting=True)
    assert len(restart) == 1 and restart[0].debounce_s == 150
    # the old single-speed call still works
    legacy = watch.classify_ready(503, {"failures": ["db"]}, None, debounce_s=90)
    assert legacy[0].debounce_s == 90
    assert watch.classify_ready(200, {}, None, debounce_s=45, restart_debounce_s=150, restarting=True) == []


# ── D: queue backlog / saturation ─────────────────────────────────────────────


def _ready(*, waiting=0, oldest=0.0, active=0, max_active=12, workers=4, limit=64, inflight=0, enabled=True):
    return {"checks": {
        "turn_queue": {"enabled": enabled, "active": active, "waiting": waiting, "max_active": max_active,
                       "max_per_identity": 2, "max_waiting": 48, "oldest_wait_s": oldest},
        "inflight": {"count": inflight // max(1, workers), "limit": limit},
        "container": {"workers": workers, "inflight": inflight},
    }}


def test_a_short_line_is_quiet_and_a_long_one_is_a_finding():
    assert watch.classify_capacity(_ready(waiting=2, active=12)) == []
    f = watch.classify_capacity(_ready(waiting=4, active=12, oldest=12), debounce_s=180)
    assert [x.key for x in f] == ["capacity:queue"]
    assert f[0].severity == cat.HIGH and f[0].group == cat.GROUP_QUEUE and f[0].debounce_s == 180
    assert "4 turns" in f[0].title
    # a single waiter that has waited too long counts too
    f = watch.classify_capacity(_ready(waiting=1, active=12, oldest=75))
    assert [x.key for x in f] == ["capacity:queue"]
    # the threshold follows the cap: max_active 30 → 10 waiting
    assert watch.classify_capacity(_ready(waiting=9, max_active=30, active=30)) == []
    assert [x.key for x in watch.classify_capacity(_ready(waiting=10, max_active=30, active=30))] == ["capacity:queue"]
    # an explicit threshold wins
    assert [x.key for x in watch.classify_capacity(_ready(waiting=2, active=12), queue_min_waiting=2)] == ["capacity:queue"]
    # a disabled queue reports nothing
    assert watch.classify_capacity(_ready(waiting=40, enabled=False)) == []


def test_saturated_workers_are_a_finding():
    assert watch.classify_capacity(_ready(inflight=200)) == []  # 200 of 256
    f = watch.classify_capacity(_ready(inflight=240))
    assert [x.key for x in f] == ["capacity:saturated"] and f[0].group == cat.GROUP_SATURATED
    assert watch.classify_capacity({"checks": {}}) == []


# ── C: off-site copy freshness ───────────────────────────────────────────────


def _success(offsite_status="ok", age_h=1.0):
    now = time.time()
    return {"epoch": now - age_h * 3600, "backup_id": "20260924_172854", "offsite": {"status": offsite_status}}


def test_offsite_not_configured_is_a_low_reminder_once_backups_exist():
    f = watch.classify_backup(_success("not_configured"), None, now=time.time(), max_age_h=26,
                              offsite_max_age_h=48, newest_offsite=None, offsite_remind=True)
    assert [x.key for x in f] == ["backup:offsite_missing"] and f[0].severity == cat.LOW
    # the reminder is opt-in: off by default
    assert watch.classify_backup(_success("not_configured"), None, now=time.time(), max_age_h=26,
                                 offsite_max_age_h=48, newest_offsite=None) == []
    # without the FIX133 arguments the old behaviour stands (no off-site findings)
    assert watch.classify_backup(_success("not_configured"), None, now=time.time(), max_age_h=26) == []
    # a record from before the off-site copy existed says nothing about it
    old = {"epoch": time.time() - 3600, "backup_id": "20260901_031500"}
    assert watch.classify_backup(old, None, now=time.time(), max_age_h=26, offsite_max_age_h=48, newest_offsite=None) == []
    # no backup at all: the stale-backup rule speaks, not the off-site one
    f = watch.classify_backup(None, None, now=time.time(), max_age_h=26, offsite_max_age_h=48, newest_offsite=None,
                              offsite_remind=True)
    assert [x.key for x in f] == ["backup:stale"]


def test_offsite_copy_older_than_48h_pages_and_a_fresh_one_is_quiet():
    now = time.time()
    fresh = watch.classify_backup(_success("ok"), None, now=now, max_age_h=26, offsite_max_age_h=48,
                                  newest_offsite=(now - 20 * 3600, "20260924_172854"))
    assert fresh == []
    stale = watch.classify_backup(_success("ok"), None, now=now, max_age_h=26, offsite_max_age_h=48,
                                  newest_offsite=(now - 49 * 3600, "20260922_031500"))
    assert [x.key for x in stale] == ["backup:offsite_stale"] and stale[0].severity == cat.HIGH
    assert "49 h" in stale[0].message
    # configured (a copy once existed) but no proven copy in the folder any more
    none = watch.classify_backup(_success("ok"), None, now=now, max_age_h=26, offsite_max_age_h=48, newest_offsite=None)
    assert [x.key for x in none] == ["backup:offsite_stale"]


def test_newest_offsite_copy_is_read_from_the_run_folders(tmp_path):
    def run(name, pass_, at):
        d = tmp_path / name
        d.mkdir()
        (d / "offsite.json").write_text(json.dumps({"schema": 1, "at": at, "pass": pass_}), encoding="utf-8")

    assert watch.newest_offsite_copy(str(tmp_path)) is None
    run("20260922_031500", True, "2026-09-22T03:20:00Z")
    run("20260923_031500", False, "2026-09-23T03:20:00Z")  # failed copy: skipped
    run("20260924_031500", True, "not a date")  # unreadable: skipped
    (tmp_path / "notes").mkdir()
    got = watch.newest_offsite_copy(str(tmp_path))
    assert got is not None and got[1] == "20260922_031500"
    assert abs(got[0] - 1790047200) < 1  # 2026-09-22T03:20:00Z
    assert watch.newest_offsite_copy(os.path.join(str(tmp_path), "missing")) is None
