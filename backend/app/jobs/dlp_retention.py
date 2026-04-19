"""DLP raw-content retention job.

Soft-deletes the raw-PII columns on ``request_logs`` rows older than
``settings.dlp_raw_retention_days`` (default 90). The row itself is
preserved — and so are the summary counts, masked snapshots, and the
per-finding metadata (``detector``, ``category``, ``severity``,
``mask_token``, ``matched_text_len``) — so analytics and audit
dashboards keep working on historical traffic.

What gets cleared on each affected row:

- ``input_raw``     → NULL  (user's verbatim message)
- ``output_raw``    → NULL  (provider response before restore)
- ``dlp_findings_json`` → ``matched_text`` stripped from every entry

What is intentionally retained:

- ``input_masked``     (mask tokens only — no raw values)
- ``output_final``     (post-restore content the user actually saw)
- ``dlp_input_findings`` / ``dlp_output_findings`` (counts)
- ``dlp_categories``   (category names)
- ``policy_action``    (allow / mask / block)
- per-finding metadata in ``dlp_findings_json``: ``direction``,
  ``detector``, ``entity_type``, ``category``, ``severity``,
  ``start``, ``end``, ``action``, ``mask_token``, ``matched_text_len``

PostgreSQL only — uses ``jsonb_array_elements`` + the ``-`` operator
to remove a key from each entry. Falls back to a Python-side rewrite
if the column isn't a true jsonb (legacy SQLite test envs).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import text, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.request_log import RequestLogORM

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RetentionStats:
    """Outcome of one cleanup pass."""

    rows_with_raw_text_cleared: int
    rows_with_findings_stripped: int
    retention_days: int
    cutoff: str
    dry_run: bool


def _strip_matched_text_python(payload: str | None) -> str | None:
    """Pure-Python fallback for non-jsonb storage (e.g. SQLite tests)."""
    if not payload:
        return payload
    try:
        parsed = json.loads(payload)
    except Exception:
        return payload
    if not isinstance(parsed, list):
        return payload
    rewritten: list[dict] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            rewritten.append(entry)
            continue
        copy = dict(entry)
        if "matched_text_len" not in copy and isinstance(copy.get("matched_text"), str):
            copy["matched_text_len"] = len(copy["matched_text"])
        copy.pop("matched_text", None)
        rewritten.append(copy)
    return json.dumps(rewritten, ensure_ascii=False)


def run_dlp_retention_cleanup(
    session: Session,
    *,
    retention_days: int | None = None,
    dry_run: bool = False,
) -> RetentionStats:
    """Clear raw PII from ``request_logs`` rows older than the retention window.

    Parameters
    ----------
    session : Session
        Active SQLAlchemy session. The caller commits unless ``dry_run``.
    retention_days : int | None
        Override for ``settings.dlp_raw_retention_days``. Useful for
        ad-hoc / forced cleanups; pass ``0`` to clear everything.
    dry_run : bool
        When True, count what *would* be cleared but don't write.
    """
    days = retention_days if retention_days is not None else settings.dlp_raw_retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    raw_count_q = (
        session.query(RequestLogORM)
        .filter(RequestLogORM.created_at < cutoff)
        .filter(
            (RequestLogORM.input_raw.isnot(None))
            | (RequestLogORM.output_raw.isnot(None))
        )
    )
    raw_count = raw_count_q.count()

    findings_count = (
        session.query(RequestLogORM)
        .filter(RequestLogORM.created_at < cutoff)
        .filter(RequestLogORM.dlp_findings_json.isnot(None))
        .count()
    )

    if dry_run:
        stats = RetentionStats(
            rows_with_raw_text_cleared=raw_count,
            rows_with_findings_stripped=findings_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
            dry_run=True,
        )
        logger.info("dlp_retention_dry_run", **asdict(stats))
        return stats

    # Step 1 — NULL-ify raw text columns.
    session.execute(
        update(RequestLogORM)
        .where(RequestLogORM.created_at < cutoff)
        .where(
            (RequestLogORM.input_raw.isnot(None))
            | (RequestLogORM.output_raw.isnot(None))
        )
        .values(input_raw=None, output_raw=None)
    )

    # Step 2 — strip matched_text from each finding object.
    # Postgres path: do it in one round-trip using jsonb operators. The
    # ``-`` operator removes a top-level key; we rebuild the array via
    # jsonb_array_elements. Wrapped in a try so SQLite test envs (which
    # store the JSON as text) fall back to the Python rewrite.
    pg_succeeded = False
    try:
        session.execute(
            text(
                """
                UPDATE request_logs
                SET dlp_findings_json = (
                    SELECT jsonb_agg(entry - 'matched_text')::text
                    FROM jsonb_array_elements(dlp_findings_json::jsonb) AS entry
                )
                WHERE created_at < :cutoff
                  AND dlp_findings_json IS NOT NULL
                  AND dlp_findings_json::text != '[]'
                """
            ),
            {"cutoff": cutoff},
        )
        pg_succeeded = True
    except DatabaseError:
        # Roll back the failed sub-statement; raw column nulling above
        # remains in the transaction, will be re-applied via the Python
        # path below.
        session.rollback()

    if not pg_succeeded:
        # Fallback: re-do the raw-column null-out then patch findings in Python.
        session.execute(
            update(RequestLogORM)
            .where(RequestLogORM.created_at < cutoff)
            .where(
                (RequestLogORM.input_raw.isnot(None))
                | (RequestLogORM.output_raw.isnot(None))
            )
            .values(input_raw=None, output_raw=None)
        )
        rows = (
            session.query(RequestLogORM)
            .filter(RequestLogORM.created_at < cutoff)
            .filter(RequestLogORM.dlp_findings_json.isnot(None))
            .all()
        )
        for row in rows:
            row.dlp_findings_json = _strip_matched_text_python(row.dlp_findings_json)

    session.commit()

    stats = RetentionStats(
        rows_with_raw_text_cleared=raw_count,
        rows_with_findings_stripped=findings_count,
        retention_days=days,
        cutoff=cutoff.isoformat(),
        dry_run=False,
    )
    logger.info("dlp_retention_cleanup_complete", **asdict(stats))
    return stats
