"""Per-model capability test — a person clicks, the provider answers,
the catalogue records what was measured (FIX64 Part 2).

FIX62 stopped guessing capabilities from model names for providers that
state nothing (correct), which left every self-hosted row at False with
nothing to replace the guess but the operator's own guess. This module
is the replacement: ONE model, ONE capability, ONE real request, and a
verdict that keeps the four outcomes apart::

    proven_yes    the provider did the thing (called the tool, read the
                  image, streamed, emitted reasoning)
    proven_no     the provider REJECTED the parameter itself (a 4xx whose
                  body names tools / images / streaming)
    inconclusive  it answered 200 without doing the thing, or refused
                  for a reason that is not about the capability — "did
                  not happen" is not "cannot"
    unreachable   transport error, timeout, throttle, account out of
                  credit — the box's state, not the model's ability

Only ``proven_yes`` / ``proven_no`` may change a flag, and never a flag a
person set: provenance ``admin`` turns the change into a PROPOSAL the
admin page shows with an apply button. ``inconclusive`` and
``unreachable`` are stored as the last test result and shown as such —
they are never folded into False (FIX57's key-status lesson).

No scheduler, no sweep: the admin page issues one request per
capability, in order, and stops issuing when the person cancels. The
request count and whether the provider bills are stated before the first
one goes out (``plan_for``).

Not testable here, and said so rather than guessed: image / video /
audio generation are tool-routed to a dedicated model (FIX62 — a chat
model has no such property of its own), and the context window costs a
whole window of tokens to probe.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy.orm.attributes import flag_modified

logger = structlog.get_logger(__name__)

PROVEN_YES = "proven_yes"
PROVEN_NO = "proven_no"
INCONCLUSIVE = "inconclusive"
UNREACHABLE = "unreachable"

# capability name -> model_configs column
CAPABILITY_FIELDS: dict[str, str] = {
    "tools": "supports_tools",
    "vision": "supports_vision",
    "streaming": "supports_streaming",
    "reasoning": "supports_reasoning",
    "video_gen": "supports_video_gen",
    "audio_gen": "supports_audio_gen",
    "tts": "supports_tts",
}

TESTABLE: tuple[str, ...] = ("tools", "vision", "streaming", "reasoning")

# Why the rest are not tested — surfaced verbatim by the plan endpoint.
NOT_TESTABLE: dict[str, str] = {
    "video_gen": "generated through the generate_video tool by a dedicated video model; not a property of a chat model",
    "audio_gen": "generated through a dedicated audio model; not a property of a chat model",
    "tts": "served by a dedicated speech model; not a property of a chat model",
    "context_window": "proving it costs a full window of tokens per model",
}

# Per-request output caps. The tools and reasoning requests need room
# for a reasoning model to think before it acts (FIX62 measured qwen
# calling the tool with 400; 64 cut it off), the others do not.
MAX_TOKENS = {"tools": 400, "vision": 400, "streaming": 16, "reasoning": 400}

# Requests per capability. Vision is two: the same two-colour image is
# sent twice with the halves swapped, and both answers must be right.
# Measured this round: a 1x1 red image is guessed ("red") and so is a
# red|blue pair ("Red, Blue" is the stereotypical pair) — one image proves
# nothing about seeing. A model that answers the swapped control
# correctly as well is reading the pixels.
REQUESTS = {"tools": 1, "vision": 2, "streaming": 1, "reasoning": 1}

# 16x8 PNGs, 79 bytes each: A = left red, right blue; B = the swap.
_VISION_IMAGE_A = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAICAIAAAB/FOjAAAAAFklEQVR42mP4z8CAFeEQ"
    "/s8wqoEWGgClN3+BkZs1VQAAAABJRU5ErkJggg=="
)
_VISION_IMAGE_B = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAICAIAAAB/FOjAAAAAFklEQVR42mNgYPiPFf3H"
    "IcEwqoEWGgClN3+BobmKlAAAAABJRU5ErkJggg=="
)
_VISION_PROMPT = (
    "This image has two halves. What colour is the left half and what colour "
    "is the right half? Answer as two words: left, right."
)

# Provider 4xx bodies that name the parameter under test. Only these
# count as "cannot"; any other refusal is inconclusive with its text.
_REJECTION_WORDS = {
    "tools": re.compile(r"tool|function", re.I),
    "vision": re.compile(r"image|vision|multimodal|modality|content.?type|input.?type|url", re.I),
    "streaming": re.compile(r"stream", re.I),
}

# Image A: red named before blue; image B: blue before red (English or Thai).
_VISION_ANSWER_A = re.compile(r"(\bred\b|แดง).*(\bblue\b|น้ำเงิน|ฟ้า)", re.I | re.S)
_VISION_ANSWER_B = re.compile(r"(\bblue\b|น้ำเงิน|ฟ้า).*(\bred\b|แดง)", re.I | re.S)


# ---------------------------------------------------------------------------
# Provenance helpers (shared with the admin PATCH and the discovery sync)
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def provenance_of(row: Any) -> dict:
    prov = getattr(row, "capability_provenance", None)
    return dict(prov) if isinstance(prov, dict) else {}


def record_provenance(
    row: Any, cap: str, source: str, *, evidence: Optional[str] = None
) -> None:
    """Write ``{cap: {source, at[, evidence]}}`` on the row (new dict so
    SQLAlchemy notices; ``flag_modified`` for the in-place JSON case)."""
    prov = provenance_of(row)
    entry: dict[str, Any] = {"source": source, "at": _now()}
    if evidence:
        entry["evidence"] = evidence[:300]
    prov[cap] = entry
    row.capability_provenance = prov
    try:
        flag_modified(row, "capability_provenance")
    except Exception:  # noqa: BLE001 — a plain object in tests has no state
        pass


def _record_test(row: Any, cap: str, result: dict) -> None:
    prov = provenance_of(row)
    tests = dict(prov.get("tests") or {})
    tests[cap] = {
        k: result[k]
        for k in ("verdict", "evidence", "at", "applied", "proposal")
        if k in result
    }
    prov["tests"] = tests
    row.capability_provenance = prov
    try:
        flag_modified(row, "capability_provenance")
    except Exception:  # noqa: BLE001
        pass


def set_by_person(row: Any, cap: str) -> bool:
    return (provenance_of(row).get(cap) or {}).get("source") == "admin"


def discovery_may_promote(row: Any, cap: str) -> bool:
    """The sync may still promote a flag nobody set and nothing proved."""
    return (provenance_of(row).get(cap) or {}).get("source") not in ("admin", "probe")


# ---------------------------------------------------------------------------
# Plan — what a click would send, before it sends anything
# ---------------------------------------------------------------------------

def plan_for(row: Any) -> dict:
    from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS

    paid = row.provider not in SELF_HOSTED_PROVIDERS
    return {
        "model_id": row.id,
        "provider": row.provider,
        "model": row.model,
        "capabilities": list(TESTABLE),
        "requests": sum(REQUESTS[c] for c in TESTABLE),
        "requests_per_capability": {c: REQUESTS[c] for c in TESTABLE},
        "max_tokens_total": sum(MAX_TOKENS[c] * REQUESTS[c] for c in TESTABLE),
        "paid": paid,
        "not_testable": dict(NOT_TESTABLE),
        "stored": {c: bool(getattr(row, CAPABILITY_FIELDS[c], False)) for c in TESTABLE},
        "set_by_person": {c: set_by_person(row, c) for c in TESTABLE},
        "last_tests": dict((provenance_of(row).get("tests") or {})),
    }


# ---------------------------------------------------------------------------
# One request per capability
# ---------------------------------------------------------------------------

def _error_text(detail: Any) -> str:
    return str(detail or "")[:600]


def _classify(cap: str, outcome: str, detail: Any) -> tuple[str, str]:
    """Map a failed dispatch to (verdict, evidence) for ``cap``."""
    text = _error_text(detail)
    if outcome in ("transport_error", "rate_limited", "no_credit"):
        return UNREACHABLE, f"{outcome}: {text}"
    if outcome == "rejected_by_provider":
        words = _REJECTION_WORDS.get(cap)
        if words is not None and words.search(text):
            return PROVEN_NO, f"provider rejected the {cap} request: {text}"
        return INCONCLUSIVE, f"provider refused for another reason: {text}"
    return INCONCLUSIVE, f"{outcome}: {text}"


async def _stream_once(provider: str, model: str, api_key: str, payload: dict) -> tuple[str, Any]:
    """Open a streaming request and read up to a few chunks."""
    from app.schemas.common import Message
    from app.schemas.public import ChatCompletionsRequest
    from app.services.capability_probe import _classify_error
    from app.services.provider_dispatcher import ProviderDispatcher

    adapter = ProviderDispatcher().adapters.get(provider)
    if adapter is None:
        return "transport_error", f"provider '{provider}' has no adapter"
    messages = [Message(**m) for m in payload["messages"]]
    req = ChatCompletionsRequest(**payload)
    chunks = 0
    try:
        async for _chunk in adapter.stream_chat(
            req, messages, resolved_model=model, api_key=api_key
        ):
            chunks += 1
            if chunks >= 3:
                break
    except Exception as exc:  # noqa: BLE001 — a probe never raises out
        return _classify_error(exc)
    return "ok", chunks


async def _test_one(cap: str, provider: str, model: str, api_key: str) -> dict:
    from app.services.capability_probe import (
        _GET_TIME_TOOL,
        _TOOLS_PROMPT,
        _allow_capability,
        _dispatch_once,
    )

    t0 = time.perf_counter()
    if cap == "tools":
        # The adapters refuse ``tools`` for a stored-False row — the very
        # flag under test — so the process-local snapshot is opened first
        # (nothing is written; see capability_probe._allow_capability).
        _allow_capability(model, tools=True)
        outcome, resp = await _dispatch_once(
            provider, model, api_key,
            {
                "model": model,
                "messages": [{"role": "user", "content": _TOOLS_PROMPT}],
                "max_tokens": MAX_TOKENS["tools"],
                "tools": _GET_TIME_TOOL,
                "tool_choice": "auto",
            },
        )
        if outcome == "ok":
            calls = getattr(resp, "tool_calls", None) or []
            if calls:
                verdict, evidence = PROVEN_YES, f"tool_call_emitted: {calls[0].get('function', {}).get('name', '?')}"
            else:
                verdict, evidence = INCONCLUSIVE, "answered_without_tool: the provider accepted the tools parameter but the model answered in prose"
        else:
            verdict, evidence = _classify(cap, outcome, resp)
    elif cap == "vision":
        _allow_capability(model, vision=True)
        answers: list[str] = []
        verdict, evidence = INCONCLUSIVE, ""
        for label, image, expect in (
            ("A red|blue", _VISION_IMAGE_A, _VISION_ANSWER_A),
            ("B blue|red", _VISION_IMAGE_B, _VISION_ANSWER_B),
        ):
            outcome, resp = await _dispatch_once(
                provider, model, api_key,
                {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": image}},
                                {"type": "text", "text": _VISION_PROMPT},
                            ],
                        }
                    ],
                    "max_tokens": MAX_TOKENS["vision"],
                },
            )
            if outcome != "ok":
                verdict, evidence = _classify(cap, outcome, resp)
                break
            content = str(getattr(resp, "content", "") or "").strip()
            finish = str(getattr(resp, "finish_reason", "") or "")
            answers.append(f"{label} -> {content[:40]!r}")
            if expect.search(content):
                continue
            if not content and finish == "length":
                verdict, evidence = INCONCLUSIVE, f"accepted the image but the reply was cut off before any text (finish_reason=length); {'; '.join(answers)}"
            elif label.startswith("B") :
                verdict, evidence = INCONCLUSIVE, f"named the first image but failed the swapped control, which is what guessing looks like; {'; '.join(answers)}"
            else:
                verdict, evidence = INCONCLUSIVE, f"accepted the image but did not name its halves; {'; '.join(answers)}"
            break
        else:
            verdict, evidence = PROVEN_YES, f"named both halves of the image and of its swapped control; {'; '.join(answers)}"
    elif cap == "streaming":
        outcome, resp = await _stream_once(
            provider, model, api_key,
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the word OK."}],
                "max_tokens": MAX_TOKENS["streaming"],
                "stream": True,
            },
        )
        if outcome == "ok":
            if resp and resp > 0:
                verdict, evidence = PROVEN_YES, f"streamed {resp} chunk(s)"
            else:
                verdict, evidence = INCONCLUSIVE, "stream opened but no chunk arrived"
        else:
            verdict, evidence = _classify(cap, outcome, resp)
    elif cap == "reasoning":
        outcome, resp = await _dispatch_once(
            provider, model, api_key,
            {
                "model": model,
                "messages": [{"role": "user", "content": "What is 17 multiplied by 23? Think it through, then answer with the number."}],
                "max_tokens": MAX_TOKENS["reasoning"],
            },
        )
        if outcome == "ok":
            reasoning = getattr(resp, "reasoning_content", None) or ""
            if str(reasoning).strip():
                verdict, evidence = PROVEN_YES, f"reasoning_content emitted ({len(str(reasoning))} chars)"
            else:
                verdict, evidence = INCONCLUSIVE, "no reasoning_content in the reply; some models reason only when asked with a provider-specific knob, which this test does not send"
        else:
            # A reasoning request carries no special parameter, so a
            # refusal can never be about reasoning.
            v, e = _classify(cap, outcome, resp)
            verdict, evidence = (UNREACHABLE if v == UNREACHABLE else INCONCLUSIVE), e
    else:
        raise ValueError(f"capability '{cap}' is not testable")
    return {
        "capability": cap,
        "verdict": verdict,
        "evidence": evidence,
        "latency_ms": round((time.perf_counter() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# The click
# ---------------------------------------------------------------------------

async def run_capability_test(session, row: Any, cap: str, *, apply: bool = True) -> dict:
    """Test ``cap`` on ``row``'s model; apply the verdict under the rules.

    Rules (each one stated to the caller in the result):
      proven_yes and stored False  -> set True, unless a person set it
                                      (then ``proposal``)
      proven_no  and stored True   -> set False, unless a person set it
                                      (then ``proposal``)
      anything else                -> no change, result recorded
    """
    if cap not in TESTABLE:
        raise ValueError(f"capability '{cap}' is not testable: {NOT_TESTABLE.get(cap, 'unknown capability')}")
    from app.services.provider_key_service import ProviderKeyService

    field_name = CAPABILITY_FIELDS[cap]
    stored_before = bool(getattr(row, field_name, False))
    api_key = ProviderKeyService().get_active_key(session, row.provider, model=row.model)
    if not api_key:
        result = {
            "capability": cap,
            "verdict": UNREACHABLE,
            "evidence": f"no usable key for provider '{row.provider}'",
            "latency_ms": 0,
        }
    else:
        # Dispatch reads the process-local registry; make sure it holds
        # the live catalogue before the first request.
        from app.services.providers.model_registry import _registry

        await _registry.reload(session)
        result = await _test_one(cap, row.provider, row.model, api_key)

    result["at"] = _now()
    result["stored_before"] = stored_before
    wants = {PROVEN_YES: True, PROVEN_NO: False}.get(result["verdict"])
    applied = False
    proposal = False
    if wants is not None and wants != stored_before:
        if set_by_person(row, cap):
            proposal = True
        elif apply:
            setattr(row, field_name, wants)
            record_provenance(row, cap, "probe", evidence=result["evidence"])
            applied = True
    if (
        cap == "reasoning"
        and result["verdict"] == PROVEN_YES
        and apply
        and not set_by_person(row, cap)
    ):
        # The reply CARRIED reasoning_content unasked, so a row with no
        # strategy gets the read-only one: the gateway parses the thinking
        # and never sends a knob a self-hosted box may reject (see
        # openai_compatible_adapter._build_body). A strategy someone
        # already chose is left alone.
        from app.services.providers.reasoning_strategies import (
            STRATEGY_NONE,
            STRATEGY_REASONING_CONTENT,
        )

        if getattr(row, "reasoning_strategy", STRATEGY_NONE) == STRATEGY_NONE:
            row.reasoning_strategy = STRATEGY_REASONING_CONTENT
            result["reasoning_strategy"] = STRATEGY_REASONING_CONTENT
    result["applied"] = applied
    result["proposal"] = proposal
    result["proposed_value"] = wants if proposal else None
    result["stored_after"] = bool(getattr(row, field_name, False))
    _record_test(row, cap, result)
    session.flush()
    session.commit()
    if applied:
        try:
            from app.services.providers.model_registry import _registry

            await _registry.reload(session)
        except Exception:  # noqa: BLE001 — the write stands; the next sync reloads
            pass
    logger.info(
        "capability_test",
        provider=row.provider,
        model=row.model,
        capability=cap,
        verdict=result["verdict"],
        applied=applied,
        proposal=proposal,
        stored_before=stored_before,
        stored_after=result["stored_after"],
    )
    result["provenance"] = provenance_of(row)
    return result
