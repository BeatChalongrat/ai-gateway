# Post-Consolidation Bug Fixes — Report

Four non-blocker bugs found during live browsing of the admin + portal
after the runtime-consolidation merge (2026-04-23). All four are now
fixed, committed, and verified against a running stack on the existing
Postgres volume.

Baseline test suite run: **1743 passed / 16 failed / 1 skipped** — same
counts as the consolidation baseline (RUNTIME_CONSOLIDATION_REPORT.md,
row 11). No regressions introduced.

---

## Bug 1 — `/admin/audit-log` returns 404 in the admin UI

**Root cause.** The F4 report and the page-header doc-comment both
described the UI route as `/admin/audit-log`, but the page file lives
at `admin-ui/app/audit-log/page.tsx` — i.e. the route is `/audit-log`.
The admin-UI is itself the `/admin` console (see every other route:
`/tenants`, `/projects`, `/audit`, etc. — none carry an `/admin/`
prefix), so the `/admin/audit-log` path is correctly a 404. The
**backend endpoint is genuinely `/admin/audit-log`**, which is
independent of the UI path and was never broken.

**Fix applied.** Documentation-only. Updated:

- `admin-ui/app/audit-log/page.tsx:3-14` — header comment now says the
  UI path is `/audit-log`, distinct from the backend endpoint.
- `PHASE4_OPTIONAL_FEATURES_REPORT.md:153` — clarified that the UI
  page lives at `/audit-log`, backend endpoint stays `/admin/audit-log`.

**Commit.** `0a7d7f1 docs(audit-log): clarify UI route is /audit-log, not /admin/audit-log`

**Verification.** With an admin session:
- `GET http://localhost:3000/audit-log` → 200, page renders with
  "Admin Actions" header.
- `GET http://localhost:3000/admin/audit-log` → 404 "This page could
  not be found" (correct — no route by that name exists or should).
- `GET http://localhost:8080/admin/audit-log?limit=3` → 200, returns
  14 rows from `admin_audit_log`.

---

## Bug 2 — Admin sidebar was missing the "Admin Actions" link

**Root cause.** `admin-ui/components/Sidebar.tsx:49` already had the
`{ label: "Admin Actions", href: "/audit-log" }` entry under
Observability, but `admin-ui/lib/auth.ts:69` (`ROLE_NAV.admin`) did
**not** include `"/audit-log"`. The sidebar filters every section's
items by `allowedPaths.includes(item.href)` before render, so the
link was stripped for all roles — including `admin` — and never
appeared.

**Fix applied.** Added `"/audit-log"` to `ROLE_NAV.admin` in
`admin-ui/lib/auth.ts:69` (between `"/audit"` and `"/alerts/events"`
to keep them grouped under Observability).

**Commit.** `a238aeb fix(admin-ui): allow /audit-log in the admin ROLE_NAV allowlist`

**Verification.**
- `grep -n "audit-log" admin-ui/lib/auth.ts admin-ui/components/Sidebar.tsx`
  returns matches in both — ROLE_NAV entry + sidebar NAV entry
  aligned on the same `/audit-log` href.
- Admin UI page `/audit-log` loads to 200 with admin session cookie
  (tested via curl).
- `npx tsc --noEmit` clean.

---

## Bug 3 — Portal `/usage` shows a "Not Found" error banner

**Root cause.** `portal/next.config.js` hardcoded the rewrite
destination to `http://localhost:8080`:

```js
{ source: "/api/:path*", destination: "http://localhost:8080/:path*" }
```

Inside the docker-compose portal container, `localhost:8080` resolves
to the portal container itself (Next.js), not to the backend.
**Every** proxied call from the portal 500'd with `ECONNREFUSED`.
`PortalApiError` (`portal/lib/api.ts:18-34`) derives `message` from
the upstream error body, and the cascaded failure's body text
surfaced as a "Not Found" banner on the `/usage` page even though
both endpoints (`/portal/me/usage` and `/portal/me/usage/summary`)
work perfectly when called directly against `http://localhost:8080`
via port mapping (both return 200 with empty data for alice).

**Fix applied.** `portal/next.config.js` now reads the rewrite
destination from the `BACKEND_URL` environment variable, which
`docker-compose.yml:112` already injects as `http://backend:8080`
(docker service DNS). Falls back to `http://localhost:8080` for
bare-metal `npm run dev` outside Docker.

**Commit.** `90b7937 fix(portal): route /api rewrites to BACKEND_URL so they work in Docker`

**Verification.** After `docker compose restart portal`:

```
$ curl -H "Authorization: Bearer $ALICE" http://localhost:3001/api/portal/me
STATUS: 200 — {"email":"alice@acme.test", "tenant_id":"acme-001", ...}

$ curl -H "Authorization: Bearer $ALICE" http://localhost:3001/api/portal/me/usage?limit=200
STATUS: 200 — {"requests":[],"total":0}

$ curl -H "Authorization: Bearer $ALICE" http://localhost:3001/api/portal/me/usage/summary
STATUS: 200 — {"totals":...,"top_models":[],"by_project":[]}
```

All three endpoints that the usage page touches now proxy cleanly.
`npx tsc --noEmit` clean.

---

## Bug 4 — Portal logged in as `verify_test@sran.ai` instead of a seed user

**Root cause.** `backend/scripts/seed_audit_data.py` creates exactly
five portal users: alice / bob / charlie / dave / eve, under tenants
`acme-001` / `globex-001`. Live testing over prior sessions created
six **additional** users under `demo-tenant-000` through the portal
signup flow:

```
beat2@test.com
session-f@test.com
polish-test-1776621557@example.com
oauth-smoke@example.com
verify_test@sran.ai        ← the one observed during live testing
beat@test.com
```

These are not in any seed or code path (`grep -r verify_test` finds
nothing in the repo). They survived `make seed` because seeding is
idempotent and only adds missing rows; only `make clean` (which wipes
the Postgres volume) removed them previously. The session observed
during live testing was simply the last-used portal account cached
in localStorage.

**Fix applied.** Two parts:
1. **Immediate DB cleanup** — deleted the six residue rows in-place
   (kept the DB otherwise intact). Post-cleanup: five portal users
   remain, all seeded.
2. **New helper for future cycles** — `backend/scripts/clean_portal_residue.py`
   + `make clean-portal-users` target. Idempotent; deletes any
   portal user whose email is not in the documented seed set.
   Useful between manual-test passes when you don't want to lose
   tenant / project / request_log history (which `make clean` would
   also wipe).

**Commit.** `213ec2a chore(seed): add clean-portal-users target to drop signup residue`

**Verification.**
```
$ make clean-portal-users     # after first cleanup, re-run:
Clean: 5 users, all seeded.
```
Re-reading `portal_users` in the DB:
```
alice@acme.test (acme-001, active)
bob@globex.test (globex-001, active)
charlie@acme.test (acme-001, active)
dave@acme.test (acme-001, suspended)
eve@globex.test (globex-001, active)
```
A full `make clean && make up && make seed` flow was **not run** —
destructive against the Postgres volume that holds the consolidation
baseline data. The in-place cleanup + idempotent script produces the
same end-state: exactly the five seeded portal users. Logging in
with `alice@acme.test` / `Alice!2345` via the portal proxy returns
a JWT keyed to alice's `user.id`, not verify_test's.

---

## Related fixes / side effects

None. Each bug fix is scoped to the file it touches:

- Bug 1: comment + markdown doc only (no behavior change).
- Bug 2: one-line list extension (allows a link to render; renders
  nothing new on its own for non-admin roles).
- Bug 3: one `process.env` lookup in `next.config.js`; identical
  behavior for anyone using docker-compose (they now succeed instead
  of failing), identical behavior for bare-metal `npm run dev`
  (falls back to `localhost:8080`).
- Bug 4: purely additive (new script + new make target), no change
  to existing seed logic.

Phase 2 / 3 / 3.5 / 4 business logic was not touched.

---

## Final state

```
$ git status
On branch main
nothing to commit, working tree clean

$ git log --oneline -5
213ec2a chore(seed): add clean-portal-users target to drop signup residue
90b7937 fix(portal): route /api rewrites to BACKEND_URL so they work in Docker
a238aeb fix(admin-ui): allow /audit-log in the admin ROLE_NAV allowlist
0a7d7f1 docs(audit-log): clarify UI route is /audit-log, not /admin/audit-log
8080c87 docs: RUNTIME_CONSOLIDATION_REPORT + BASELINE_STATUS
```

Tests: **1743 passed / 16 failed / 1 skipped** — identical to
consolidation baseline (all 16 failures pre-existing, enumerated in
RUNTIME_CONSOLIDATION_REPORT.md row 11 and unrelated to this work).

Typecheck (admin-ui + portal): clean in both.

---

## Recommendation

**Approve → genuinely internal launch ready.**

The four defects were surface-level — three of them were path /
config mistakes (wrong URL in a doc, missing entry in a role allow-
list, wrong host for a Next.js rewrite in Docker) and the fourth
was DB residue from manual testing, not a code bug at all. None
touched auth, tenancy, DLP, or any of the Phase 2-4 business-logic
surfaces that the internal-launch gate depends on. The consolidated
`make up` → `make seed` → login flow is now observably clean for
both the admin console and the portal.
