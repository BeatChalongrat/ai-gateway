"""Capability-aware fallback tests.

A silent fallback must never land on a model that can't serve the
request. These cover the three new pieces:

  * detect_required_capabilities — vision (image blocks) + tools.
  * filter_candidates — the requires_vision / requires_tools gates.
  * model_meets — the Layer-2 chain gate (registry-backed).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.routing import ModelCandidate
from app.services.capability_detection import (
    RequiredCapabilities,
    detect_required_capabilities,
    model_meets,
)
from app.services.providers.model_registry import filter_candidates


def _payload(messages=None, tools=None):
    return SimpleNamespace(messages=messages or [], tools=tools)


def _msg(content):
    return SimpleNamespace(content=content)


class TestDetectRequiredCapabilities:
    def test_plain_text_needs_nothing(self):
        caps = detect_required_capabilities(_payload([_msg("hello")]))
        assert caps == RequiredCapabilities(vision=False, tools=False)
        assert caps.any is False

    def test_image_block_needs_vision(self):
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
            {"type": "text", "text": "what is this?"},
        ]
        caps = detect_required_capabilities(_payload([_msg(content)]))
        assert caps.vision is True
        assert caps.tools is False

    def test_tools_present_needs_tools(self):
        caps = detect_required_capabilities(
            _payload([_msg("call a function")], tools=[{"type": "function"}])
        )
        assert caps.tools is True
        assert caps.vision is False

    def test_malformed_content_does_not_raise(self):
        # Non-dict parts in a list must be ignored, not crash.
        caps = detect_required_capabilities(_payload([_msg([None, 42, "x"])]))
        assert caps.vision is False


class TestFilterByCapability:
    def _pair(self):
        return [
            ModelCandidate(provider="openai", model="gpt-4o", supports_vision=True, supports_tools=True),
            ModelCandidate(provider="groq", model="llama-text", supports_vision=False, supports_tools=False),
        ]

    def test_requires_vision_excludes_non_vision(self):
        result = filter_candidates(self._pair(), requires_vision=True)
        assert [c.model for c in result] == ["gpt-4o"]

    def test_requires_tools_excludes_non_tools(self):
        result = filter_candidates(self._pair(), requires_tools=True)
        assert [c.model for c in result] == ["gpt-4o"]

    def test_no_requirement_keeps_all(self):
        result = filter_candidates(self._pair())
        assert len(result) == 2

    def test_capability_and_provider_filters_compose(self):
        # vision-required AND only groq allowed → nothing (groq model is
        # non-vision here), proving the gates AND together.
        result = filter_candidates(
            self._pair(),
            requires_vision=True,
            allowed_providers=frozenset({"groq"}),
        )
        assert result == []


class TestModelMeets:
    def test_no_requirement_passes_any_model(self):
        # Even an unknown model id passes when nothing is required.
        assert model_meets(RequiredCapabilities(), "totally-unknown-model") is True

    def test_unknown_model_fails_a_hard_requirement(self):
        # Can't verify capability → don't risk a degraded fallback.
        assert model_meets(RequiredCapabilities(vision=True), "totally-unknown-model") is False
