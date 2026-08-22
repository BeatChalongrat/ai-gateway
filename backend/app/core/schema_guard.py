"""Startup schema-drift guard (FIX 3).

The backend runs ``alembic upgrade head`` only at container start, but dev
hot-reloads the source — so code can advance to a new migration head while a
long-lived container's schema stays behind. With no startup check, that drift
surfaced as a generic 500 (``column tenants.search_pii_egress does not
exist``) on EVERY request, silently, for a long time.

This module makes drift impossible to miss: at startup, BEFORE serving
traffic, compare the DB's applied Alembic revision to the code's head and act:

* **in sync** → proceed (the common case; one cheap revision read).
* **DB behind** (stale schema) → mode-configurable:
    - ``autoupgrade`` (dev default): loud warning + advisory-locked
      in-process ``alembic upgrade head``, then proceed.
    - ``failclosed`` (prod default): refuse to start with a named,
      actionable error so no worker serves traffic against a schema it will
      500 on.
    - ``warn``: log loudly and proceed anyway (escape hatch).
    - ``off``: skip the guard entirely.
* **DB ahead** (a migration the code doesn't have — rollback scenario) →
  loud warning and proceed (additive migrations are backward-compatible;
  flagged so it's visible).

The pure decision (`decide_schema_action`) is unit-tested exhaustively; the
runner wires it to Alembic + the sync engine and dispatches.
"""

from __future__ import annotations

import pathlib

import structlog

logger = structlog.get_logger(__name__)

# Advisory-lock id for the in-process autoupgrade. Lives in the same 9000xx
# block main.py documents (worker.py 900001-900003, media job loop 900004,
# startup seed 900005; 900006 retired) — grep "pg_advisory" before adding one.
# SESSION-level (pg_advisory_lock) because the migration spans several
# transactions on Alembic's own engine; held on a dedicated connection across
# the whole upgrade so exactly one of N gunicorn workers migrates.
SCHEMA_MIGRATE_LOCK_ID = 900007

_VALID_MODES = {"autoupgrade", "failclosed", "warn", "off"}


class SchemaDriftError(RuntimeError):
    """Raised in failclosed mode when the DB schema is behind the code head.

    Raising this from the FastAPI lifespan startup aborts boot (the worker
    exits non-zero) — the deploy pipeline surfaces it instead of a per-request
    500.
    """


# ---------------------------------------------------------------------------
# Pure decision — the unit-tested core
# ---------------------------------------------------------------------------


def decide_schema_action(
    db_rev: str | None,
    code_head: str | None,
    known_revs: set[str],
    mode: str,
) -> tuple[str, str]:
    """Decide what to do given the DB revision vs the code head.

    Returns ``(action, message)`` where action is one of:
      * ``"ok"``      — schema in sync (or guard off); proceed silently.
      * ``"upgrade"`` — DB behind; run alembic upgrade (autoupgrade mode).
      * ``"fail"``    — DB behind; refuse to start (failclosed mode).
      * ``"warn"``    — proceed but log loudly (behind+warn, ahead, or a
                        head-less code tree).

    ``known_revs`` is the set of every revision id in the code's migration
    tree — used to distinguish "DB is at an older rev we know" (behind) from
    "DB is at a rev we've never heard of" (ahead / newer than this image).
    """
    mode = (mode or "").strip().lower()
    if mode == "off":
        return ("ok", "schema guard disabled (SCHEMA_DRIFT_MODE=off)")

    if not code_head:
        # No migrations in the tree at all — nothing to compare against.
        return ("ok", "no code migrations found; skipping schema guard")

    if db_rev == code_head:
        return ("ok", f"schema in sync (revision {code_head})")

    # Ahead: the DB is at a revision this code image has never seen (a newer
    # migration was applied, then the code rolled back). Additive migrations
    # are backward-compatible, so proceed — but flag it loudly.
    if db_rev is not None and db_rev not in known_revs:
        return (
            "warn",
            f"SCHEMA AHEAD: DB at unknown revision {db_rev!r} (code head is "
            f"{code_head!r}). The database has a migration this build does not "
            "ship - likely a rollback. Proceeding (additive migrations are "
            "backward-compatible), but verify the deployment.",
        )

    # Behind: db_rev is None (empty DB) or a known older revision.
    gap = f"DB at {db_rev or '(empty / no alembic_version)'}, code needs {code_head}"
    if mode == "autoupgrade":
        return ("upgrade", f"SCHEMA DRIFT: {gap} - auto-upgrading in-process.")
    if mode == "warn":
        return (
            "warn",
            f"SCHEMA DRIFT: {gap}. Proceeding anyway (SCHEMA_DRIFT_MODE=warn) - "
            "requests hitting new columns/tables will 500 until you run "
            "'alembic upgrade head'.",
        )
    # failclosed (and any unrecognised mode -> safest behaviour).
    return (
        "fail",
        f"SCHEMA DRIFT: {gap} - refusing to start. Run 'alembic upgrade head' "
        "(or 'make migrate') before booting the app.",
    )


