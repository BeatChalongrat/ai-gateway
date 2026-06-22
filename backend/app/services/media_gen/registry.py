"""Adapter registry — provider name → MediaGenAdapter (Sprint 2A).

A tiny in-process registry. Adapters register themselves at import time
(see ``media_gen/__init__.py``). The job loop resolves the right adapter
for a job's ``provider`` + ``job_type``.
"""

from __future__ import annotations

from app.services.media_gen.base import MediaGenAdapter


class UnknownProviderError(Exception):
    """No adapter is registered for the requested provider / job type."""


_REGISTRY: dict[str, type[MediaGenAdapter]] = {}


def register_adapter(adapter_cls: type[MediaGenAdapter]) -> None:
    """Register an adapter class under its ``provider_name``."""
    if not adapter_cls.provider_name:
        raise ValueError("adapter has no provider_name")
    _REGISTRY[adapter_cls.provider_name] = adapter_cls


def get_adapter(provider: str, job_type: str) -> MediaGenAdapter:
    """Return a fresh adapter instance for (provider, job_type).

    Raises ``UnknownProviderError`` if the provider isn't registered or
    the adapter doesn't service ``job_type``.
    """
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownProviderError(
            f"no adapter registered for provider {provider!r}"
        )
    adapter = cls()
    if job_type not in adapter.supported_job_types:
        raise UnknownProviderError(
            f"adapter {provider!r} does not support job_type {job_type!r}"
        )
    return adapter


def registered_providers() -> list[str]:
    return sorted(_REGISTRY)
