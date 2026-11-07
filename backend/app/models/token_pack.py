from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TokenPackORM(Base):
    __tablename__ = "token_packs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_price_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # Phase 3 (migration 031): tenant-scoped packs. NULL means the
    # pack is global (visible to every tenant) — the initial seed
    # from migration 025 lands here. A non-null value means the
    # pack is only visible to that one tenant.
    tenant_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id", name="fk_token_packs_tenant_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
