"""The nightly retention pipeline — one ordered run at 00:00 UTC.

Replaces the two independent 24-hour loops (DLP raw-stripper and
log-archive mover) whose lack of ordering was the race that let the
mover copy un-stripped rows toward permanent cold storage. The order
is now STRUCTURAL — one function, four steps, each awaited before the
next starts — and pinned by ``tests/test_retention_pipeline.py``,
which fails if the order is ever flipped:

    1. strip    — DLP raw-retention cleanup (always runs; stopping the
                  mover must never stop the stripping)
    2. archive  — hot → cold move           (``log_archive_enabled``, default off)
    3. expiry   — archive aging             (``archive_expiry_enabled``, default off;
                  dry-run by default)
    4. audit    — admin-audit-log aging     (``audit_log_aging_enabled``, default off;
                  dry-run by default)

Every deleting step is flag-gated off by default, starts in dry-run
(counts only, deletes nothing) and its live runs write a row into the
admin audit log — retention actions are themselves audited. The
pipeline runs at a fixed time (00:00 UTC), never at boot: a restart
must not move the deletion hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger(__name__)

# The pipeline's audit identity for rows in admin_audit_log.
PIPELINE_ACTOR = "system:retention-pipeline"
PIPELINE_TENANT = "__system__"


def seconds_until_next_midnight_utc(now: datetime | None = None) -> float:
    """Seconds from ``now`` to the next 00:00 UTC."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    next_midnight = (
        now.replace(hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=1)
    )
    return (next_midnight - now).total_seconds()


@dataclass
class PipelineResult:
    """Counts-only summary of one nightly run (never content)."""

    steps_run: list[str] = field(default_factory=list)
    strip_rows: int = 0
    archive_rows: int = 0
    expiry: dict | None = None
    audit_aging: dict | None = None
    errors: list[str] = field(default_factory=list)


def run_retention_pipeline(session_factory) -> PipelineResult:
    """One ordered nightly pass. ``session_factory`` is SessionLocal.

    Each step gets its own session (a failed step must not poison the
    next one's transaction), and a step failure is recorded and the
    pipeline CONTINUES — except a strip failure, which also SKIPS the
    archive step: moving rows whose stripping just failed is exactly
    the leak this ordering exists to prevent.
    """
    from app.core.config import settings

    result = PipelineResult()

    # ── 1. strip — always ────────────────────────────────────────────
    strip_ok = False
    session = session_factory()
    try:
        from app.jobs.dlp_retention import run_dlp_retention_cleanup

        stats = run_dlp_retention_cleanup(session)
        result.strip_rows = stats.rows_with_raw_text_cleared
        result.steps_run.append("strip")
        strip_ok = True
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"strip: {exc}")
        logger.error("retention_pipeline_step_failed", step="strip", error=str(exc))
    finally:
        session.close()

    # ── 2. archive move — flag-gated, and NEVER after a failed strip ─
    if settings.log_archive_enabled:
        if not strip_ok:
            result.errors.append("archive: skipped (strip failed this run)")
            logger.warning(
                "retention_pipeline_archive_skipped_strip_failed"
            )
        else:
            session = session_factory()
            try:
                from app.jobs.log_archive_job import run_log_archive

                stats = run_log_archive(session)
                result.archive_rows = getattr(stats, "rows_archived", 0)
                result.steps_run.append("archive")
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"archive: {exc}")
                logger.error(
                    "retention_pipeline_step_failed", step="archive", error=str(exc)
                )
            finally:
                session.close()

    # ── 3. archive expiry — flag-gated, dry-run by default ───────────
    if settings.archive_expiry_enabled:
        session = session_factory()
        try:
            from app.jobs.archive_expiry_job import run_archive_expiry

            stats = run_archive_expiry(
                session,
                retention_days=settings.archive_retention_days,
                dry_run=settings.archive_expiry_dry_run,
            )
            result.expiry = stats.to_dict()
            result.steps_run.append("expiry")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"expiry: {exc}")
            logger.error(
                "retention_pipeline_step_failed", step="expiry", error=str(exc)
            )
        finally:
            session.close()

    # ── 4. audit-log aging — flag-gated, dry-run by default ──────────
    if settings.audit_log_aging_enabled:
        session = session_factory()
        try:
            from app.services.retention_service import RetentionService

            svc = RetentionService(
                audit_log_days=settings.audit_log_retention_days
            )
            aged = svc.cleanup_audit_logs(
                session, dry_run=settings.audit_log_aging_dry_run
            )
            if not settings.audit_log_aging_dry_run:
                session.commit()
            result.audit_aging = aged
            result.steps_run.append("audit_aging")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"audit_aging: {exc}")
            logger.error(
                "retention_pipeline_step_failed", step="audit_aging", error=str(exc)
            )
        finally:
            session.close()

    logger.info(
        "retention_pipeline_complete",
        steps=result.steps_run,
        strip_rows=result.strip_rows,
        archive_rows=result.archive_rows,
        expiry=result.expiry,
        audit_aging=result.audit_aging,
        errors=result.errors,
    )
    return result
