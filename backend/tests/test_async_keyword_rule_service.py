"""Track A: async variant of KeywordRuleService.

Verifies ``get_rules_for_request_async`` loads scoped rules via the
awaited SELECT and honours the Phase-1 ``_rules_cache``.
"""

import uuid

import pytest

from app.models.keyword_rule import KeywordRuleORM
from app.services import keyword_rule_service
from app.services.keyword_rule_service import (
    KeywordRuleService,
    clear_rules_cache,
)
import app.models  # noqa: F401


@pytest.mark.asyncio
async def test_get_rules_for_request_async_loads_global_rule(async_db):
    clear_rules_cache()
    name = f"async-rule-{uuid.uuid4().hex[:8]}"
    async_db.add(
        KeywordRuleORM(
            id=str(uuid.uuid4()),
            name=name,
            scope="global",
            scope_id=None,
            keywords_json='["urgent"]',
            model_override="gpt-4o",
            priority=5,
            enabled=True,
        )
    )
    await async_db.flush()

    # Unique tenant/project cache key so this test owns its cache slot.
    tenant = f"t-{uuid.uuid4().hex[:6]}"
    project = f"p-{uuid.uuid4().hex[:6]}"
    rules = await KeywordRuleService().get_rules_for_request_async(
        async_db, tenant, project
    )
    names = {r.name for r in rules}
    assert name in names
    clear_rules_cache()


@pytest.mark.asyncio
async def test_get_rules_for_request_async_uses_cache(async_db):
    clear_rules_cache()
    tenant = f"t-{uuid.uuid4().hex[:6]}"
    project = f"p-{uuid.uuid4().hex[:6]}"
    # First call: cache miss -> resolves (likely just global rules) and
    # caches the list under (tenant, project).
    first = await KeywordRuleService().get_rules_for_request_async(
        async_db, tenant, project
    )
    assert (tenant, project) in keyword_rule_service._rules_cache
    # Second call returns the SAME cached list object (no re-query).
    second = await KeywordRuleService().get_rules_for_request_async(
        async_db, tenant, project
    )
    assert second is first
    clear_rules_cache()
