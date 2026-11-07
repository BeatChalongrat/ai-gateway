"""Legacy-chain guard: a tool-bearing request never lands on a
tools-blind model.

The smart-routing path filters on ``has_tools`` inside the router, but a
caller that sends ``model="auto"`` with no routing hints falls through to
the semantic/keyword chain, where a rule can hand the turn to whatever
model it likes. This guard is the backstop for that path.

Called unbound — it reads nothing off ``self``, so the tests skip the
orchestrator's (heavy) construction.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.orchestrator import GatewayOrchestrator


class _Log:
    def __init__(self):
        self.events = []

    def info(self, event, **kw):
        self.events.append((event, kw))

    def warning(self, event, **kw):
        self.events.append((event, kw))


@pytest.fixture(autouse=True)
def _no_leaked_auto_preference():
    """Drop the routing-settings cache around every test in this file.

    These tests pass ``session=None`` to reach the hardcoded Auto
    preference, and ``get_auto_preference`` checks its process-global
    cache BEFORE it checks the session. So whenever an earlier test in
    the same process had warmed that cache from the database, this file
    silently asserted against the DATABASE's default model instead of
    the shipped one — and the swap target stopped being "gpt-4o".

    That is why it drifted with a fixed order and a fixed seed: the cache
    has a 30-second wall-clock TTL, so whether it was still warm by the
    time these tests ran depended on how fast the preceding ~4000 tests
    had gone that day. One run in three, near enough.
    """
    from app.services import routing_settings

    routing_settings.clear_cache()
    yield
    routing_settings.clear_cache()


def _guard(model: str, tools):
    payload = SimpleNamespace(model=model, tools=tools)
    log = _Log()
    GatewayOrchestrator._enforce_tool_capable_model(None, payload, None, log)
    return payload, log


# A real registry id that carries supports_tools=False (see
# model_registry SEED_CANDIDATES).
TOOLS_BLIND = "abab5.5-chat"


def test_tools_blind_model_is_swapped_for_the_auto_default():
    payload, log = _guard(TOOLS_BLIND, [{"type": "function"}])
    assert payload.model == "gpt-4o"
    assert log.events[0][0] == "routing_tool_capability_override"


def test_request_without_tools_is_left_alone():
    payload, _ = _guard(TOOLS_BLIND, None)
    assert payload.model == TOOLS_BLIND


def test_tool_capable_model_is_left_alone():
    payload, _ = _guard("gpt-4o-mini", [{"type": "function"}])
    assert payload.model == "gpt-4o-mini"


def test_unknown_model_is_left_alone():
    """The registry is a cache, not the authority on what exists."""
    payload, _ = _guard("some-private-deployment", [{"type": "function"}])
    assert payload.model == "some-private-deployment"


@pytest.mark.parametrize("tools", [[], None])
def test_empty_tool_list_counts_as_no_tools(tools):
    payload, _ = _guard(TOOLS_BLIND, tools)
    assert payload.model == TOOLS_BLIND
