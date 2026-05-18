"""Keyword-based model hint — runs BEFORE RoutingService.select_route.

This is a *pre-route hint*: it inspects message content for keywords and may
override the requested model. Provider/cost/latency selection is still
performed by the existing RoutingService on whatever model we return.

Rules are evaluated in priority order; the first match wins. If no rule
matches, the caller-requested model is returned unchanged.

DB-backed rules (``KeywordRuleORM``) can be loaded via
``KeywordRuleService.get_rules_for_request`` and passed into the
constructor — they merge with ``DEFAULT_RULES`` and are sorted by the
same priority key. ``KeywordRuleService`` gives project-scoped rules a
priority offset so they always beat tenant-scoped rules which always
beat global/default rules, regardless of their numeric priority.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.providers.model_registry import resolve_provider


# Floor that ``KeywordRuleService._SCOPE_OFFSET["project"]`` adds to any
# project-scoped DB rule's numeric priority. Mirrored here so
# ``get_project_scoped_hint`` can filter the rule list to only the
# project band without re-importing the service module. Keep these two
# constants in sync.
PROJECT_SCOPE_PRIORITY_FLOOR = 10_000_000


@dataclass(frozen=True)
class KeywordRule:
    name: str
    model_override: str
    keywords: tuple[str, ...]
    priority: int = 0


DEFAULT_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        name="complex_analysis",
        model_override="gpt-4o",
        keywords=(
            "analyze",
            "research",
            "compare in detail",
            "comprehensive",
            "evaluate thoroughly",
            "detailed report",
        ),
        priority=10,
    ),
    KeywordRule(
        name="code_tasks",
        model_override="gpt-4o",
        keywords=(
            "write code",
            "debug this",
            "fix the bug",
            "refactor",
            "unit test",
            "algorithm",
            "implement a function",
        ),
        priority=10,
    ),
    KeywordRule(
        name="simple_tasks",
        model_override="gpt-4o-mini",
        keywords=(
            "tldr",
            "one sentence",
            "briefly",
            "simple question",
            "what is",
            "define ",
            "yes or no",
        ),
        priority=5,
    ),
)


class KeywordRouter:
    """Evaluates keyword rules against message content and returns a model hint.

    Parameters
    ----------
    rules : list[KeywordRule] | tuple[KeywordRule, ...] | None
        Full rule list. Defaults to DEFAULT_RULES.
    db_rules : list[KeywordRule] | None
        Extra rules (typically loaded from ``keyword_rules`` via
        ``KeywordRuleService``) to merge on top of ``rules``. Useful
        when the caller wants per-request DB rules in addition to the
        hardcoded defaults.
    """

    def __init__(
        self,
        rules: list[KeywordRule] | tuple[KeywordRule, ...] | None = None,
        db_rules: list[KeywordRule] | None = None,
    ) -> None:
        base = tuple(rules) if rules is not None else DEFAULT_RULES
        combined = list(base)
        if db_rules:
            combined.extend(db_rules)
        self.rules: tuple[KeywordRule, ...] = tuple(
            sorted(combined, key=lambda r: r.priority, reverse=True)
        )

    def get_model_hint(
        self,
        messages: list[dict],
        requested_model: str,
        allowed_providers: frozenset[str] | set[str] | None = None,
    ) -> tuple[str, str | None]:
        """Return ``(model_to_use, matched_rule_name or None)``.

        If ``allowed_providers`` is given, any matching rule whose
        ``model_override`` resolves to a provider NOT in that set is
        silently skipped — iteration continues to the next rule so a
        lower-priority rule targeting an allowed provider can still
        win. If every surviving rule is filtered out, the requested
        model is returned unchanged (i.e. the keyword router defers
        to ``RoutingService``).
        """
        text_parts: list[str] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            content = m.get("content")
            if isinstance(content, str):
                text_parts.append(content.lower())
        text = " ".join(text_parts)

        if not text:
            return requested_model, None

        for rule in self.rules:
            if not any(keyword.lower() in text for keyword in rule.keywords):
                continue
            if allowed_providers is not None:
                provider = resolve_provider(rule.model_override)
                if provider is not None and provider not in allowed_providers:
                    continue
            return rule.model_override, rule.name

        return requested_model, None

    def get_project_scoped_hint(
        self,
        messages: list[dict],
        requested_model: str,
        allowed_providers: frozenset[str] | set[str] | None = None,
    ) -> tuple[str, str | None]:
        """Return a hint, but consider project-scoped DB rules only.

        Project rules carry a fixed priority offset
        (``PROJECT_SCOPE_PRIORITY_FLOOR``) applied by
        :class:`KeywordRuleService`. The orchestrator consults this
        method *before* the semantic router so a tenant's explicit
        per-project routing always wins — even when the embedding
        classifier is highly confident on the same prompt. Without
        the project band the rule would be drowned out by semantic.

        DEFAULT_RULES and tenant / global DB rules sit below the floor
        and are deliberately ignored here; they are evaluated later by
        the regular ``get_model_hint`` pass.
        """
        text_parts: list[str] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            content = m.get("content")
            if isinstance(content, str):
                text_parts.append(content.lower())
        text = " ".join(text_parts)

        if not text:
            return requested_model, None

        for rule in self.rules:
            if rule.priority < PROJECT_SCOPE_PRIORITY_FLOOR:
                continue
            if not any(keyword.lower() in text for keyword in rule.keywords):
                continue
            if allowed_providers is not None:
                provider = resolve_provider(rule.model_override)
                if provider is not None and provider not in allowed_providers:
                    continue
            return rule.model_override, rule.name

        return requested_model, None
