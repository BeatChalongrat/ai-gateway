"""Reasoning on a self-hosted, OpenAI-compatible box.

The reported symptom was "vLLM sends ``reasoning_content`` in the delta
but ``openai_compatible_adapter`` doesn't know the field". The adapter
does know it — it shares ``_parse_sse_line`` with the OpenAI adapter,
which has read both spellings since the OpenRouter fix. The first tests
here pin that, so the wrong diagnosis cannot be re-derived.

The real gap was three layers up: ``_infer_reasoning`` only ever
recognised OpenAI's o-series, so every other reasoning model depended on
a seed migration naming it — fine for the cloud catalogs, impossible for
a box the operator runs themselves. The row landed with
``supports_reasoning=False``, the orchestrator's auto-enable bailed on
that flag, ``show_summary`` stayed off, and the reasoning bytes the
adapter had already parsed were dropped before they reached the client.
"""

from __future__ import annotations

import pytest

from app.domain.routing import ModelCandidate
from app.schemas.public import ReasoningConfig
from app.services.discovery.provider_discovery import (
    _REASONING_MODEL_MARKERS,
    _infer_reasoning,
)
from app.services.providers import model_registry
from app.services.providers.openai_adapter import _parse_sse_line
from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter
from app.services.providers.parsers import parse_openai_response
from app.services.providers.reasoning_strategies import (
    KNOWN_STRATEGIES,
    STRATEGIES_WITH_EFFORT_KNOB,
    STRATEGY_GEMINI_THINKING,
    STRATEGY_NONE,
    STRATEGY_OPENAI_COMPATIBLE,
    STRATEGY_OPENAI_RESPONSES,
    STRATEGY_REASONING_CONTENT,
)


# ---------------------------------------------------------------------------
# The parsers already read the field — pin it
# ---------------------------------------------------------------------------

def test_stream_delta_reasoning_content_is_parsed():
    """Exactly the frame a vLLM box emits."""
    line = (
        'data: {"model":"qwen3-27b","choices":[{"delta":'
        '{"reasoning_content":"let me think"},"finish_reason":null}]}'
    )
    chunk = _parse_sse_line(line, provider_name="sranai")
    assert chunk is not None
    assert chunk.reasoning_delta == "let me think"


def test_non_streaming_reasoning_content_is_parsed():
    data = {
        "id": "x",
        "model": "qwen3-27b",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "4",
                    "reasoning_content": "2+2",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    assert parse_openai_response(data).reasoning_content == "2+2"


# ---------------------------------------------------------------------------
# Inference — the actual gap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model_id",
    [
        "qwen3.8-27b-fp8",          # the model the live box serves
        "Qwen3-235B-A22B",          # case is not significant
        "deepseek-r1",
        "deepseek-r1-distill-qwen-32b",
        "qwq-32b",
        "qvq-72b-preview",
        "glm-4.6-thinking",
        "magistral-small",
        "gpt-oss-120b",
        "phi-4-reasoning-plus",
        "exaone-deep-32b",
        "minimax-m2",
        "glm-z1-9b",
    ],
)
def test_self_hosted_reasoning_families_are_recognised(model_id):
    supports, strategy = _infer_reasoning("sranai", model_id)
    assert supports is True
    # Parse-only: the gateway reads what the box streams and sends
    # nothing extra, because a box that rejects unknown request fields
    # must not be broken by a guess.
    assert strategy == STRATEGY_REASONING_CONTENT


@pytest.mark.parametrize(
    "model_id",
    ["llama-3.3-70b-instruct", "mistral-small-latest", "gemma-3-27b-it", "gpt-4o"],
)
def test_ordinary_chat_models_are_not_flagged(model_id):
    assert _infer_reasoning("sranai", model_id) == (False, STRATEGY_NONE)


def test_openai_keeps_its_native_strategy_and_takes_no_marker():
    """The o-series branch owns ``openai`` outright.

    A marker leaking into the native adapter would hand it a strategy
    meant for the compatible wire, and the Responses-API path would stop
    firing. ``gpt-oss`` is the trap: it matches a marker AND is an
    OpenAI-branded name.
    """
    assert _infer_reasoning("openai", "o3-mini") == (
        True,
        STRATEGY_OPENAI_RESPONSES,
    )
    assert _infer_reasoning("openai", "gpt-oss-120b") == (False, STRATEGY_NONE)
    assert _infer_reasoning("openai", "gpt-4o") == (False, STRATEGY_NONE)
    # …while the same id on someone else's box IS recognised.
    assert _infer_reasoning("sranai", "gpt-oss-120b") == (
        True,
        STRATEGY_REASONING_CONTENT,
    )


def test_markers_are_lowercase():
    """The matcher lowercases the id, so an uppercase marker never fires."""
    for marker in _REASONING_MODEL_MARKERS:
        assert marker == marker.lower(), marker


# ---------------------------------------------------------------------------
# The effort knob goes out only where it is welcome
# ---------------------------------------------------------------------------

def _candidate(provider, model, *, strategy):
    return ModelCandidate(
        provider=provider,
        model=model,
        supports_reasoning=True,
        reasoning_strategy=strategy,
        default_reasoning_mode="auto",
    )


def _payload(model, **kw):
    from app.schemas.common import Message
    from app.schemas.public import ChatCompletionsRequest

    return ChatCompletionsRequest(
        model=model, messages=[Message(role="user", content="hi")], **kw
    )


