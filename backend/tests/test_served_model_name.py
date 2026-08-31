"""FIX35 Part B — the response names the model that actually ran.

Two callers exist: the response builders (``served_model_name``) and the
usage/request-log writer (``resolved_log_model``). Both must resolve a
meta-router id ("auto" fallback sentinel / "openrouter/auto") to the model the
provider reported, and both must leave a concrete routed id verbatim so the
provider's dated aliases never fragment per-model aggregation.
"""

from __future__ import annotations

import pytest

from app.domain.observability import (
    META_ROUTER_MODELS,
    is_meta_router_model,
    resolved_log_model,
    served_model_name,
)


@pytest.mark.parametrize(
    "model, expected",
    [
        ("auto", True),
        ("openrouter/auto", True),
        ("somevendor/auto", True),
        ("gpt-4o-mini", False),
        ("anthropic/claude-sonnet-4.5", False),
        ("", False),
        (None, False),
    ],
)
def test_is_meta_router_model(model, expected):
    assert is_meta_router_model(model) is expected


def test_meta_router_set_is_the_two_ids_the_gateway_uses():
    assert META_ROUTER_MODELS == frozenset({"auto", "openrouter/auto"})


def test_served_meta_id_becomes_the_reported_model():
    assert served_model_name("auto", "deepseek/deepseek-v4-flash-0731") == "deepseek/deepseek-v4-flash-0731"
    assert served_model_name("openrouter/auto", "anthropic/claude-sonnet-4.5") == "anthropic/claude-sonnet-4.5"


def test_served_bare_sentinel_never_reaches_the_wire():
    # Before the first chunk (or a provider that omits ``model``) the bare
    # fallback sentinel is normalised to the router's public id.
    assert served_model_name("auto", None) == "openrouter/auto"
    assert served_model_name("auto", "") == "openrouter/auto"
    assert served_model_name("auto", "auto") == "openrouter/auto"
    assert served_model_name("openrouter/auto", None) == "openrouter/auto"


def test_served_concrete_route_stays_verbatim():
    # The alias the gateway resolved wins over the provider's dated name.
    assert served_model_name("gpt-4o-mini", "gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    assert served_model_name("gpt-4o-mini", None) == "gpt-4o-mini"


def test_log_model_matches_served_for_meta_ids_and_keeps_routed_otherwise():
    assert resolved_log_model("auto", "google/gemini-2.5-flash") == "google/gemini-2.5-flash"
    assert resolved_log_model("openrouter/auto", "openai/gpt-4o") == "openai/gpt-4o"
    assert resolved_log_model("gpt-4o", "gpt-4o-2024-08-06") == "gpt-4o"
    # No report yet → the routed id (unchanged from the pre-FIX35 contract).
    assert resolved_log_model("auto", None) == "auto"
