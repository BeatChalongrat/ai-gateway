"""Capability gate — the stored flags decide what a request may carry.

FIX64 Part 1 measured the switches: turning ``supports_tools`` off
changed the body that left for the provider (the adapters already refuse
``tools`` for a tools-blind row), but turning ``supports_vision`` off
changed nothing — one image part still went out to a model the catalogue
says cannot see. The web composer gates image attachments client-side,
the desktop host does not gate at all, and neither client refreshes its
catalogue mid-session. This module is the one place both send paths pass
through: the adapter body builders call it before serialising.

Same contract as the tools gate: loud, not silent. Stripping the image
would make the model answer as if it had seen nothing, which is the
worst outcome for the person who attached it; ``model_no_vision_support``
is in the error catalogue with a non-retryable, actionable message.

A model the registry does not know is left alone — the registry is a
cache of the catalogue, not the authority on what exists.
"""

from __future__ import annotations

from typing import Iterable

_IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})


def image_part_count(messages: Iterable) -> int:
    """Count image content blocks across the messages (str content = 0)."""
    n = 0
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in _IMAGE_PART_TYPES:
                n += 1
    return n


def require_vision(model: str, messages: Iterable) -> None:
    """Raise ``model_no_vision_support`` when images ride to a blind model.

    Only a stored ``supports_vision = False`` refuses; an unknown model
    passes (see module docstring).
    """
    if image_part_count(messages) == 0:
        return
    from app.core.exceptions import ValidationError
    from app.services.providers.model_registry import get_candidate

    cand = get_candidate(model)
    if cand is None or cand.supports_vision:
        return
    raise ValidationError(
        f"Model '{model}' does not support image input",
        code="model_no_vision_support",
        details={"model": model},
    )
