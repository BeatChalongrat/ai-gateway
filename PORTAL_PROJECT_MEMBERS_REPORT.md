# Portal Project Member Management — Gate Report

**Date:** 2026-04-24
**Branch:** `main`
**Scope:** match admin project-member surface on the portal side +
two admin-side bugs Beat surfaced during invite-flow verification.

## Summary

Portal end-users can now see, add, and manage project members the
same way admin can — with the hybrid ("Model C") scope model Beat
locked in:

- **Tenant Owner**: sees every project in their tenant, passes
  every project-role check via short-circuit.
- **Tenant Member**: sees only projects with an explicit
  `project_members` row. Direct URL to a non-member project
  returns 403 `project_access_denied`.
- **Project roles**: `admin` (manage members + keys), `member`
  (use keys), `viewer` (read-only).

Admin bugs A1 + A2 shipped first as fast unblockers.

**143 / 143 portal + admin-project tests passing** (15 new in
`test_portal_project_members.py`, 5 updated in
`test_portal_project_tenant_isolation.py` for the 404→403 shape
change that the hybrid model requires).

**Commits**

| SHA | Subject |
| --- | ------- |
| `91862f8` | fix(admin): project creator backfill + modal theme consistency |
| `9299cf1` | feat(portal): hybrid project-member scope model + member CRUD |
| `ced3f5e` | feat(portal): project detail Members section + role-aware empty state |

---

## Scope model — decision recap

> Tenant Owner sees all projects automatically. Tenant Member sees
> only projects where they're explicitly added as a project member.
> New users added to tenant have no automatic project access. Project
> member roles: Admin / Member / Viewer.

Implementation decisions made in this PR:

- **Owner bypass is implicit, not via a membership row.** A
  `tenant_role='owner'` user short-circuits the project-role check
  inside `_resolve_project`. Rationale: the tenant-level fact is
  already stored on `portal_users.tenant_role`, so duplicating it
  into `project_members` would mean two sources of truth and a
  risk of drift. One tradeoff: a tenant owner's implicit access
  doesn't show up on the Members list — acceptable because the
  Owner badge is visible elsewhere (Team page), and duplicating
  would require a "synthetic-owner" row concept that confuses the
  data model.
- **POST /portal/projects auto-grants.** Creator → `admin`. If the
  creator isn't the tenant owner, the earliest tenant owner is
  *also* added as `admin` — pins the "every project has at least
  one admin" invariant even if the creator later leaves.
- **Last-admin guard.** Blocks `DELETE` and `PATCH role != 'admin'`
  when the target is the sole admin. Applies to self-leave too. The
  owner bypass means the tenant owner can always jump in even
  after the last admin leaves, but the invariant reads cleanly
  without relying on that bypass.
- **404 → 403 on cross-tenant + no-access.** Pre-redesign the
  `_own_project` helper returned 404 for any not-owned project,
  which meant cross-tenant probes and same-tenant-no-access were
  indistinguishable at the code level (but also both opaque to the
  client). The new `_resolve_project` returns 403
  `project_access_denied` for both. This is what the brief
  specifies. Cross-tenant isolation is unchanged; the error code
  is the only visible delta.

---

## Migration 035 — creator backfill (Bug A1)

Projects created before migration 034 had a NULL `portal_user_id`
so the new admin modal rendered "—" for Created by. Migration 035:

```sql
UPDATE projects p
SET portal_user_id = (earliest tenant owner's id)
WHERE p.tenant_id = ...
  AND portal_user_id IS NULL;

INSERT INTO project_members (project_id, user_id, role, added_at)
SELECT ... FROM projects WHERE portal_user_id IS NOT NULL
ON CONFLICT DO NOTHING;   -- (expressed as NOT EXISTS)
```

Heuristic pick: the tenant's earliest `tenant_role='owner'` user.
Tenants with zero portal users (the seed `demo-tenant-000` and
`perf-test-tenant`) stay NULL — the route layer renders those as
"System" with a monospace label + "Pre-034 project with no recorded
creator" hint. Not a lie, just an honest "we don't know."

Post-migration state:

