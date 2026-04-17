"""DB-backed keyword rule loader.

Loads matching ``KeywordRuleORM`` rows for the current request's tenant
and project and returns a list of ``KeywordRule`` dataclasses ready to
pass into ``KeywordRouter(db_rules=...)``.

Scope priority — project > tenant > global — is enforced by adding a
large offset to the numeric priority when the rule is loaded. The
offsets are far larger than any reasonable user-supplied priority, so
a project rule with ``priority=1`` still outranks a global rule with
``priority=999``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from app.models.keyword_rule import KeywordRuleORM
from app.services.keyword_router import KeywordRule

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Scope priority offsets. Stacked on top of the user's numeric priority
# so the rules sort in the right order via ``KeywordRouter``'s existing
# ``sorted(..., key=priority, reverse=True)`` call.
_SCOPE_OFFSET = {
    "project": 10_000_000,
    "tenant": 5_000_000,
    "global": 0,
}


def _row_to_rule(row: KeywordRuleORM) -> KeywordRule:
    try:
        keywords = tuple(json.loads(row.keywords_json or "[]"))
    except (TypeError, ValueError):
        keywords = ()
    offset = _SCOPE_OFFSET.get(row.scope, 0)
    return KeywordRule(
        name=row.name,
        model_override=row.model_override,
        keywords=keywords,
        priority=row.priority + offset,
    )


class KeywordRuleService:

    def get_rules_for_request(
        self,
        session: "Session",
        tenant_id: str | None,
        project_id: str | None,
    ) -> list[KeywordRule]:
        """Return enabled rules whose scope matches the request.

        Includes:
          - ``scope='global'`` (always)
          - ``scope='tenant'`` + ``scope_id == tenant_id``
          - ``scope='project'`` + ``scope_id == project_id``
        """
        conditions = [KeywordRuleORM.scope == "global"]
        if tenant_id:
            conditions.append(
                (KeywordRuleORM.scope == "tenant")
                & (KeywordRuleORM.scope_id == tenant_id)
            )
        if project_id:
            conditions.append(
                (KeywordRuleORM.scope == "project")
                & (KeywordRuleORM.scope_id == project_id)
            )

        stmt = (
            select(KeywordRuleORM)
            .where(KeywordRuleORM.enabled == True)  # noqa: E712
            .where(or_(*conditions))
        )
        rows = session.execute(stmt).scalars().all()
        return [_row_to_rule(r) for r in rows]

    # ------------------------------------------------------------------
    # Admin CRUD helpers
    # ------------------------------------------------------------------

    def list_all(self, session: "Session") -> list[KeywordRuleORM]:
        stmt = select(KeywordRuleORM).order_by(KeywordRuleORM.created_at)
        return list(session.execute(stmt).scalars().all())

    def get(self, session: "Session", rule_id: str) -> KeywordRuleORM | None:
        return session.get(KeywordRuleORM, rule_id)

    @staticmethod
    def serialize_keywords(keywords: list[str]) -> str:
        return json.dumps(keywords)

    @staticmethod
    def deserialize_keywords(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            loaded = json.loads(raw)
            return [str(k) for k in loaded] if isinstance(loaded, list) else []
        except (TypeError, ValueError):
            return []
