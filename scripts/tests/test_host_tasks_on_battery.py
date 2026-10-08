"""A task of the host that the pager depends on runs on battery too.

Task Scheduler's defaults are that a task does not start on battery and is stopped when the charger is
pulled. On 9 Oct 2026 the daily smoke task was found registered that way (it would not have run at
06:30 on a laptop left unplugged), and a watch of the battery registered that way would stop at the
moment it is for. Both register scripts say the two settings in so many words.

    python -m pytest scripts/tests/test_host_tasks_on_battery.py -q
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = {"scripts/smoke-register-task.ps1": "SRAN smoke daily", "scripts/ops/power-register-task.ps1": "SRAN power watch"}


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _settings(src: str) -> str:
    m = re.search(r"New-ScheduledTaskSettingsSet\b(.*?)\n(?=\S)", src, re.S)
    assert m, "no New-ScheduledTaskSettingsSet"
    return m.group(1)


def test_each_task_may_start_on_battery_and_is_not_stopped_by_it():
    for rel, name in TASKS.items():
        src = _read(rel)
        assert name in src, rel
        settings = _settings(src)
        assert "-AllowStartIfOnBatteries" in settings, f"{rel}: the task would not start on battery"
        assert "-DontStopIfGoingOnBatteries" in settings, f"{rel}: the task would be stopped when the charger is pulled"


def test_the_power_watch_is_started_again_if_it_dies_and_never_twice():
    src = _read("scripts/ops/power-register-task.ps1")
    assert "-AtLogOn" in src and "-RepetitionInterval" in src
    assert "-MultipleInstances IgnoreNew" in _settings(src)
    assert "[TimeSpan]::Zero" in _settings(src)          # no time limit: it runs for as long as the session does


def test_the_reading_is_written_whole_and_without_a_mark_the_watcher_cannot_read():
    src = _read("scripts/ops/power-watch.ps1")
    assert "-Encoding ascii" in src                      # (Windows PowerShell's utf8 puts a byte-order mark first)
    assert re.search(r"Move-Item -Force -Path \$tmp", src)
    for field in ("epoch", "battery", "on_battery", "charge_pct", "minutes_left"):
        assert re.search(rf"\b{field}\s+=", src), field
    # and the watcher reads those same fields
    watch = _read("backend/app/ops/watch.py")
    for field in ("epoch", "battery", "on_battery", "charge_pct", "minutes_left"):
        assert f'record.get("{field}")' in watch, field
