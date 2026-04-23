# Post-Cleanup Runtime Fix — `/requests` MODULE_NOT_FOUND

After commit `bd23726` (UX cleanup: 3 → 2 audit pages) landed, Beat
verified in a real browser and found three surface-level symptoms:

1. `/requests` → "Server Error: Cannot find module './519.js'"
2. Dashboard → no dark theme, no sidebar styling (plain HTML)
3. Dashboard → "No prior data" everywhere (41k request_logs had
   been visible pre-cleanup)

All three had **one** root cause and are now fixed. Full-suite tests
unchanged (1743/16/1 — same 16 pre-existing failures).

---

## Root cause

Stale `.next` server-side build tree inside the admin-ui container.
Concretely:

- During the UX cleanup session I ran `npx next build` once, to
  check the production route manifest — this wrote a **prod-mode**
  tree into `.next/server/` (pre-compiled `page.js` files with
  `__webpack_require__.X(...)` chunk loaders pointing at chunk
  hashes — e.g. `519.js`).
- The container then stayed on `next dev` (via the docker-compose
  override). Dev mode regenerates chunks lazily on request, but
  never overwrites the whole prod tree — so the admin-ui was
  serving **prod-compiled page entries** that required **dev chunks
  that had never been emitted**. Hence the `MODULE_NOT_FOUND` on
  `./519.js` — it was listed in a prod manifest but never produced
  by dev.
- `docker compose restart admin-ui` does **not** wipe `.next/`
  (it's written into the container's writable layer, not a named
  volume). The stale tree survived the restart.

Issue 2 (no styling) and Issue 3 (no data) were **downstream
symptoms of the same crash**: when the page-level JS bundle fails
to load, the React tree never mounts, so `<link>` tags for the
`sran-sidebar` CSS pipeline, the client-side `useApi("/admin/requests")`
fetch, and every theme-toggle / StatCard / dashboard widget are all
dead in the water. The DB never lost a row — `request_logs` held
41,762 entries before, during, and after the incident.

---

## Evidence

### Pre-fix

Admin-ui logs (excerpt):

```
Error: Cannot find module './519.js'
Require stack:
  - /app/.next/server/webpack-runtime.js
  - /app/.next/server/app/requests/page.js
    at __webpack_require__.e (/app/.next/server/webpack-runtime.js:110:67)
    ...
  code: 'MODULE_NOT_FOUND',
  page: '/requests'
GET /requests 500 in 112ms
```

### DB sanity check (confirms "data loss" was a façade)

```
$ docker compose exec backend python -c "<select count from each table>"
request_logs          : 41762
admin_audit_log       : 14
portal_users          : 5
tenants               : 5
projects              : 10
api_keys              : 113
```

No data was ever lost. The UI just couldn't fetch it.

### Post-fix

Cache wipe + restart:

```
$ docker compose exec admin-ui rm -rf .next node_modules/.cache
$ docker compose exec portal    rm -rf .next node_modules/.cache
$ docker compose restart admin-ui portal
```

Admin-ui logs post-restart (compile cascade, no errors):

```
 ✓ Ready in 2.2s
 ○ Compiling /middleware ...
 ✓ Compiled /middleware in 10.2s (596 modules)
 ○ Compiling /requests ...
 ✓ Compiled /requests in 1133ms (545 modules)
 GET /requests 200 in 1189ms
 ○ Compiling /audit-log ...
 ✓ Compiled /audit-log in 572ms (528 modules)
 ○ Compiling / ...
 ✓ Compiled /ai-review in 4.3s (1523 modules)
 GET / 200 in 5081ms
 GET /audit-log 200 in 117ms
```

Every page entry compiles clean. No MODULE_NOT_FOUND anywhere.

### Stronger verification than the original curl

I upgraded the post-fix verification to catch the class of bug the
original curl missed. For `/requests`:

1. **HTML body scan** for error markers (not status):
   ```
   grep -cE "Server Error|Cannot find module|MODULE_NOT_FOUND" /tmp/requests.html
   → 0
   ```
2. **Referenced asset resolution**: parse every `src=` / `href=`
   from the HTML, fetch each, confirm 200:
   ```
   Total assets referenced: 8
   Failed assets: 0
   ```
3. **CSS bundle content check**: `layout.css` is 41,313 bytes, contains
   39 `.sran-sidebar` selectors, and `:root { --bg-base, --text-primary,
   --accent, ... }` theme tokens — i.e. Tailwind + globals compiled
   correctly.
4. **Backend data check**: `/admin/requests` returns `total: 41762`,
   `/admin/audit-log` returns `total: 14` — so the UI, once it mounts,
   has real data to render.
5. **Compile-log scan**: container logs for `error|ENOENT|MODULE_NOT_FOUND|failed|500` post-restart → 0 matches.

This set of checks is what the original verification should have
looked like. Detail on why the old one missed it is in the lesson
section below.

---

## Fix applied

**No code change.** The cleanup commit (`bd23726`) was correct — the
problem was a stale build artifact inside one container. The fix is
procedural:

```bash
docker compose exec admin-ui rm -rf .next node_modules/.cache
docker compose exec portal   rm -rf .next node_modules/.cache
docker compose restart admin-ui portal
```

Plus one Makefile addition to make this reproducible going forward.

