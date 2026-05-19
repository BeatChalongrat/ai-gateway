"""Model-to-provider registry — DB-backed with a hardcoded seed fallback.

Backing store: ``model_configs`` table (see migrations 036 + 037).
The runtime singleton (``_registry``) reads only ``is_active=True``
rows and rebuilds its in-memory maps on every ``reload()`` call.

Public API (intentionally synchronous so existing callers — routing
service, usage service, tests — work unchanged):

  - ``get_candidate(model)``  → ``ModelCandidate | None``
  - ``get_all_candidates()``  → ``list[ModelCandidate]``
  - ``resolve_provider(model)`` → ``str | None``
  - ``filter_candidates(...)`` → ``list[ModelCandidate]``
  - ``list_models()``         → ``list[dict[str, str]]``
  - ``_MODEL_MAP``, ``_PREFIX_MAP`` — kept for back-compat (read-only
    snapshots of the singleton state).

Module-load behavior: the singleton is pre-seeded from
``SEED_CANDIDATES`` so callers that touch it at *import* time
(notably ``routing_service._build_auto_defaults``) get real data
before the FastAPI lifespan has had a chance to call ``reload()``.
The lifespan then swaps in the DB rows.

Discovery flow (``app/services/discovery``) writes to the DB and
calls ``await _registry.reload(db)`` to refresh the in-memory cache
without restarting the process.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.domain.routing import ModelCandidate


if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Cost-tier helper — kept module-level so the seed migration, discovery
# service, and tests all share one threshold definition.
# ---------------------------------------------------------------------------

def _derive_cost_tier(cost_per_input_token: float) -> str:
    """Derive cost_tier from real input token price.

    Thresholds (in $/token):
      ≤ $0.50 / 1M tokens → "budget"
      ≤ $3.00 / 1M tokens → "standard"
      otherwise           → "premium"

    A zero/unknown price returns the neutral "standard" bucket so the
    rest of the routing path keeps working with a sensible default.
    """
    if cost_per_input_token == 0.0:
        return "standard"
    if cost_per_input_token <= 0.0000005:   # ≤ $0.50/1M
        return "budget"
    if cost_per_input_token <= 0.000003:    # ≤ $3.00/1M
        return "standard"
    return "premium"


# ---------------------------------------------------------------------------
# Seed list — used by:
#   1. Module-load pre-population so the registry is non-empty at
#      *import* time (before lifespan runs ``reload``).
#   2. Migration 037 ships an identical row set into ``model_configs``;
#      if a deployment is rolled back below 037 (no rows in DB) the
#      registry still has a viable candidate list.
#   3. Tests that assert tier-derivation behaviour against the canonical
#      list (``tests/test_routing_cost_scoring.py``).
# ---------------------------------------------------------------------------

SEED_CANDIDATES: list[ModelCandidate] = [
    # --- OpenAI -------------------------------------------------------
    ModelCandidate(
        provider="openai", model="gpt-4o",
        cost_per_input_token=0.0000025, cost_per_output_token=0.000010,
        cost_tier=_derive_cost_tier(0.0000025), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="openai", model="gpt-4o-mini",
        cost_per_input_token=0.00000015, cost_per_output_token=0.0000006,
        cost_tier=_derive_cost_tier(0.00000015), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="openai", model="gpt-4-turbo",
        cost_per_input_token=0.00001, cost_per_output_token=0.00003,
        cost_tier=_derive_cost_tier(0.00001), latency_tier="slow",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="openai", model="gpt-3.5-turbo",
        cost_per_input_token=0.0000005, cost_per_output_token=0.0000015,
        cost_tier=_derive_cost_tier(0.0000005), latency_tier="fast",
        supports_streaming=True,
    ),
    # --- Anthropic ----------------------------------------------------
    ModelCandidate(
        provider="anthropic", model="claude-3-5-sonnet-20241022",
        cost_per_input_token=0.000003, cost_per_output_token=0.000015,
        cost_tier=_derive_cost_tier(0.000003), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="anthropic", model="claude-3-opus-20240229",
        cost_per_input_token=0.000015, cost_per_output_token=0.000075,
        cost_tier=_derive_cost_tier(0.000015), latency_tier="slow",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="anthropic", model="claude-3-haiku-20240307",
        cost_per_input_token=0.00000025, cost_per_output_token=0.00000125,
        cost_tier=_derive_cost_tier(0.00000025), latency_tier="fast",
        supports_streaming=True,
    ),
    # --- Minimax ------------------------------------------------------
    ModelCandidate(
        provider="minimax", model="abab6.5s-chat",
        cost_per_input_token=0.00000045, cost_per_output_token=0.0000045,
        cost_tier=_derive_cost_tier(0.00000045), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="minimax", model="abab5.5-chat",
        cost_per_input_token=0.00000015, cost_per_output_token=0.00000015,
        cost_tier=_derive_cost_tier(0.00000015), latency_tier="fast",
        supports_streaming=True,
    ),
    # --- Qwen ---------------------------------------------------------
    ModelCandidate(
        provider="qwen", model="qwen-turbo",
        cost_per_input_token=0.00000005, cost_per_output_token=0.0000002,
        cost_tier=_derive_cost_tier(0.00000005), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="qwen", model="qwen-plus",
        cost_per_input_token=0.00000056, cost_per_output_token=0.0000016,
        cost_tier=_derive_cost_tier(0.00000056), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="qwen", model="qwen-max",
        cost_per_input_token=0.0000024, cost_per_output_token=0.0000096,
        cost_tier=_derive_cost_tier(0.0000024), latency_tier="slow",
        supports_streaming=True,
    ),
    # --- Kimi / Moonshot ----------------------------------------------
    ModelCandidate(
        provider="kimi", model="moonshot-v1-8k",
        cost_per_input_token=0.0000012, cost_per_output_token=0.0000012,
        cost_tier=_derive_cost_tier(0.0000012), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="kimi", model="moonshot-v1-32k",
        cost_per_input_token=0.0000024, cost_per_output_token=0.0000024,
        cost_tier=_derive_cost_tier(0.0000024), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="kimi", model="moonshot-v1-128k",
        cost_per_input_token=0.0000060, cost_per_output_token=0.0000060,
        cost_tier=_derive_cost_tier(0.0000060), latency_tier="slow",
        supports_streaming=True,
    ),
    # --- Z.AI / Yi ----------------------------------------------------
    ModelCandidate(
        provider="zai", model="yi-large",
        cost_per_input_token=0.000003, cost_per_output_token=0.000003,
        cost_tier=_derive_cost_tier(0.000003), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="zai", model="yi-medium",
        cost_per_input_token=0.0000004, cost_per_output_token=0.0000004,
        cost_tier=_derive_cost_tier(0.0000004), latency_tier="fast",
        supports_streaming=True,
    ),
    # --- OpenRouter ---------------------------------------------------
    ModelCandidate(
        provider="openrouter", model="openrouter/auto",
        cost_per_input_token=0.000001, cost_per_output_token=0.000003,
        cost_tier=_derive_cost_tier(0.000001), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="openrouter", model="anthropic/claude-3.5-sonnet",
        cost_per_input_token=0.000003, cost_per_output_token=0.000015,
        cost_tier=_derive_cost_tier(0.000003), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="openrouter", model="openai/gpt-4o",
        cost_per_input_token=0.0000025, cost_per_output_token=0.000010,
        cost_tier=_derive_cost_tier(0.0000025), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="openrouter", model="meta-llama/llama-3.1-405b",
        cost_per_input_token=0.000003, cost_per_output_token=0.000003,
        cost_tier=_derive_cost_tier(0.000003), latency_tier="slow",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="openrouter", model="google/gemini-pro-1.5",
        cost_per_input_token=0.0000035, cost_per_output_token=0.0000105,
        cost_tier=_derive_cost_tier(0.0000035), latency_tier="standard",
        supports_streaming=True,
    ),
    # --- Google -------------------------------------------------------
    ModelCandidate(
        provider="google", model="gemini-2.0-flash-exp",
        cost_per_input_token=0.0000000375, cost_per_output_token=0.00000015,
        cost_tier=_derive_cost_tier(0.0000000375), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="google", model="gemini-1.5-pro",
        cost_per_input_token=0.0000035, cost_per_output_token=0.0000105,
        cost_tier=_derive_cost_tier(0.0000035), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="google", model="gemini-1.5-flash",
        cost_per_input_token=0.000000075, cost_per_output_token=0.0000003,
        cost_tier=_derive_cost_tier(0.000000075), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="google", model="gemini-1.5-flash-8b",
        cost_per_input_token=0.0000000375, cost_per_output_token=0.00000015,
        cost_tier=_derive_cost_tier(0.0000000375), latency_tier="fast",
        supports_streaming=True,
    ),
    # --- Groq ---------------------------------------------------------
    ModelCandidate(
        provider="groq", model="llama-3.3-70b-versatile",
        cost_per_input_token=0.00000059, cost_per_output_token=0.00000079,
        cost_tier=_derive_cost_tier(0.00000059), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="groq", model="llama-3.1-8b-instant",
        cost_per_input_token=0.00000005, cost_per_output_token=0.00000008,
        cost_tier=_derive_cost_tier(0.00000005), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="groq", model="mixtral-8x7b-32768",
        cost_per_input_token=0.00000024, cost_per_output_token=0.00000024,
        cost_tier=_derive_cost_tier(0.00000024), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="groq", model="gemma2-9b-it",
        cost_per_input_token=0.0000002, cost_per_output_token=0.0000002,
        cost_tier=_derive_cost_tier(0.0000002), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="groq", model="deepseek-r1-distill-llama-70b",
        cost_per_input_token=0.00000075, cost_per_output_token=0.000003,
        cost_tier=_derive_cost_tier(0.00000075), latency_tier="fast",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="groq", model="qwen-2.5-32b",
        cost_per_input_token=0.00000079, cost_per_output_token=0.00000079,
        cost_tier=_derive_cost_tier(0.00000079), latency_tier="fast",
        supports_streaming=True,
    ),
    # --- DeepSeek -----------------------------------------------------
    ModelCandidate(
        provider="deepseek", model="deepseek-chat",
        cost_per_input_token=0.00000027, cost_per_output_token=0.0000011,
        cost_tier=_derive_cost_tier(0.00000027), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="deepseek", model="deepseek-coder",
        cost_per_input_token=0.00000027, cost_per_output_token=0.0000011,
        cost_tier=_derive_cost_tier(0.00000027), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="deepseek", model="deepseek-reasoner",
        cost_per_input_token=0.00000055, cost_per_output_token=0.00000219,
        cost_tier=_derive_cost_tier(0.00000055), latency_tier="slow",
        supports_streaming=True,
    ),
    # --- xAI ----------------------------------------------------------
    ModelCandidate(
        provider="xai", model="grok-2-latest",
        cost_per_input_token=0.000002, cost_per_output_token=0.000010,
        cost_tier=_derive_cost_tier(0.000002), latency_tier="standard",
        supports_streaming=True,
    ),
    ModelCandidate(
        provider="xai", model="grok-2-mini",
        cost_per_input_token=0.0000002, cost_per_output_token=0.0000004,
        cost_tier=_derive_cost_tier(0.0000002), latency_tier="fast",
        supports_streaming=True,
    ),
    # Mistral / Cohere / Perplexity seed entries moved to
    # ``_PENDING_PROVIDERS`` below. Their adapters live under
    # ``providers/_pending/`` and raise ``ProviderNotImplementedError``;
    # listing their models here would let routing pick one and trigger
    # a runtime error. Re-add to SEED_CANDIDATES (and re-register in
    # ``provider_dispatcher.py``) when each provider is production-ready.
]


# Pending providers — adapter stubs exist on disk but are not wired
# into ``provider_dispatcher.py``. Kept here so the model catalogue
# (discovery, pricing, future UI toggles) still has authoritative
# pricing + tier metadata. Read-only; do NOT merge into the routing
# registry until the corresponding adapter goes live.
_PENDING_PROVIDERS: list[ModelCandidate] = [
    # --- Mistral ------------------------------------------------------
    ModelCandidate(
        provider="mistral", model="mistral-large-latest",
        cost_per_input_token=0.000003, cost_per_output_token=0.000009,
        cost_tier=_derive_cost_tier(0.000003), latency_tier="standard",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="mistral", model="mistral-small-latest",
        cost_per_input_token=0.000001, cost_per_output_token=0.000003,
        cost_tier=_derive_cost_tier(0.000001), latency_tier="fast",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="mistral", model="codestral-latest",
        cost_per_input_token=0.000001, cost_per_output_token=0.000003,
        cost_tier=_derive_cost_tier(0.000001), latency_tier="standard",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="mistral", model="mistral-embed",
        cost_per_input_token=0.0000001, cost_per_output_token=0.0,
        cost_tier=_derive_cost_tier(0.0000001), latency_tier="fast",
        supports_streaming=False,
    ),
    # --- Cohere -------------------------------------------------------
    ModelCandidate(
        provider="cohere", model="command-r-plus-08-2024",
        cost_per_input_token=0.0000025, cost_per_output_token=0.00001,
        cost_tier=_derive_cost_tier(0.0000025), latency_tier="standard",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="cohere", model="command-r-08-2024",
        cost_per_input_token=0.00000015, cost_per_output_token=0.0000006,
        cost_tier=_derive_cost_tier(0.00000015), latency_tier="standard",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="cohere", model="command",
        cost_per_input_token=0.0000005, cost_per_output_token=0.0000015,
        cost_tier=_derive_cost_tier(0.0000005), latency_tier="standard",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="cohere", model="command-light",
        cost_per_input_token=0.0000003, cost_per_output_token=0.0000006,
        cost_tier=_derive_cost_tier(0.0000003), latency_tier="fast",
        supports_streaming=False,
    ),
    # --- Perplexity ---------------------------------------------------
    ModelCandidate(
        provider="perplexity", model="llama-3.1-sonar-large-128k-online",
        cost_per_input_token=0.000001, cost_per_output_token=0.000001,
        cost_tier=_derive_cost_tier(0.000001), latency_tier="standard",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="perplexity", model="llama-3.1-sonar-small-128k-online",
        cost_per_input_token=0.0000002, cost_per_output_token=0.0000002,
        cost_tier=_derive_cost_tier(0.0000002), latency_tier="fast",
        supports_streaming=False,
    ),
    ModelCandidate(
        provider="perplexity", model="llama-3.1-sonar-huge-128k-online",
        cost_per_input_token=0.000005, cost_per_output_token=0.000005,
        cost_tier=_derive_cost_tier(0.000005), latency_tier="slow",
        supports_streaming=False,
    ),
]


# Backwards-compatible alias — older tests + the existing routing-test
# suite import ``_CANDIDATES`` from this module.
_CANDIDATES = SEED_CANDIDATES


# Prefix → provider fallback (for models not in the explicit registry).
# Stays as a module-level constant; the prefix mapping is policy, not
# data — it tells routing "anything starting with 'gpt-' belongs to
# OpenAI" regardless of which exact gpt- variants are registered.
_PREFIX_MAP: list[tuple[str, str]] = [
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("claude-", "anthropic"),
    ("openai/", "openrouter"),
    ("anthropic/", "openrouter"),
    ("meta-llama/", "openrouter"),
    ("google/", "openrouter"),
    # ``mistralai/`` (OpenRouter-hosted) is still a valid route — the
    # OpenRouter adapter answers it. The bare ``mistral-`` / ``codestral-``
    # prefixes that pointed at the native (pending) Mistral adapter were
    # removed to keep dispatch from picking a non-registered provider.
    ("mistralai/", "openrouter"),
    ("abab", "minimax"),
    ("qwen-", "qwen"),
    ("moonshot-", "kimi"),
    ("yi-", "zai"),
    ("gemini-", "google"),
    ("grok-", "xai"),
    ("deepseek-", "deepseek"),
]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class ModelRegistry:
    """In-memory cache of active ``model_configs`` rows.

    Public lookup methods are synchronous (the cache itself is local
    state). ``reload()`` is async because it dispatches the DB read
    to a thread — the project uses synchronous SQLAlchemy sessions,
    so blocking the event loop on psycopg I/O would be a regression.

    Concurrency: ``_lock`` serialises overlapping ``reload()`` calls
    (e.g. lifespan + manual admin sync racing on cold start). Readers
    take a snapshot via ``list(self._candidates)`` rather than locking
    so the routing hot path never waits on a sync.
    """

    def __init__(self) -> None:
        self._candidates: list[ModelCandidate] = []
        self._model_map: dict[str, ModelCandidate] = {}
        self._lock = asyncio.Lock()

    # --- Bulk loading -----------------------------------------------

    def _populate_from_seed(self) -> None:
        """Synchronous pre-population used at module import time.

        Without this, code that touches the registry before the
        FastAPI lifespan runs (notably ``routing_service`` at import)
        would see an empty registry and the auto-default lookup would
        fall through to its hardcoded ``ModelCandidate(...)`` fallback
        every single time.
        """
        self._candidates = list(SEED_CANDIDATES)
        self._model_map = {c.model: c for c in self._candidates}

    async def reload(self, db: "Session") -> None:
        """Replace the in-memory cache with active rows from the DB.

        Runs the DB query in a thread (synchronous SQLAlchemy) and
        only swaps in the new snapshot when the load succeeds. On any
        exception the existing cache is preserved — a failed reload
        must never empty the registry.
        """
        from app.models.model_config import ModelConfigORM

        def _load() -> list[ModelCandidate]:
            rows = (
                db.query(ModelConfigORM)
                .filter(ModelConfigORM.is_active.is_(True))
                .all()
            )
            return [self._row_to_candidate(r) for r in rows]

        async with self._lock:
            try:
                candidates = await asyncio.to_thread(_load)
            except Exception:
                # Keep the existing snapshot — better stale than empty.
                raise
            # Empty DB result is a legitimate state on first boot
            # before the seed migration has applied. Keep the seed
            # pre-population rather than collapsing to no candidates.
            if not candidates:
                return
            self._candidates = candidates
            self._model_map = {c.model: c for c in candidates}

    # --- Lookup helpers ---------------------------------------------

    @staticmethod
    def _row_to_candidate(row) -> ModelCandidate:
        return ModelCandidate(
            provider=row.provider,
            model=row.model,
            supports_streaming=row.supports_streaming,
            supports_tools=row.supports_tools,
            max_context_tokens=row.max_context_tokens,
            cost_tier=row.cost_tier,
            latency_tier=row.latency_tier,
            cost_per_input_token=row.cost_per_input_token,
            cost_per_output_token=row.cost_per_output_token,
        )

    def get_candidate(self, model: str) -> ModelCandidate | None:
        return self._model_map.get(model)

    def get_all_candidates(self) -> list[ModelCandidate]:
        return list(self._candidates)

    def model_map_snapshot(self) -> dict[str, ModelCandidate]:
        """Return a copy of ``_model_map`` for back-compat consumers."""
        return dict(self._model_map)


_registry = ModelRegistry()
_registry._populate_from_seed()


# ---------------------------------------------------------------------------
# Module-level public API (kept synchronous for back-compat)
# ---------------------------------------------------------------------------

def resolve_provider(model_name: str) -> str | None:
    """Resolve a model name to a provider identifier.

    Consults the live registry first, then falls back to the static
    prefix map. ``None`` if neither matches.
    """
    candidate = _registry.get_candidate(model_name)
    if candidate is not None:
        return candidate.provider

    for prefix, provider in _PREFIX_MAP:
        if model_name.startswith(prefix):
            return provider

    return None


def get_candidate(model_name: str) -> ModelCandidate | None:
    """Look up a model's full candidate metadata."""
    return _registry.get_candidate(model_name)


