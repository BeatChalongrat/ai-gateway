"""SemanticRouter tests.

The real sentence-transformers model is not loaded in the test suite
(it's an optional dependency). Instead, each test injects a stub
encoder so we can pin exact similarities and control the classifier
deterministically. The tests exercise:

- Intent classification above threshold.
- Low-confidence fallback to ``general``.
- ``allowed_providers`` filtering of the intent→model map.
- The disabled stub.
- Orchestrator wiring when the feature flag is off.
"""

from __future__ import annotations

import pytest

from app.services.semantic_router import (
    INTENT_MODEL_MAP,
    SemanticRouter,
    SemanticRouterDisabled,
    _cosine,
)


# ---------------------------------------------------------------------------
# Stub encoder helpers
# ---------------------------------------------------------------------------


_INTENT_KEYS = ["coding", "analysis", "creative", "simple_qa", "translation", "general"]


def _one_hot(intent: str) -> list[float]:
    """Return a 6-dimensional one-hot vector for the given intent."""
    vec = [0.0] * len(_INTENT_KEYS)
    vec[_INTENT_KEYS.index(intent)] = 1.0
    return vec


def _router_with_fake_encoder(
    input_to_intent: dict[str, str], threshold: float = 0.65
) -> SemanticRouter:
    """Build a SemanticRouter with pre-populated anchors + a stub
    encoder driven by text fragments.

    We skip the real anchor-encoding pass entirely by setting
    ``router._anchors`` directly to orthogonal one-hot vectors. Then
    the encoder only runs for ``classify()`` inputs — each test can
    steer classify() deterministically by listing fragment → intent
    pairs in ``input_to_intent``.
    """

    def encode(text: str) -> list[float]:
        lowered = text.lower()
        for fragment, intent in input_to_intent.items():
            if fragment in lowered:
                return _one_hot(intent)
        return _one_hot("general")

    router = SemanticRouter(encoder=encode, threshold=threshold)
    router._anchors = {i: _one_hot(i) for i in _INTENT_KEYS}
    return router


# ---------------------------------------------------------------------------
# classify() / get_model_hint()
# ---------------------------------------------------------------------------


class TestClassifyAndHint:
    def test_classify_coding_intent(self):
        router = _router_with_fake_encoder(
            {"write a python function": "coding", "parse json": "coding"}
        )
        intent, confidence = router.classify(
            [{"role": "user", "content": "write a python function to parse JSON"}]
        )
        assert intent == "coding"
        assert confidence > 0.65

    def test_classify_simple_qa_intent(self):
        router = _router_with_fake_encoder(
            {"capital of france": "simple_qa"}
        )
        intent, confidence = router.classify(
            [{"role": "user", "content": "what is the capital of France"}]
        )
        assert intent == "simple_qa"
        assert confidence > 0.65

    def test_low_confidence_returns_general(self):
        # All-ones vector has cosine 1/sqrt(6) ≈ 0.408 against every
        # orthogonal one-hot anchor — below the 0.65 threshold.
        def stub(text: str) -> list[float]:
            return [1.0] * len(_INTENT_KEYS)

        router = SemanticRouter(encoder=stub, threshold=0.65)
        router._anchors = {i: _one_hot(i) for i in _INTENT_KEYS}

        model, intent, conf = router.get_model_hint(
            [{"role": "user", "content": "some off-topic blob"}]
        )
        assert model is None
        assert intent == "general"
        assert conf < 0.65

    def test_allowed_providers_filters_model_map(self):
        router = _router_with_fake_encoder({"write code": "coding"})

        # coding → [claude-3-5-sonnet-20241022, gpt-4o]. Only openai is
        # allowed → the preferred claude model is filtered out and the
        # router falls through to gpt-4o.
        model, intent, _conf = router.get_model_hint(
            [{"role": "user", "content": "write code to sort a list"}],
            allowed_providers=frozenset(["openai"]),
        )
        assert intent == "coding"
        assert model == "gpt-4o"


# ---------------------------------------------------------------------------
# Disabled stub
# ---------------------------------------------------------------------------


class TestSemanticRouterDisabled:
    def test_disabled_stub_returns_none(self):
        stub = SemanticRouterDisabled()
        model, intent, conf = stub.get_model_hint(
            [{"role": "user", "content": "anything"}]
        )
        assert model is None
        assert intent == "general"
        assert conf == 0.0

    def test_semantic_router_flag_false_uses_stub(self, monkeypatch):
        """When SEMANTIC_ROUTER_ENABLED=False the orchestrator must
        instantiate the no-op stub — never import sentence-transformers."""
        from app.core.config import settings
        from app.services.orchestrator import GatewayOrchestrator

        monkeypatch.setattr(settings, "semantic_router_enabled", False)
        orch = GatewayOrchestrator()
        assert isinstance(orch.semantic_router, SemanticRouterDisabled)


# ---------------------------------------------------------------------------
# Sanity: intent map is complete and _cosine behaves
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_intent_map_covers_all_classified_intents(self):
        # Every intent the classifier can return must have a model list.
        for intent in _INTENT_KEYS:
            assert intent in INTENT_MODEL_MAP
            assert INTENT_MODEL_MAP[intent]  # non-empty

    def test_cosine_identity(self):
        assert _cosine([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
        assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