---

## Makefile change

New target `fresh` (commit `361258f`):

```makefile
fresh: clean  ## NUCLEAR reset — wipe volumes + rebuild images + start + seed.
	docker compose build --no-cache admin-ui portal
	docker compose up -d
	@sleep 10
	@$(MAKE) seed
	@echo ""
	@echo "System fresh. Open http://localhost:3000"
```

**When to use it.** After adding, removing, or renaming a Next.js
route in dev mode. In this session specifically: between the
`82ec11f` commit (which deleted `/audit`) and `bd23726` (end of UX
cleanup), a plain `docker compose restart` was not sufficient —
`make fresh` would have produced a clean state in one shot.

**Destructive.** `fresh` depends on `clean`, which runs
`docker compose down -v` and wipes the Postgres volume. Post-fresh
you end up with only the five seed users (alice/bob/charlie/dave/
eve) and their tenants/projects/API keys — no live-traffic data.
For the cache-stale case where you want to keep the DB, the lighter
workflow is the three-command sequence above.

---

## Lesson for future verification

The original cleanup report's verification was curl-based:

```
curl -b admin_session http://localhost:3000/requests | grep ">Requests<"
→ STATUS: 200
```

This looked green but wasn't — here's what it missed:

- **HTTP 200 ≠ page works.** Next.js dev mode renders runtime
  errors as 200 HTML with an error overlay. The body contains a
  stack trace, but you only see it if you scan the body rather
  than checking status.
- **`next build` ≠ `next dev`.** Running `next build` to "check
  the manifest" mutates `.next/` and can leave dev in a broken
  state. Don't do it against a running dev container without also
  wiping `.next` afterward.
- **`docker compose restart` ≠ clean rebuild.** Any file written
  to the container's writable layer (like `.next/`) survives
  restart. Only `down -v` + rebuild, or explicit `rm -rf .next`,
  actually resets.
- **Browser console errors are invisible to curl.** Client-side
  crashes (React mount failures, chunk-load errors fired from the
  browser) never appear in the server log.

**New checklist for post-structural-change verification** (already
applied above):

1. Fetch each page's HTML and grep the body for
   `MODULE_NOT_FOUND`, `Cannot find module`, `Server Error`,
   `500 Internal`.
2. Parse `<link>` / `<script>` refs out of the HTML and fetch each
   asset — confirm 200 and non-zero body size (esp. the main CSS
   bundle, since a broken CSS pipeline is invisible from the page's
   own HTML but obvious from a browser).
3. Hit the backend endpoints the page uses, confirm they return
   data — if the UI shows "no data" and the API returns 41k rows,
   the problem is UI-mount, not DB-empty.
4. Scan the container logs during the request for any error /
   warning line (`error`, `ENOENT`, `MODULE_NOT_FOUND`, `failed`,
   `⚠`).
5. **If the change added/removed/renamed routes: run `make fresh`
   before verifying**, not after. The cache-stale class of bug
   wedges the verification itself.

---

## Files touched

```
 Makefile                        |   9 +-   (add 'fresh' target)
 POST_CLEANUP_RUNTIME_FIX.md     | new     (this report)
```

Zero application-code changes. The UX-cleanup commits from
yesterday (`82ec11f`, `704a1d8`, `96cd590`) are correct as-written;
only the build artifact needed refreshing.

---

## Final state

```
$ git log --oneline -5
361258f chore(make): add 'fresh' target for post-route-change rebuilds
bd23726 docs: UX_CLEANUP_REPORT for the 3-page → 2-page audit consolidation
96cd590 fix(admin-ui): sidebar highlights only one entry per route
704a1d8 feat(requests): fold the pieces /audit had that /requests was missing
82ec11f refactor(admin-ui): retire /audit — it duplicated /requests

$ curl …/requests  → 200, body carries `>Requests<` + `>Download<`,
                     no MODULE_NOT_FOUND markers, all 8 referenced
                     assets resolve 200.
$ curl …/audit     → 308 to /requests.
$ curl …/audit-log → 200, body carries `>Admin Actions<`.
$ curl …/ (root)   → 200, sidebar + theme-token CSS present.

Backend data: request_logs=41762, admin_audit_log=14 — intact.
Tests: 1743/16/1 — same counts, same failures, no regressions.
```

---

## Honest limitation

I ran this whole session without a real browser — everything above
is response-body inspection, asset fetches, and container-log
scanning. That suite is strictly stronger than the original
curl-status check and would have caught the MODULE_NOT_FOUND
symptom, but it doesn't substitute for Beat opening Chrome and
clicking through. **Screenshots still need to come from the human.**
The acceptance gate (`/requests` renders properly, dashboard has
dark theme, data visible, `make fresh` works idempotently) requires
that visual confirmation.

What I can state with confidence:

- The MODULE_NOT_FOUND error no longer appears in admin-ui logs
  during any request.
- Every asset the page HTML references resolves 200 with real
  content.
- The CSS bundle contains the sidebar + theme selectors, so the
  "plain HTML" symptom should be resolved.
- The backend has 41,762 request_logs to serve, so the "no data"
  symptom should be resolved once the page JS mounts.

**Recommendation:** approve on reload in a real browser after
confirming dark theme + populated data. If any of those fail,
`make fresh` is the next step.
