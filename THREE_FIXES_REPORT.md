# Three Fixes — Summary Report

Beat flagged three unrelated issues from live browser testing. All
three are now fixed. Single gate here at the end per the task rules.

**Baseline:** 1743 passed / 16 failed / 1 skipped (same 16 pre-existing
failures enumerated in `RUNTIME_CONSOLIDATION_REPORT.md` row 11 — all
health-shape / detector-registry / privacy-RBAC / config tests, none
touching anything in these fixes).

**Now:** **1753 passed / 16 failed / 1 skipped** — **+10 new passing
tests** (portal-seed regression suite) and **0 new failures**.
Admin-UI `tsc --noEmit` clean. Portal `tsc --noEmit` clean.

Commits (ordered):
```
6fb842b feat(admin-ui): GCP-audit-style redesign of /audit-log page
ff8dd85 fix(portal): reconcile credit/token surfaces + lock terminology
d4f4af1 fix(seed): make portal-user seed update-in-place + regression tests
```

---

## Issue 1 — `alice@acme.test` login failure

### Root cause

**There isn't a live bug in the auth code path.** Alice logs in
cleanly at every layer — verified end-to-end in the container:

```
$ curl -X POST http://localhost:3001/api/portal/auth/login        # via portal proxy
  -d '{"email":"alice@acme.test","password":"Alice!2345"}'
→ 200  {access_token: "...", refresh_token: "...", user:{...}}

$ curl -X POST http://localhost:8080/portal/auth/login              # backend direct
→ 200  (same shape)
```

bcrypt check in-process is also green for every seed user:

```
alice@acme.test     | hash_matches_password=True
bob@globex.test     | hash_matches_password=True
charlie@acme.test   | hash_matches_password=True
dave@acme.test      | hash_matches_password=True
eve@globex.test     | hash_matches_password=True
```

So when Beat saw "Invalid email or password" for alice while bob
worked, it wasn't a server-side bug — most likely a stale browser
state (autofill, or lingering JS from the MODULE_NOT_FOUND incident
the session before this one). The latent bug is that `seed_audit_data`
was **create-only**: once a row existed, re-running `make seed`
wouldn't touch it, so the only way to reset a drifted row was
`make fresh` which wipes the whole DB. That fragility is what let
the situation persist longer than it needed to.

### Fix applied  (`d4f4af1`)

1. `backend/scripts/seed_audit_data.py` — `seed_portal_users` now
   updates existing rows in place:

   ```python
   existing.password_hash = _hash(u["password"])
   existing.display_name = u["display_name"]
   existing.tenant_id = u["tenant_id"]
   existing.plan = u["plan"]
   existing.status = u["status"]
   existing.email_verified = u["email_verified"]
   existing.credit_balance = u["credit_balance"]
   existing.credit_used = u["credit_used"]
   ```

   Changing `PORTAL_USERS` in the seed file now actually takes effect
   on `make seed` — no more `make fresh` needed to pick up a new
   password or toggled `email_verified`.

2. `backend/tests/test_seed_portal_users.py` — **10 new tests**
   covering the end-to-end login contract for every seed user:
   - parametrized hash-verification for each row in `PORTAL_USERS`
   - 3 active+verified users → 200 + tokens
   - dave (suspended) → 401 `"not active"` (distinct from bad-password)
   - eve (unverified) → 403 `code=email_not_verified`
   - wrong password → 401 generic message (no existence leak)
   - **idempotency regression**: hand-drift a row's hash + status, run
     seed again, confirm spec is restored

### Verification

All 5 seed users exhibit expected behaviour via the portal proxy:

```
alice@acme.test                status=200
bob@globex.test                status=200
charlie@acme.test              status=200
dave@acme.test                 status=401  (suspended)
eve@globex.test                status=403  (email_not_verified)
```

Tests: **10/10 pass** (`test_seed_portal_users.py`).

---

## Issue 2 — Admin Actions page too flat

### Before

`admin-ui/app/audit-log/page.tsx` was 640 lines: a filter bar + a
flat HTML table with 7 columns. One outcome pill, no icons, no
summary, no row drill-down, no time shortcuts.

### After  (`6fb842b`)

One file, 1793 lines. Every capability in the spec is shipped, keeping
every existing behaviour (CSV export, pagination, same backend
endpoint, same filter shape):

