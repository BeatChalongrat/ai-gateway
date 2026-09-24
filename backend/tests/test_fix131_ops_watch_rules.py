"""FIX131 B3 / D5 / D6 — the new rules, and alerts in words.

* **No fresh backup** (B3): nothing alerted on a missing backup — ops-watch
  had no backup check and the backup container cannot report its own death
  (NIGHT3: the 03:15 run fired 2 h 37 min late and nobody would have known
  had it not run at all). ops-watch now reads the backup folder (read-only)
  — ``last_success.json`` / ``last_failure.json`` written by the backup
  container, or the newest proven directory for older backups.
* **Turns stuck** (D5): assistant rows still ``streaming`` after 15 min
  (the orphaned-turn sweep should close them; this catches it failing).
* **Human-first text** (D6): the backend rule's page dumped
  "metric_value: 68.707031 / threshold: 60.0"; titles now say what is wrong
  for users and bodies what to do.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import Base
from app.models.alert_rule import AlertRuleORM
from app.ops import watch
from app.services import alert_channels, pg_activity
from app.services.alert_evaluator import AlertEvaluator

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc).timestamp()
H = 3600.0


def _success(age_h: float, **extra) -> dict:
    return {"schema": 1, "epoch": NOW - age_h * H, "backup_id": "20260924_031500",
            "drill": {"pass": True}, "offsite": {"status": "not_configured", "notified": False}, **extra}


# ---------------------------------------------------------------------------
# No fresh backup
# ---------------------------------------------------------------------------

def test_a_fresh_proven_backup_is_quiet():
    assert watch.classify_backup(_success(7), None, now=NOW, max_age_h=26) == []


def test_no_backup_newer_than_26_hours_pages():
    [f] = watch.classify_backup(_success(30), None, now=NOW, max_age_h=26)
    assert f.incident == "backup" and f.severity == "warning"
    assert f.title == "Backups: no fresh backup for 30 h 0 min"
    assert "20260924_031500" in f.message and "run --rm backup now" in f.message


def test_no_backup_at_all_pages():
    [f] = watch.classify_backup(None, None, now=NOW, max_age_h=26)
    assert f.title == "Backups: no proven backup found"


def test_a_failed_run_newer_than_the_last_success_pages_unless_the_backup_container_did():
    failure = {"epoch": NOW - 600, "status": "DRILL FAILED", "attempts": 1, "notified": True,
               "next_retry_at": "2026-09-24T10:10:00Z"}
    [f] = watch.classify_backup(_success(7), failure, now=NOW, max_age_h=26)
    assert f.key == "backup:failed" and f.incident == "backup"
    assert f.title == "Backups are failing: the last backup could not be restored in the drill"
    assert f.notified_elsewhere is True                             # the container paged the transition
    [g] = watch.classify_backup(_success(7), {**failure, "notified": False}, now=NOW, max_age_h=26)
    assert g.notified_elsewhere is False                             # it did not: ops-watch pages
    # A failure OLDER than the newest success is history.
    assert watch.classify_backup(_success(1), {**failure, "epoch": NOW - 5 * H}, now=NOW, max_age_h=26) == []


def test_an_offsite_failure_is_its_own_incident():
    s = _success(7, offsite={"status": "fail", "notified": True})
    [f] = watch.classify_backup(s, None, now=NOW, max_age_h=26)
    assert f.incident == "backup:offsite" and f.notified_elsewhere is True
    assert f.title == "Off-site backup copy failed (the local backup is fine)"


def _write(path, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def test_backup_records_read_the_folder_and_fall_back_to_proven_directories(tmp_path):
    root = str(tmp_path)
    assert watch.backup_records(root) == (None, None, None)
    _write(f"{root}/20260923_031500/manifest.json", {"created_at": "2026-09-22T20:16:00Z"})
    _write(f"{root}/20260923_031500/drill.json", {"pass": True})
    _write(f"{root}/20260924_031500/manifest.json", {"created_at": "2026-09-23T20:16:00Z"})
    _write(f"{root}/20260924_031500/drill.json", {"pass": False})   # newest, but its drill failed
    os.makedirs(f"{root}/20260925_031500.failed")
    success, failure, newest = watch.backup_records(root)
    assert success is None and failure is None
    assert newest == (datetime(2026, 9, 22, 20, 16, tzinfo=timezone.utc).timestamp(), "20260923_031500")
    _write(f"{root}/last_success.json", _success(2))
    assert watch.backup_records(root)[0]["backup_id"] == "20260924_031500"


def test_ops_watch_pages_a_missing_backup_once_and_recovers(tmp_path, monkeypatch):
    """End to end through ops-watch's tick: the backup folder (read-only in
    production) has nothing fresh → one page; a new backup → one recovered."""
    sent: list = []
    monkeypatch.setattr(watch, "deliver", lambda n: sent.append(n) or {"ntfy": True})
    for name in ("docker_containers",):
        monkeypatch.setattr(watch, name, lambda *a, **k: [])
    monkeypatch.setattr(watch, "probe_ready", lambda url: (200, {}, None))
    monkeypatch.setattr(watch, "disk_usage", lambda paths: {})
    monkeypatch.setattr(watch, "meminfo", lambda: (0, 0))
    root = str(tmp_path)
    _write(f"{root}/last_success.json", {**_success(0), "epoch": time.time() - 30 * H})
    cfg = {"project": "p", "sock": "/none", "backend_url": "", "disk_paths": [], "disk_pct": 90.0,
           "mem_pct": 90.0, "ignore_exited": (), "pg_dsns": [], "pg_stuck_s": 60.0,
           "turn_window_min": 5, "turn_min_sample": 10, "turn_error_pct": 5.0,
           "backup_dir": root, "backup_max_age_h": 26}
    state = watch.AlertState()
    watch.tick(state, cfg)
    watch.tick(state, cfg)
    assert [n.title for n in sent] == ["Backups: no fresh backup for 30 h 0 min"]
    _write(f"{root}/last_success.json", {**_success(0), "epoch": time.time()})
    watch.tick(state, cfg)
    assert [n.title for n in sent][1:] == ["Recovered: Backups: no fresh backup for 30 h 0 min"]


# ---------------------------------------------------------------------------
# Turns stuck "streaming"
# ---------------------------------------------------------------------------

def test_turns_stuck_streaming():
    assert watch.classify_stuck_turns(0, 0.0, after_min=15) == []
    [f] = watch.classify_stuck_turns(3, 47 * 60, after_min=15)
    assert f.incident == "turns:stuck" and f.severity == "warning"
    assert f.title == "Chat: 3 replies stuck 'typing' for over 15 min"
    assert "47 min" in f.message and "sweep" in f.message
    [one] = watch.classify_stuck_turns(1, 16 * 60, after_min=15)
    assert one.title == "Chat: 1 reply stuck 'typing' for over 15 min"


def test_turns_stuck_query_reads_recent_streaming_assistant_rows_only():
    sql = " ".join(watch._STUCK_TURNS_SQL.split())
    assert "role = 'assistant' AND status = 'streaming'" in sql
    assert "created_at < now() - make_interval(mins => %s)" in sql
    assert "created_at > now() - make_interval(hours => %s)" in sql  # old orphans are not a page


def test_ops_watch_runs_the_stuck_turn_check_on_the_chat_database(monkeypatch):
    sent: list = []
    monkeypatch.setattr(watch, "deliver", lambda n: sent.append(n) or {"ntfy": True})
    monkeypatch.setattr(watch, "docker_containers", lambda *a, **k: [])
    monkeypatch.setattr(watch, "disk_usage", lambda paths: {})
    monkeypatch.setattr(watch, "meminfo", lambda: (0, 0))
    monkeypatch.setattr(watch, "pg_activity_rows", lambda dsn: [])
    monkeypatch.setattr(watch, "turn_counts", lambda dsn, w: (5, 0))
    asked: list = []
    monkeypatch.setattr(watch, "stuck_turns", lambda dsn, m, h: asked.append((m, h)) or (2, 20 * 60))
    cfg = {"project": "p", "sock": "/none", "backend_url": "", "disk_paths": [], "disk_pct": 90.0,
           "mem_pct": 90.0, "ignore_exited": (), "pg_dsns": [("chat", "postgresql://x@y/z")], "pg_stuck_s": 60.0,
           "turn_window_min": 5, "turn_min_sample": 10, "turn_error_pct": 5.0,
           "turn_stuck_min": 15, "turn_stuck_lookback_h": 24}
    watch.tick(watch.AlertState(), cfg)
    assert asked == [(15, 24)]
    assert [n.title for n in sent] == ["Chat: 2 replies stuck 'typing' for over 15 min"]


# ---------------------------------------------------------------------------
# Words, not metric dumps
# ---------------------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()
        pg_activity._last = None


@pytest.mark.asyncio
async def test_the_rule_page_is_words_not_a_metric_dump(session, monkeypatch):
    """NIGHT3: "metric_value: 68.707031 / threshold: 60.0" in the page body."""
    sent: list = []
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/t")
    monkeypatch.setattr(alert_channels, "deliver", lambda n: sent.append(n) or {"ntfy": True})
    session.add(AlertRuleORM(id=str(uuid.uuid4()), name="Transaction stuck longer than 60 s",
                             metric="stuck_transaction", threshold=60.0, window_minutes=1,
                             scope="global", enabled=True))
    session.commit()
    pg_activity._set_last(pg_activity.ActivitySample(
        at=time.time(), idle_in_transaction=1, idle_in_transaction_max_age_s=68.707031,
        worst=[{"pid": 31926, "state": "idle in transaction", "application_name": "sran-backend-async",
                "wait": "Client", "xact_age_s": 68.707031, "query": "SELECT 1"}],
    ))
    await AlertEvaluator().evaluate_all(session)
    [n] = sent
    for raw in ("metric_value", "threshold:", "68.707031", "window_minutes", "stuck_age_s"):
        assert raw not in n.title and raw not in n.message, raw
    assert n.title == "Chat may hang: a database transaction is stuck (gateway, 68 s)"
    assert "has been idle for 68 s (limit 60 s): pid 31926" in n.message
    assert "pg_stuck_transaction" in n.message                       # where to look
    assert n.severity == "warning"
