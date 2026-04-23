# UX Cleanup — 3 Audit Pages → 2

Beat flagged that the admin-UI carried three audit-related pages with
real overlap: `/requests`, `/audit`, and `/audit-log`. This pass
consolidated them to two, fixed a sidebar double-highlight bug, and
removed the dead nav entry.

**Outcome.** The admin has **one** page for gateway request traffic
(`/requests`) and **one** page for admin-operation history
(`/audit-log`). The duplicate in the middle is gone.

Baseline test suite: **1743 passed / 16 failed / 1 skipped** — same
counts as before this work, same 16 pre-existing failures (see
RUNTIME_CONSOLIDATION_REPORT.md row 11). No regressions.

---

## Before / After

### Before (3 pages, overlapping)

| Sidebar label   | Route        | What it showed                          | Source |
| --------------- | ------------ | --------------------------------------- | ------ |
| Requests        | `/requests`  | Gateway request log + sparkline + filter tabs + detail pane | `GET /admin/requests` |
| Request Audit   | `/audit`     | **Same data.** Table + expand-inline + stats chips + CSV export | `GET /admin/requests` |
| Admin Actions   | `/audit-log` | Admin-operation log (key create / revoke / rotate / cross-tenant / session events) | `GET /admin/audit-log` |

Plus two cosmetic bugs:
- Sidebar label "Request Audit" but the page's own `<h1>` said "Audit Log" — a mismatch
  (the page title was literally "Audit Log" on line 226 of the retired `audit/page.tsx`).
- On `/audit-log`, the sidebar lit up **both** "Request Audit" and
  "Admin Actions" — classic `startsWith` collision because `/audit`
  is a prefix of `/audit-log`.

### After (2 pages, disjoint)

| Sidebar label   | Route        | What it shows                           | Source |
| --------------- | ------------ | --------------------------------------- | ------ |
| Requests        | `/requests`  | Gateway request log. Now also carries the stats chips, Copy-JSON, and CSV-download that /audit had. | `GET /admin/requests` |
| Admin Actions   | `/audit-log` | Admin-operation log (unchanged)         | `GET /admin/audit-log` |

Plus the two bugs are gone:
- `/audit-log` page's `<h1>` was already "Admin Actions" (line 215 of
  `audit-log/page.tsx`) — the "Audit Log" title Beat saw was on the
  `/audit` page that just got deleted, so removing the page resolves
  the mismatch automatically.
- Sidebar now highlights exactly one entry on `/audit-log`.

---

## Per-decision breakdown

### D1 — Delete `/audit`, redirect to `/requests`

**Done.** `admin-ui/app/audit/page.tsx` removed (440 lines). A
permanent 308 redirect from `/audit → /requests` added in
`admin-ui/next.config.js`:

```js
async redirects() {
  return [
    { source: "/audit", destination: "/requests", permanent: true },
  ];
},
```

### D2 — Fold /audit's unique features into /requests

Audited `/requests` first to avoid duplication. Pre-existing features
that didn't need touching:

- ✅ Live 24h sparkline with Live blinking indicator.
- ✅ Filter tabs (All / OK / Error / Masked) with counts.
- ✅ Text search over request_id / model / provider / status.
- ✅ List with select-to-detail pane on the right.
- ✅ Raw JSON view of the payload (via `JsonBlock`).
- ✅ 4-stage DLP trace (User→Service→AI→…→User).

**Added (from /audit):**

- Stats chips row above the sparkline — `N total · N allowed ·
  N masked · N blocked`. Same totals /audit used to show; now sits
  where Beat was looking.
- "Copy JSON" button on the detail-pane raw-payload block, via a
  small `JsonPanelHeader` helper so the button lines up with the
  label.
- "Download" button in the page header, beside Refresh. Fetches up
  to 1000 rows, applies the same filter+search the user sees, writes
  `requests-YYYY-MM-DD.csv` with columns `request_id, timestamp,
  provider, model, policy, latency_ms, status`.

Not added from /audit:
- The `masked_fields` CSV column (required hitting the per-row
  detail endpoint and would have fired N+1 requests on download
  — /audit had the same concern and worked around it by caching
  expanded rows only; dropped for simplicity because no rows in the
  current data have `policy_action=mask` yet).

### D3 — Rename `/audit-log` page title to "Admin Actions"

**Already done.** `admin-ui/app/audit-log/page.tsx:215` already had
`<h1 className="sran-page-title">Admin Actions</h1>`. The "Audit Log"
title Beat observed was on the **retired** `/audit` page's
`audit/page.tsx:226`. Deleting that page (D1) implicitly resolves the
label mismatch. No code change was needed for D3 itself; verified by
curl'ing `/audit-log` and grep'ing the HTML.

### D4 — Fix sidebar active-route highlight

