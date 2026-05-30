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
import time
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from app.models.keyword_rule import KeywordRuleORM
from app.services.keyword_router import KeywordRule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session


# Phase-1 B8: keyword rules are loaded on EVERY ``auto`` request (a sync
# DB SELECT on the event loop). They change rarely, so cache the resolved
# ``list[KeywordRule]`` per (tenant, project) for a short TTL. The values
# are plain immutable dataclasses (not ORM rows), so sharing them across
# requests is safe. Per-worker-process; a stale read self-heals within
# the TTL. ``clear_rules_cache()`` is exposed for explicit invalidation
# after an admin rule edit.
_RULES_CACHE_TTL_S = 30.0
_rules_cache: dict[tuple[str | None, str | None], tuple[float, list[KeywordRule]]] = {}


def clear_rules_cache() -> None:
    """Drop all cached keyword rules (call after an admin CRUD edit)."""
    _rules_cache.clear()


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
        cache_key = (tenant_id, project_id)
        mono = time.monotonic()
        cached = _rules_cache.get(cache_key)
        if cached is not None and cached[0] > mono:
            return cached[1]

        stmt = self._rules_stmt(tenant_id, project_id)
        rows = session.execute(stmt).scalars().all()
        rules = [_row_to_rule(r) for r in rows]
        _rules_cache[cache_key] = (mono + _RULES_CACHE_TTL_S, rules)
        return rules

    async def get_rules_for_request_async(
        self,
        session: "AsyncSession",
        tenant_id: str | None,
        project_id: str | None,
    ) -> list[KeywordRule]:
        """Async variant of :meth:`get_rules_for_request`.

        Identical scope/priority logic. The Phase-1 ``_rules_cache``
        (TTL'd resolved ``list[KeywordRule]`` per tenant/project) sits in
        front exactly as the sync path; only the cache-MISS DB SELECT is
        awaited. Cache dict access stays plain sync (no I/O).
        """
        cache_key = (tenant_id, project_id)
        mono = time.monotonic()
        cached = _rules_cache.get(cache_key)
        if cached is not None and cached[0] > mono:
            return cached[1]

        stmt = self._rules_stmt(tenant_id, project_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        rules = [_row_to_rule(r) for r in rows]
        _rules_cache[cache_key] = (mono + _RULES_CACHE_TTL_S, rules)
        return rules

    @staticmethod
    def _rules_stmt(tenant_id: str | None, project_id: str | None):
        """Build the enabled-rules-by-scope SELECT shared by the sync and
        async ``get_rules_for_request`` variants."""
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

        return (
            select(KeywordRuleORM)
            .where(KeywordRuleORM.enabled == True)  # noqa: E712
            .where(or_(*conditions))
        )

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
        # Keyword rules routinely contain Thai / CJK substrings — keep
        # the on-disk bytes legible (and 3-6x smaller than the
        # ``\uXXXX`` form) so admin UI edits round-trip cleanly.
        return json.dumps(keywords, ensure_ascii=False)

    @staticmethod
    def deserialize_keywords(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            loaded = json.loads(raw)
            return [str(k) for k in loaded] if isinstance(loaded, list) else []
        except (TypeError, ValueError):
            return []
