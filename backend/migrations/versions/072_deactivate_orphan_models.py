"""Deactivate the four adapter-less (orphan) model rows from 070.

071's investigation established that mistral / cohere / perplexity
have no live adapter — ``provider_dispatcher`` registers 12 adapters
and those three exist only as ``providers/_pending/`` stubs raising
ProviderNotImplementedError. The four rows 070 inserted for them are
therefore catalog-ready but natively unroutable:

    mistral/mistral-medium-latest, cohere/command-a,
    perplexity/sonar, perplexity/sonar-pro

They were already invisible in /v1/models (which filters by validated
provider keys, impossible without an adapter), but ``is_active=true``
still exposed them to admin listings and routing candidates. Hide
them until the adapters actually land — prices and metadata stay on
the rows, so re-activating is the only step needed then.

The pre-070 rows of the same providers (mistral-large/small,
codestral, command/command-r*, llama-3.1-sonar-*) are left as they
were — same orphan condition, but they pre-date this effort and the
owner has only decided on the 070 four; flagged in the MR instead.

Downgrade re-activates exactly these four (all were is_active=true).

Revision ID: 072
Revises: 071
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op


revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


_ORPHANS: list[tuple[str, str]] = [
    ("mistral", "mistral-medium-latest"),
    ("cohere", "command-a"),
    ("perplexity", "sonar"),
    ("perplexity", "sonar-pro"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for provider, model in _ORPHANS:
        conn.execute(
            sa.text(
                """
                UPDATE model_configs
                   SET is_active = false,
                       updated_at = now()
                 WHERE provider = :provider AND model = :model
                """
            ),
            {"provider": provider, "model": model},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for provider, model in _ORPHANS:
        conn.execute(
            sa.text(
                """
                UPDATE model_configs
                   SET is_active = true,
                       updated_at = now()
                 WHERE provider = :provider AND model = :model
                """
            ),
            {"provider": provider, "model": model},
        )
