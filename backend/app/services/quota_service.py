"""Quota enforcement service.

Checks project-level request quotas before allowing requests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import QuotaExceededError
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM


class QuotaService:

    def check_request_quota(
        self, session: Session, project_id: str
    ) -> None:
        """Check if the project has exceeded its daily request quota.

        Raises QuotaExceededError if the limit is exceeded.
        Does nothing if no quota is set (max_requests_per_day is None).
        """
        project = session.get(ProjectORM, project_id)
        if project is None:
            return

        limit = project.max_requests_per_day
        if limit is None:
            return

        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        count = session.execute(
            select(func.count())
            .select_from(RequestLogORM)
            .where(
                RequestLogORM.project_id == project_id,
                RequestLogORM.created_at >= today_start,
            )
        ).scalar() or 0

        if count >= limit:
            raise QuotaExceededError(
                f"Project '{project_id}' has exceeded its daily request quota ({limit}).",
                details={
                    "project_id": project_id,
                    "limit": limit,
                    "used": count,
                },
            )
