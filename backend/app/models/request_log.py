from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import String, Integer, Float, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class RequestLogORM(Base):
    __tablename__ = "request_logs"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50))
    policy_action: Mapped[str] = mapped_column(String(50))
    latency_ms: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    prompt_tokens: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    estimated_cost_usd: Mapped[float] = mapped_column(Float, server_default=sa.text("0.0"))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # User feedback (populated by POST /v1/feedback)
    feedback_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback_comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # DLP findings summary (populated by the orchestrator after a round-trip).
    # Raw matched values are never stored here — only counts, the worst action,
    # and the set of categories found.
    dlp_input_findings: Mapped[int] = mapped_column(
        Integer, server_default=sa.text("0")
    )
    dlp_input_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dlp_output_findings: Mapped[int] = mapped_column(
        Integer, server_default=sa.text("0")
    )
    # Stored as a comma-separated list so SQLite doesn't need JSON support.
    dlp_categories: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Which router won the routing decision: "semantic" / "keyword" / "service".
    # Added in migration 018_add_keyword_rules.
    router_used: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Effective policy profile applied to this request — the project
    # default OR a per-request ``policy_profile_id`` override. Added
    # in migration 020_add_policy_profile_id_to_request_logs.
    policy_profile_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # DLP trace (migration 021). Four message snapshots bounding the
    # pipeline plus a JSON-encoded list of finding objects. Unlike the
    # summary columns above, these hold raw content — admin endpoint
    # auth is the only access control.
    input_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_masked: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_final: Mapped[str | None] = mapped_column(Text, nullable=True)
    dlp_findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