# ---------------------------------------------------------------------------
# Alembic / engine plumbing
# ---------------------------------------------------------------------------


def _alembic_config():
    """Build an Alembic Config pointing at backend/alembic.ini.

    env.py already overrides ``sqlalchemy.url`` from ``settings.database_url``,
    so we only need the ini so ``script_location`` resolves to backend/migrations.
    """
    from alembic.config import Config

    ini_path = pathlib.Path(__file__).resolve().parents[2] / "alembic.ini"
    return Config(str(ini_path))


def _code_head_and_revs(cfg) -> tuple[str | None, set[str]]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    known = {rev.revision for rev in script.walk_revisions()}
    return head, known


def _read_db_revision(engine) -> str | None:
    """Read the DB's applied Alembic revision via a MigrationContext.

    Returns None on an empty DB (no ``alembic_version`` row/table).
    """
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _loud(message: str) -> None:
    """Emit an unmissable message to stderr (survives even if structlog is
    reconfigured or the traceback is noisy)."""
    print(f"[schema-guard] {message}", flush=True)


def _locked_upgrade(engine, cfg) -> None:
    """Run ``alembic upgrade head`` under a session-level advisory lock so
    only one gunicorn worker migrates; the others block, then re-check and
    no-op. On non-Postgres (SQLite tests) the lock is skipped."""
    from alembic import command
    from sqlalchemy import text

    is_pg = getattr(engine.dialect, "name", "") == "postgresql"
    if not is_pg:
        command.upgrade(cfg, "head")
        return

    lock_conn = engine.connect()
    try:
        # Blocks until acquired; session-scoped, held across the whole upgrade.
        lock_conn.execute(
            text("SELECT pg_advisory_lock(:id)"), {"id": SCHEMA_MIGRATE_LOCK_ID}
        )
        lock_conn.commit()
        # Re-check under the lock: a worker that raced ahead may have already
        # migrated while we waited.
        current = _read_db_revision(engine)
        head, _ = _code_head_and_revs(cfg)
        if current == head:
            logger.info("schema_autoupgrade_skipped_already_head", revision=head)
            _loud(f"another worker already migrated to {head}; continuing.")
            return
        command.upgrade(cfg, "head")
        logger.warning("schema_autoupgrade_applied", head=head)
        _loud(f"auto-upgraded schema to {head}.")
    finally:
        try:
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:id)"),
                {"id": SCHEMA_MIGRATE_LOCK_ID},
            )
            lock_conn.commit()
        except Exception:  # noqa: BLE001 — unlock is best-effort; conn close frees it
            pass
        lock_conn.close()


def _dispatch_action(action: str, message: str, *, upgrade_fn) -> None:
    """Execute a decided action. Isolated for unit testing (spy upgrade_fn)."""
    if action == "ok":
        logger.info("schema_guard_ok", detail=message)
        return
    if action == "warn":
        logger.warning("schema_guard_warning", detail=message)
        _loud(message)
        return
    if action == "fail":
        logger.error("schema_drift_failclosed", detail=message)
        _loud(message)
        raise SchemaDriftError(message)
    if action == "upgrade":
        logger.warning("schema_autoupgrade_starting", detail=message)
        _loud(message)
        upgrade_fn()
        return
    # Unknown action — be conservative and refuse.
    raise SchemaDriftError(f"schema guard: unknown action {action!r}")


# ---------------------------------------------------------------------------
# The runner — called once from the FastAPI lifespan (before serving traffic)
# ---------------------------------------------------------------------------


def run_schema_guard(engine=None) -> str:
    """Compare DB schema to code head and act per ``SCHEMA_DRIFT_MODE``.

    Runs synchronously (the lifespan dispatches it via ``asyncio.to_thread``).
    Returns the decided action (for logging/tests). Raises
    :class:`SchemaDriftError` in failclosed mode when the DB is behind.
    """
    from app.core.config import settings

    mode = settings.schema_drift_mode_effective
    if mode == "off":
        logger.info("schema_guard_disabled")
        return "ok"

    if engine is None:
        from app.core.db import engine as sync_engine

        engine = sync_engine

    cfg = _alembic_config()
    try:
        code_head, known_revs = _code_head_and_revs(cfg)
        db_rev = _read_db_revision(engine)
    except Exception as exc:  # noqa: BLE001
        # If we can't even read the revisions, don't wedge startup on the
        # guard itself — log loudly and let the app proceed (it will surface
        # any real schema problem via the Part-C error mapping).
        logger.warning("schema_guard_probe_failed", error=str(exc))
        _loud(f"could not read schema revision ({exc}); skipping guard.")
        return "ok"

    action, message = decide_schema_action(db_rev, code_head, known_revs, mode)
    _dispatch_action(
        action, message, upgrade_fn=lambda: _locked_upgrade(engine, cfg)
    )
    return action
