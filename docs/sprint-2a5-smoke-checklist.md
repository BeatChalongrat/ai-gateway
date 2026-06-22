# Sprint 2A.5 Smoke Checklist (Manual)

Run when you have time. ~10 minutes. **No real money** — still the mock provider.

> ⚠️ **Read first — the direct-dispatch UI is currently DORMANT.**
> Dedicated media models (e.g. `mock-video-1`) are **not yet listed in
> `/v1/models`**, so the ModelSelector shows no video/audio model and
> `model_kind` is `"chat"` for everything. The **DIRECT PATH** section
> below will therefore NOT light up until the "Model-listing activation"
> follow-up lands (see `Sprint 2A.5 report` / the open decision). The
> **TOOL PATH** (Sprint 2A) is unaffected and should work. Run the tool
> path + the "build sanity" checks now; run the direct path after activation.

## Prereq
- [ ] `VIDEO_GEN_ENABLED=true` in the backend env, backend restarted
- [ ] `MOCK_VIDEO_DONE_SECONDS=4` (+ `GENERATION_JOB_ADAPTER_POLL_SECONDS=2`) for speed
- [ ] All services on current images: `docker compose up -d backend chat-edge-api web-chat`

## Build sanity (no UI needed)
- [ ] `curl -s localhost:8081/openapi.json | python -c "import sys,json;print([p for p in json.load(sys.stdin)['paths'] if 'videos' in p or 'audio' in p or '/v1/jobs' in p])"`
      → shows `/v1/videos/generations`, `/v1/audio/generations`, `/v1/jobs/{job_id}`
- [ ] Direct submit works by model id (bypasses the selector):
      `POST /v1/videos/generations` with a valid session/key, body
      `{"prompt":"a cat","model":"mock-video-1","duration_sec":5}`
      → `200` with `job_id`, `estimated_cost_usd:"$0.35"`, `requires_confirmation:false`, `poll_url`
- [ ] `GET {poll_url}` every 2s → `status` goes `queued→processing→done`,
      `output_url` set, `actual_cost_usd:"$0.35"`
- [ ] DB: `SELECT status, actual_cost_credits, conversation_id FROM generation_jobs ORDER BY created_at DESC LIMIT 1;`
      → `done`, `1050000`, `conversation_id` may be NULL (direct dispatch)

## TOOL PATH (Sprint 2A regression — should still work)
- [ ] http://localhost:3002 → select **GPT-4o** (chat model)
- [ ] See 🎨 🎬 buttons next to the chat input
- [ ] Click 🎬 → type "a sunset" → send
- [ ] Placeholder → video plays after ~5s

## DIRECT PATH (Sprint 2A.5 — **dormant until model-listing activation**)
- [ ] ModelSelector shows a "Video" group with `mock-video-1` (🎬 badge)  ← *blocked: model not listed yet*
- [ ] Select `mock-video-1` → chat input replaced by **VideoGenForm**
- [ ] Banner: "🎬 Video Generation Mode — Mock Video (Dev)"
- [ ] Enter prompt, duration=5 → **CostPreview** shows "$0.35" + balance delta
- [ ] Click Generate → placeholder → video plays after ~5s
- [ ] DB row: `status=done`, `conversation_id` NULL (direct dispatch)

## MODE GATE (after activation)
- [ ] DeepSeek R1 (no tools) → plain text input, no buttons
- [ ] GPT-4o (tools) → chat + 🎨 🎬 buttons (tool path)
- [ ] `mock-video-1` (video model) → VideoGenForm, no chat composer

## COST PREVIEW
- [ ] Duration 10s → estimate updates to ~$0.70
- [ ] A model priced ≥ $0.10/s at 10s → estimate ≥ $1.00 → "⚠ Premium action" confirmation appears

## Notes
- **Web-chat → backend wiring:** the direct-dispatch endpoints live on the
  BACKEND (`/v1/...`); web-chat's client talks to chat-edge. The submit
  call sites carry a `proxy-pending` comment — confirm the request actually
  reaches the backend (chat-edge proxy or a direct backend client) as part
  of activation.
- **Audio/TTS:** forms are built but there's no real audio provider yet
  (Sprint 4). The mock path is video-only.
