# Sprint 2A.5 Smoke Checklist (Manual)

Run when you have time. ~10 minutes. **No real money** — still the mock provider.

> ✅ **Both paths are live.** With `VIDEO_GEN_ENABLED=true` (see Prereq) the
> direct-dispatch UI activates end-to-end: dedicated media models are listed
> in `/v1/models` (`model_kind=video`), the ModelSelector shows them, and the
> chat-edge proxy forwards the gen forms to the backend public routes
> (`/api/v1/videos|audio/generations`, `/api/v1/jobs/{id}`). The TOOL PATH
> (Sprint 2A) is independent and also works. Audio/TTS forms render but have
> no real provider yet (Sprint 4).

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

## DIRECT PATH (Sprint 2A.5 — live)
- [ ] ModelSelector shows a "Video" group with `mock-video-1` (🎬 badge)
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
- **Web-chat → backend wiring:** wired. The chat-edge MediaProxy handler
  forwards `/api/v1/videos|audio/generations` + `/api/v1/jobs/{id}` to the
  backend public routes with the internal-trust headers (same pattern as the
  generate_video tool); the backend enforces auth + DLP + balance + the
  feature gate. Covered by `media_proxy_test.go`.
- **Audio/TTS:** forms are built but there's no real audio provider yet
  (Sprint 4). The mock path is video-only.
