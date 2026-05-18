"""Add ``input_raw_encrypted`` column to ``request_logs``.

Stores AES-256-GCM ciphertext of the user's original prompt (before
DLP masking) as base64(nonce || ciphertext || tag). The key lives in
``settings.dlp_audit_key`` (env: ``DLP_AUDIT_KEY``) and is held only
by the backend process — the DB row alone is not enough to recover
plaintext.

Purpose: admins viewing the Safe Trace panel need to see what the
user actually sent in stage 1 (User → Service). The plain
``input_raw`` column is intentionally kept at ``[MASKED_BY_DLP]``
for non-admin views and DB exports; the encrypted column is the
admin-only side channel decrypted on demand by
``GET /admin/requests/{id}/trace``.

Revision ID: 039
Revises: 038
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("input_raw_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "input_raw_encrypted")