**Root cause.** `components/Sidebar.tsx` used
`pathname.startsWith(item.href)`. On `/audit-log` that matches both
`"/audit"` (prefix) and `"/audit-log"` — two entries highlighted at
once.

**Fix.** Exact-match OR child-path match with a trailing slash:

```tsx
const isActive =
  item.href === "/"
    ? pathname === "/"
    : pathname === item.href ||
      pathname.startsWith(item.href + "/");
```

This is immune to prefix collisions for every current and future
sibling pair (`/alerts` vs `/alerts/events`, `/projects` vs
`/projects/123`, etc.).

### D5 — Update RBAC nav config

`admin-ui/lib/auth.ts:ROLE_NAV` trimmed. `/audit` removed from all
four role lists (`viewer`, `project_admin`, `tenant_admin`, `admin`).
`/audit-log` stays in the `admin`-only list (already added in the
post-consolidation fix-pass). Also removed the `{ label: "Request
Audit", href: "/audit", … }` entry from the sidebar NAV_SECTIONS
(same commit as the active-route fix — both are sidebar edits).

---

## Commits (3 logical units)

```
96cd590 fix(admin-ui): sidebar highlights only one entry per route
704a1d8 feat(requests): fold the pieces /audit had that /requests was missing
82ec11f refactor(admin-ui): retire /audit — it duplicated /requests
```

- `82ec11f` covers D1 + the `ROLE_NAV` part of D5 + the F4 doc update
  (four small related changes, logically one unit — "retire /audit").
- `704a1d8` covers D2.
- `96cd590` covers D4 + the sidebar-entry-removal part of D5 (both
  are one-file edits to Sidebar.tsx — same commit).

---

## Self-verification evidence

**Route behavior with an admin session cookie:**

```
$ curl -b admin_session -o /dev/null -w "%{http_code} %header{location}\n" \
       http://localhost:3000/audit
308 /requests
                                              ← permanent redirect installed

$ curl -bL admin_session -o /dev/null -w "%{http_code} %{url_effective}\n" \
       http://localhost:3000/audit
200 http://localhost:3000/requests
                                              ← redirect resolves cleanly

$ curl -b admin_session -w "%{http_code}\n" http://localhost:3000/requests | \
  grep -oE '>Requests<|>Download<'
>Requests<
>Download<
                                              ← header renders; Download button present

$ curl -b admin_session -w "%{http_code}\n" http://localhost:3000/audit-log | \
  grep -oE '>Admin Actions<|>Audit Log<'
>Admin Actions<
                                              ← only "Admin Actions" (no stray "Audit Log")
```

**Build output (Next.js route table):**

```
├ ○ /audit-log             ← kept
├ ○ /requests              ← kept
```

`/audit` no longer appears in the route table — the directory was
removed.

**Sidebar logic fix, verified by reading the file:**

```
Sidebar.tsx:113-115
  ? pathname === "/"
  : pathname === item.href ||
    pathname.startsWith(item.href + "/");
```

(On `/audit-log`: `pathname === "/audit-log"` matches exactly one
entry. There is no `/audit` entry left to cross-highlight anyway —
belt and braces.)

**Typecheck / build:**
- `npx tsc --noEmit` — clean.
- `npx next build` — clean, all 19 routes static-generated.

**Backend tests:** 1743 passed / 16 failed / 1 skipped. The 16
failures are the same pre-existing ones enumerated in
RUNTIME_CONSOLIDATION_REPORT.md row 11 — health-shape tests that
assert `status == "ok"` against a backend currently reporting
`"degraded"`, detector-registry tests sensitive to spaCy model
availability, a privacy-RBAC shape test, etc. None of them touch
UI routes, sidebar, or /requests. No regressions.

---

## Files touched

```
 admin-ui/app/audit/page.tsx                  | 440 ----  (deleted)
 admin-ui/app/requests/page.tsx               | 146 ++-   (D2 features)
 admin-ui/components/Sidebar.tsx              |   8 ++-   (D4 + D5-part)
 admin-ui/lib/auth.ts                         |   8 +-    (D5 ROLE_NAV)
 admin-ui/next.config.js                      |  11 +-    (D1 redirect)
 PHASE4_OPTIONAL_FEATURES_REPORT.md           |   2 +-    (doc note)
 6 files changed, 172 insertions(+), 445 deletions(-)
```

Net: **−273 lines** of duplicated UI.

---

## Recommendation

**Approve → final UI is the clean 2-page audit architecture.**

The admin now has one place for request traffic (`/requests`, with
the stats + Copy-JSON + Download affordances folded in) and one
place for admin-operation history (`/audit-log`). The sidebar
shows exactly one entry highlighted at all times. `/audit` survives
as a 308 redirect for anyone with a stale bookmark. No backend
behavior changed; no test regressions.