| tenant | projects | resolved creator |
| ------ | -------- | ---------------- |
| `acme-001` | 3 seeded | Alice Anderson (owner) |
| `globex-001` | 4 (inc. a test project created mid-flight) | Bob Beaumont (owner) |
| `demo-tenant-000` | 3 | NULL → "System" |
| `perf-test-tenant` | 1 | NULL → "System" |

---

## Admin Modal theme (Bug A2)

Beat reported the modal read as "white with dark text" on dark
theme. Root cause was two-fold:

1. `Section` sub-components inside `ProjectDetailModal` used the
   `.sran-card` class. Inline `background: var(--bg-elevated)`
   overrode its background, but the default padding + shadow from
   the class fought the inline styles and made the cards feel
   cramped + foreign against the modal body.
2. Default modal width of 28rem was too narrow for a four-section
   layout, making the few correctly-themed pixels read wrong.

Fix: dropped the `.sran-card` class on `Section`, inlined the full
shell with theme tokens only. Added a `maxWidth` prop to
`components/ui/Modal.tsx` (default 28rem for backward-compat, 640px
for `ProjectDetailModal`). Creator row now degrades gracefully to
monospace "System" when `created_by.system === true`.

---

## Endpoints

All under `/portal/projects/*`, require portal JWT. Project-role
gates short-circuit for tenant owners.

| Method | Path | Minimum role | Notes |
| ------ | ---- | ------------ | ----- |
| GET | `/portal/projects` | member | tenant-owner bypass lists all projects in tenant |
| POST | `/portal/projects` | (any tenant user) | auto-grants creator + owner as admin |
| GET | `/portal/projects/{id}` | viewer | 403 `project_access_denied` otherwise |
| PATCH | `/portal/projects/{id}` | admin | |
| DELETE | `/portal/projects/{id}` | admin | revokes all keys first |
| GET | `/portal/projects/{id}/members` | viewer | |
| POST | `/portal/projects/{id}/members` | admin | 409 on dup / cross-tenant, 422 on bad role |
| PATCH | `/portal/projects/{id}/members/{uid}` | admin | 409 last-admin demote |
| DELETE | `/portal/projects/{id}/members/{uid}` | admin OR self | 409 last-admin, self-leave allowed |
| GET | `/portal/projects/{id}/keys` | viewer | |
| POST | `/portal/projects/{id}/keys` | member | |
| POST | `/portal/projects/{id}/keys/{key_id}/rotate` | member | |
| DELETE | `/portal/projects/{id}/keys/{key_id}` | member | |

New helpers:
- `_resolve_project(session, user, project_id) -> (Project, role)`
- `_require_project_role(session, user, project_id, minimum='...')`
- `_block_if_last_admin(session, project_id, excluded_user_id)`

---

## UI

### Portal `/projects/{id}` page
- New **Members** card between API Keys and the Create-key modal.
- Rows: avatar (with `avatar-hover` lift), name, email, role badge
  (amber + ShieldCheck for Admin, neutral + UserRound for Member,
  muted + Eye for Viewer), ⋯ kebab.
- Kebab menu re-uses the 150ms slide-anim + a11y story from the
  Team page RowMenu (smart flip, Escape, focus return,
  `aria-expanded`, `aria-haspopup`, `role='menu'` /
  `role='menuitem'`).
- Kebab offers role change (Admin/Member/Viewer, current greyed),
  then either "Remove from project" (admin on others' rows) or
  "Leave project" (any role on own row).
- **+ Add member** button: only shown when the caller is project-
  admin or tenant-owner. Opens a modal with:
  - Tenant-scoped search (populated from `/portal/team/members`,
    excluding users already on this project)
  - Three-up role picker cards

### Portal `/projects` list empty state
Branches on `me.tenant_role`:
- **Owner**: "Create your first project to organize API keys by
  application or environment." + Create + View docs
- **Member**: "Ask your team admin to grant access, or create one
  of your own." + Create (no docs secondary — shorter, more
  focused)

### Admin project modal
- A1: "Created by" row renders real creator when known, falls back
  to monospace **System** + subtle "Pre-034 project with no
  recorded creator" caption when backend returns
  `created_by.system === true`.
- A2: wider modal (640px), `Section` cards fully inlined so theme
  tokens drive every surface.

---

## Tests

### Backend

