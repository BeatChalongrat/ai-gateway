"""Add fallback-routing columns to request_logs (+ archive).

Revision ID: 042
Revises: 041
Create Date: 2026-05-18

Phase-2 fallback routing records, per request:
  * ``fallback_used``       — was the response served by a fallback
                              candidate rather than the originally
                              routed provider?
  * ``fallback_attempts``   — JSON-encoded list of failed prior
                              attempts. Each entry has ``provider``,
                              ``model``, ``error_code``,
                              ``error_message``, ``latency_ms``, and
                              ``attempted_at``. ``NULL`` when no
                              fallback ran. Stored as ``jsonb`` on
                              Postgres so the admin dashboard can
                              aggregate via ``->`` operators.
  * ``original_provider`` / ``original_model``
                              — the route the *primary* dispatch
                              targeted. Differs from ``provider`` /
                              ``model`` exactly when ``fallback_used``
                              is true; equal otherwise (set by the
                              orchestrator on every success path so
                              fallback rate calculations work without
                              a NULL-aware case).

The partition root (``request_logs`` after migration 041) gets the
columns; Postgres propagates the schema change to every child
partition. The cold-storage ``request_logs_archive`` mirrors the
schema so ``log_archive_job`` continues to use the bulk
``INSERT ... SELECT`` pattern unchanged.

### Downgrade

Drops the four columns from both tables. Loses the fallback audit
trail but keeps the rest of the row intact.
"""

from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


_TABLES = ("request_logs", "request_logs_archive")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS fallback_used boolean DEFAULT false"
        )
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS fallback_attempts jsonb"
        )
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS original_provider text"
        )
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS original_model text"
        )

    # Supports the /admin/dashboard/fallback-stats endpoint's
    # ``WHERE fallback_used = true`` filter. Tiny partial index — only
    # the rows that actually triggered a fallback are stored, so the
    # overhead on every-other write is negligible.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_request_logs_fallback_used "
        "ON request_logs (created_at DESC) "
        "WHERE fallback_used = true"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_request_logs_fallback_used")
    for table in _TABLES:
        for col in (
            "original_model",
            "original_provider",
            "fallback_attempts",
            "fallback_used",
        ):
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")
