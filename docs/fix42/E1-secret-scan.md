# E1 — secret scan of every commit in `gitlab/main..main` before push

Captured 2026-09-02 (UTC+7). Method: `git log -p gitlab/main..main` (11,985,335 bytes,
114 commits + this round's) scanned for key/token shapes and, separately, for the
**actual values** of every `*SECRET|*PASSWORD|*KEY|*TOKEN` line in the local `.env`
(values ≥ 12 chars, exact-string match).

## Pattern hits, grouped by file — all synthetic

| file | hits | what it is |
|---|---|---|
| `backend/evals/dlp/data/must_catch/api_key_secret.json` | 30 | DLP eval corpus — fake keys the detector MUST catch (`"type": "api_key_secret"` fixtures) |
| `backend/evals/mask_quality/data/messages_b.json` / `messages_c.json` | 3 + 3 | FIX37 mask-quality corpus — fabricated `sk-live-…`, `AKIAIOSFODNN7EXAMPLE` (AWS's own documented example key), a fake `Bearer eyJ…` |
| `services/chat-edge-api/internal/handler/fix41_p95_test.go`, `fix41_probe_test.go` | 1 + 1 | test constants `"fix41-…"` used as the JWT secret of an httptest server |
| `scripts/fix33-probe/probe.js` | 1 | the compose **default dev** value `dev-stable-portal-key-change-in-prod` (already in `docker-compose.yml` on the remote) |
| `apps/desktop/test/gateway-auth-split.test.ts` | 1 | test stub returning `"sk-live…"` |
| `backend/tests/...` (`TEST_KEY = "sk-or-v1-" + "a" * 64`) | 2 | test constants |
| `apps/web-chat/src/app/chat/tasks/page.tsx` | 1 | false positive — a Tailwind `className="…text-text-…"` matched the `token=` shape |

## Real values

- `GATEWAY_API_KEY` from `.env` (51 chars, `gw_202…9e10`): **0** occurrences in the push set.
- Every other `.env` secret value (≥ 12 chars): **0** occurrences.
- `OPENROUTER_API_KEY` in `.env` is the placeholder `sk-or-...` (the working key lives encrypted in `provider_keys`); the 4 matches of that literal are docs describing the placeholder.
- Files whose names look like credentials in the push set: `.env.example`, `backend/.env.example` (template comments only — checked), `DpapiCredentialStore.cs` (source), the eval corpus above.

**Verdict: nothing to withhold. Push allowed by E1.**