def test_parse_only_row_sends_no_reasoning_effort(monkeypatch):
    """The whole point of the parse-only strategy.

    vLLM and friends validate the request body; an unasked-for
    ``reasoning_effort`` can turn a working reasoning model into a 400.
    """
    monkeypatch.setattr(
        model_registry,
        "get_candidate",
        lambda m: _candidate("sranai", m, strategy=STRATEGY_REASONING_CONTENT),
    )
    adapter = OpenAICompatibleAdapter()
    adapter.provider_name = "sranai"
    adapter.base_url = "http://box:8000/v1"
    payload = _payload("qwen3-27b", reasoning=ReasoningConfig(mode="high"))
    body = adapter._build_body(payload, payload.messages, resolved_model="qwen3-27b")
    assert "reasoning_effort" not in body


def test_knob_taking_row_still_sends_reasoning_effort(monkeypatch):
    """DeepSeek/Groq seed rows are untouched by the gate."""
    monkeypatch.setattr(
        model_registry,
        "get_candidate",
        lambda m: _candidate("deepseek", m, strategy=STRATEGY_OPENAI_COMPATIBLE),
    )
    adapter = OpenAICompatibleAdapter()
    adapter.provider_name = "deepseek"
    adapter.base_url = "https://api.deepseek.com/v1"
    payload = _payload("deepseek-reasoner", reasoning=ReasoningConfig(mode="high"))
    body = adapter._build_body(
        payload, payload.messages, resolved_model="deepseek-reasoner"
    )
    assert body["reasoning_effort"] == "high"


def test_effort_knob_set_matches_the_strategy_that_documents_it():
    assert STRATEGIES_WITH_EFFORT_KNOB == {STRATEGY_OPENAI_COMPATIBLE}
    assert STRATEGY_REASONING_CONTENT not in STRATEGIES_WITH_EFFORT_KNOB


def test_every_strategy_the_adapters_branch_on_is_admin_settable():
    """A strategy an adapter honours but the API rejects is unreachable."""
    for strategy in (
        STRATEGY_NONE,
        STRATEGY_REASONING_CONTENT,
        STRATEGY_OPENAI_COMPATIBLE,
        STRATEGY_OPENAI_RESPONSES,
        STRATEGY_GEMINI_THINKING,
    ):
        assert strategy in KNOWN_STRATEGIES


# ---------------------------------------------------------------------------
# Admin override — the escape hatch for anything the markers miss
# ---------------------------------------------------------------------------

def test_admin_update_rejects_an_unknown_strategy():
    from pydantic import ValidationError as PydanticValidationError

    from app.api.routes.admin_model_discovery import ModelConfigUpdate

    ModelConfigUpdate(reasoning_strategy=STRATEGY_REASONING_CONTENT)  # accepted
    with pytest.raises(PydanticValidationError):
        ModelConfigUpdate(reasoning_strategy="openai_compatible_reasonig")  # typo


def test_admin_update_rejects_an_unknown_default_mode():
    from pydantic import ValidationError as PydanticValidationError

    from app.api.routes.admin_model_discovery import ModelConfigUpdate

    ModelConfigUpdate(default_reasoning_mode="medium")  # accepted
    with pytest.raises(PydanticValidationError):
        ModelConfigUpdate(default_reasoning_mode="maximum")


# ---------------------------------------------------------------------------
# The live shape: a row that already exists, flagged False
# ---------------------------------------------------------------------------

def test_existing_row_is_promoted_by_the_next_sync():
    """The self-hosted row is already in the catalog, saying False.

    Discovery inserted it before the markers existed, so the fix has to
    reach it through the UPDATE path, not the insert path. The persist
    step is promote-only, which is what makes this safe to ship: the same
    pass cannot demote a seed row an inference misses.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.db import Base
    from app.models.model_config import ModelConfigORM
    from app.services.discovery.discovery_service import ModelDiscoveryService
    from app.services.discovery.provider_discovery import DiscoveredModel

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            ModelConfigORM(
                provider="sranai",
                model="qwen3.8-27b-fp8",
                supports_reasoning=False,          # what the live row says
                reasoning_strategy=STRATEGY_NONE,
                is_active=True,
                is_manual_override=False,
            )
        )
        # A seed row an inference would miss — it must survive untouched.
        session.add(
            ModelConfigORM(
                provider="deepseek",
                model="deepseek-reasoner",
                supports_reasoning=True,
                reasoning_strategy=STRATEGY_OPENAI_COMPATIBLE,
                is_active=True,
                is_manual_override=False,
            )
        )
        session.commit()

        supports, strategy = _infer_reasoning("sranai", "qwen3.8-27b-fp8")
        discovered = [
            DiscoveredModel(
                provider="sranai",
                model="qwen3.8-27b-fp8",
                supports_reasoning=supports,
                reasoning_strategy=strategy,
            ),
            # Discovery does not infer this one (no marker matches the
            # bare id on a fresh sync); it must not be demoted.
            DiscoveredModel(provider="deepseek", model="deepseek-reasoner"),
        ]
        ModelDiscoveryService()._persist_results(
            session,
            discovered=discovered,
            seen_keys={(m.provider, m.model) for m in discovered},
            synced_providers={"sranai", "deepseek"},
            provider_filter=None,
        )
        session.expire_all()

        promoted = (
            session.query(ModelConfigORM)
            .filter_by(provider="sranai", model="qwen3.8-27b-fp8")
            .one()
        )
        assert promoted.supports_reasoning is True
        assert promoted.reasoning_strategy == STRATEGY_REASONING_CONTENT

        seeded = (
            session.query(ModelConfigORM)
            .filter_by(provider="deepseek", model="deepseek-reasoner")
            .one()
        )
        assert seeded.supports_reasoning is True
        assert seeded.reasoning_strategy == STRATEGY_OPENAI_COMPATIBLE
    finally:
        session.close()
        engine.dispose()
