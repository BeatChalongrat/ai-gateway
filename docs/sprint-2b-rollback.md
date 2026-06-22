# Sprint 2B Rollback Plan

Strategies in order of preference (least invasive first).

## Strategy 1 — Fast Disable (no code, no migration revert)

Best for: token leak, spend spike, adapter buggy.

Sprint 2B.5: the adapter registers UNCONDITIONALLY and resolves its key
per-request, so it fails closed only when ALL key sources are removed.
Disable by clearing every source (see precedence below):

```bash
# 1. UI keys (highest priority): /providers → Replicate → delete/deactivate
# 2. Legacy single key:
docker compose exec -T postgres psql -U postgres -d ai_gateway \
  -c "UPDATE providers SET is_active=false WHERE name='replicate';"
# 3. Env fallback:
#    blank REPLICATE_API_TOKEN in backend/.env, then:
docker compose restart backend
```

Effects:
- With all key sources gone, every new Replicate submit raises
  `ProviderKeyMissing` (fails closed) before any provider call
- mock-video-1 path still works
- In-flight Replicate jobs:
  - If webhook arrives → still processed normally
  - Otherwise → poll continues; the next provider call fails closed once
    the key is gone, and the job is marked `failed` after retries
- All DB rows preserved
- All Redis spend counters preserved

### Key precedence reference

Replicate adapter resolves keys in this priority order (highest first):
1. `provider_keys` table — active + valid (UI Add-Key flow)
2. `providers.api_key_encrypted` — legacy single key (rarely used)
3. `REPLICATE_API_TOKEN` env — fallback for dev / CI

To fully disable, ALL THREE must be absent (see the commands above).

## Strategy 2 — Circuit Breaker (emergency spend stop)

Best for: bug causing the adapter to keep submitting despite limits.

```bash
# Force L1 cap hit for all NEW requests immediately:
docker compose exec redis redis-cli SET \
  "replicate:spend:session:$(date -u +%Y-%m-%d)" 999999

# Optionally per-tenant:
docker compose exec redis redis-cli SET \
  "spend:tenant:<TENANT_UUID>:$(date -u +%Y-%m-%d)" 999999
```

Effects:
- New submits hit L1/L2 guard → 402 "budget exceeded"
- In-flight jobs unaffected (they already reserved budget)
- Restore by `DEL` on the same keys

## Strategy 3 — Full Sprint 2B Revert

Best for: deep design flaw requiring rebuild.

```bash
git reset --hard sprint-2a5-complete
docker compose exec -e PYTHONPATH=. -T backend alembic downgrade 057
docker compose up -d --build backend
```

This removes:
- Replicate adapter + tests
- Spend guard infrastructure
- Webhook receiver route
- Migrations 058 + 059
- `tenants.max_daily_spend_usd` column
- `model_configs` Replicate rows

Sprint 2A.5 (mock provider + direct dispatch UI) remains fully intact.

## Diagnostic Queries

```sql
-- Daily spend by tenant
SELECT 
  tenant_id, 
  COUNT(*) AS jobs,
  SUM(actual_cost_credits)::numeric / 3000000 AS usd_spent
FROM generation_jobs
WHERE provider='replicate' 
  AND DATE(completed_at) = CURRENT_DATE
  AND status='done'
GROUP BY tenant_id
ORDER BY usd_spent DESC;

-- Recent failures with error breakdown
SELECT 
  error_code, 
  COUNT(*) AS count,
  array_agg(substring(error_message, 1, 80) ORDER BY created_at DESC) FILTER (WHERE error_message IS NOT NULL) AS sample_messages
FROM generation_jobs
WHERE provider='replicate' 
  AND status='failed'
  AND created_at > NOW() - INTERVAL '1 day'
GROUP BY error_code
ORDER BY count DESC;

-- Stuck jobs (queued/processing too long)
SELECT id, tenant_id, status, created_at, started_at
FROM generation_jobs
WHERE provider='replicate'
  AND status IN ('queued','processing')
  AND created_at < NOW() - INTERVAL '15 minutes'
ORDER BY created_at;
```

## Active Spend Counters (Redis)

```bash
# Session counter today (UTC)
docker compose exec redis redis-cli GET "replicate:spend:session:$(date -u +%Y-%m-%d)"

# All tenant counters today
docker compose exec redis redis-cli --scan --pattern "spend:tenant:*:$(date -u +%Y-%m-%d)"

# Reset all today's counters (recovery from corrupt state)
docker compose exec redis redis-cli --scan --pattern "*:$(date -u +%Y-%m-%d)" | \
  xargs -I {} docker compose exec redis redis-cli DEL {}
```

## Webhook Recovery

If webhook secret was leaked → rotate:

```bash
# Generate new secret
openssl rand -hex 32

# Update REPLICATE_WEBHOOK_SECRET in backend/.env
docker compose restart backend

# Any in-flight Replicate jobs configured with the old secret will fall
# back to polling — no data lost, just longer time to done.
```