| File | Tests | Status |
| ---- | ----- | ------ |
| `test_portal_project_members.py` (new) | 15 | ✅ |
| `test_portal_project_tenant_isolation.py` (5 assertions migrated 404 → 403) | 7 | ✅ |
| All other portal + admin tests | 121 | ✅ |
| **Total portal + admin-project-detail** | **143** | **all passing** |

Test coverage for the new behaviour:

- Scopes: owner-sees-all, member-sees-only-explicit, 403 on non-
  member access, cross-tenant 403 (not 404)
- Auto-grants: owner-create pins only the owner, member-create
  pins both member + earliest tenant owner
- Member CRUD: admin-can-add, viewer-cannot-add, cross-tenant
  rejected, duplicate rejected, patch works, delete works
- Invariants: last-admin demote blocked, last-admin delete
  blocked, self-leave works when not last admin

### Portal

- `tsc --noEmit`: clean

---

## Verification — all 13 scenarios

Tested end-to-end against the live dev stack (HTTP + cookie-based
admin auth + portal JWT):

| # | Scenario | Result |
| - | -------- | ------ |
| 1 | Bob (Owner, Globex) /portal/projects sees all 4 Globex projects | ✅ |
| 2 | Bob's member list on Globex web-app shows bob@globex.test -> admin | ✅ |
| 3 | Alice/Bob add-member modal lists only not-yet-added tenant mates | ✅ verified via endpoint + UI fetch |
| 4 | Add Eve as viewer → row appears on next load | ✅ |
| 5 | PATCH role → Member, Admin, Viewer all work | ✅ |
| 6 | DELETE Eve → row gone | ✅ |
| 7 | Charlie (Member, Acme) /portal/projects initially empty | ✅ |
| 8 | Alice adds Charlie to acme-web-app as Viewer → 200 | ✅ |
| 9 | Charlie /portal/projects now shows that one project | ✅ |
| 10 | Charlie GET /portal/projects/acme-web-app → 200 | ✅ |
| 11 | Charlie GET /portal/projects/acme-mobile-app → 403 `project_access_denied` | ✅ |
| 11b | Charlie POST /portal/projects/acme-web-app/members → 403 `project_admin_required` (viewer-level) | ✅ |
| 12 | Admin /admin/projects/acme-web-app shows Alice Anderson as creator (A1) | ✅ |
| 13 | Admin /admin/projects/demo-project-000 shows "System" as creator (A1 fallback) | ✅ |

Caveat (same as every prior report): **no headless Chromium** in
the run environment. The above verifies the endpoint contract +
browser-inspect-ready HTML structure but does not capture
screenshots. The dev server at http://localhost:3001 (portal) and
http://localhost:3000 (admin) is the canonical source of truth for
visual review; a human should log in as Bob, Alice, Charlie, and
`admin/admin1234` to walk the UI paths behind each scenario.

---

## Known limitations

- **Admin modal `maxWidth=640px` is a point-fix, not a full modal-
  sizing overhaul.** Other admin modals (Create Project, Delete
  Confirm) still use the 28rem default. When the next modal needs
  custom width, pass `maxWidth` — the prop is there.
- **Tenant-owner implicit access doesn't appear on the project's
  Members list.** The UI says "Members (1)" for a project where
  the only explicit row is Bob, but the tenant owner (if different)
  can still see + manage. Adding a synthetic row would confuse
  the data model; the Owner badge on the Team page makes the
  access obvious at the tenant level.
- **No "change ownership" / "transfer project admin" flow.** The
  kebab only changes role, not ownership; `portal_user_id` on the
  project row stays put. If that becomes a need, add a POST
  `/transfer` endpoint that updates both `portal_user_id` and the
  target's project_members role.
- **"Leave project" as the last admin** fails with a toast rather
  than being disabled at render time. The client can't know
  authoritatively whether the caller is the last admin without
  inspecting every member row's role, so we surface the server's
  `last_admin_remaining` as a toast. Acceptable — the user sees
  the error immediately and the copy explains why.

## Recommendation

Approve. Three commits, one logical unit each, 143 tests, zero
new deps. The hybrid scope model is implemented on both sides of
the UI now (admin and portal), so Beat's asymmetry report is
resolved and the two admin bugs that showed up during verification
are fixed.
