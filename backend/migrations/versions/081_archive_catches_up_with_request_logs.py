"""081 — request_logs_archive catches up with request_logs.

FIX50 P1-E (OPEN_DECISIONS #57): four columns were added to the hot
table over time without touching the archive twin —

* ``reasoning_tokens`` / ``reasoning_cost`` (log-worker Track B)
* ``user_id`` (migration 062, per-user attribution)
* ``task_category`` (migration 079, auto-router)

The archive job's ``INSERT ... SELECT r.*`` has failed on every run
since ("INSERT has more expressions than target columns"), so the hot
table has grown unbounded and the archive has never received a row.
This migration re-aligns the schemas; the job itself now names its
columns and refuses to run on drift instead of failing mid-INSERT.

Revision ID: 081
Revises: 080
"""

from alembic import op

revision = "081"
down_revision = "080"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("reasoning_tokens", "integer DEFAULT 0"),
    ("reasoning_cost", "double precision DEFAULT 0.0"),
    ("user_id", "character varying(36)"),
    ("task_category", "character varying(40)"),
)


def upgrade() -> None:
    for name, ddl_type in _COLUMNS:
        op.execute(
            f"ALTER TABLE request_logs_archive "
            f"ADD COLUMN IF NOT EXISTS {name} {ddl_type}"
        )


def downgrade() -> None:
    for name, _ in _COLUMNS:
        op.execute(
            f"ALTER TABLE request_logs_archive DROP COLUMN IF EXISTS {name}"
        )
