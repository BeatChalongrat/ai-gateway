"""Add reasoning token + cost columns to ``request_logs``.

Thinking Mode T3 completes the audit trail for reasoning-capable
calls. T2 surfaced ``reasoning_tokens`` on the response wire but
never persisted it; this migration adds the per-row columns so the
admin dashboard, usage aggregator, and billing exports can attribute
spend to reasoning separately from regular completion tokens.

* ``reasoning_tokens``  — int count. Sourced from
                          ``usage.completion_tokens_details.reasoning_tokens``
                          (OpenAI o-series), ``thoughtsTokenCount``
                          (Gemini 2.5+), or length-estimated for
                          Anthropic (which folds thinking into
                          ``output_tokens``). Defaults to 0 so existing
                          aggregations that ``SUM(reasoning_tokens)``
                          read sensible values for pre-T3 rows.
* ``reasoning_cost``    — USD cost attributable to reasoning tokens
                          alone. Calculated at the model's output-token
                          rate (matches how OpenAI / Anthropic bill
                          reasoning today). Float for parity with
                          ``estimated_cost_usd``.

### Downgrade

Drops the two columns. Historical reasoning spend is lost; the
``estimated_cost_usd`` total stays intact because it already includes
the reasoning portion via ``completion_tokens``.

Revision ID: 046
Revises: 045
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column(
            "reasoning_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "request_logs",
        sa.Column(
            "reasoning_cost",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "reasoning_cost")
    op.drop_column("request_logs", "reasoning_tokens")