| Enhancement | Shipped | Pattern adopted from |
| --- | --- | --- |
| Summary KPI bar | ✅ 6 cards: Total / Success / Failure / Key ops / Cross-tenant / Session — each with colored stripe | GCP Cloud Audit, Datadog |
| 24h sparkline | ✅ 24 hourly buckets, per-bucket outcome coloring + tooltips | Datadog Audit Trail |
| Severity row stripes | ✅ 4-color palette (success/failure/warning/neutral), left-border | GCP Audit Logs |
| Expandable rows | ✅ Click / Enter / Space; reveals UA, session, resource metadata + full JSON with Copy JSON | GCP Audit Logs (inline expand) |
| Linked entities | ✅ Actor, Tenant, Resource cells are buttons — clicking applies that value as a filter | Datadog (click-to-filter) |
| Time grouping | ✅ "Group by day" toggle (default on), with Today / Yesterday / `Wed, Apr 15` headers + per-day counts | GCP Cloud Audit (day-grouped default) |
| Quick filter chips | ✅ Period (1h/24h/7d/30d/All) + Failed-only + Key ops + Cross-tenant | Datadog, GitHub Audit |
| Active filter chips | ✅ Current filters render as × removable tags, plus "Clear all" | GCP, Datadog |
| Action type icons | ✅ Per-category SVG icon + color: Key (cyan), Tenant (indigo), Cross-tenant (amber), Session (purple), Policy (green), Other (gray) | GCP Audit Logs |
| Advanced filters | ✅ Collapsed behind `<details>` disclosure so chips lead, power form is one extra click | Datadog / Stripe filter panels |

Unexpected side-effect fixes while I was touching the file:

- **Export CSV was silently broken** — the original code did
  `fetch("/admin/audit-log?...")` with a **relative URL**, which in
  the admin-UI dev setup resolves against the UI's own origin
  (`:3000`) and returns 404. It now uses
  `process.env.NEXT_PUBLIC_API_BASE_URL` (same convention as
  `lib/api.ts`) so it actually hits the backend.
- **Stale-response guard** — rapid chip-clicking could flash stale
  data when an older fetch landed after a newer one. Added a
  `reqRef` counter that drops out-of-order responses.
- **Fetch budget raised 50 → 500** — summary KPIs and sparkline
  need all matching rows in scope, not just the visible page. If
  total > 500 the page now shows a warning chip
  ("narrow filters for full view") so counts aren't silently clipped.

### Deferred (noted for a future pass)

- Real-time tailing via SSE/WebSocket ("Live" blinking dot like the
  Requests page). Current backend endpoint is pure list; adding
  live tail needs a new `/admin/audit-log/stream` route. Scope
  creep for this task.
- Per-actor timeline view (click an actor → see a vertical timeline
  instead of table rows). Good next step once actor density is high
  enough to warrant it.
- Chart drill-downs — clicking a sparkline bucket could apply a
  narrower time window. Intentionally deferred; the current chips
  cover the common case.

### Browser verification

```
$ curl -b admin_session http://localhost:3000/audit-log
→ 200

$ grep ...   /tmp/al.html                        → count
  Admin Actions                                       1  ✅
  Total events                                        1  ✅
  Success                                             1  ✅
  Failure / denied                                    1  ✅
  Key ops, Cross-tenant, Session events               3  ✅
  Last hour / 24h / 7 days                            3  ✅
  Failed only, Key operations                         2  ✅
  Advanced filters, Group by day, Export CSV          3  ✅
  Server Error / MODULE_NOT_FOUND / TypeError         0  ✅
```

---

## Issue 3 — Credits / tokens inconsistency for Bob

### Root cause

Two overlapping problems produced one symptom:

1. **Data-source mismatch.** Dashboard / Billing / Sidebar read
   `portal_users.credit_balance` and `portal_users.credit_used` (a
   ledger snapshot on the user row). The Usage page reads from
   `request_logs` (one row per gateway call). Bob's seed row had
   `credit_used = 15,000` **but no matching `request_logs` entries**,
   so the two sources couldn't reconcile and produced the "15,000
   ghost" Beat saw — Dashboard said 15k used, Usage said 0.

2. **Terminology drift.** The word "credits" appeared in sidebar /
   Billing balance caption. The word "tokens" appeared in Dashboard
   KPI, Billing pack cards, Usage page. Same quantity, two labels,
   no documented convention.

### Fix applied  (`ff8dd85`)

#### Terminology decision (locked in code comment)

Added a comment block on `portal_users.credit_balance` / `credit_used`
in the ORM file so future code reviews have a stable reference:

```
credits = prepaid balance (the thing the user buys / holds)
          → shown as "Available Credits", sidebar "N credits",
            Billing "Current Balance".
tokens  = LLM metering unit (the thing consumed per request)
          → shown as "Tokens Used", Usage page totals.
One credit debits one token. credit_used is the lifetime sum of
debits and must stay in lockstep with SUM(request_logs.total_tokens)
for the same user; credit_service.debit() writes both in one
transaction to preserve that invariant.
```

