from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import String, Integer, Float, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
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
    # Counts, worst action, and category set only — these summary columns
    # never contain raw PII and are safe to retain indefinitely. The raw
    # content lives in the trace columns further down (input_raw / output_raw
    # / dlp_findings_json), each of which has its own privacy gate.
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

    # DLP trace columns (migration 021). Four message snapshots bounding
    # the pipeline plus a JSON-encoded list of per-finding objects.
    #
    # Privacy posture (Fix 4):
    # - ``input_raw`` and ``output_raw`` hold verbatim message content —
    #   the user's actual passport / card / email and the provider's
    #   response before mask-token restore. The /admin/requests/{id}
    #   endpoint gates these two columns behind ``Role.ADMIN`` (see
    #   admin.py::get_request); lower admin tiers receive ``None`` for
    #   them while still seeing the masked snapshots and per-finding
    #   metadata.
    # - ``dlp_findings_json`` includes ``matched_text`` (raw values).
    #   The same admin endpoint strips ``matched_text`` for non-ADMIN
    #   callers via ``_strip_raw_from_findings``, leaving
    #   ``matched_text_len`` as a privacy-safe replacement.
    # - Both ``input_raw`` / ``output_raw`` and the ``matched_text``
    #   keys inside ``dlp_findings_json`` are NULL-ified by the
    #   ``app.jobs.dlp_retention.run_dlp_retention_cleanup`` job after
    #   ``settings.dlp_raw_retention_days`` (default 90). Masked
    #   snapshots, summary counts, and per-finding metadata survive.
    input_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_masked: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_final: Mapped[str | None] = mapped_column(Text, nullable=True)
    dlp_findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AES-256-GCM ciphertext of the user's original prompt — written
    # by the orchestrator BEFORE DLP masking. Decrypted on demand by
    # ``GET /admin/requests/{id}/trace`` (audited). The DB row alone
    # is not enough to recover plaintext; the key (DLP_AUDIT_KEY env)
    # lives only in the backend process. See migration 039.
    input_raw_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase-2 fallback routing (migration 042).
    #
    # When the primary dispatch fails with a fallback-eligible error
    # (see ``FALLBACK_ELIGIBLE_ERRORS`` in fallback_orchestrator), the
    # gateway tries up to 2 cross-provider candidates. ``provider`` /
    # ``model`` always describe what *actually answered*, while
    # ``original_provider`` / ``original_model`` describe what the
    # routing engine *originally picked*. On non-fallback requests the
    # pairs are equal so dashboard queries don't need NULL handling.
    #
    # ``fallback_attempts`` is a list of attempt dicts shaped as
    # ``[{"provider","model","error_code","error_message",
    # "latency_ms","attempted_at"}]``. Mapped to ``JSONB`` so the
    # parameter is bound natively (psycopg sends the value as JSON,
    # not VARCHAR) — sending a ``::VARCHAR`` cast against a jsonb
    # column makes Postgres reject the INSERT with DatatypeMismatch.
    # The dashboard can aggregate via the ``->`` operator unchanged.
    fallback_used: Mapped[bool] = mapped_column(
        sa.Boolean, server_default=sa.text("false"), default=False
    )
    # Portable JSON column: ``JSONB`` on Postgres (native binary,
    # supports ``->`` operators) and generic ``JSON`` on SQLite so
    # the test suite's in-memory engine can still create the table.
    # The ``with_variant`` form is the canonical SQLAlchemy way to
    # diverge dialect-specific behaviour without forking the column.
    fallback_attempts: Mapped[list | None] = mapped_column(
        sa.JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    original_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_model: Mapped[str | None] = mapped_column(Text, nullable=True)
