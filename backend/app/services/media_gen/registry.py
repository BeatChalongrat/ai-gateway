"""Adapter registry — provider name → MediaGenAdapter (Sprint 2A).

A tiny in-process registry. Adapters register themselves at import time
(see ``media_gen/__init__.py``). The job loop resolves the right adapter
for a job's ``provider`` + ``job_type``.
"""

from __future__ import annotations

from app.services.media_gen.base import MediaGenAdapter


class UnknownProviderError(Exception):
    """No adapter is registered for the requested provider / job type."""


# Sprint 4: keyed by (provider_name, job_type) so MULTIPLE adapters can
# share a provider — e.g. Replicate serves both "video" (ReplicateVideoAdapter)
# and "music" (ReplicateMusicAdapter), and "openai" serves both chat keys and
# the TTS adapter. The old provider-only key collided (the second registration
# for a provider overwrote the first). ``register_adapter(cls)`` and
# ``get_adapter(provider, job_type)`` signatures are unchanged.
_REGISTRY: dict[tuple[str, str], type[MediaGenAdapter]] = {}


def register_adapter(adapter_cls: type[MediaGenAdapter]) -> None:
    """Register an adapter class under each of its (provider, job_type) keys."""
    if not adapter_cls.provider_name:
        raise ValueError("adapter has no provider_name")
    if not adapter_cls.supported_job_types:
        raise ValueError(
            f"adapter {adapter_cls.provider_name!r} has no supported_job_types"
        )
    for job_type in adapter_cls.supported_job_types:
        _REGISTRY[(adapter_cls.provider_name, job_type)] = adapter_cls


def get_adapter(provider: str, job_type: str) -> MediaGenAdapter:
    """Return a fresh adapter instance for (provider, job_type).

    Raises ``UnknownProviderError`` if no adapter is registered for that
    exact (provider, job_type) pair.
    """
    cls = _REGISTRY.get((provider, job_type))
    if cls is None:
        raise UnknownProviderError(
            f"no adapter registered for provider {provider!r} job_type {job_type!r}"
        )
    return cls()


def registered_providers() -> list[str]:
    """Distinct provider names with at least one registered adapter."""
    return sorted({provider for (provider, _job_type) in _REGISTRY})


def registered_adapters() -> list[tuple[str, str]]:
    """All registered (provider, job_type) pairs — for diagnostics/verification."""
    return sorted(_REGISTRY)
