# UAT environment setup — AI Gateway

Single runbook for bringing up a fresh UAT box for customer testing.
Everything below targets the **remote UAT server** — do not run any of
these commands against the Desktop dev DB.

The local Desktop DB stays untouched: it carries the Phase 1.4 / 2 / 3
audit data, the `admin/admin1234` working login, and the 459 discovered
provider models we just verified. UAT prep is a separate track.

---

## Prerequisites on the UAT server

- Docker + docker-compose installed
- Repository cloned to `/opt/ai-gateway` (or wherever; commands below use
  relative paths from the repo root)
- Ports 3000, 3001, 8080, 5432, 6379, 8001, 9090 reachable to whoever the
  customer's testers are

---

## 1. Configure `.env`

```bash
cp scripts/uat/.env.uat.template .env
# Replace <SERVER_IP> with the box's hostname/IP in CORS_ORIGINS,
# PORTAL_PUBLIC_URL, ADMIN_UI_ORIGIN, PORTAL_ORIGIN
$EDITOR .env
```

`APP_ENV=development` keeps the strict secret validator off so the box
boots without pre-generating 32-byte secrets. **Before customer handoff**,
rotate every `dev-...` value in `.env` and flip `APP_ENV=production`.

---

## 2. Bring up the stack

```bash
docker compose up -d --build
# Wait for backend to be healthy
until curl -fsS http://localhost:8080/health > /dev/null; do sleep 2; done
echo "backend ready"
```

Alembic migrations auto-run via the backend's lifespan hook. The
auto-seed-admin path runs at boot if `admin_users` is empty, but the
UAT seed script below also handles it, so you can skip relying on it.

---

## 3. Reset DB to a clean UAT state

```bash
# Destroys all data — review the SQL before running.
docker exec -i ai-gateway-postgres-1 \
    psql -U postgres -d ai_gateway \
    < scripts/uat/01_truncate.sql
```

---

## 4. Seed UAT data

```bash
# Copies the script into the container's /work, runs it, and prints
# credentials + raw API keys at the end. CAPTURE THIS OUTPUT.
docker cp scripts/uat/02_seed_uat.py ai-gateway-backend-1:/work/seed_uat.py
docker exec -it ai-gateway-backend-1 python /work/seed_uat.py | tee uat-seed-output.txt
```

The bottom of `uat-seed-output.txt` will contain:

- `admin / admin1234`
- 4 portal user emails + their shared password (`Demo@2025!`)
- 3 raw API keys (one per project) — these are shown ONCE, never again

Store the API keys in the customer's secret manager immediately; the
seed script does not re-print them on a second run (idempotent path
detects existing keys and skips them).

The backend's in-memory model registry was loaded before the TRUNCATE
ran, so it still holds the *previous* DB state. Restart the backend so
the lifespan hook reloads from the fresh `model_configs`:

```bash
docker compose restart backend
until curl -fsS http://localhost:8080/health > /dev/null; do sleep 2; done
```

---

## 5. Verify

```bash
docker exec -i ai-gateway-postgres-1 \
    psql -U postgres -d ai_gateway \
    < scripts/uat/03_verify.sql
```

Expected:

| table_name           | rows |
|----------------------|------|
| admin_users          | 1    |
| api_keys             | 3    |
| model_configs (active) | 48 |
| policies             | 1    |
| portal_users         | 4    |
| projects             | 3    |
| tenants              | 3    |

The 3rd tenant (`demo-tenant-000`) is intentional — portal project
creation still hardcodes that ID until
`PORTAL_TENANCY_MIGRATION_PLAN.md` lands. Removing it now would break
the portal's "create your first project" onboarding flow.

`model_configs = 48` is the SEED_CANDIDATES total. Once the discovery
sync runs (every 24h, or on-demand via the admin Sync button), this
will balloon to several hundred as openai + openrouter catalogs come
in.

---

## 6. UAT checklist (run through with the customer)

### Admin UI — http://&lt;ip&gt;:3000

- [ ] Login as `admin / admin1234` succeeds
- [ ] Providers page shows all 14 providers (openai, anthropic, google,
      groq, deepseek, mistral, cohere, perplexity, openrouter, xai,
      kimi, minimax, qwen, zai)
- [ ] Add a real API key for at least one provider via the UI
- [ ] Tenants page shows 3 tenants: Demo Tenant, Acme Corporation, TechStart
- [ ] Projects page shows 3 projects
- [ ] Portal Users page shows 4 users
- [ ] DLP Coverage page loads
- [ ] Requests page loads (empty is OK — no traffic yet)
- [ ] Dashboard stats are coherent (not all zero)

### Portal — http://&lt;ip&gt;:3001

- [ ] Login `somchai@acme.ai.gateway / Demo@2025!` succeeds
- [ ] Sees Acme projects only (Customer Support Bot, Internal Assistant)
- [ ] Login `wichai@techstart.ai.gateway / Demo@2025!` succeeds
- [ ] Sees TechStart projects only (AI Chatbot MVP) — tenant isolation OK
- [ ] Credit balance shows 1,000,000 (somchai) / 500,000 (wichai)
- [ ] Profile page can update display name

### Gateway API

```bash
# Replace <RAW_KEY> with the value printed by the seed script.
curl -s http://<ip>:8080/v1/chat/completions \
    -H "Authorization: Bearer <RAW_KEY>" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "gpt-4o-mini",
      "messages": [{"role": "user", "content": "hello"}]
    }'
```

- [ ] Returns a 200 with a real response (needs a working OPENAI_API_KEY
      added through the admin UI first)
- [ ] Thai PII test: send "เลขบัตรประชาชนผมคือ 1234567890123" and confirm the
      response shows `[ID_CARD_...]` instead of the raw 13 digits
- [ ] Rate-limit test: 100 rapid requests → at least one 429

---

## 7. Customer handoff

Send the customer:

1. The server URL (`http://<ip>:3000` admin, `http://<ip>:3001` portal)
2. Admin credentials: `admin / admin1234`
3. The 4 portal user emails + shared password `Demo@2025!`
4. The 3 raw API keys (per project) from `uat-seed-output.txt`
5. A 2-line note: "Change the admin password and rotate the API keys
   before any non-test workload."

---

## Re-running

Steps 3 → 5 can be re-run any time to reset the UAT box to a clean
state. The seed script is idempotent — re-running without truncating
first will skip existing rows but **will not show the raw API keys
again** (only their prefixes, marked `<existing — not shown>`).

To get fresh API keys, always run `01_truncate.sql` first.

---

## Files in this directory

- `.env.uat.template` — copy to repo-root `.env`, edit `<SERVER_IP>` and
  (later) the secret values
- `01_truncate.sql` — destructive cleanup of customer data
- `02_seed_uat.py` — bundled seed of tenants/projects/users/keys/policies
- `03_verify.sql` — post-seed sanity counts
- `README.md` — this file
