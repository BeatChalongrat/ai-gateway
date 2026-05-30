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

import asyncio
import hashlib
import math
import random
import threading
import time
from typing import Callable, Sequence

import numpy as np
import structlog

from app.services.providers.model_registry import resolve_provider

logger = structlog.get_logger(__name__)

# Phase-1 B4: bound concurrent embedding encodes offloaded to the
# threadpool. Separate from the DLP semaphore so the two together
# (8 + 4) stay under the worker's 16-thread default executor.
_SEMANTIC_ENCODE_SEMAPHORE = asyncio.Semaphore(4)


# ---------------------------------------------------------------------------
# Variant-C mock encoder (perf-baseline only).
#
# Used when ``settings.semantic_router_mock`` is true. Produces a
# deterministic 384-dim unit vector (matching the real MiniLM encoder's
# output dimension) derived from ``sha256(text)`` — so repeated calls on
# the same prompt return the same vector, making classifier decisions
# reproducible across runs.
#
# Sleep distribution is calibrated from reported MiniLM CPU benchmarks:
#   p50 =  200 ms
#   p95 =  600 ms
#   p99 = 1500 ms
# Fitted as lognormal(mu=ln(0.2), sigma=0.65) which gives approximately
# those percentiles. The sleep is SYNC ``time.sleep`` to mirror the real
# encoder's behaviour — ``sentence_transformers.encode`` is sync + CPU-
# bound, so a faithful mock must block the event loop the same way.
# Round B / C numbers therefore include event-loop stall.
# ---------------------------------------------------------------------------

_MOCK_VECTOR_DIM = 384
_MOCK_LOG_MU = math.log(0.2)  # median ~200 ms
_MOCK_LOG_SIGMA = 0.65


def _mock_encode(text: str) -> list[float]:
    """Deterministic 384-dim unit vector + calibrated-latency sleep.

    The exact numeric values don't matter — the orchestrator only cares
    that ``classify`` returns a consistent intent for a given prompt
    (so retries don't flap) and that the call takes measurable wall-
    clock time (so the baseline report can attribute router cost).
    """
    seed_bytes = hashlib.sha256(text.encode("utf-8")).digest()[:8]
    seed_int = int.from_bytes(seed_bytes, "big")
    rng = random.Random(seed_int)

    vec = [rng.gauss(0.0, 1.0) for _ in range(_MOCK_VECTOR_DIM)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    unit = [x / norm for x in vec]

    # Separate RNG for the sleep so vector + latency are independently
    # reproducible per-process but still tied to the input.
    sleep_rng = random.Random(seed_int ^ 0x5f3759df)
    sample = sleep_rng.lognormvariate(_MOCK_LOG_MU, _MOCK_LOG_SIGMA)
    # Clip to a sane envelope so a long-tailed sample can't stall the
    # load generator for minutes.
    time.sleep(min(sample, 5.0))
    return unit


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
        # B4: pre-normalised anchor matrix (N x dim) + names, for the
        # vectorised numpy cosine used by the async hot path. Built once
        # in ``_ensure_loaded``. Guarded by ``_load_lock`` because the
        # async path triggers the one-time load from a worker thread.
        self._anchor_matrix: np.ndarray | None = None
        self._anchor_names: list[str] = []
        # Serialises the one-time lazy load when the async path triggers
        # it from a worker thread (so the model loads exactly once).
        self._load_lock = threading.Lock()

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
        if self._anchor_matrix is not None:
            # B4 fast path: one matmul against the pre-normalised anchor
            # matrix. Used in production (matrix built by _ensure_loaded).
            q = np.asarray(vec, dtype=np.float64)
            norm = float(np.linalg.norm(q))
            if norm == 0.0:
                return ("general", 0.0)
            scores = self._anchor_matrix @ (q / norm)
            idx = int(np.argmax(scores))
            best_score = float(scores[idx])
            best_intent = self._anchor_names[idx]
        else:
            # Fallback (e.g. tests that set _anchors directly without the
            # matrix): exact same result via the pure-Python cosine.
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
        # Double-checked locking: the async path (get_model_hint_async →
        # to_thread(get_model_hint)) can trigger the one-time load from a
        # worker thread, so concurrent first requests must not each build
        # the encoder. threading.Lock (not asyncio) — we may be in a thread.
        if self._anchors:
            return
        with self._load_lock:
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        if self._anchors:
            return

        if self._encoder is None:
            # Variant C of the perf baseline runs with
            # ``SEMANTIC_ROUTER_MOCK=1`` — bypass the real
            # sentence-transformers import and use the deterministic
            # hash-seeded mock encoder so the gateway can exercise the
            # semantic-router code path without requiring a multi-GB
            # torch install on the perf host.
            try:
                from app.core.config import settings as _settings

                use_mock = bool(getattr(_settings, "semantic_router_mock", False))
            except Exception:
                use_mock = False

            if use_mock:
                self._encoder = _mock_encode
                logger.info(
                    "semantic_router_loaded",
                    model="mock-384d-lognormal",
                    mock=True,
                )
            else:
                # Lazy import so the whole test suite doesn't pay the cost
                # of loading sentence-transformers when the flag is off.
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError(
                        "Semantic router requires sentence-transformers. "
                        "Install from requirements-semantic.txt or disable "
                        "SEMANTIC_ROUTER_ENABLED. For perf-baseline Variant "
                        "C, set SEMANTIC_ROUTER_MOCK=1 instead."
                    ) from exc
                self._model = SentenceTransformer(self.model_name)
                self._encoder = lambda text: self._model.encode(
                    text, convert_to_numpy=True
                ).tolist()
                logger.info(
                    "semantic_router_loaded", model=self.model_name
                )

        names: list[str] = []
        rows: list[np.ndarray] = []
        for intent, description in _INTENT_DESCRIPTIONS.items():
            anchor = list(self._encoder(description))
            self._anchors[intent] = anchor
            # Pre-normalise each anchor to a unit vector so the hot-path
            # cosine is a single matmul (anchor_matrix @ unit_query).
            a = np.asarray(anchor, dtype=np.float64)
            norm = float(np.linalg.norm(a))
            rows.append(a / norm if norm else a)
            names.append(intent)
        self._anchor_names = names
        self._anchor_matrix = np.stack(rows) if rows else None

    async def get_model_hint_async(
        self,
        messages: list[dict],
        allowed_providers: frozenset[str] | set[str] | None = None,
    ) -> tuple[str | None, str, float]:
        """B4 async variant: offload the synchronous ``get_model_hint``
        (classify → embedding encode → cosine) to a worker thread so the
        50–600 ms MiniLM encode never blocks the event loop.

        Delegates to the sync method (rather than reimplementing the
        chain) so test monkeypatches on ``get_model_hint`` still apply
        and the numpy cosine in ``classify`` is reused. Bounded by
        ``_SEMANTIC_ENCODE_SEMAPHORE`` so concurrent encodes can't starve
        the shared executor.
        """
        async with _SEMANTIC_ENCODE_SEMAPHORE:
            return await asyncio.to_thread(
                self.get_model_hint, messages, allowed_providers
            )

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

    async def get_model_hint_async(
        self,
        messages: list[dict],
        allowed_providers: frozenset[str] | set[str] | None = None,
    ) -> tuple[str | None, str, float]:
        # Delegate to the sync method (instant no-op) so tests that
        # monkeypatch ``get_model_hint`` on this class still take effect.
        return self.get_model_hint(messages, allowed_providers)


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
