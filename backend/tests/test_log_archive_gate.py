"""The retention pipeline's gating contract.

``log_archive_enabled`` was off by default for as long as the archive
was a table with no way out. It has one now (the expiry step), the
strip→archive ordering is structural rather than hoped for, and the
windows were signed off in FIX60 — so the mover defaults ON and these
pins move with it.

What is asserted here is deliberately NOT "the flags are off". It is
the part that actually protects data:

* every DELETING step still defaults to DRY RUN — enabling a step
  makes it report, never destroy. Turning a dry-run off is a separate,
  explicit act;
* the archive move is not a deletion: it copies to cold storage and
  removes from hot, and it only runs after the strip;
* the lifespan starts exactly ONE retention task — the pipeline — and
  the old free-running archive loop is gone entirely;
* inside the pipeline the strip step is unconditional and precedes
  the flag-gated archive step;
* a failed strip skips the archive step even when the flag is on;
* the flag still works as a kill switch: False means the archive step
  never runs.
"""

import inspect


def test_retention_steps_enabled_per_the_signed_windows():
    """FIX60 — the three steps are on, and the windows are the signed ones.

    This replaces an older pin that asserted the flags were False. That
    pin existed to keep the steps inert *until an operator signed off on
    the windows*; the sign-off happened, so asserting False now would
    pin the pre-decision state and fail the decision instead of the
    code. The guard that matters — dry run — is asserted separately
    below and is unchanged.
    """
    from app.core.config import Settings

    for flag in (
        "log_archive_enabled",
        "archive_expiry_enabled",
        "audit_log_aging_enabled",
    ):
        assert Settings.model_fields[flag].default is True, flag

    assert Settings.model_fields["log_archive_days"].default == 90
    assert Settings.model_fields["archive_retention_days"].default == 365
    # Equal to the archive window, not longer: deletion evidence still
    # outlives the data because an expiry audit row is written at the
    # moment of deletion and then ages on its own clock.
    assert Settings.model_fields["audit_log_retention_days"].default == 365


def test_every_deleting_step_is_dry_run_by_default():
    """The one assertion that must never be relaxed.

    Enabling a step makes it COUNT. Destroying anything requires
    turning a dry-run off, which is a separate deliberate act — so a
    flag flipped by accident (or by a config merge) reports numbers
    instead of deleting rows.
    """
    from app.core.config import Settings

    for flag in ("archive_expiry_dry_run", "audit_log_aging_dry_run"):
        assert Settings.model_fields[flag].default is True, flag


def test_audit_default_constant_matches_the_setting():
    """The service's fallback and the setting must agree.

    ``RetentionService`` takes ``audit_log_days`` with a module-level
    default. A fallback that disagrees with the configured window is a
    second retention policy nobody knows they have.
    """
    from app.core.config import Settings
    from app.services.retention_service import DEFAULT_AUDIT_LOG_RETENTION_DAYS

    assert (
        DEFAULT_AUDIT_LOG_RETENTION_DAYS
        == Settings.model_fields["audit_log_retention_days"].default
    )


def test_lifespan_runs_one_pipeline_task_and_no_free_archive_loop():
    from app import main as main_mod

    src = inspect.getsource(main_mod)
    lifespan_src = inspect.getsource(main_mod.lifespan)

    assert lifespan_src.count(
        "create_task(_nightly_retention_pipeline())"
    ) == 1
    # The old independent loops must not come back.
    assert "_daily_log_archive" not in src
    assert "_daily_dlp_retention" not in src
    # The disabled branch still tells the operator why nothing is
    # being archived.
    assert "log archive NOT started" in lifespan_src


def test_pipeline_source_orders_strip_before_gated_archive():
    from app.jobs import retention_pipeline

    src = inspect.getsource(retention_pipeline.run_retention_pipeline)

    strip = src.index("run_dlp_retention_cleanup")
    gate = src.index("settings.log_archive_enabled")
    archive = src.index("run_log_archive")
    expiry = src.index("run_archive_expiry")

    assert strip < gate < archive < expiry, (
        "pipeline order must be strip -> (flag) archive -> expiry"
    )
    # The strip step must not sit behind the archive flag: everything
    # before the flag check must already contain the strip call.
    assert "run_dlp_retention_cleanup" in src[:gate]


