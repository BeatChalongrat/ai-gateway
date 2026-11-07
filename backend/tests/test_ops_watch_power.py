"""ops-watch says when the host is on battery, and pages while there is still time to plug in.

The host is a laptop. On 9 Oct 2026 it ran on its battery from 98 % to 61 % over two hours with live on
it and nothing said so: no rule looked at the power, and the pager stops with the machine. The watcher's
container cannot see a battery, so a task of the host (scripts/ops/power-watch.ps1) writes
``<BACKUP_DIR>/power_last.json`` once a minute — the folder ops-watch reads at /backups — and the rule
is pure: ``classify_power(record, now=..., max_age_s=, page_pct=, page_minutes=, debounce_s=)``.
"""

from __future__ import annotations

import json
import time

from app.ops import watch
from app.services import alert_catalog as cat


def _reading(on_battery=False, pct=100, minutes=None, age_s=20.0, battery=True, now=None):
    now = time.time() if now is None else now
    return {"schema": 1, "at": "2026-10-09T02:50:00Z", "epoch": now - age_s, "battery": battery, "on_battery": on_battery,
            "charge_pct": pct, "minutes_left": minutes, "line": "Offline" if on_battery else "Online"}


def test_on_mains_power_is_quiet():
    assert watch.classify_power(_reading(on_battery=False, pct=100), now=time.time()) == []
    assert watch.classify_power(_reading(on_battery=False, pct=12), now=time.time()) == []      # charging from low: quiet


def test_on_battery_is_said_once_it_has_lasted_and_is_not_a_page():
    f = watch.classify_power(_reading(on_battery=True, pct=98, minutes=310), now=time.time())
    assert [x.key for x in f] == ["power:on_battery"]
    assert f[0].group == cat.GROUP_POWER == "power"
    assert f[0].severity == cat.HIGH                      # degraded: said, not the 3 a.m. page
    assert f[0].debounce_s == 120.0                       # carrying the machine to another socket says nothing
    assert "98%" in f[0].title and "310 min" in f[0].message
    assert "40%" in f[0].message and "60 min" in f[0].message   # when the louder page comes
    assert "Plug it in" in f[0].message


def test_the_night_of_9_october_would_have_been_said_and_then_paged():
    # 98 % → 61 % in two hours; the readings of that night, as the task would have written them
    now = time.time()
    keys = [[x.key for x in watch.classify_power(_reading(on_battery=True, pct=p, minutes=m, now=now), now=now)]
            for p, m in ((98, 320), (73, 180), (64, 150), (61, 136), (45, 95), (40, 80), (31, 55))]
    assert keys == [["power:on_battery"]] * 5 + [["power:low"]] * 2


def test_at_the_threshold_it_pages_while_there_is_still_time():
    f = watch.classify_power(_reading(on_battery=True, pct=40, minutes=85), now=time.time())
    assert [x.key for x in f] == ["power:low"] and f[0].group == cat.GROUP_POWER
    assert f[0].severity == cat.CRITICAL and f[0].debounce_s == 0.0      # at once
    assert "40%" in f[0].title and "plug" in f[0].title.lower()
    assert "last warning" in f[0].message and "85 min" in f[0].message
    # the time left counts too: a worn battery at 55 % with half an hour left is low
    f = watch.classify_power(_reading(on_battery=True, pct=55, minutes=30), now=time.time())
    assert [x.key for x in f] == ["power:low"]
    # one incident for both, so the pager says "on battery" once and pages again when it turns critical
    said = watch.classify_power(_reading(on_battery=True, pct=80, minutes=200), now=time.time())[0]
    assert said.incident == f[0].incident == "power" and cat.rank(f[0].severity) > cat.rank(said.severity)


def test_the_thresholds_are_settings():
    now = time.time()
    r = _reading(on_battery=True, pct=55, minutes=200, now=now)
    assert [x.key for x in watch.classify_power(r, now=now)] == ["power:on_battery"]
    assert [x.key for x in watch.classify_power(r, now=now, page_pct=60)] == ["power:low"]
    assert [x.key for x in watch.classify_power(r, now=now, page_minutes=240)] == ["power:low"]


