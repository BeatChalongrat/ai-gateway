from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class KeywordRuleORM(Base):
    """DB-backed keyword → model override rule.

    ``keywords_json`` is a JSON-encoded list of strings. Looked up by
    scope (global/tenant/project) via KeywordRuleService at request
    time and merged with the hardcoded DEFAULT_RULES.
    """

    __tablename__ = "keyword_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    keywords_json: Mapped[str] = mapped_column(Text(), server_default="[]")
    model_override: Mapped[str] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    scope: Mapped[str] = mapped_column(String(20), server_default="global")
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=sa.text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
