# Sprint 2B Smoke Checklist (Manual)

> ⚠️ **This checklist spends real money on Replicate.**
> Expected total: ~$1.40–$2.10.
> Don't run if budget is tight or token isn't set.

---

## Pre-flight (no cost)

Add the Replicate key through the admin UI (Sprint 2B.5) — no env var, no restart:

- [ ] Open http://localhost:3000/providers
- [ ] Find the "Replicate" card → click "Add Key"
- [ ] Paste your `r8_...` token from https://replicate.com/account/api-tokens
- [ ] Click Save → wait for the green "valid" badge
      (the backend pings Replicate `GET /v1/account` ~500ms before saving)
- [ ] Card now shows "1/1 keys"
- [ ] Verify backend resolves it (adapter registers unconditionally at boot):
      `docker compose logs backend --tail=50 | grep -i "Replicate adapter registered"`
      → shows `Replicate adapter registered (key resolved per-request)`
- [ ] No restart needed — the adapter resolves the key per-request from the DB
- [ ] `REPLICATE_TEST_BUDGET_USD=5.00` set in `backend/.env` (or default applies)
- [ ] Migrations at head: `docker compose exec -e PYTHONPATH=. -T backend alembic current` → `059 (head)`
- [ ] Session counter reset (in case of stale value):
      `docker compose exec redis redis-cli DEL "replicate:spend:session:$(date -u +%Y-%m-%d)"`
- [ ] Get `DEV_AUTH_TOKEN`: extract a bearer for a dev tenant with $5+ balance
      (check `backend/scripts/` or existing dev fixtures for the token-mint helper)

### Optional: env fallback (legacy, for CI / dev convenience)

- [ ] Set `REPLICATE_API_TOKEN=r8_xxx` in `backend/.env`, then `docker compose restart backend`
- [ ] The Replicate card shows a "via env" badge
- [ ] A UI-added key (priority 1) overrides the env key (priority 3) when both are set

---

## Unit Validation (no cost)

- [ ] `docker compose exec -T backend pytest tests/unit/test_replicate_adapter.py -q --timeout=30`
      → all green (uses respx mock, no real calls)
- [ ] `docker compose exec -T backend pytest tests/integration/test_spend_guard.py -q --timeout=30`
      → all green (uses real Redis DB 15)
- [ ] `docker compose exec -T backend pytest tests/integration/test_daily_spend_cap.py -q --timeout=30`
      → all green
- [ ] Mock video gen still works (Sprint 2A regression):
      Browser → GPT-4o → 🎬 → "a cat" → mock video plays ~5 sec

---

## Live Smoke #1 — Script Run (~$0.35)

- [ ] Export tokens:
      `export REPLICATE_API_TOKEN=r8_xxx`
      `export DEV_AUTH_TOKEN=<dev_bearer>`
- [ ] Run smoke:
      `python scripts/replicate_live_smoke.py --confirm-spend`
- [ ] Type `yes` when prompted
- [ ] Wait 30–90 seconds
- [ ] Verify exit code 0
- [ ] `/tmp/smoke_output.mp4` exists, plays in any video player
- [ ] DB check:
```sql
      SELECT status, actual_cost_credits, output_metadata
      FROM generation_jobs ORDER BY created_at DESC LIMIT 1;
```
      Expect: `status=done`, `actual_cost_credits ≈ 1,050,000` (= $0.35)
- [ ] Session counter:
      `docker compose exec redis redis-cli GET "replicate:spend:session:$(date -u +%Y-%m-%d)"`
      Expect: `≈ 0.35`

---

## Live Smoke #2 — UI E2E (~$0.35)

- [ ] Open http://localhost:3002
- [ ] ModelSelector → select "Kling 1.5 Pro" (badge 🎬)
- [ ] VideoGenForm renders (no chat input)
- [ ] Type "a sunset over mountains", duration = 5s
- [ ] CostPreview shows "$0.35"
- [ ] No confirmation modal ($0.35 < $1.00 threshold)
- [ ] Click Generate → placeholder shows
- [ ] After 30–90 sec, video plays in VideoPlayer
- [ ] Session counter now ~$0.70

---

## Threshold Confirmation Test (~$0.70)

- [ ] In VideoGenForm, set duration to 10s
- [ ] CostPreview updates to "$0.70" (still no modal)
- [ ] Generate again
- [ ] After done, session counter ~$1.40

---

## Budget Cap Test (should NOT spend)

- [ ] Temporarily lower cap in `backend/.env`:
      `REPLICATE_TEST_BUDGET_USD=1.50`
- [ ] `docker compose restart backend`
- [ ] Current session counter (~$1.40) is near the new cap
- [ ] Submit another video (5s, $0.35 estimate)
- [ ] Expect HTTP 402 or clear "budget exceeded" error in UI
- [ ] Session counter unchanged
- [ ] `replicate_budget_blocks_total{guard_level="L1_session"}` incremented:
      `curl -s localhost:8081/metrics | grep replicate_budget_blocks_total`
- [ ] **Restore** `REPLICATE_TEST_BUDGET_USD=5.00`, restart backend

---

## Post-smoke

- [ ] **Total actual spend:** __________ (record from `actual_cost_usd` sum)
- [ ] All checks above ✓ → `git tag sprint-2b-smoke-passed`
- [ ] Document any deviations in `docs/sprint-2b-report.md`
- [ ] Before alpha rollout: set production tenants' `max_daily_spend_usd` via SQL

---

## Rollback if anything looks wrong

See `docs/sprint-2b-rollback.md`. Fast disable (Sprint 2B.5 — the adapter now
registers unconditionally and resolves the key per-request, so blanking the env
var alone is NOT enough; remove the UI key too):

- UI: open /providers → Replicate card → delete / deactivate all keys
- Legacy: `UPDATE providers SET is_active=false WHERE name='replicate';`
- Env: blank `REPLICATE_API_TOKEN` + `docker compose restart backend`

All three must be absent for the adapter to fail closed (`ProviderKeyMissing`).
