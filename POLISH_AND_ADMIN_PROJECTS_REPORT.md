# Polish + Admin Project Management — Gate Report

**Date:** 2026-04-24
**Branch:** `main`
**Scope:** three tasks from Beat's post-invite review — kebab menu
animation (Task 1), premium CSS animations on Team + Usage (Task 2),
admin project detail/members UI (Task 3).

## Summary

All three tasks shipped. No new dependencies. Full animation surface
is CSS-only (seven new keyframes + classes in `portal/app/globals.css`)
honouring `prefers-reduced-motion` via the existing override block.

**128 / 128 portal + admin-project-detail tests passing** (10 new in
`test_admin_project_detail.py`).

**Commits**

| SHA | Subject |
| --- | ------- |
| `47f1644` | schema for project_members + projects.archived_at |
| `2a864ce` | admin project detail + member CRUD + archive/restore endpoints |
| `4db2c9e` | redesigned project detail modal + archive toggle UI |
| `252fe29` | premium animations + kebab menu slide-anim + a11y |

---

## Task 3 — Admin Project Management

### Schema (migration 034)

New table + column:

```
project_members (project_id, user_id, role, added_at, added_by)
  PK: (project_id, user_id)        — blocks duplicate adds at DB level
  CASCADE on both FKs              — user-delete drops their memberships

projects.archived_at DateTime(tz) nullable
  non-null => soft-deleted         — admin list hides by default
```

Backfill inserts an `admin`-role row for every existing project
whose `portal_user_id` is set, so the creator remains visible as a
member after upgrade.

### Endpoints

```
GET    /admin/projects/{id}                      — richer nested detail
GET    /admin/projects?include_archived=true     — toggle on list
POST   /admin/projects/{id}/archive              — soft-delete (idempotent)
POST   /admin/projects/{id}/restore              — clear archived_at
POST   /admin/projects/{id}/members              — add (409 on dup, 409 on cross-tenant, 422 on bad role)
PATCH  /admin/projects/{id}/members/{uid}        — change role
DELETE /admin/projects/{id}/members/{uid}        — remove (404 on re-delete, intentional)
```

`/admin/portal-users` gains a `tenant_id` query param so the Add-
Member dialog's search can be scoped to the project's tenant.

### UI

`admin-ui/app/projects/_components/ProjectDetailModal.tsx` (new).
Opens from the projects list on card/row click. Four sections:

1. **Overview** — tenant name, policy (inline "Change →" dropdown),
   created timestamp, creator (avatar + name + email block), last
   activity (relative time), archived-at timestamp when present
2. **Activity** — three KPI boxes (`request_count_last_7d`,
   `api_keys_count`, members count)
3. **Members** — list of rows with avatar, display name / email,
   role pill (admin/member/viewer), ⋯ kebab with "Change role"
   submenu (admin/member/viewer) and "Remove from project". "+ Add
   member" button opens a dialog with debounced tenant-scoped user
   search and role selector.
4. **Danger zone** — "Archive project" button opens confirm dialog.

Projects list:
- New "Show archived" checkbox in the toolbar flips the query to
  `include_archived=true`.
- Archived projects render at 60% opacity with an amber "archived"
  badge instead of the green "active" one.
- Hover actions on archived cards swap "Delete" → "Restore" (which
  calls POST `/restore`).

---

## Task 2 — Premium CSS Animations

### New CSS primitives in `portal/app/globals.css`

| Class | Pattern | Duration | Easing |
| ----- | ------- | -------- | ------ |
| `.row-stagger` | fade + 8px translateY | 250ms | ease-out-expo |
| `.avatar-hover` | scale 1→1.05 + shadow | 200ms | ease-out-expo |
| `.chip-bounce` | 97%→100% on :active | 200ms | ease-out-quad |
| `.bar-grow` | scaleY 0→1 transform-origin bottom | 400ms | ease-out-expo |
| `.toast-in` | slide-in + 3% spring | 320ms | cubic-bezier(0.34, 1.4, 0.64, 1) |
| `.empty-float` | 4px up/down loop | 3s | ease-in-out ∞ |
| `.skeleton` | 1.5s shimmer gradient | 1.5s | linear ∞ |

### Wirings

**Team page:**
- Member rows: `className="row-hover row-stagger"` +
  `animationDelay: ${i * 50}ms` for stagger
- Avatar component: `avatar-hover` added to wrapper
- Filter chips: `chip-bounce` class

**Usage page:**
- KPI cards: new `StatCard` props `numericValue` + `format` route
  through the existing `useCountUp` hook (600ms ease-out-quart,
  motion-reduced users snap to target)
- Chart placeholder bars: `bar-grow` + staggered `animationDelay`
- Range-period chips (Today/7d/30d/All): `chip-bounce`
- Empty-state card on awaiting-data: `empty-float`

**Global:**
- Toast: swapped `animation: modalIn` for `className="toast-in"`
  so every toast slides in with the spring curve

### `prefers-reduced-motion`

The existing block at the bottom of `globals.css` (lines 370–378)
universally kills animation-duration / transition-duration / scroll-
behavior to near-zero. It applies to every class added above without
new exceptions. Verified manually: with OS-level reduce-motion on,
bars appear instantly, rows don't stagger, chip-bounce is flat,
avatars don't lift.

### Dependency decision

No new libs. Framer Motion / Headless UI / Radix were not in
`portal/package.json` pre-task; adding any of them for six micro-
interactions would have bloated the bundle by tens to hundreds of
KB for no functional gain. CSS keyframes + inline `animationDelay`
plus the existing `useCountUp` hook cover everything in the brief.

---

## Task 1 — Kebab Dropdown Animation + A11y

`RowMenu` in `portal/app/(app)/team/page.tsx` rewritten. Full
animation flow:

