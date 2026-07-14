from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ConnectorTokenORM(Base):
    """Per-user OAuth tokens for MCP connectors (Desktop Phase 7).

    Provider tokens NEVER leave the backend unencrypted: both columns
    hold AES-256-GCM blobs produced by ``security.encrypt_provider_key``
    with the same master key as provider keys. The desktop app only
    ever learns "connected / not connected"; the mcp-gateway fetches the
    (decrypted) access token server-to-server on the user's behalf via
    ``POST /portal/connectors/{id}/token`` — which refreshes first and
    never returns the refresh token.
    """

    __tablename__ = "connector_tokens"
    __table_args__ = (
        UniqueConstraint(
            "portal_user_id", "connector_id",
            name="uq_connector_tokens_user_connector",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    portal_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portal_users.id", name="fk_connector_tokens_portal_user"),
        nullable=False,
        index=True,
    )
    # Catalog connector id (e.g. "gmail", "slack") — scopes differ per
    # connector even on the same provider, so this is the natural key.
    connector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL = the provider issued a non-expiring token (e.g. GitHub OAuth
    # app tokens, Slack bot tokens) — no refresh cycle needed.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
