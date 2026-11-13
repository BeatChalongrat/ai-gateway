# Media Generation API

Agent video follow-up (2026-11-13): Desktop now submits via `generate_video`; both agents have `video_job` status/cancel. A shared jobs panel recovers owner-scoped jobs, confirms quoted costs explicitly and plays/downloads results. Backend list/status/output/cancel now require end-user ownership. See [details](OFFICE_EDITOR.md#video-generation-through-agents).

Source contract updated 2026-11-13. Direct-dispatch HTTP API for images, video, music, and text-to-speech generation.
Parallel to the chat-edge tool path — same backend infrastructure
(`generation_jobs`, the worker loop, provider adapters); only the entry point
differs.

> **Alpha status.** All provider adapters are dormant until a provider key is
> configured in the admin **Providers** UI (Replicate, OpenAI, ElevenLabs).
> Video readiness additionally requires `VIDEO_GEN_ENABLED`, owner model verification, a DB-backed key and an installed adapter. Images use their existing independent route and limits.

## Authentication

Every endpoint requires an API key:

```
Authorization: Bearer gw_live_...
```

Browser forms use the cookie-authenticated Chat Edge proxy, which adds trusted identity headers on the gateway request. External callers use the gateway API-key contract. Jobs are
tenant-scoped: a job created under one tenant is invisible (404) to another
tenant's key.

## Image generation and reference edits

`POST /v1/images/generations` keeps its existing prompt/size/quality contract and adds
optional `source_images`, an array of inline PNG/JPEG/WebP base64 data URLs. With
references it calls the provider edit endpoint; without references it generates
from text. This is a SRAN extension, not a public `/v1/images/edits` route.

- Up to four still images, 4 MiB each, 12 MiB total, 16 megapixels each.
- No HTTP/file URLs, animated images or masks in this contract.
- Raw image egress requires an existing tenant with `media_pii_egress=allow`.
- Chat tools use `source_file_ids` instead of embedding base64 in model arguments;
  both clients validate conversation ownership before resolving bytes.
- Existing auth, rate, DLP prompt checks and credit gate are reused. Image pixels
  are not scanned/redacted by text DLP. A fallback cannot discard the references.
- Default model is `gpt-image-2.6-flare`; configured destination order remains
  authoritative. Migration 090 only replaces the exact old seeded image order.
- Response includes producing model/provider and `usage.cost_estimated=true`.
  Full token breakdowns include input-image cost; missing breakdowns use the
  historical output-only estimate, requiring reconciliation.

See [the implementation and research review](OFFICE_MEDIA_PARITY.md) for current
model retirement dates, activation, tests and unsupported controls.

## Video readiness and approval

`GET /v1/video/config` reports availability, reason and `supported_controls`.
The cookie-authenticated client equivalent is `GET /api/v1/video/config` on Chat
Edge. Current resolution is provider-default (`auto`) and no audio toggle is
implemented. Explicit unsupported controls return 422. Valid duration is 1 to
`REPLICATE_MAX_VIDEO_DURATION_SEC` (at most 60 seconds); excess duration is not silently clamped.

A video quote at or above `VIDEO_GEN_DEFAULT_THRESHOLD_USD` creates a
`pending_confirm` row. The worker only claims `queued`, so no provider dispatch
occurs while waiting. `POST /v1/jobs/{job_id}/confirm` (Chat Edge:
`POST /api/v1/jobs/{id}/confirm`) checks owner, expiry, current model gates, balance
and price before an atomic transition to queued. Repeated confirmation is
idempotent and cannot revive a cancelled job. Cancel works on pending jobs
without refunding a reservation that has not been made. The displayed post-cost
balance is a projection, not proof of a debit/reservation at submit time.

The shared video form uses this endpoint. The chat tool reports pending approval
and directs the user to the video form/API; it does not approve costs itself.
Audio still uses its existing submit workflow and is not covered by this new
video-specific confirmation gate.

## Endpoints

### POST /v1/videos/generations

Submit a video generation job. Returns immediately with a `queued` or `pending_confirm` job; confirm when required, then poll
`poll_url` for completion.

**Rate limit:** 5/hour per key (`MEDIA_RATE_LIMIT_VIDEO_PER_HOUR`)

**Request:**
```json
{
  "prompt": "a cat playing piano",
  "model": "configured-owner/model",
  "duration_sec": 5,
  "resolution": "auto",
  "has_audio": false,
  "idempotency_key": "optional-dedupe-key",
  "conversation_id": "optional-chat-edge-conversation"
}
```

**Response (200):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "estimated_cost_credits": 1050000,
  "estimated_cost_usd": "$0.35",
  "balance_after_credits": 44850000,
  "balance_after_usd": "$14.95",
  "requires_confirmation": false,
  "poll_url": "/v1/jobs/550e8400-e29b-41d4-a716-446655440000"
}
```

**Errors:** `400` content-policy / DLP block or model not video-capable ·
`401` invalid auth · `403` video generation disabled · `404` model not found ·
`429` rate limit · (cost/cap failures surface as `402`-class errors via the
spend guard).

### POST /v1/audio/generations

Submit a TTS or music generation job, branched by `mode`.

**Rate limit:** 30/hour per key (`MEDIA_RATE_LIMIT_AUDIO_PER_HOUR`)

**Request (TTS):**
```json
{
  "prompt": "Hello from the gateway.",
  "model": "elevenlabs-multilingual-v2",
  "mode": "tts",
  "voice_id": "21m00Tcm4TlvDq8ikWAM"
}
```

**Request (music):**
```json
{
  "prompt": "lofi hip hop, rainy night",
  "model": "replicate-musicgen",
  "mode": "music",
  "duration_sec": 8
}
```

`mode` must be `"tts"` (validates `supports_tts`, cost = `cost_per_character ×
len(text)`) or `"music"` (validates `supports_audio_gen`, cost =
`cost_per_generation`). Response shape matches the video submit response.

**Errors:** `400` invalid/unsupported `mode`, model lacks the required
capability, or content-policy / DLP block · `401` · `403` media generation
disabled · `404` model not found · `429`.

### GET /v1/jobs/{job_id}

Poll a job's status. Shared across all modalities.

**Rate limit:** 60/min per key (`MEDIA_RATE_LIMIT_JOBS_PER_MIN`)

**Response (200):**
```json
{
  "job_id": "550e8400-...",
  "status": "done",
  "progress_pct": 100,
  "output_url": "/v1/jobs/550e8400-.../output",
  "provenance": {
    "provider": "replicate",
    "model_id": "meta/musicgen",
    "c2pa_embedded": false,
    "synthid_embedded": false,
    "visible_watermark": false,
    "generation_seed": 12345
  },
  "actual_cost_credits": 1050000,
  "actual_cost_usd": "$0.35",
  "error_code": null,
  "error_message": null,
  "created_at": "2026-06-23T00:00:00Z",
  "completed_at": "2026-06-23T00:00:42Z"
}
```

`status` ∈ `queued | processing | done | failed | cancelled`. `output_url` and
`provenance` populate only once `status == "done"`.

### POST /v1/jobs/{job_id}/cancel

Cancel a pending-confirmation / queued / in-flight job. Idempotent — a job already in a terminal
state is returned unchanged. Refunds the L1 session reservation and
best-effort cancels the provider job.

**Rate limit:** 10/min per key (`MEDIA_RATE_LIMIT_CANCEL_PER_MIN`)

### GET /v1/jobs/{job_id}/output

Stream the generated artifact bytes (`FileResponse`). `404` until the job is
`done`. The bytes are persisted server-side by the job loop under
`MEDIA_GEN_OUTPUT_DIR`, so a direct API caller can refetch without a chat-edge
conversation.

## Cost model

- The credit ledger uses high-precision integer math (3,000,000 credits =
  $1.00). API responses carry both the raw `*_credits` integer and a
  human-readable `*_usd` string.
- Per-model rates live on `model_configs`: `cost_per_second` (video),
  `cost_per_character` (TTS), `cost_per_generation` (music, flat).
- Per-tenant markup is applied at submit time; an L1 session reservation +
  L2 tenant daily cap guard runaway spend.

## Provenance

Each completed job's status response includes a `provenance` object —
provider-disclosed transparency metadata, built by the job loop (never an
adapter). Fields: `provider`, `model_id`, `c2pa_embedded`,
`synthid_embedded`, `visible_watermark`, and `generation_seed` /
`provider_disclosed_at` when the provider returns them. Defaults are
conservative (see `app/services/media_gen/provenance.py`).

## Polling vs webhooks

Polling: `GET /v1/jobs/{id}` every 5–10s until `status` is `done`/`failed`.

Webhooks (optional): when `REPLICATE_WEBHOOK_SECRET` is configured, Replicate
callbacks finalize jobs faster; the poll loop re-reads and stops once it sees a
terminal status. Polling remains the fallback baseline.

## Rate limits summary

| Endpoint | Limit | Setting |
|----------|-------|---------|
| POST /v1/videos/generations | 5/hour | `MEDIA_RATE_LIMIT_VIDEO_PER_HOUR` |
| POST /v1/audio/generations | 30/hour | `MEDIA_RATE_LIMIT_AUDIO_PER_HOUR` |
| GET /v1/jobs/{id} | 60/min | `MEDIA_RATE_LIMIT_JOBS_PER_MIN` |
| POST /v1/jobs/{id}/cancel | 10/min | `MEDIA_RATE_LIMIT_CANCEL_PER_MIN` |

Limits are per resolved key (per-end-user when a shared web-chat key carries a
trusted `X-User-ID`) and fail open on a Redis outage.
