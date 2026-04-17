from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Float, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class ProjectORM(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.tenant_id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    policy_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_requests_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
