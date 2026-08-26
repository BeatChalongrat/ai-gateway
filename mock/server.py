"""Realistic mock LLM provider for perf baseline runs.

Accepts OpenAI /v1/chat/completions requests (the OpenAI adapter shape —
``{"model", "messages", "stream", "stream_options"}``), streams SSE
chunks token-by-token at a configurable tokens-per-second, and produces
a latency distribution that matches the targets in ``latency_profile.yaml``.

Design notes:

* Single FastAPI app on a separate port (default 9090). Run with
  ``uvicorn perf.mock_provider.server:app --port 9090``.
* Point the gateway at this by setting ``OPENAI_BASE_URL`` when starting
  the backend (the OpenAI adapter honors the environment-style base URL
  override, but since the adapter hard-codes ``OPENAI_BASE_URL`` at the
  module level, the simpler path for the baseline runner is to use k6
  with ``OPENAI_API_BASE=http://localhost:9090/v1`` and a custom adapter
  flag — see perf/run-baseline.py 'mock wiring' section for the exact
  command).
* Minimal OpenAI-compatible: echoes back a lorem-ipsum style response
  so the gateway's DLP pipeline runs on realistic text. For DLP-heavy
  scenarios, the prompt selects a response template that contains PII
  (emails, Thai national IDs, credit cards) — see ``_pick_response()``.
* Identifies itself via the ``X-Mock-Provider`` response header so
  downstream log analysis can tell real vs mock runs apart.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


_PROFILE_PATH = Path(__file__).parent / "latency_profile.yaml"
_PROFILE_NAME = os.environ.get("MOCK_PROFILE", "realistic")

with _PROFILE_PATH.open("r", encoding="utf-8") as fh:
    _config = yaml.safe_load(fh)

_profile = _config["profiles"][_PROFILE_NAME]


def _lognormal_sample(params: dict) -> float:
    mu = params["mu"]
    sigma = params["sigma"]
    min_v = params.get("min", 0.0)
    max_v = params.get("max", math.inf)
    # random.lognormvariate is exp(N(mu, sigma)).
    val = random.lognormvariate(mu, sigma)
    return max(min_v, min(val, max_v))


def _uniform_sample(params: dict) -> float:
    return random.uniform(params["min"], params["max"])


def _sample(params: dict) -> float:
    dist = params["distribution"]
    if dist == "lognormal":
        return _lognormal_sample(params)
    if dist == "uniform":
        return _uniform_sample(params)
    raise ValueError(f"unknown distribution: {dist!r}")


# Realistic response templates. The "dlp_heavy" ones include PII so the
# outbound DLP path actually does work during load tests.
_CLEAN_RESPONSES = [
    "Sure, here's a short explanation of the concept you asked about. "
    "It works by chaining several steps together, each refining the "
    "previous result until the answer stabilises.",
    "Here's one approach you could take: break the problem into smaller "
    "chunks, solve each chunk independently, then combine the partial "
    "answers at the end.",
    "The difference between the two usually comes down to where the "
    "boundary lives. In the first case the boundary is explicit; in the "
    "second it's inferred from context.",
    "That's a common question. Most teams end up picking based on "
    "operational constraints rather than raw performance — the gap is "
    "smaller than people expect in practice.",
    "Interesting choice. A few things to watch out for: make sure the "
    "reads stay consistent under concurrent writes, and keep the retry "
    "policy conservative so a bad request doesn't cascade.",
]

_DLP_HEAVY_RESPONSES = [
    "I found the contact you asked about: email alice@example.com, "
    "phone +1-415-555-0134, and address 742 Evergreen Terrace.",
    "Here is the test account you requested — username test_user, "
    "password Hunter2! (please rotate after the first login), and API "
    "key sk-test-ABCDEFGH1234567890.",
    "รายการเลขประจำตัวที่ต้องยืนยัน: 1-1017-00230-70-8 สำหรับเจ้าของบัญชี "
    "และ 3-9876-54321-09-7 สำหรับผู้ติดต่อสำรอง.",
    "Sample card: 4111 1111 1111 1111, expiry 12/27, CVV 123. Use it "
    "only in the sandbox environment — production gateway will reject "
    "test card numbers at the issuer tier.",
    "The server config lives at /etc/gateway/config.yaml. Reach the "
    "admin at admin@example.org or call 555-012-3456; SSH key "
    "fingerprint is SHA256:aBcD1234eFgH5678.",
]


def _pick_response(messages: list[dict]) -> str:
    """Choose a response template based on the request flavour.

    If the last user message contains any DLP trigger keyword, we pick a
    DLP-heavy response so the gateway's outbound DLP regex path actually
    has work to do. Otherwise use a clean template.
    """
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = str(m.get("content", "")) or ""
            break
    triggers = ("email", "phone", "card", "ssn", "password", "ไทย", "เลขประจำตัว")
    if any(t in last_user.lower() for t in triggers):
        return random.choice(_DLP_HEAVY_RESPONSES)
    return random.choice(_CLEAN_RESPONSES)


def _tokenize(text: str) -> list[str]:
    """Rough word + punctuation tokenization — we only need N chunks."""
    # Whitespace-split for the streaming cadence; preserves spaces by
    # re-attaching them to the following token so the client assembles a
    # readable string.
    pieces: list[str] = []
    parts = text.split(" ")
    for i, p in enumerate(parts):
        if i == 0:
            pieces.append(p)
        else:
            pieces.append(" " + p)
    return pieces


def _maybe_inject_error() -> tuple[int | None, str | None]:
    """Return (status_code, kind) when an error should fire, else (None, None)."""
    err = _profile.get("errors", {})
    rate = float(err.get("rate", 0.0))
    if random.random() >= rate:
        return None, None
    buckets = err.get("distribution", {})
    pick = random.random()
    acc = 0.0
    for k, v in buckets.items():
        acc += float(v)
        if pick <= acc:
            return (int(k) if str(k).isdigit() else 0, str(k))
    return 500, "500"


# ---------------------------------------------------------------------------
# Request capture (DLP instrument). Opt-in via MOCK_CAPTURE_PATH; when set,
# EVERY request to the two completion endpoints is appended to that file as
# one JSON object per line:
#   {"ts", "endpoint", "method", "path", "headers", "body_raw", "body_json"}
# * body_raw is the ENTIRE raw request body (utf-8, errors replaced) — not
#   just ``messages`` — so top-level fields such as Anthropic's ``system``
#   are captured verbatim. body_json is a best-effort parse of the same
#   bytes (null when not JSON).
# * headers are captured with credential-bearing values REDACTED BY NAME
#   (authorization, x-api-key, api-key, cookie, …); the value is replaced
#   whole, never truncated, so no key fragment can reach the file.
# * Capture is append-only and must never change the response; a capture
#   failure is swallowed (logged to stderr) so the mock keeps serving.
# The file will contain whatever PII the test sends — keep it out of git.
# ---------------------------------------------------------------------------
_CAPTURE_PATH = os.environ.get("MOCK_CAPTURE_PATH", "").strip()
_REDACT_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "x-goog-api-key",
        "cookie",
        "set-cookie",
        "x-internal-service-secret",
    }
)
_capture_lock = asyncio.Lock()


def _redact_headers(raw_headers) -> dict:
    out: dict[str, str] = {}
    for k, v in raw_headers:
        key = k.decode("latin-1") if isinstance(k, bytes) else str(k)
        val = v.decode("latin-1", "replace") if isinstance(v, bytes) else str(v)
        if key.lower() in _REDACT_HEADERS:
            out[key] = "<redacted>"
        else:
            out[key] = val
    return out


async def _capture_request(endpoint: str, req: Request, body_bytes: bytes) -> None:
    if not _CAPTURE_PATH:
        return
    try:
        body_raw = body_bytes.decode("utf-8", "replace")
        try:
            body_json = json.loads(body_bytes)
        except Exception:  # noqa: BLE001 — non-JSON bodies are still captured raw
            body_json = None
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            + f".{int((time.time() % 1) * 1000):03d}Z",
            "endpoint": endpoint,
            "method": req.method,
            "path": req.url.path,
            "client": req.client.host if req.client else None,
            "headers": _redact_headers(req.headers.raw),
            "body_len": len(body_bytes),
            "body_raw": body_raw,
            "body_json": body_json,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        # One os.write per record on an O_APPEND fd: each record lands as a
        # single atomic append even with several uvicorn workers.
        async with _capture_lock:
            fd = os.open(_CAPTURE_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
    except Exception as exc:  # noqa: BLE001 — never let capture break the mock
        print(f"[mock-capture] failed: {exc!r}", flush=True)


app = FastAPI(title="mock-llm-provider", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "profile": _PROFILE_NAME, "capture": bool(_CAPTURE_PATH)}


@app.get("/v1/models")
async def list_models() -> dict:
    """Minimal OpenAI-compatible model list.

    Exists so the gateway's self-hosted provider flow can point at this
    mock the supported way (admin UI → ``GET {base}/models`` probe +
    discovery). It does not affect completions.
    """
    return {
        "object": "list",
        "data": [
            {"id": "mock-chat-1", "object": "model", "owned_by": "mock"},
        ],
    }


@app.get("/_calibration_samples")
async def calibration_samples(n: int = 1000) -> dict:
    """Return N synthetic total_completion samples so we can plot the
    real distribution without having to run a full load test. Used by
    the calibration harness in perf/run-baseline.py."""
    samples = [_sample(_profile["total_completion"]) for _ in range(n)]
    samples.sort()
    def p(q: float) -> float:
        idx = int(q * (len(samples) - 1))
        return samples[idx]
    return {
        "n": n,
        "p50": p(0.5),
        "p95": p(0.95),
        "p99": p(0.99),
        "mean": sum(samples) / len(samples),
        "min": samples[0],
        "max": samples[-1],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: Request) -> object:
    body_bytes = await req.body()
    await _capture_request("/v1/chat/completions", req, body_bytes)
    body = json.loads(body_bytes)
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    model = body.get("model", "mock-model")

    err_code, err_kind = _maybe_inject_error()
    if err_code is not None:
        if err_kind == "timeout":
            # Emulate a timeout by sleeping past any reasonable client
            # deadline. The gateway's read-timeout is 30s by default;
            # sleep long enough that the adapter sees it as a timeout.
            await asyncio.sleep(35.0)
            raise HTTPException(status_code=504, detail="mock timeout")
        if err_code == 429:
            return JSONResponse(
                status_code=429,
                content={"error": {"message": "mock rate limit", "type": "rate_limit_error"}},
                headers={"X-Mock-Provider": _PROFILE_NAME, "Retry-After": "1"},
            )
        return JSONResponse(
            status_code=err_code,
            content={"error": {"message": f"mock {err_code}", "type": "server_error"}},
            headers={"X-Mock-Provider": _PROFILE_NAME},
        )

    ttfb_s = _sample(_profile["ttfb"])
    total_s = _sample(_profile["total_completion"])
    tps = _sample(_profile["tokens_per_second"])
    response_text = _pick_response(messages)
    tokens = _tokenize(response_text)

    if not stream:
        # Non-streaming path — sleep the full total_s, then return a
        # single JSON body. This is what the orchestrator's
        # non-streaming tests hit.
        await asyncio.sleep(total_s)
        return JSONResponse(
            status_code=200,
            content={
                "id": f"mock-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": len(tokens),
                    "total_tokens": 10 + len(tokens),
                },
            },
            headers={"X-Mock-Provider": _PROFILE_NAME},
        )

    # Streaming: ttfb delay, then emit deltas at tps tokens/sec. We
    # budget the remaining (total_s - ttfb_s) against the token count
    # so the final duration hits the target distribution.
    async def event_stream():
        await asyncio.sleep(ttfb_s)
        remaining_s = max(0.0, total_s - ttfb_s)
        per_token_s = remaining_s / max(1, len(tokens))
        # Clamp per-token gap so it lines up with the configured tps:
        # whichever is slower (the distribution-driven total or the
        # tps-driven per-token) wins so both targets are honored.
        per_token_s = max(per_token_s, 1.0 / tps)
        for tok in tokens:
            chunk = {
                "id": f"mock-{int(time.time() * 1000)}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": tok},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(per_token_s)

        # Final stop chunk + usage (matches OpenAI stream_options.include_usage).
        yield (
            "data: "
            + json.dumps(
                {
                    "id": f"mock-{int(time.time() * 1000)}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": len(tokens),
                        "total_tokens": 10 + len(tokens),
                    },
                }
            )
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Mock-Provider": _PROFILE_NAME, "Cache-Control": "no-cache"},
    )


# --- Anthropic-shaped endpoint (for the Anthropic adapter) ---
# The AnthropicAdapter POSTS to /v1/messages with a similar body; a
# minimal echo shim keeps both providers covered in the same process.
@app.post("/v1/messages")
async def anthropic_messages(req: Request) -> object:
    body_bytes = await req.body()
    await _capture_request("/v1/messages", req, body_bytes)
    body = json.loads(body_bytes)
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    model = body.get("model", "mock-claude")

    err_code, _ = _maybe_inject_error()
    if err_code is not None:
        return JSONResponse(
            status_code=err_code or 500,
            content={"error": {"type": "api_error", "message": "mock error"}},
            headers={"X-Mock-Provider": _PROFILE_NAME},
        )

    response_text = _pick_response(messages)
    total_s = _sample(_profile["total_completion"])

    if not stream:
        await asyncio.sleep(total_s)
        return JSONResponse(
            status_code=200,
            content={
                "id": f"mock-{int(time.time() * 1000)}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": response_text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": len(response_text.split())},
            },
            headers={"X-Mock-Provider": _PROFILE_NAME},
        )

    async def event_stream():
        ttfb_s = _sample(_profile["ttfb"])
        await asyncio.sleep(ttfb_s)
        for tok in _tokenize(response_text):
            delta_event = {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": tok},
            }
            yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"
            await asyncio.sleep(1.0 / _sample(_profile["tokens_per_second"]))
        yield "event: message_stop\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Mock-Provider": _PROFILE_NAME, "Cache-Control": "no-cache"},
    )