def get_all_candidates() -> list[ModelCandidate]:
    """Return all active model candidates."""
    return _registry.get_all_candidates()


def get_all_providers() -> frozenset[str]:
    """Return the set of provider names that have at least one active
    model in the registry.

    Used by ``routing_service`` to build the region-allowlist dynamically
    instead of hardcoding ``{"openai", "anthropic", "openrouter"}`` —
    which had left auto-routing unable to pick Google / Groq / xAI /
    DeepSeek / Qwen / Kimi / Z.AI / Minimax for region-constrained
    requests (May 2026 routing audit).

    Pending providers (Mistral / Cohere / Perplexity) are absent from
    ``SEED_CANDIDATES`` and ``_PENDING_PROVIDERS`` is NOT consulted
    here, so this method only ever returns provider names whose
    adapters are live in ``provider_dispatcher.py``.
    """
    return frozenset(c.provider for c in _registry.get_all_candidates())


def list_models() -> list[dict[str, str]]:
    """Return a list of active models with their providers."""
    return [
        {"model": c.model, "provider": c.provider}
        for c in _registry.get_all_candidates()
    ]


def filter_candidates(
    candidates: list[ModelCandidate],
    *,
    requires_streaming: bool = False,
    allowed_providers: frozenset[str] | None = None,
    blocked_models: frozenset[str] | None = None,
) -> list[ModelCandidate]:
    """Filter candidates by capability requirements and constraints."""
    result = []
    for c in candidates:
        if requires_streaming and not c.supports_streaming:
            continue
        if allowed_providers and c.provider not in allowed_providers:
            continue
        if blocked_models and c.model in blocked_models:
            continue
        result.append(c)
    return result


def _MODEL_MAP_snapshot() -> dict[str, ModelCandidate]:
    """Back-compat indirection for callers that read ``_MODEL_MAP``."""
    return _registry.model_map_snapshot()


# Read-only alias to the live snapshot — never mutate this dict.
# A handful of older sites import ``_MODEL_MAP`` directly; keeping the
# attribute prevents an ImportError without granting them write access
# (assignments would only update this local binding, not the registry).
_MODEL_MAP = _registry.model_map_snapshot()
