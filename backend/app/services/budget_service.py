"""Budget cap enforcement using persisted usage/cost data."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import QuotaExceededError
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM


class BudgetService:

    def check_budget(self, session: Session, project_id: str) -> None:
        """Check if project has exceeded its budget_limit.

        Raises QuotaExceededError if accumulated cost exceeds the limit.
        Does nothing if budget_limit is None.
        """
        project = session.get(ProjectORM, project_id)
        if project is None or project.budget_limit is None:
            return

        total_cost = session.execute(
            select(func.coalesce(func.sum(RequestLogORM.estimated_cost_usd), 0.0))
            .where(RequestLogORM.project_id == project_id)
        ).scalar() or 0.0

        if total_cost >= project.budget_limit:
            raise QuotaExceededError(
                f"Project '{project_id}' has exceeded its budget limit "
                f"(${project.budget_limit:.2f}).",
                details={
                    "project_id": project_id,
                    "budget_limit": project.budget_limit,
                    "accumulated_cost": round(total_cost, 6),
                    "is_estimate": True,
                },
            )

    async def check_budget_async(
        self, session: AsyncSession, project_id: str
    ) -> None:
        """Async variant of :meth:`check_budget`.

        Identical logic on an :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
        Raises QuotaExceededError if accumulated cost exceeds the limit.
        Does nothing if budget_limit is None.
        """
        project = await session.get(ProjectORM, project_id)
        if project is None or project.budget_limit is None:
            return

        result = await session.execute(
            select(func.coalesce(func.sum(RequestLogORM.estimated_cost_usd), 0.0))
            .where(RequestLogORM.project_id == project_id)
        )
        total_cost = result.scalar() or 0.0

        if total_cost >= project.budget_limit:
            raise QuotaExceededError(
                f"Project '{project_id}' has exceeded its budget limit "
                f"(${project.budget_limit:.2f}).",
                details={
                    "project_id": project_id,
                    "budget_limit": project.budget_limit,
                    "accumulated_cost": round(total_cost, 6),
                    "is_estimate": True,
                },
            )
