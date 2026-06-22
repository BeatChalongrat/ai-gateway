# Replicate Alpha Tenant Rollout

## Phase 0 — Build Sign-off

- [ ] All Sprint 2B commits present (tag `sprint-2b-build-complete` exists)
- [ ] Smoke checklist run successfully (tag `sprint-2b-smoke-passed` exists)
- [ ] No outstanding P0/P1 bugs

## Phase 1 — Infrastructure

- [ ] Production Replicate account funded with at least $20
- [ ] Production `REPLICATE_API_TOKEN` provisioned (SEPARATE from dev token)
- [ ] Production token added to production secrets (NOT to repo, NOT to dev .env)
- [ ] `REPLICATE_TEST_BUDGET_USD=20.00` (or higher per business decision)
- [ ] `REPLICATE_WEBHOOK_SECRET` generated and configured (`openssl rand -hex 32`)
- [ ] Public base URL configured so Replicate can reach `/webhooks/replicate`
- [ ] HTTPS terminating in front of the webhook endpoint (Replicate requires it)

## Phase 2 — Tenant Configuration

```sql
-- Set per-tenant daily cap for each alpha tenant
UPDATE tenants 
SET max_daily_spend_usd = 5.00 
WHERE id IN (
  '<alpha-tenant-uuid-1>',
  '<alpha-tenant-uuid-2>'
);

-- Verify
SELECT id, name, max_daily_spend_usd, credit_balance_credits
FROM tenants 
WHERE id IN ('<alpha-tenant-uuid-1>', '<alpha-tenant-uuid-2>');
```

- [ ] Enable per-tenant DLP feature scope mode if used:
      `DLP_FEATURE_SCOPE_MODE=allowlist` + tenant IDs in `DLP_FEATURE_SCOPE_TENANTS`
- [ ] `VIDEO_GEN_ENABLED=true`

## Phase 3 — Monitoring

- [ ] Grafana dashboard "Media Gen — Video" loaded from
      `infra/grafana/provisioning/dashboards/media_gen.json`
- [ ] Alerts wired (verify firing on test trigger):
  - [ ] `replicate_session_spend_usd > 0.9 × REPLICATE_TEST_BUDGET_USD` → Slack #ops warning
  - [ ] `rate(replicate_api_errors_total{error_class="auth"}[5m]) > 0` → Slack #ops critical
  - [ ] `rate(replicate_api_errors_total{error_class="rate_limit"}[15m]) > 0` → Slack #ops warning
  - [ ] `rate(replicate_budget_blocks_total[15m]) > 5` → Slack #ops warning
- [ ] Daily 5pm review query saved (from `docs/sprint-2b-rollback.md` diagnostics)
- [ ] On-call rotation knows the rollback strategies

## Phase 4 — Kill Switch Verification

Before opening to alpha users, verify all kill switches work:

- [ ] Test fast disable: blank `REPLICATE_API_TOKEN` → `docker compose restart backend`
      → log shows "NOT registered" → restore token
- [ ] Test L1 circuit breaker: `redis-cli SET ... 999999` → next submit returns 402
      → `redis-cli DEL ...` → submit succeeds again
- [ ] Team has tested both methods at least once on staging

## Phase 5 — Go/No-Go

Limit alpha to 1 tenant for first 24h. Criteria to advance:

- [ ] First alpha tenant generates 1 video successfully
- [ ] Actual cost within 10% of estimate
- [ ] No L1 or L2 spurious blocks
- [ ] No DLP false positives in 24h
- [ ] No webhook delivery failures in 24h
- [ ] No `replicate_api_errors_total{error_class!="content_policy"}` over 24h

If all green → add 2nd tenant. Repeat.

## Phase 6 — Post-Alpha Cleanup

- [ ] Document any actual spend vs budget gap
- [ ] Adjust per-tenant `max_daily_spend_usd` based on observed usage
- [ ] Tag `sprint-2b-alpha-stable` once 7 days clean
- [ ] Plan Sprint 4 (music + content policy detectors) based on alpha feedback
