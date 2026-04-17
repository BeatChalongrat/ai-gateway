"""KeywordRouter tests — pre-route model-hint engine."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
import app.models  # noqa: F401 — register every ORM model
from app.models.keyword_rule import KeywordRuleORM
from app.services.keyword_router import KeywordRouter, KeywordRule
from app.services.keyword_rule_service import KeywordRuleService


@pytest.fixture
def router() -> KeywordRouter:
    return KeywordRouter()


class TestDefaults:
    def test_code_keyword_upgrades_to_gpt4o(self, router):
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "debug this function for me"}],
            requested_model="gpt-4o-mini",
        )
        assert model == "gpt-4o"
        assert rule == "code_tasks"

    def test_analysis_keyword_upgrades_to_gpt4o(self, router):
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "Please analyze this dataset"}],
            requested_model="gpt-4o-mini",
        )
        assert model == "gpt-4o"
        assert rule == "complex_analysis"

    def test_simple_keyword_downgrades_to_mini(self, router):
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "What is 2+2?"}],
            requested_model="gpt-4o",
        )
        assert model == "gpt-4o-mini"
        assert rule == "simple_tasks"

    def test_no_match_returns_original(self, router):
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "Tell me a story about a dragon"}],
            requested_model="claude-3-5-sonnet-20241022",
        )
        assert model == "claude-3-5-sonnet-20241022"
        assert rule is None


class TestMatchingBehavior:
    def test_case_insensitive(self, router):
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "DEBUG THIS CODE"}],
            requested_model="gpt-4o-mini",
        )
        assert model == "gpt-4o"
        assert rule == "code_tasks"

    def test_scans_all_messages(self, router):
        model, rule = router.get_model_hint(
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "debug this error"},
            ],
            requested_model="gpt-4o-mini",
        )
        assert model == "gpt-4o"

    def test_empty_messages_returns_original(self, router):
        model, rule = router.get_model_hint([], requested_model="gpt-4o-mini")
        assert model == "gpt-4o-mini"
        assert rule is None

    def test_non_string_content_ignored(self, router):
        model, rule = router.get_model_hint(
            [{"role": "user", "content": None}],
            requested_model="gpt-4o-mini",
        )
        assert rule is None


class TestPriority:
    def test_higher_priority_rule_wins(self):
        # high-priority rule matches "tldr" (not the default "simple_tasks")
        router = KeywordRouter(
            rules=[
                KeywordRule(
                    name="low_priority",
                    model_override="low-model",
                    keywords=("tldr",),
                    priority=1,
                ),
                KeywordRule(
                    name="high_priority",
                    model_override="high-model",
                    keywords=("tldr",),
                    priority=100,
                ),
            ]
        )
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "tldr please"}],
            requested_model="ignored",
        )
        assert model == "high-model"
        assert rule == "high_priority"

    def test_custom_rules_replace_defaults(self):
        router = KeywordRouter(
            rules=[
                KeywordRule(
                    name="only_rule",
                    model_override="custom-model",
                    keywords=("magic-token",),
                    priority=1,
                )
            ]
        )
        # Default "debug this" keyword should NOT match when custom rules given
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "debug this code"}],
            requested_model="gpt-4o-mini",
        )
        assert model == "gpt-4o-mini"
        assert rule is None
        # But the custom keyword does
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "please magic-token"}],
            requested_model="gpt-4o-mini",
        )
        assert model == "custom-model"
        assert rule == "only_rule"


# ---------------------------------------------------------------------------
# allowed_providers guard + DB rules (Phase — part A)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _insert_keyword_rule(
    session,
    *,
    name: str,
    keywords: list[str],
    model_override: str,
    priority: int = 0,
    scope: str = "global",
    scope_id: str | None = None,
) -> KeywordRuleORM:
    row = KeywordRuleORM(
        id=str(uuid.uuid4()),
        name=name,
        keywords_json=KeywordRuleService.serialize_keywords(keywords),
        model_override=model_override,
        priority=priority,
        scope=scope,
        scope_id=scope_id,
        enabled=True,
    )
    session.add(row)
    session.flush()
    return row


class TestAllowedProvidersGuardAndDBRules:
    def test_keyword_hint_skips_blocked_provider(self):
        """code_tasks matches → gpt-4o (provider=openai). With
        allowed_providers={'anthropic'} the rule must be skipped and
        the requested_model returned unchanged."""
        router = KeywordRouter()
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "please debug this function"}],
            requested_model="claude-3-5-sonnet-20241022",
            allowed_providers=frozenset(["anthropic"]),
        )
        assert model == "claude-3-5-sonnet-20241022"
        assert rule is None

    def test_keyword_hint_uses_db_rule_over_default(self, db_session):
        """A DB rule with high priority beats the DEFAULT_RULES."""
        _insert_keyword_rule(
            db_session,
            name="thai_override",
            keywords=["thai"],
            model_override="qwen-plus",
            priority=20,
            scope="global",
        )
        svc = KeywordRuleService()
        db_rules = svc.get_rules_for_request(
            db_session, tenant_id="t", project_id="p"
        )
        router = KeywordRouter(db_rules=db_rules)
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "please translate this thai text"}],
            requested_model="gpt-4o-mini",
        )
        assert model == "qwen-plus"
        assert rule == "thai_override"

    def test_keyword_hint_project_rule_beats_global(self, db_session):
        """Project-scoped rule wins even with a numerically lower priority.

        KeywordRuleService applies a fixed scope offset (project > tenant
        > global) before handing the rules to KeywordRouter, so a
        project rule at priority=5 still outranks a global rule at
        priority=10.
        """
        _insert_keyword_rule(
            db_session,
            name="global_rule",
            keywords=["special"],
            model_override="gpt-4o-mini",
            priority=10,
            scope="global",
        )
        _insert_keyword_rule(
            db_session,
            name="project_rule",
            keywords=["special"],
            model_override="gpt-4o",
            priority=5,
            scope="project",
            scope_id="proj-A",
        )

        svc = KeywordRuleService()
        rules = svc.get_rules_for_request(
            db_session, tenant_id="t-A", project_id="proj-A"
        )
        router = KeywordRouter(rules=[], db_rules=rules)
        model, rule = router.get_model_hint(
            [{"role": "user", "content": "this is a special request"}],
            requested_model="auto",
        )
        assert rule == "project_rule"
        assert model == "gpt-4o"