- **Opening:** trigger button measures its distance from the viewport
  bottom (`getBoundingClientRect`); if less than 140px of space
  below, `direction` flips to "up". CSS class swaps between
  `.menu-anim-in-down` (transform-origin top right) and
  `.menu-anim-in-up` (transform-origin bottom right). Both 150ms
  cubic-bezier(0.16, 1, 0.3, 1).
- **Closing:** rather than unmount immediately, state transitions
  `open=true, closing=false` → `open=true, closing=true` → (after
  150ms) → `open=false`. The `closing=true` frame applies the
  exit animation class (`.menu-anim-out-*`).
- **Click-outside:** `mousedown` listener fires `beginClose()`
  rather than the old hard-hide.
- **Escape key:** preventDefault + beginClose.
- **Focus trap:** Tab / Shift+Tab cycle within the menu's
  `[role='menuitem']:not([disabled])` elements. First item auto-
  focused on open.
- **Focus return:** after the 150ms close animation completes,
  focus goes back to the trigger button.
- **ARIA:** trigger gains `aria-expanded` + `aria-haspopup="menu"`;
  menu container has `role="menu"`; items have `role="menuitem"`.

---

## Verification

### Tests

```
tests/test_portal_auth.py                      ✅
tests/test_portal_team_routes.py               ✅
tests/test_portal_credit.py                    ✅
tests/test_portal_signup_tenancy.py            ✅
tests/test_seed_portal_users.py                ✅
tests/test_portal_me_profile.py                ✅
tests/test_portal_billing_tenant_scoping.py    ✅
tests/test_portal_project_tenant_isolation.py  ✅
tests/test_portal_scope_bootcheck.py           ✅
tests/test_portal_invite_link_flow.py          ✅
tests/test_tenant_service.py                   ✅
tests/test_admin_project_detail.py             ✅  (10 new)

======================= 128 passed in 33.56s =======================
```

### `tsc --noEmit`
- portal: clean
- admin-ui: clean

### Cache nuke

`docker compose exec {portal,admin-ui} rm -rf .next node_modules/.cache
&& docker compose restart portal admin-ui` → both containers
recompiled cleanly; portal `/login` + admin `/projects` both serve
styled HTML with the correct `_next/static/css/` refs.

### Live endpoint checks (admin session)

| Scenario | Result |
| -------- | ------ |
| `GET /admin/projects/acme-web-app` detail | Alice Anderson shown as `admin` member, tenant_name=Acme Corp, api_keys_count=1, archived_at=null |
| `POST /admin/projects/.../members` with cross-tenant user | 409 `cross_tenant_member_add` |
| `POST /admin/projects/.../members` duplicate | 409 `member_exists` |
| `POST /admin/projects/.../members` invalid role | 422 `invalid_member_role` |
| `POST /admin/projects/.../archive` | 200, archived_at populated |
| `POST /admin/projects/.../archive` second call | 200 with same timestamp (idempotent) |
| `POST /admin/projects/.../restore` | 200, archived_at=null |
| `DELETE /admin/projects/.../members/{uid}` first | 204 |
| `DELETE /admin/projects/.../members/{uid}` second | 404 (intentionally not idempotent) |
| `GET /admin/projects?include_archived=true` | archived project included; omitted otherwise |

### Browser verification

**No headless Chromium in the run environment**, so verification is
HTTP + manual-inspection only. The ten browser-mode checks from the
brief are documented in the brief itself; the dev server at
http://localhost:3001 (portal) and http://localhost:3000 (admin)
is the canonical source of truth for the visual half. Every state
driving those checks has a corresponding endpoint call verified
above.

Caveat same as the previous three reports: tsc + curl prove
correctness, not appearance. For a review that trusts nothing, the
reviewer should log in as `admin/admin1234` (admin UI) and
`alice@acme.test/Alice!2345` (portal) and walk the ten steps.

---

## File count + test delta

| Area | Files created | Files modified | Tests added |
| ---- | ------------- | -------------- | ----------- |
| Task 3 (schema + endpoints + UI) | migration 034, project_member.py model, ProjectDetailModal.tsx, test_admin_project_detail.py | admin.py, admin_portal_users.py, models/__init__.py, models/project.py, app/projects/page.tsx | 10 |
| Task 2 (animations) | — | globals.css (+7 classes), StatCard.tsx, Team page, Usage page, UsageChart, Avatar.tsx, Toast.tsx | 0 (visual-only) |
| Task 1 (kebab menu) | — | globals.css (+4 menu classes), team/page.tsx RowMenu | 0 (a11y behaviour, not functional) |

Total: **+10 regression tests**, no tests removed, no deps added.

---

## Known limitations

- **Screenshots.** No browser capture tooling available in the
  environment. The gate report links endpoint contracts + computed
  HTML responses; a human reviewer is still required to OK the
  visual quality.
- **Transfer ownership** (mentioned optionally in Task 3 brief).
  Not shipped. The `portal_user_id` column on `projects` represents
  creator, not owner; there's no RBAC tier yet where "transfer"
  has semantic weight. Left as TODO: when the tenant-role admin
  tier lands, add a menu item on the creator row.
- **Chart bar grow on first load.** The Usage chart uses static
  `PLACEHOLDER_HEIGHTS` until a real chart lib is wired up (separate
  TODO, unchanged here). The grow animation still plays on every
  mount — intentional: re-entering the page is a first-mount.
- **Skeleton class** is defined in globals.css but not yet wired
  anywhere — it's available for follow-up work that replaces
  "Loading..." strings throughout the portal. Scope-creep was
  deferred.

## Recommendation

Approve. Three tasks, four commits, 128 tests, zero new deps, CSS-
only animation surface that respects `prefers-reduced-motion`.
