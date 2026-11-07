"""FIX62 — a model's capabilities come from data, not from its name.

Three things this pins:

1. Name inference is limited to vendors whose ids mean something. A
   model on a self-hosted or relay endpoint (``sranai``, any unknown
   provider) starts with tools/vision OFF whatever it is called; the
   admin turns them on after testing.
2. What the endpoint STATES always wins, in both directions — including
   OpenRouter's ``supported_parameters`` / ``architecture.input_modalities``,
   which the old code ignored.
3. "Can call tools" is not "can make images": the via-tools image flag
   needs a real image target (a usable key at the image backend).
"""
from __future__ import annotations

from app.api.routes.admin_model_discovery import ModelConfigUpdate
# FIX67 — the IMAGE_GENERATION_PROVIDERS frozenset was retired (Part 1.2). Image
# backends are a registry now; a second backend is added there, not in a client.
from app.services.media_gen.image_backends import registered_image_providers
from app.services.discovery.provider_discovery import (
    _NAME_INFERENCE_PROVIDERS,
    _infer_capability,
)


# --- 1. the name proves nothing on an endpoint we don't control --------

def test_self_hosted_model_named_qwen_starts_with_everything_off():
    caps = _infer_capability("qwen3.8-27b-fp8", {}, "sranai")
    assert caps["supports_tools"] is False
    assert caps["supports_vision"] is False
    assert caps["is_chat"] is True


def test_unknown_provider_gets_no_name_inference_either():
    caps = _infer_capability("gpt-4o-vision-tools", {}, "some-relay")
    assert caps["supports_tools"] is False
    assert caps["supports_vision"] is False


def test_no_provider_means_no_name_inference():
    caps = _infer_capability("claude-3-5-sonnet", {})
    assert caps["supports_tools"] is False


def test_vendor_catalog_still_uses_the_name():
    assert "openai" in _NAME_INFERENCE_PROVIDERS
    caps = _infer_capability("gpt-4o", {}, "openai")
    assert caps["supports_tools"] is True
    assert caps["supports_vision"] is True


def test_is_chat_filter_is_name_based_for_every_provider():
    # kind, not capability: a self-hosted embedding model is still not chat
    assert _infer_capability("bge-embed-large", {}, "sranai")["is_chat"] is False


# --- "vl" only as its own token ----------------------------------------

def test_vl_token_is_vision_but_vllm_is_not():
    assert _infer_capability("qwen2.5-vl-7b", {}, "qwen")["supports_vision"] is True
    assert _infer_capability("qwen2-vl", {}, "qwen")["supports_vision"] is True
    assert _infer_capability("vllm-served-llama", {}, "qwen")["supports_vision"] is False
    assert _infer_capability("level-3-model", {}, "qwen")["supports_vision"] is False


# --- 2. what the endpoint states wins, both ways -----------------------

def test_openrouter_supported_parameters_turn_tools_on_for_an_unknown_name():
    raw = {"supported_parameters": ["temperature", "tools", "top_p"]}
    assert _infer_capability("acme/mystery-7b", raw, "openrouter")["supports_tools"] is True


def test_openrouter_supported_parameters_turn_tools_off_despite_the_name():
    raw = {"supported_parameters": ["temperature", "top_p"]}
    assert _infer_capability("openai/gpt-4o", raw, "openrouter")["supports_tools"] is False


def test_openrouter_input_modalities_decide_vision_both_ways():
    on = {"architecture": {"input_modalities": ["text", "image"]}}
    off = {"architecture": {"input_modalities": ["text"]}}
    assert _infer_capability("acme/mystery-7b", on, "openrouter")["supports_vision"] is True
    assert _infer_capability("openai/gpt-4o", off, "openrouter")["supports_vision"] is False


def test_stated_booleans_win_on_a_self_hosted_endpoint():
    raw = {"supports_function_calling": True, "supports_vision": True}
    caps = _infer_capability("anything-at-all", raw, "sranai")
    assert caps["supports_tools"] is True
    assert caps["supports_vision"] is True


# --- 3. tools ≠ images: the image target ------------------------------

def test_image_target_needs_a_registered_image_backend():
    # FIX67 — tools ≠ images still holds: an image target exists only at a
    # provider that has a registered IMAGE backend. openai does; sranai/deepseek
    # do not, so a valid key there is not an image target. (Whether a given
    # deployment offers image gen further depends on the media_order + a usable
    # key — see usable_image_destinations; this pins the backend-existence half.)
    regs = registered_image_providers()
    assert "openai" in regs
    assert "sranai" not in regs
    assert "deepseek" not in regs


def test_image_backends_are_named_in_the_backend_not_in_a_client():
    # the registry; a second image backend is added here, not in a client
    assert {"openai", "mock"} <= registered_image_providers()


# --- 4. every capability is admin-editable through the same PATCH ------

def test_admin_update_accepts_every_capability_flag():
    body = ModelConfigUpdate(
        supports_tools=False,
        supports_vision=False,
        supports_video_gen=True,
        supports_audio_gen=True,
        supports_tts=True,
    )
    updates = body.model_dump(exclude_unset=True)
    assert updates == {
        "supports_tools": False,
        "supports_vision": False,
        "supports_video_gen": True,
        "supports_audio_gen": True,
        "supports_tts": True,
    }
