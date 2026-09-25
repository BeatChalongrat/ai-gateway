"""FIX134 F — ops-watch pages when the daily smoke suite is red or has stopped running.

The suite (scripts/smoke.sh, run daily by scripts/smoke-daily.ps1) writes its result to
``<BACKUP_DIR>/smoke_last.json`` — the folder ops-watch already reads at /backups. The rule
is pure: ``classify_smoke(record, now=..., max_age_h=26, missing_since=...)``.
"""

from __future__ import annotations

import json
import time

from app.ops import watch
from app.services import alert_catalog as cat


def _record(ok=True, age_h=1.0, checks=None, target="live"):
    now = time.time()
    checks = checks if checks is not None else [
        {"status": "PASS", "name": "pages.web_version", "detail": "200"},
        {"status": "SKIP", "name": "admin.login", "detail": "no account"},
    ]
    counts = {"pass": sum(c["status"] == "PASS" for c in checks), "fail": sum(c["status"] == "FAIL" for c in checks),
              "skip": sum(c["status"] == "SKIP" for c in checks)}
    return {"schema": 1, "target": target, "at": "2026-09-25T06:30:00Z", "epoch": now - age_h * 3600, "ok": ok,
            "counts": counts, "checks": checks}


def test_a_fresh_green_result_is_quiet():
    assert watch.classify_smoke(_record(ok=True), now=time.time(), max_age_h=26) == []


def test_a_red_result_pages_with_the_failing_checks_named():
    checks = [
        {"status": "PASS", "name": "pages.web_version", "detail": "200"},
        {"status": "FAIL", "name": "portal.proxy_me_unauth", "detail": "503 (want 401)"},
        {"status": "FAIL", "name": "desktop.startup", "detail": "TLS error"},
        {"status": "SKIP", "name": "admin.login", "detail": "no account"},
    ]
    f = watch.classify_smoke(_record(ok=False, checks=checks), now=time.time(), max_age_h=26)
    assert [x.key for x in f] == ["smoke:failed"]
    assert f[0].group == cat.GROUP_SMOKE == "smoke"
    assert f[0].severity == cat.HIGH
    assert "2" in f[0].title and "4" in f[0].title  # 2 of 4 checks red
    assert "portal.proxy_me_unauth" in f[0].message and "desktop.startup" in f[0].message
    assert "smoke_last.json" in f[0].message and "scripts/smoke.sh" in f[0].message
    assert "pages.web_version" not in f[0].message  # only the red ones are listed


def test_a_result_older_than_the_limit_means_the_suite_stopped_running():
    now = time.time()
    f = watch.classify_smoke(_record(ok=True, age_h=27), now=now, max_age_h=26)
    assert [x.key for x in f] == ["smoke:stale"] and f[0].group == cat.GROUP_SMOKE and f[0].severity == cat.HIGH
    assert "27 h" in f[0].message and "26" in f[0].message
    assert "smoke-daily.ps1" in f[0].message or "SRAN smoke daily" in f[0].message
    # a stale red result is reported as stale (the suite is not running: that is the incident)
    f = watch.classify_smoke(_record(ok=False, age_h=30), now=now, max_age_h=26)
    assert [x.key for x in f] == ["smoke:stale"]
    # just inside the limit: quiet
    assert watch.classify_smoke(_record(ok=True, age_h=25.5), now=now, max_age_h=26) == []


def test_a_missing_file_pages_only_after_the_limit():
    now = time.time()
    assert watch.classify_smoke(None, now=now, max_age_h=26) == []
    assert watch.classify_smoke(None, now=now, max_age_h=26, missing_since=now - 2 * 3600) == []
    f = watch.classify_smoke(None, now=now, max_age_h=26, missing_since=now - 27 * 3600)
    assert [x.key for x in f] == ["smoke:missing"] and f[0].group == cat.GROUP_SMOKE
    assert "27 h" in f[0].message


def test_an_unreadable_record_counts_as_stale():
    f = watch.classify_smoke({"schema": 1, "ok": True}, now=time.time(), max_age_h=26)
    assert [x.key for x in f] == ["smoke:stale"]
    assert "unreadable" in f[0].message or "no timestamp" in f[0].message


def test_the_record_is_read_from_the_backup_folder(tmp_path):
    p = tmp_path / "smoke_last.json"
    assert watch.smoke_record(str(p)) is None
    p.write_text(json.dumps(_record(ok=True)), encoding="utf-8")
    rec = watch.smoke_record(str(p))
    assert rec is not None and rec["ok"] is True
    p.write_text("not json", encoding="utf-8")
    assert watch.smoke_record(str(p)) is None


def test_collect_tracks_when_the_file_went_missing(tmp_path, monkeypatch):
    """The I/O side: no file → the first tick remembers when; a file again → forgotten."""
    cfg = {"smoke_file": str(tmp_path / "smoke_last.json"), "smoke_max_age_h": 26.0}
    t = [1_000_000.0]
    monkeypatch.setattr(watch.time, "time", lambda: t[0])
    assert watch.smoke_findings(cfg) == []
    assert cfg["_smoke_missing_since"] == 1_000_000.0
    t[0] += 27 * 3600
    assert [f.key for f in watch.smoke_findings(cfg)] == ["smoke:missing"]
    (tmp_path / "smoke_last.json").write_text(json.dumps({"schema": 1, "ok": True, "epoch": t[0] - 60, "checks": []}), encoding="utf-8")
    assert watch.smoke_findings(cfg) == []
    assert "_smoke_missing_since" not in cfg
    # no smoke file configured: nothing to say
    assert watch.smoke_findings({}) == []