def test_pipeline_calls_steps_in_order_and_respects_flags(monkeypatch):
    """Functional order pin: with all flags on, the steps run
    strip -> archive -> expiry -> audit_aging; with the archive flag
    off, the move never runs; with a FAILED strip, the move is skipped
    even though its flag is on."""
    import app.jobs.retention_pipeline as rp

    calls: list[str] = []

    class _Stats:
        rows_with_raw_text_cleared = 3
        rows_with_findings_stripped = 1
        retention_days = 90
        rows_archived = 7

    def fake_strip(session):
        calls.append("strip")
        return _Stats()

    def fake_archive(session):
        calls.append("archive")
        return _Stats()

    class _ExpiryStats:
        def to_dict(self):
            return {"rows_matched": 0, "rows_deleted": 0, "dry_run": True}

    def fake_expiry(session, *, retention_days, dry_run):
        calls.append("expiry")
        return _ExpiryStats()

    class _FakeRetentionService:
        def __init__(self, audit_log_days):
            pass

        def cleanup_audit_logs(self, session, *, dry_run=True):
            calls.append("audit_aging")
            return {"rows_matched": 0, "rows_deleted": 0, "dry_run": dry_run}

    import app.jobs.dlp_retention as dlp_mod
    import app.jobs.log_archive_job as arch_mod
    import app.jobs.archive_expiry_job as exp_mod
    import app.services.retention_service as rs_mod

    monkeypatch.setattr(dlp_mod, "run_dlp_retention_cleanup", fake_strip)
    monkeypatch.setattr(arch_mod, "run_log_archive", fake_archive)
    monkeypatch.setattr(exp_mod, "run_archive_expiry", fake_expiry)
    monkeypatch.setattr(rs_mod, "RetentionService", _FakeRetentionService)

    class _FakeSession:
        def close(self):
            pass

        def commit(self):
            pass

    from app.core.config import settings

    monkeypatch.setattr(settings, "log_archive_enabled", True)
    monkeypatch.setattr(settings, "archive_expiry_enabled", True)
    monkeypatch.setattr(settings, "audit_log_aging_enabled", True)

    result = rp.run_retention_pipeline(_FakeSession)
    assert calls == ["strip", "archive", "expiry", "audit_aging"]
    assert result.steps_run == ["strip", "archive", "expiry", "audit_aging"]
    assert result.strip_rows == 3
    assert result.archive_rows == 7

    # Archive flag off -> the move never runs.
    calls.clear()
    monkeypatch.setattr(settings, "log_archive_enabled", False)
    rp.run_retention_pipeline(_FakeSession)
    assert "archive" not in calls
    assert calls[0] == "strip"

    # Strip FAILURE skips the move even with the flag on.
    calls.clear()
    monkeypatch.setattr(settings, "log_archive_enabled", True)

    def broken_strip(session):
        calls.append("strip")
        raise RuntimeError("strip exploded")

    monkeypatch.setattr(dlp_mod, "run_dlp_retention_cleanup", broken_strip)
    result = rp.run_retention_pipeline(_FakeSession)
    assert "archive" not in calls, (
        "a failed strip must skip the archive move — copying un-stripped "
        "rows to cold storage is the exact leak the ordering prevents"
    )
    assert any("strip" in e for e in result.errors)
    assert any("archive: skipped" in e for e in result.errors)


def test_midnight_math_lands_on_utc_midnight():
    from datetime import datetime, timedelta, timezone

    from app.jobs.retention_pipeline import seconds_until_next_midnight_utc

    at = datetime(2026, 9, 3, 21, 30, tzinfo=timezone.utc)
    s = seconds_until_next_midnight_utc(at)
    assert s == 2.5 * 3600
    landing = at + timedelta(seconds=s)
    assert landing.hour == 0 and landing.minute == 0
