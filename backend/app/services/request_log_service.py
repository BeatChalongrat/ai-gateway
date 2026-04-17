"""Request log persistence and query service."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.observability import RequestRecord
from app.models.request_log import RequestLogORM


class RequestLogService:

    def persist(self, session: Session, record: RequestRecord) -> None:
        """Write a RequestRecord to the database."""
        log = RequestLogORM(
            request_id=record.request_id,
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            provider=record.provider,
            model=record.model,
            status=record.status.value if hasattr(record.status, "value") else str(record.status),
            policy_action=record.policy_action,
            latency_ms=record.latency_ms,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            total_tokens=record.total_tokens,
            estimated_cost_usd=record.estimated_cost_usd,
            error_code=record.error_code,
            dlp_input_findings=record.dlp_input_findings,
            dlp_input_action=record.dlp_input_action,
            dlp_output_findings=record.dlp_output_findings,
            dlp_categories=",".join(record.dlp_categories) if record.dlp_categories else None,
            router_used=record.router_used,
            policy_profile_id=record.policy_profile_id,
            input_raw=record.input_raw,
            input_masked=record.input_masked,
            output_raw=record.output_raw,
            output_final=record.output_final,
            dlp_findings_json=record.dlp_findings_json,
        )
        session.add(log)
        session.flush()

    def list_recent(
        self, session: Session, limit: int = 100
    ) -> list[RequestLogORM]:
        stmt = (
            select(RequestLogORM)
            .order_by(RequestLogORM.created_at.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    def list_by_project(
        self, session: Session, project_id: str, limit: int = 100
    ) -> list[RequestLogORM]:
        stmt = (
            select(RequestLogORM)
            .where(RequestLogORM.project_id == project_id)
            .order_by(RequestLogORM.created_at.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    def get_by_id(
        self, session: Session, request_id: str
    ) -> RequestLogORM | None:
        return session.get(RequestLogORM, request_id)

    def usage_summary_by_project(
        self, session: Session, project_id: str
    ) -> dict:
        """Aggregate usage for a project."""
        rows = self.list_by_project(session, project_id, limit=10000)
        total_tokens = sum(r.total_tokens for r in rows)
        total_cost = sum(r.estimated_cost_usd for r in rows)
        return {
            "project_id": project_id,
            "total_requests": len(rows),
            "total_tokens": total_tokens,
            "total_estimated_cost_usd": round(total_cost, 6),
        }

    def usage_summary_all(self, session: Session) -> dict:
        """Aggregate usage across every project.

        Uses a SQL ``SUM`` rather than loading rows into memory — the
        per-project variant's 10k row cap is a pragmatic compromise,
        but the all-projects path can easily blow past that on a real
        deployment. ``project_id`` in the returned dict is ``None`` so
        clients can distinguish "total across everything" from any
        individual project's summary.
        """
        from sqlalchemy import func

        stmt = select(
            func.count().label("count"),
            func.coalesce(func.sum(RequestLogORM.total_tokens), 0).label("tokens"),
            func.coalesce(
                func.sum(RequestLogORM.estimated_cost_usd), 0.0
            ).label("cost"),
        )
        row = session.execute(stmt).one()
        return {
            "project_id": None,
            "total_requests": int(row.count or 0),
            "total_tokens": int(row.tokens or 0),
            "total_estimated_cost_usd": round(float(row.cost or 0.0), 6),
        }