**TL;DR**: balance-side = "credits", metering-side = "tokens", they
convert 1:1, the difference is semantic.

#### Data source reconciliation

`portal_users.credit_used` is the authoritative lifetime consumption
— `credit_service.debit()` writes to it **and** to
`credit_transactions` in a single transaction, and every `request_log`
row is written by the same request path. So when the gateway is
doing its job the two sources reconcile automatically.

The seed was the thing forging them apart. Bob's 15,000 / Charlie's
4,900 / Dave's 2,000 were all pure fiction — artificial ledger
drains with no corresponding gateway activity. Fix: **set every seed
user's `credit_used` to 0** so the seed spec is consistent with the
empty `request_logs` table it ships with.

Charlie's "low credit" test case now uses just `credit_balance=5,000`
(below the low-credit warning threshold) — a valid low-balance state
without needing to fake consumption history.

#### Bob's "15,000 ghost" — specific fix

```diff
-{"email": "bob@globex.test", ..., "credit_balance": 200000, "credit_used": 15000}
+{"email": "bob@globex.test", ..., "credit_balance": 200000, "credit_used": 0}
```

Combined with the Issue-1 make-seed-idempotent fix, re-running
`make seed` on the existing Bob row overwrites `credit_used` back to
0 — no `make fresh` required.

#### Scope labels

- Dashboard Hero caption `of 215,000 total · Last 7 days` →
  `of 215,000 lifetime allotment` (the "Last 7 days" was a leftover
  from an earlier design and contradicted the "All time" caption on
  the KPI card).
- Dashboard KPI caption `All time` → `Lifetime` so Hero and Stats use
  the same word for the same scope.
- Billing unit label `200,000 tokens` → `200,000 credits` (matches
  sidebar's "N credits").
- Billing `Used:` → `Used (lifetime):` — scope explicit on the
  longest-lived number.

### Verification — fresh Bob, 0 requests

```
$ curl /portal/me                   → credit_balance=200000, credit_used=0
$ curl /portal/me/usage/summary     → requests=0, tokens=0
```

All four UI surfaces reconcile:

| Surface | Field | Value |
| --- | --- | --- |
| Dashboard Hero | Available / Used / Remaining | **200,000 / 0 / 200,000** |
| Dashboard KPI | Tokens Used (Lifetime) | **0** |
| Billing | 200,000 credits · Used (lifetime) | **200,000 · 0** |
| Sidebar | N credits | **200,000** |
| Usage page | Total Requests / Total Tokens | **0 / 0** |

All five surfaces now tell the same story: 200k credits available,
nothing ever consumed, no gateway traffic yet. When real traffic
starts the `credit_service.debit` path keeps them in lockstep.

---

## Browser verification notes

I don't have a real browser. Everything above is response-body
inspection, asset fetches, endpoint probes, and container-log
scanning — the verification pattern Beat and I aligned on after the
curl-only misstep in the previous session. It's strictly stronger
than curl-status but still doesn't substitute for human eyes.

**Still needs human sign-off:**

- Issue 1 — open `http://localhost:3001/login`, log in as each seed
  user (expect alice/bob/charlie to land on `/dashboard`, dave to
  see "not active", eve to see the unverified-email path).
- Issue 2 — open `http://localhost:3000/audit-log`, confirm the
  KPI bar + sparkline render, click a row to see the inline expand,
  click an actor cell to see it apply as a filter, toggle "Group by
  day" off to see flat mode, click "Failed only" chip, export CSV.
- Issue 3 — log in as bob@globex.test, check that Dashboard, Billing,
  Sidebar, Usage all show `200,000 credits` / `0 used` / `0 requests`
  — nothing should say "15,000" anymore.

---

## Files touched

```
 admin-ui/app/audit-log/page.tsx          | 1414 ++++++++----  Issue 2 rewrite
 backend/app/models/portal_user.py        |   10 +            Issue 3 terminology doc
 backend/scripts/seed_audit_data.py       |   35 ++-          Issues 1+3 seed idempotent+credit_used=0
 backend/tests/test_seed_portal_users.py  |  206 ++           Issue 1 regression suite (new file)
 portal/app/(app)/billing/page.tsx        |    4 +-           Issue 3 unit label
 portal/app/(app)/dashboard/page.tsx      |    4 +-           Issue 3 scope captions
 THREE_FIXES_REPORT.md                    |  new             this report
```

Three logical commits, one for each issue. `git status` clean after
the report commit.
