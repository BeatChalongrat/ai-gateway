"""Add custom_detectors + dlp_rule_audit_log — admin-configurable DLP (Sprint 6).

Detector-as-Data: admins define custom DLP detectors (regex / keyword list /
semantic) via the admin UI; they take effect instantly via Redis pub/sub
hot-reload alongside the 31 built-in detectors. GLOBAL scope only in Sprint 6
(no per-tenant).

Schema convention (same as migrations 055/056 — adapted to the REAL gateway
schema, not the idealized spec):

- All ids are app-generated ``String(36)`` UUIDs, NOT native ``UUID`` /
  ``gen_random_uuid()`` — the same migration + ORM run against the SQLite test
  engine and Postgres alike.
- ``created_by_user_id`` / ``changed_by_user_id`` reference
  ``portal_users.id`` (itself ``String(36)``), so the FK types match.
- JSON columns use ``sa.JSON`` with a ``JSONB`` variant on Postgres.
- ``ix_custom_detectors_active`` is PARTIAL on Postgres (only the
  hot ``active``/``testing`` rows the loader reads); under SQLite the
  predicate is dropped and it degrades to a full index (harmless).
- The audit FK is ``ON DELETE SET NULL`` so a hard-deleted detector keeps its
  audit trail (with a null detector id).

Revision ID: 061
Revises: 060
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


# JSON in production-Postgres becomes JSONB; under SQLite it stays JSON.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "custom_detectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        # pii | identity | financial | credential | secret | network | content_policy
        sa.Column("category", sa.String(32), nullable=False),
        # regex | keyword_list | semantic
        sa.Column("detection_type", sa.String(16), nullable=False),
        sa.Column("pattern", _JSON, nullable=False),
        sa.Column(
            "severity", sa.String(16), nullable=False, server_default=sa.text("'medium'")
        ),
        sa.Column(
            "default_action",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'mask'"),
        ),
        # draft | testing | active | disabled
        sa.Column(
            "status", sa.String(16), nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column(
            "test_cases", _JSON, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "test_passed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "last_match_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "timeout_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("portal_users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        # Enumerated columns get CHECK constraints so a bad write can never
        # produce a malformed/active detector (defence in depth — the service
        # + Pydantic layer validate first).
        sa.CheckConstraint(
            "status IN ('draft', 'testing', 'active', 'disabled')",
            name="ck_custom_detectors_status",
        ),
        sa.CheckConstraint(
            "detection_type IN ('regex', 'keyword_list', 'semantic')",
            name="ck_custom_detectors_detection_type",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_custom_detectors_severity",
        ),
        sa.CheckConstraint(
            "default_action IN ('mask', 'block', 'pass', 'restore')",
            name="ck_custom_detectors_default_action",
        ),
    )
    # Loader scan index — only active/testing rows are ever read on the hot
    # path, so the index is partial on Postgres to stay small.
    op.create_index(
        "ix_custom_detectors_active",
        "custom_detectors",
        ["status"],
        postgresql_where=sa.text("status IN ('active', 'testing')"),
    )

    op.create_table(
        "dlp_rule_audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "custom_detector_id",
            sa.String(36),
            sa.ForeignKey("custom_detectors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Snapshot of the name even if the detector is later hard-deleted.
        sa.Column("detector_name", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column(
            "changed_by_user_id",
            sa.String(36),
            sa.ForeignKey("portal_users.id"),
            nullable=False,
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("before_value", _JSON, nullable=True),
        sa.Column("after_value", _JSON, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_audit_log_detector",
        "dlp_rule_audit_log",
        ["custom_detector_id", "changed_at"],
    )
    op.create_index(
        "ix_audit_log_user",
        "dlp_rule_audit_log",
        ["changed_by_user_id", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_user", table_name="dlp_rule_audit_log")
    op.drop_index("ix_audit_log_detector", table_name="dlp_rule_audit_log")
    op.drop_table("dlp_rule_audit_log")
    op.drop_index("ix_custom_detectors_active", table_name="custom_detectors")
    op.drop_table("custom_detectors")