def test_a_reading_with_no_charge_or_time_in_it_is_still_on_battery():
    f = watch.classify_power(_reading(on_battery=True, pct=None, minutes=None), now=time.time())
    assert [x.key for x in f] == ["power:on_battery"] and "charge unknown" in f[0].title
    # Windows says -1 when it does not know the time left; it is not "0 minutes"
    f = watch.classify_power(_reading(on_battery=True, pct=90, minutes=-1), now=time.time())
    assert [x.key for x in f] == ["power:on_battery"] and "min at this rate" not in f[0].message


def test_a_machine_with_no_battery_and_a_host_without_the_task_are_quiet():
    assert watch.classify_power(_reading(battery=False, on_battery=False, pct=None), now=time.time()) == []
    assert watch.classify_power(None, now=time.time()) == []
    assert watch.classify_power({}, now=time.time()) == []


def test_a_reading_that_stopped_coming_is_said_whatever_it_last_showed():
    now = time.time()
    for last in (_reading(on_battery=False, pct=100, age_s=900, now=now), _reading(on_battery=True, pct=70, age_s=900, now=now)):
        f = watch.classify_power(last, now=now)
        assert [x.key for x in f] == ["power:stale"] and f[0].group == cat.GROUP_POWER and f[0].severity == cat.HIGH
        assert "15 min" in f[0].message and "10 min" in f[0].message
        assert "SRAN power watch" in f[0].message and "power-register-task.ps1" in f[0].message
        assert f[0].debounce_s >= 300.0          # a machine waking from sleep writes a new one before this
    # just inside the limit: read as it is
    assert watch.classify_power(_reading(on_battery=False, age_s=590, now=now), now=now) == []
    # a reading with no time in it is one that stopped
    assert [x.key for x in watch.classify_power({"schema": 1, "battery": True, "on_battery": True}, now=now)] == ["power:stale"]


def test_the_reading_is_read_from_the_backup_folder_each_tick(tmp_path, monkeypatch):
    cfg = {"power_file": str(tmp_path / "power_last.json"), "power_page_pct": 40.0, "power_page_min": 60.0,
           "power_max_age_s": 600.0, "power_debounce_s": 120.0}
    t = [1_800_000_000.0]
    monkeypatch.setattr(watch.time, "time", lambda: t[0])
    assert watch.power_findings(cfg) == []                               # no task on this host: quiet
    p = tmp_path / "power_last.json"
    p.write_text(json.dumps(_reading(on_battery=True, pct=64, minutes=150, now=t[0])), encoding="ascii")
    assert [f.key for f in watch.power_findings(cfg)] == ["power:on_battery"]
    p.write_text(json.dumps(_reading(on_battery=True, pct=38, minutes=70, now=t[0])), encoding="ascii")
    assert [f.key for f in watch.power_findings(cfg)] == ["power:low"]
    p.write_text(json.dumps(_reading(on_battery=False, pct=41, now=t[0])), encoding="ascii")
    assert watch.power_findings(cfg) == []                               # plugged in again
    t[0] += 3600
    assert [f.key for f in watch.power_findings(cfg)] == ["power:stale"]
    p.write_text("half a fi", encoding="ascii")
    assert watch.power_findings(cfg) == []                               # (the task writes beside and moves: not seen)
    assert watch.power_findings({}) == [] and watch.power_findings({"power_file": ""}) == []


def test_the_watcher_reads_the_power_every_tick(monkeypatch):
    seen = []
    monkeypatch.setattr(watch, "power_findings", lambda cfg: seen.append(1) or [])
    monkeypatch.setattr(watch, "smoke_findings", lambda cfg: [])
    monkeypatch.setattr(watch, "docker_containers", lambda *a: [])
    monkeypatch.setattr(watch, "disk_usage", lambda paths: {})
    monkeypatch.setattr(watch, "meminfo", lambda: (0, 0))
    watch.collect({"project": "x", "sock": "", "ignore_exited": (), "backend_url": "", "disk_paths": [], "disk_pct": 90.0,
                   "mem_pct": 90.0, "pg_dsns": [], "pg_stuck_s": 60.0})
    assert seen == [1]
