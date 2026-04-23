# Security Incident Log

Phase 2B Commit 9 mandated a git-history grep for any exposed admin API
keys, documenting findings and rotation requirements here.

## 2026-04-23 — P0-1 residual risk assessment

**Context.** Phase 2A Commit 6 (`20e3836`) removed the
`NEXT_PUBLIC_ADMIN_API_KEY` variable from the admin UI bundle and
Dockerfile. Phase 2B Commit 9 closes the remaining Bearer-accepting
path on `/admin/*` endpoints so any leaked admin API key is now
useless against the admin surface.

**Git history grep:**

```
$ git log --all --oneline -S "NEXT_PUBLIC_ADMIN_API_KEY"
10e8fe9 docs(admin-auth): Phase 2A commit 8 — ...
20e3836 feat(admin-auth): Phase 2A commit 6 — frontend cleanup, closes P0-1
f06cc7e chore: wire NEXT_PUBLIC_ADMIN_API_KEY as build arg, ...
a459f12 test(e2e): 22-case full loop, ...
81f8398 feat: initial commit — AI Gateway platform

$ git log --all --oneline -S "gw_666dff9bed"  # sample key value
(no results)
```

**Finding.** Only the VARIABLE NAME appears in git history (introduced
in `81f8398`, wired to CI in `f06cc7e`, removed in `20e3836`). The
actual KEY VALUES were always stored in untracked `.env.local` files
— never committed. The `admin-ui/.env.local` on a developer workstation
may still contain a real key from pre-Commit-6 dev, but that key is
now accepted by NO code path:

* Frontend: `api.ts` no longer reads `NEXT_PUBLIC_ADMIN_API_KEY`
  (Commit 6, `20e3836`).
* Backend: `/admin/*` endpoints reject `Authorization: Bearer ...`
  outright with 401 (Commit 9, this landing).
* Public `/v1/*` endpoints still accept gw_ keys — unchanged — but
  those use the normal per-tenant API-key auth flow (not admin auth).

**Rotation action.** None required. No key was committed. The admin
surface is no longer reachable via Bearer tokens, so any cached
copies of pre-Phase-2A admin keys cannot be used for privilege
escalation.

**Follow-up.** Developers with stale `admin-ui/.env.local` files
should refresh them from `.env.example` to remove the obsolete
variable; this is cosmetic, not security-critical.

**Verification.**

* `tests/test_admin_bearer_rejection.py` (7 tests) pins the
  cookie-only behaviour.
* Live replay: curl with `Authorization: Bearer gw_<anything>`
  against `/admin/tenants` returns 401 with
  `"Bearer tokens are not accepted on admin endpoints"`.
