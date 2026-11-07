"""FIX64 — capability switches have effect, and the per-model test fills
them in from evidence.

Part 1: the vision switch is enforced where the request is built (the
tools switch always was); the request-shape log describes a body without
leaking content. Part 2: the four verdicts stay apart, only a proven
verdict writes, a person's setting is proposed-not-overwritten, the
discovery sync stops re-guessing what a person set or a test proved.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from app.core.exceptions import GatewayError, ProviderError, RateLimitExceededError, ValidationError
from app.schemas.common import Message
from app.services import capability_test as ct
from app.services.providers.capability_gate import image_part_count, require_vision
from app.services.providers.http_client import request_shape
from app.services.providers.model_registry import _registry


# ---------------------------------------------------------------------------
# Part 1 — the vision gate and the request-shape log
# ---------------------------------------------------------------------------

def _image_turn() -> list[Message]:
    return [
        Message(role="system", content="be brief"),
        Message(
            role="user",
            content=[
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "text", "text": "what is this?"},
            ],
        ),
    ]


def test_image_part_count_sees_only_image_blocks():
    assert image_part_count(_image_turn()) == 1
    assert image_part_count([Message(role="user", content="hi")]) == 0
    assert image_part_count([{"role": "user", "content": [{"type": "input_image"}, {"type": "text", "text": "x"}]}]) == 1


def _with_snapshot(model: str, **flags):
    cand = _registry._model_map.get(model)
    assert cand is not None, f"{model} missing from the registry snapshot"
    _registry._model_map[model] = dataclasses.replace(cand, **flags)
    return cand


def test_vision_gate_refuses_images_for_a_blind_row():
    cand = next(c for c in _registry.get_all_candidates() if not c.supports_vision)
    with pytest.raises(ValidationError) as exc:
        require_vision(cand.model, _image_turn())
    assert exc.value.code == "model_no_vision_support"
    assert exc.value.details == {"model": cand.model}


def test_vision_gate_lets_a_vision_row_and_text_only_turns_through():
    blind = next(c for c in _registry.get_all_candidates() if not c.supports_vision)
    require_vision(blind.model, [Message(role="user", content="no image here")])
    # An id the registry does not know is not refused — the registry is a
    # cache, not the authority on what exists.
    require_vision("model-nobody-has-heard-of", _image_turn())


def test_vision_gate_follows_the_switch_on_the_same_row():
    # The test registry seed carries no vision row, so the switch is
    # flipped on the process-local snapshot both ways, the way the
    # capability probe opens its own gate.
    cand = next(c for c in _registry.get_all_candidates() if not c.supports_vision)
    with pytest.raises(ValidationError):
        require_vision(cand.model, _image_turn())
    original = _with_snapshot(cand.model, supports_vision=True)
    try:
        require_vision(cand.model, _image_turn())
    finally:
        _registry._model_map[cand.model] = original
    with pytest.raises(ValidationError):
        require_vision(cand.model, _image_turn())


def test_openai_compatible_body_builder_applies_the_vision_gate():
    from app.schemas.public import ChatCompletionsRequest
    from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter

    blind = next(c for c in _registry.get_all_candidates() if not c.supports_vision)
    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    payload = ChatCompletionsRequest(model=blind.model, messages=[{"role": "user", "content": "x"}])
    with pytest.raises(ValidationError) as exc:
        adapter._build_body(payload, _image_turn(), resolved_model=blind.model)
    assert exc.value.code == "model_no_vision_support"


def test_request_shape_counts_and_never_copies_content():
    body = json.dumps(
        {
            "model": "m",
            "stream": True,
            "messages": [
                {"role": "system", "content": "SECRET-PROMPT"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}, {"type": "text", "text": "SECRET-TEXT"}]},
            ],
            "tools": [{"type": "function", "function": {"name": "t"}}] * 3,
            "tool_choice": "auto",
            "reasoning_effort": "low",
        }
    ).encode()
    shape = request_shape(body)
    assert shape == {
        "model": "m", "stream": True, "messages": 2, "tools": 3, "image_parts": 1,
        "tool_choice": "auto", "reasoning_effort": True,
    }
    assert "SECRET" not in json.dumps(shape)
    assert request_shape(b"") is None
    assert request_shape(b"not json") is None
    assert request_shape(b"[1,2]") is None


# ---------------------------------------------------------------------------
# Part 2 — four verdicts, kept apart
# ---------------------------------------------------------------------------

def test_transport_throttle_and_credit_failures_are_unreachable_not_no():
    for outcome in ("transport_error", "rate_limited", "no_credit"):
        verdict, evidence = ct._classify("tools", outcome, "boom")
        assert verdict == ct.UNREACHABLE
        assert outcome in evidence


def test_only_a_rejection_naming_the_parameter_is_proven_no():
    assert ct._classify("tools", "rejected_by_provider", "400: 'tools' is not supported by this model")[0] == ct.PROVEN_NO
    assert ct._classify("vision", "rejected_by_provider", "image_url is not supported")[0] == ct.PROVEN_NO
    assert ct._classify("streaming", "rejected_by_provider", "stream is not allowed here")[0] == ct.PROVEN_NO
    # A 404 for the model id, or a 400 about something else, says nothing
    # about the capability.
    assert ct._classify("tools", "rejected_by_provider", "404 model not found")[0] == ct.INCONCLUSIVE
    assert ct._classify("vision", "rejected_by_provider", "max_tokens too large")[0] == ct.INCONCLUSIVE
    # Reasoning sends no parameter, so nothing can reject "reasoning".
    assert ct._classify("reasoning", "rejected_by_provider", "reasoning not supported")[0] == ct.INCONCLUSIVE


def test_vision_answer_needs_both_halves_in_order():
    assert ct._VISION_ANSWER_A.search("Red, Blue")
    assert ct._VISION_ANSWER_A.search("ซ้ายสีแดง ขวาสีน้ำเงิน")
    assert not ct._VISION_ANSWER_A.search("Blue, Red")
    assert not ct._VISION_ANSWER_A.search("red")
    assert ct._VISION_ANSWER_B.search("blue, red")
    assert not ct._VISION_ANSWER_B.search("Red, Blue")


def test_plan_states_the_request_count_and_what_is_not_testable():
    row = SimpleNamespace(
        id=1, provider="sranai", model="m", supports_tools=False, supports_vision=False,
        supports_streaming=True, supports_reasoning=False, capability_provenance={},
    )
    plan = ct.plan_for(row)
    assert plan["capabilities"] == ["tools", "vision", "streaming", "reasoning"]
    assert plan["requests"] == 5
    assert plan["requests_per_capability"]["vision"] == 2
    assert plan["paid"] is False
    assert set(plan["not_testable"]) == {"video_gen", "audio_gen", "tts", "context_window"}
    paid = ct.plan_for(SimpleNamespace(**{**row.__dict__, "provider": "openai"}))
    assert paid["paid"] is True


# ---------------------------------------------------------------------------
# Part 2 — what a verdict may write
# ---------------------------------------------------------------------------

class _Session:
    def __init__(self):
        self.commits = 0

    def flush(self):
        pass

    def commit(self):
        self.commits += 1


def _row(**over):
    base = dict(
        id=7, provider="sranai", model="m", supports_tools=False, supports_vision=False,
        supports_streaming=True, supports_reasoning=False, reasoning_strategy="none",
        capability_provenance={},
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def fake_dispatch(monkeypatch):
    """Replace the network with a scripted verdict; keys and registry too."""
    state = {"verdict": ct.PROVEN_YES, "evidence": "tool_call_emitted: get_time"}

    async def _test_one(cap, provider, model, api_key):
        return {"capability": cap, "verdict": state["verdict"], "evidence": state["evidence"], "latency_ms": 1}

    class _Keys:
        def get_active_key(self, session, provider, model=None):
            return "k"

    async def _reload(session):
        return None

    monkeypatch.setattr(ct, "_test_one", _test_one)
    monkeypatch.setattr("app.services.provider_key_service.ProviderKeyService", _Keys)
    monkeypatch.setattr(_registry, "reload", _reload)
    return state


@pytest.mark.asyncio
async def test_proven_yes_promotes_and_records_probe_provenance(fake_dispatch):
    row, s = _row(), _Session()
    r = await ct.run_capability_test(s, row, "tools")
    assert r["verdict"] == ct.PROVEN_YES and r["applied"] is True and r["proposal"] is False
    assert row.supports_tools is True
    assert row.capability_provenance["tools"]["source"] == "probe"
    assert row.capability_provenance["tests"]["tools"]["verdict"] == ct.PROVEN_YES
    assert s.commits == 1


@pytest.mark.asyncio
async def test_proven_no_demotes_with_evidence(fake_dispatch):
    fake_dispatch.update(verdict=ct.PROVEN_NO, evidence="provider rejected the tools request: 400 tools unsupported")
    row, s = _row(supports_tools=True), _Session()
    r = await ct.run_capability_test(s, row, "tools")
    assert r["applied"] is True and row.supports_tools is False
    assert "rejected" in row.capability_provenance["tools"]["evidence"]


@pytest.mark.asyncio
async def test_inconclusive_and_unreachable_write_nothing_but_are_recorded(fake_dispatch):
    for verdict in (ct.INCONCLUSIVE, ct.UNREACHABLE):
        fake_dispatch.update(verdict=verdict, evidence="x")
        row, s = _row(supports_tools=True), _Session()
        r = await ct.run_capability_test(s, row, "tools")
        assert r["applied"] is False and r["proposal"] is False
        assert row.supports_tools is True  # never folded into False
        assert "tools" not in row.capability_provenance  # no provenance change
        assert row.capability_provenance["tests"]["tools"]["verdict"] == verdict


@pytest.mark.asyncio
async def test_a_persons_setting_is_proposed_not_overwritten(fake_dispatch):
    row = _row(supports_tools=False, capability_provenance={"tools": {"source": "admin", "at": "2026-09-01T00:00:00Z"}})
    r = await ct.run_capability_test(_Session(), row, "tools")
    assert r["proposal"] is True and r["proposed_value"] is True and r["applied"] is False
    assert row.supports_tools is False
    assert row.capability_provenance["tools"]["source"] == "admin"


@pytest.mark.asyncio
async def test_report_only_never_writes(fake_dispatch):
    row = _row()
    r = await ct.run_capability_test(_Session(), row, "tools", apply=False)
    assert r["verdict"] == ct.PROVEN_YES and r["applied"] is False
    assert row.supports_tools is False


@pytest.mark.asyncio
async def test_proven_reasoning_fills_the_read_only_strategy(fake_dispatch):
    fake_dispatch.update(evidence="reasoning_content emitted (200 chars)")
    row = _row()
    r = await ct.run_capability_test(_Session(), row, "reasoning")
    assert row.supports_reasoning is True
    assert row.reasoning_strategy == "openai_compatible_reasoning_content"
    assert r["reasoning_strategy"] == "openai_compatible_reasoning_content"
    # A strategy someone chose is left alone.
    row2 = _row(supports_reasoning=True, reasoning_strategy="openai_compatible_reasoning")
    await ct.run_capability_test(_Session(), row2, "reasoning")
    assert row2.reasoning_strategy == "openai_compatible_reasoning"


@pytest.mark.asyncio
async def test_no_key_is_unreachable(monkeypatch):
    class _Keys:
        def get_active_key(self, session, provider, model=None):
            return None

    monkeypatch.setattr("app.services.provider_key_service.ProviderKeyService", _Keys)
    row = _row(supports_tools=True)
    r = await ct.run_capability_test(_Session(), row, "tools")
    assert r["verdict"] == ct.UNREACHABLE and "no usable key" in r["evidence"]
    assert row.supports_tools is True


@pytest.mark.asyncio
async def test_untestable_capability_is_refused_by_name():
    with pytest.raises(ValueError):
        await ct.run_capability_test(_Session(), _row(), "video_gen")


# ---------------------------------------------------------------------------
# Provenance — the sync stops re-guessing a person's or a test's value
# ---------------------------------------------------------------------------

def test_discovery_may_promote_only_unclaimed_flags():
    assert ct.discovery_may_promote(_row(), "tools") is True
    assert ct.discovery_may_promote(_row(capability_provenance={"tools": {"source": "inferred"}}), "tools") is True
    assert ct.discovery_may_promote(_row(capability_provenance={"tools": {"source": "provider"}}), "tools") is True
    assert ct.discovery_may_promote(_row(capability_provenance={"tools": {"source": "admin"}}), "tools") is False
    assert ct.discovery_may_promote(_row(capability_provenance={"tools": {"source": "probe"}}), "tools") is False


def test_record_provenance_writes_a_new_dict():
    row = _row()
    before = row.capability_provenance
    ct.record_provenance(row, "vision", "admin")
    assert row.capability_provenance is not before
    assert row.capability_provenance["vision"]["source"] == "admin"
    assert "at" in row.capability_provenance["vision"]


def test_classify_error_shapes_from_capability_probe_are_reused():
    from app.services.capability_probe import _classify_error

    assert _classify_error(RateLimitExceededError("x", details={"status": 429, "body": {}}))[0] == "rate_limited"
    assert _classify_error(ProviderError("x", details={"status": 400}))[0] == "rejected_by_provider"
    assert _classify_error(GatewayError("x"))[0] == "rejected_by_provider"
    assert _classify_error(ConnectionError("x"))[0] == "transport_error"
