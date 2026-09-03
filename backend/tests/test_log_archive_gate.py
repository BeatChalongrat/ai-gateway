"""The log-archive mover is OFF by default and off means NEVER CREATED.

``request_logs_archive`` has no expiry and no delete path, and nothing
orders the archive loop against the DLP raw-retention loop within a
day — so with the mover running, a day where archive fires first copies
rows with raw columns intact into permanent cold storage. Until the
retention plan (docs/DATA_RETENTION_PLAN.md) decides the archive's
fate, the mover must stay off, and "off" must mean the task object is
never constructed — a created-but-idle task is one refactor away from
running.

These tests pin: (1) the default is False; (2) the lifespan only
creates the archive task inside the flag guard, with a boot log in the
else branch so an operator can see WHY nothing is being archived.
"""

import inspect


def test_log_archive_disabled_by_default():
    from app.core.config import Settings

    field = Settings.model_fields["log_archive_enabled"]
    assert field.default is False


def test_lifespan_creates_archive_task_only_behind_the_flag():
    from app import main as main_mod

    src = inspect.getsource(main_mod.lifespan)

    # Exactly one construction site for the archive task…
    assert src.count("create_task(_daily_log_archive())") == 1

    # …and it sits strictly after the flag check. If someone hoists the
    # create_task back above the guard this ordering breaks.
    guard = src.index("if settings.log_archive_enabled:")
    create = src.index("create_task(_daily_log_archive())")
    assert guard < create, "archive task is created outside the flag guard"

    # The disabled branch tells the operator why nothing is archiving.
    else_branch = src[src.index("else:", guard):]
    assert "log archive NOT started" in else_branch

    # Shutdown tolerates the task never having been created.
    assert "if archive_task is not None:" in src


def test_dlp_retention_loop_is_not_gated_by_the_archive_flag():
    """Stopping the MOVER must not stop the STRIPPER: the DLP
    raw-retention loop keeps NULL-ing raw columns on the hot table
    unconditionally."""
    from app import main as main_mod

    src = inspect.getsource(main_mod.lifespan)
    retention = src.index("create_task(_daily_dlp_retention())")
    guard = src.index("if settings.log_archive_enabled:")
    assert retention < guard, (
        "the DLP retention task must be created before (outside) the "
        "archive flag guard"
    )
