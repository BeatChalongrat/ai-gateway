"""Embedding-based intent classifier with per-intent model routing.

The semantic router runs between the keyword router and
``RoutingService.select_route`` inside the orchestrator:

    semantic → keyword (+ allowed_providers guard) → service

When the embedding similarity to the best-matching intent anchor
clears ``threshold`` it returns a concrete model hint — that decision
overrides any keyword-router hint. Below threshold it returns
``(None, 'general', score)`` which signals "defer to the keyword
router or routing service."

Design notes
------------
- ``sentence-transformers`` is an *optional* dependency (torch pulls in
  hundreds of MB). The import happens lazily inside ``_load_model`` so
  simply instantiating ``SemanticRouter`` — or running the test suite
  with ``SEMANTIC_ROUTER_ENABLED=False`` — never touches it.
- The encoder is injectable via the ``encoder`` kwarg so unit tests
  can drive classification with a deterministic stub instead of a
  real embedding model.
- ``SemanticRouterDisabled`` is a no-op stub used when the flag is
  off; it lets the orchestrator wire to a single type without any
  feature-flag branching at the call site.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

import structlog

from app.services.providers.model_registry import resolve_provider

logger = structlog.get_logger(__name__)


# Six built-in intents. Each is anchored on a short descriptive
# sentence that the encoder embeds once at startup. At classify() time
# we compare the incoming text's embedding to each anchor.
_INTENT_DESCRIPTIONS: dict[str, str] = {
    "coding": "Write, debug, or refactor source code in a programming language.",
    "analysis": "Analyse data, compare options, or produce a detailed report.",
    "creative": "Write a story, poem, marketing copy, or other creative content.",
    "simple_qa": "Answer a short factual question with a brief reply.",
    "translation": "Translate text from one natural language to another.",
    "general": "General-purpose conversational assistant request.",
}


# Intent → preferred model list (highest preference first). The
# semantic router walks this list and returns the first model whose
# provider is allowed by the current request's ``allowed_providers``
# constraint (or the first entry if no constraint is given).
INTENT_MODEL_MAP: dict[str, list[str]] = {
    "coding":      ["claude-3-5-sonnet-20241022", "gpt-4o"],
    "analysis":    ["claude-3-5-sonnet-20241022", "gpt-4o"],
    "creative":    ["claude-3-5-sonnet-20241022", "gpt-4o"],
    "simple_qa":   ["gpt-4o-mini", "qwen-turbo"],
    "translation": ["qwen-plus", "gpt-4o-mini"],
    "general":     ["gpt-4o-mini"],
}


Encoder = Callable[[str], "Sequence[float]"]


class SemanticRouter:
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        threshold: float = 0.65,
        encoder: Encoder | None = None,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._encoder = encoder
        self._model = None
        self._anchors: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, messages: list[dict]) -> tuple[str, float]:
        """Return ``(best_intent, best_score)`` for the given messages.

        If the best score is below ``threshold`` the method returns
        ``("general", best_score)`` — callers check the score to
        distinguish a deliberate "general" classification from a
        low-confidence fallback.
        """
        self._ensure_loaded()
        text = self._join_messages(messages)
        if not text:
            return ("general", 0.0)

        vec = list(self._encode(text))
        best_intent = "general"
        best_score = -1.0
        for intent, anchor in self._anchors.items():
            score = _cosine(vec, anchor)
            if score > best_score:
                best_score = score
                best_intent = intent

        if best_score < self.threshold:
            return ("general", best_score)
        return (best_intent, best_score)

    def get_model_hint(
        self,
        messages: list[dict],
        allowed_providers: frozenset[str] | set[str] | None = None,
    ) -> tuple[str | None, str, float]:
        """Return ``(model, intent, confidence)``.

        ``model`` is ``None`` when classification is below threshold —
        the caller should defer to the keyword router and the main
        routing service. When ``model`` is not None it has already
        been filtered against ``allowed_providers`` (if given).
        """
        intent, confidence = self.classify(messages)
        if confidence < self.threshold:
            return (None, intent, confidence)

        for candidate in INTENT_MODEL_MAP.get(intent, []):
            provider = resolve_provider(candidate)
            if allowed_providers is None or (
                provider is not None and provider in allowed_providers
            ):
                return (candidate, intent, confidence)

        # All preferred candidates blocked by allowed_providers.
        return (None, intent, confidence)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._anchors:
            return

        if self._encoder is None:
            # Lazy import so the whole test suite doesn't pay the cost
            # of loading sentence-transformers when the flag is off.
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "Semantic router requires sentence-transformers. "
                    "Install from requirements-semantic.txt or disable "
                    "SEMANTIC_ROUTER_ENABLED."
                ) from exc
            self._model = SentenceTransformer(self.model_name)
            self._encoder = lambda text: self._model.encode(
                text, convert_to_numpy=True
            ).tolist()
            logger.info(
                "semantic_router_loaded", model=self.model_name
            )

        for intent, description in _INTENT_DESCRIPTIONS.items():
            self._anchors[intent] = list(self._encoder(description))

    def _encode(self, text: str) -> Sequence[float]:
        assert self._encoder is not None, "encoder not initialised"
        return self._encoder(text)

    @staticmethod
    def _join_messages(messages: list[dict]) -> str:
        parts: list[str] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            content = m.get("content")
            if isinstance(content, str):
                parts.append(content)
        return " ".join(parts).strip()


class SemanticRouterDisabled:
    """No-op semantic router used when the feature flag is off."""

    threshold: float = 0.0

    def classify(self, messages: list[dict]) -> tuple[str, float]:
        return ("general", 0.0)

    def get_model_hint(
        self,
        messages: list[dict],
        allowed_providers: frozenset[str] | set[str] | None = None,
    ) -> tuple[str | None, str, float]:
        return (None, "general", 0.0)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
