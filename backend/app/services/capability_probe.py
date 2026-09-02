"""Capability probe — prove ``supports_tools`` / ``supports_vision``
instead of trusting discovery metadata (FIX47.6 Part B).

The catalogue's capability flags come from discovery metadata and were
never tested against the providers. This module sends, per
(provider-with-a-valid-key × active model) pair, the lightest possible
real requests and compares what the provider actually does with what the
row claims. Capability is a property of the TUPLE (model × provider
route × adapter), never of a model name — the probe iterates pairs and
records results per pair.

Three probes per chat pair, in order, later ones skipped when the model
is not alive:

  1. alive  — the same 1-token "ping" as ``check_model_health`` (the
              admin Test button), with failures CLASSIFIED: a dead
              model is False, but an account out of credits or a
              throttle is None (unknown) — a provider-wide billing
              state must not read as "every model died".
  2. tools  — one request offering a single trivial ``get_time`` function
              and a prompt that should trigger it.
  3. vision — one request with a 1×1 PNG data URI (optional).

The probe REPORTS; it never writes to the database. Silent self-editing
of capability data is how wrong flags would become invisible again — a
human reads the mismatch table and decides.

Bypassing our own gate: the adapters refuse ``tools`` for a model whose
STORED ``supports_tools`` is False (``model_no_tool_support``) — the very
flag under test. The probe therefore flips the flag on its own
process-local registry snapshot before the tools request, so the
provider's verdict is heard instead of our own echo. Nothing is written
back; the flip dies with the process.

Cost guards (all mandatory per the brief): ``max_tokens`` small, one
attempt per pair (single retry only on a provider 429, after a pause),
a hard cap on total requests, ``dry_run`` prints the pair list without
calling anything, and key material is never logged.

Run inside the backend container (the only place provider keys resolve):

    python -m app.services.capability_probe --dry-run
    python -m app.services.capability_probe --json > report.json

``scripts/probe_model_capabilities.py`` is the host-side wrapper.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

# Hard ceiling on total paid requests per run. The full sweep today is
# ~90 pairs × up to 3 requests ≈ 270; the cap leaves headroom without
# letting a runaway catalogue burn money.
DEFAULT_MAX_REQUESTS = 400

# Pause between requests to the SAME provider — openai has ~70 active
# models and hammering them back-to-back trips rate limits (seen live in
# FIX42), which would misreport live models as dead.
PER_REQUEST_DELAY_S = 0.3

# One retry, only for a provider 429, after this pause. A 429 is
# throttling, not a capability verdict — retrying once keeps the
# classification honest without violating the one-attempt rule for
# real rejections.
RATE_LIMIT_RETRY_DELAY_S = 10.0

_GET_TIME_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current time.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]

_TOOLS_PROMPT = "What time is it right now? Use the get_time tool to check."

# 1×1 red PNG, 67 bytes — the smallest real image a vision model can read.
_PROBE_IMAGE_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

# tools probe outcomes:
#   tool_call_emitted     — the model called get_time: tools proven.
#   answered_without_tool — provider ACCEPTED the tools param but the
#                           model answered in prose. The route tolerates
#                           tools; whether the model ever calls one is
#                           not disproven by one prompt.
#   rejected_by_provider  — provider 4xx'd the request (raw error kept).
#   rate_limited          — 429 after the single retry; no verdict.
#   transport_error       — network / timeout; no verdict.
#   skipped_dead          — alive probe failed; nothing else sent.
#   skipped_media         — video/audio/tts row; a chat probe is meaningless.
#   skipped_cap           — the run hit max_requests before this pair.


def _classify_error(exc: Exception) -> tuple[str, str]:
    """Map an exception from dispatch to (outcome, detail)."""
    from app.core.exceptions import GatewayError

    detail = str(exc)[:300]
    status = None
    details = getattr(exc, "details", None)
    body_text = ""
    if isinstance(details, dict):
        status = details.get("status")
        body_text = str(details.get("body", ""))[:500]
    # Account out of credits ≠ dead model. OpenAI reports this as a 429
    # with type=insufficient_quota — measured live in this round: the
    # whole provider "died" at once and the cause was the billing page,
    # not the models. Classified separately so the curation ranking can
    # treat it as ALIVE-UNKNOWN instead of never proposing the provider.
    if any(m in body_text for m in ("insufficient_quota", "credit_balance_exhausted", "no credits")):
        return "no_credit", (detail + " | " + body_text)[:400]
    if status == 429 or "429" in detail or "rate limit" in detail.lower():
        return "rate_limited", detail
    if isinstance(exc, GatewayError):
        return "rejected_by_provider", detail
    return "transport_error", detail


async def _dispatch_once(
    provider: str, model: str, api_key: str, payload: Any
) -> tuple[str, Any]:
    """One dispatch; single retry on 429. Returns (outcome, response|detail)."""
    from app.schemas.common import Message
    from app.services.provider_dispatcher import ProviderDispatcher
    from app.domain.entities import RouteDecision

    route = RouteDecision(provider_name=provider, model_name=model)
    messages = [Message(**m) for m in payload["messages"]]
    from app.schemas.public import ChatCompletionsRequest

    req = ChatCompletionsRequest(**payload)
    for attempt in (1, 2):
        try:
            resp = await ProviderDispatcher().dispatch(
                route=route, payload=req, messages=messages, api_key=api_key
            )
            return "ok", resp
        except Exception as exc:  # noqa: BLE001 — a probe never raises out
            outcome, detail = _classify_error(exc)
            if outcome == "rate_limited" and attempt == 1:
                await asyncio.sleep(RATE_LIMIT_RETRY_DELAY_S)
                continue
            return outcome, detail
    return "transport_error", "unreachable"


def _allow_capability(model: str, *, tools: bool = False, vision: bool = False) -> None:
    """Flip the flag on the process-local registry snapshot only.

    The adapters consult ``get_candidate(model)`` — the very flags under
    test. Without this, probing a stored-False model would return our
    own ``model_no_tool_support`` echo instead of the provider's verdict.
    """
    from app.services.providers.model_registry import _registry

    cand = _registry._model_map.get(model)
    if cand is None:
        return
    changes: dict[str, bool] = {}
    if tools and not cand.supports_tools:
        changes["supports_tools"] = True
    if vision and not cand.supports_vision:
        changes["supports_vision"] = True
    if changes:
        _registry._model_map[model] = dataclasses.replace(cand, **changes)


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------

async def run_probe(
    session: Session,
    *,
    providers: Optional[set[str]] = None,
    include_vision: bool = True,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    dry_run: bool = False,
) -> dict:
    """Probe every (valid-key provider × active model) pair.

    Returns a JSON-able report: per-pair outcomes, the mismatch table
    (stored flag vs observed), and the request count against the cap.
    Never writes to the database.
    """
    from app.models.model_config import ModelConfigORM
    from app.services.provider_key_service import ProviderKeyService
    from app.services.providers.model_registry import _registry

    key_service = ProviderKeyService()
    valid = key_service.providers_with_valid_keys(session)
    if providers:
        valid &= providers

    rows = (
        session.execute(
            select(ModelConfigORM)
            .where(ModelConfigORM.is_active.is_(True))
            .order_by(ModelConfigORM.provider, ModelConfigORM.model)
        )
        .scalars()
        .all()
    )

    pairs: list[dict] = []
    for r in rows:
        if r.provider not in valid:
            continue
        media = r.supports_video_gen or r.supports_audio_gen or r.supports_tts
        pairs.append(
            {
                "provider": r.provider,
                "model": r.model,
                "stored_supports_tools": r.supports_tools,
                "stored_supports_vision": r.supports_vision,
                "stored_supports_reasoning": r.supports_reasoning,
                "media": bool(media),
            }
        )

    per_pair_requests = 2 + (1 if include_vision else 0)
    chat_pairs = [p for p in pairs if not p["media"]]
    estimate = len(chat_pairs) * per_pair_requests
    report: dict = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "providers_with_valid_keys": sorted(valid),
        "pair_count": len(pairs),
        "chat_pair_count": len(chat_pairs),
        "estimated_max_requests": estimate,
        "request_cap": max_requests,
        "include_vision": include_vision,
        "dry_run": dry_run,
    }
    if dry_run:
        report["pairs"] = pairs
        return report

    # Load the live catalogue into this process's registry before any
    # dispatch — a bare process only has the module-load seed.
    await _registry.reload(session)

    spent = 0
    lock = asyncio.Lock()

    async def _take() -> bool:
        nonlocal spent
        async with lock:
            if spent >= max_requests:
                return False
            spent += 1
            return True

    results: list[dict] = []

    async def _probe_provider(provider: str, models: list[dict]) -> None:
        api_key = key_service.get_active_key(session, provider)
        for p in models:
            entry = dict(p)
            results.append(entry)
            if p["media"]:
                entry["outcome"] = "skipped_media"
                continue
            if not api_key:
                entry["outcome"] = "skipped_no_key"
                continue

            # 1 — alive: the same 1-token "ping" that check_model_health
            # (the admin Test button) sends, but dispatched through the
            # probe's own wrapper so the failure CLASSIFIES: an account
            # out of credits or a throttle is alive-UNKNOWN (None), never
            # "dead" — the FIX42 trap, measured again this round when the
            # whole openai catalogue "died" of an empty billing balance.
            if not await _take():
                entry["outcome"] = "skipped_cap"
                continue
            t0 = time.perf_counter()
            a_outcome, a_resp = await _dispatch_once(
                provider,
                p["model"],
                api_key,
                {
                    "model": p["model"],
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            entry["alive_latency_ms"] = round((time.perf_counter() - t0) * 1000)
            if a_outcome == "ok":
                entry["alive"] = True
            elif a_outcome in ("no_credit", "rate_limited"):
                entry["alive"] = None
                entry["outcome"] = f"skipped_{a_outcome}"
                entry["alive_error"] = a_resp
                continue
            else:
                entry["alive"] = False
                entry["outcome"] = "skipped_dead"
                entry["alive_error"] = a_resp
                continue
            await asyncio.sleep(PER_REQUEST_DELAY_S)

            # 2 — tools.
            if not await _take():
                entry["outcome"] = "skipped_cap"
                continue
            _allow_capability(p["model"], tools=True)
            outcome, resp = await _dispatch_once(
                provider,
                p["model"],
                api_key,
                {
                    "model": p["model"],
                    "messages": [{"role": "user", "content": _TOOLS_PROMPT}],
                    "max_tokens": 64,
                    "tools": _GET_TIME_TOOL,
                    "tool_choice": "auto",
                },
            )
            if outcome == "ok":
                emitted = bool(getattr(resp, "tool_calls", None))
                entry["outcome"] = (
                    "tool_call_emitted" if emitted else "answered_without_tool"
                )
                entry["observed_supports_tools"] = True
            else:
                entry["outcome"] = outcome
                entry["error"] = resp
                if outcome == "rejected_by_provider":
                    entry["observed_supports_tools"] = False
            await asyncio.sleep(PER_REQUEST_DELAY_S)

            # 3 — vision (optional).
            if not include_vision:
                continue
            if not await _take():
                entry["vision_outcome"] = "skipped_cap"
                continue
            _allow_capability(p["model"], vision=True)
            v_outcome, v_resp = await _dispatch_once(
                provider,
                p["model"],
                api_key,
                {
                    "model": p["model"],
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": _PROBE_IMAGE_URI},
                                },
                                {
                                    "type": "text",
                                    "text": "What colour is this image? One word.",
                                },
                            ],
                        }
                    ],
                    "max_tokens": 16,
                },
            )
            if v_outcome == "ok":
                entry["vision_outcome"] = "answered"
                entry["observed_supports_vision"] = True
            else:
                entry["vision_outcome"] = v_outcome
                entry["vision_error"] = v_resp
                if v_outcome == "rejected_by_provider":
                    entry["observed_supports_vision"] = False
            await asyncio.sleep(PER_REQUEST_DELAY_S)

    by_provider: dict[str, list[dict]] = {}
    for p in pairs:
        by_provider.setdefault(p["provider"], []).append(p)
    # Serial within a provider (rate limits), providers in parallel.
    await asyncio.gather(
        *(_probe_provider(prov, models) for prov, models in by_provider.items())
    )

    mismatches = [
        {
            "provider": r["provider"],
            "model": r["model"],
            "flag": flag,
            "stored": r[f"stored_supports_{flag}"],
            "observed": r[f"observed_supports_{flag}"],
            "outcome": r.get("outcome" if flag == "tools" else "vision_outcome"),
            "error": r.get("error" if flag == "tools" else "vision_error"),
        }
        for r in results
        for flag in ("tools", "vision")
        if f"observed_supports_{flag}" in r
        and r[f"observed_supports_{flag}"] != r[f"stored_supports_{flag}"]
    ]

    report["requests_sent"] = spent
    report["results"] = results
    report["mismatches"] = mismatches
    report["mismatch_count"] = len(mismatches)
    return report


# ---------------------------------------------------------------------------
# Weekly drift check (FIX47.6 Part B.3) — same in-process scheduler the
# discovery sync uses. Reports drift (a structured log entry per run,
# WARNING when any flag disagrees); it repairs nothing.
# ---------------------------------------------------------------------------

# Advisory-lock key: every gunicorn worker runs its own scheduler (see
# scheduler.py) — redundant discovery syncs are idempotent upserts, but a
# redundant probe is a paid request per model per worker, so exactly one
# worker takes the lock and the rest skip.
_PROBE_LOCK_KEY = 0x47_6B  # arbitrary, stable

async def run_weekly_probe() -> None:
    from sqlalchemy import text
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _PROBE_LOCK_KEY}
        ).scalar()
        if not got_lock:
            logger.info("capability_probe_skipped", reason="another worker holds the lock")
            return
        try:
            report = await run_probe(db)
            log = logger.warning if report["mismatch_count"] else logger.info
            log(
                "capability_flag_drift_report",
                mismatch_count=report["mismatch_count"],
                mismatches=report["mismatches"][:50],
                requests_sent=report["requests_sent"],
                request_cap=report["request_cap"],
                pair_count=report["pair_count"],
            )
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PROBE_LOCK_KEY})
            db.commit()
    except Exception as exc:  # noqa: BLE001 — a scheduled probe never raises
        logger.warning("capability_probe_failed", error=str(exc)[:300])
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CLI (run inside the backend container)
# ---------------------------------------------------------------------------

def _main() -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="list pairs + estimated request count; send nothing")
    ap.add_argument("--provider", action="append", help="limit to this provider (repeatable)")
    ap.add_argument("--no-vision", action="store_true", help="skip the vision probe")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    args = ap.parse_args()

    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        report = asyncio.run(
            run_probe(
                db,
                providers=set(args.provider) if args.provider else None,
                include_vision=not args.no_vision,
                max_requests=args.max_requests,
                dry_run=args.dry_run,
            )
        )
    finally:
        db.close()
    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
