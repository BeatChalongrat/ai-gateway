# Invite Flow Redesign — Pattern 2 (Link + Optional Email) — Gate Report

**Date:** 2026-04-24
**Branch:** `main`
**Scope:** portal Team page + backend `tenant_invites` semantics +
`/invite/{code}` landing route + `send-email` fan-out.

## Summary

The old "Email hint + Generate code" form is gone. In its place:

1. **Block 1 — Shareable invite link.** Owner-visible card reading
   "Invite teammates to {TenantName}" with either a "Create invite
   link" primary CTA (fresh state) or a full URL field plus Copy /
   Regenerate / Revoke controls (active state). Role selector on the
   right shows Member / Admin; Admin is visible-but-disabled until
   RBAC tiers land.
2. **Block 2 — Send via email.** Chip-style multi-email input below
   an "Or send via email" separator. Per-chip validation, paste-
   split on comma/space, partial-fail handling ("Sent to 1 of 3 —
   some emails failed" keeps the bad chips red), disabled banner
   when Resend isn't configured.
3. **Member view.** Non-owners see a compact "Only {Owner} can
   invite teammates" card with the owner's avatar — no controls, no
   empty form.

## Commits

| SHA | Subject |
| --- | ------- |
| `9526aaf` | feat(portal): schema for link-based invite redesign (role + revoked_at) |
| `3d35a28` | feat(portal): link-based invite endpoints + public preview + email fan-out |
| `3589aaa` | feat(portal): /invite/{code} landing page with pre-filled signup |
| `8a52c9e` | feat(portal): Team page Pattern-2 invite UI (link block + email block) |

Four logical units. No single commit larger than +741/-23.

## Schema changes

Migration **033_tenant_invites_role_and_revoke.py** — extends the
existing `tenant_invites` table. Two new columns:

| Column | Type | Default | Purpose |
| ------ | ---- | ------- | ------- |
| `role` | String(16) | `'member'` | Role granted on redemption — today only `'member'` exercised, `'admin'` wired end-to-end on the backend |
| `revoked_at` | DateTime(tz) | NULL | Soft-delete for Revoke flow. Separates "killed by owner" from "last redeemed at" (pre-redesign both wrote to `used_at`) |

Plus a partial unique index:

```sql
CREATE UNIQUE INDEX uq_tenant_invites_one_active_per_tenant
  ON tenant_invites (tenant_id) WHERE revoked_at IS NULL;
```

Enforces the "one live invite per tenant" UX contract at the DB
layer — Regenerate must now explicitly revoke the old row before
inserting a new one (the handler does this automatically on
`force=true`).

Backfill inside the migration marks every pre-redesign
`used_at`-non-null row as revoked, plus every already-expired row,
plus any historical duplicates that would violate the new unique
index. Safe to run on long-lived dev DBs without manual cleanup.

## Endpoints

### Added
- `GET /portal/team/invite` — live invite or `{invite: null}`
- `POST /portal/team/invite` — idempotent create; `force: true` rotates
- `DELETE /portal/team/invite` — soft-revoke current
- `POST /portal/team/invite/send-email` — Resend fan-out with per-
  recipient result array
- `GET /portal/auth/invite/{code}` — **public**, unauthenticated
  preview for the landing page

### Modified
- `GET /portal/me` — now includes `tenant_name` so the Team header
  reads "Invite teammates to Globex Inc" without a second GET
- `GET /portal/auth/public-settings` — now includes
  `resend_configured: bool` so the Block 2 Send button can disable
  itself without leaking the env var
- `DELETE /portal/team/invites/{id}` (plural, old path) — now sets
  `revoked_at` instead of the pre-redesign `used_at`-sentinel
  overload, matching the new column split
- `tenant_service.redeem_invite` — switched from single-use to
  multi-use-until-expiry; fails with `invite_revoked` instead of
  `invite_already_used`

### Unchanged
- `GET/POST /portal/team/invites` (plural, old), `POST
  /portal/team/redeem`, members + remove-member routes — all still
  work, backward compatible with any caller still on them

## UI Components

### New
- `portal/app/(app)/team/_components/InviteSection.tsx` —
  owner-only Block 1 + Block 2, drives all the singular-endpoint
  calls, reusable if we later surface invites elsewhere
- `portal/app/(auth)/invite/[code]/page.tsx` — server-rendered
  invite landing route; fetches `preview` and hands the result to
  the shared SignupForm or an error card

### Modified
- `portal/app/(app)/team/page.tsx` — old "Invite a teammate" form +
  "Pending invites" list gone; `<InviteSection />` +
  `<MemberOnlyInviteCard />` in their place
- `portal/app/(auth)/signup/_components/SignupForm.tsx` — optional
  `presetInviteCode` + `inviteContext` props; when set, hides the
  "Have an invite code?" toggle and swaps the header copy to
  "Join {tenant}" + "Bob Beaumont invited you to join as a member"
- `portal/middleware.ts` — `/invite/*` added to AUTH_PATHS so
  middleware lets unauthenticated visitors through and redirects
  already-authed users to `/dashboard`
- `portal/lib/types.ts` — `TenantInvite` gains `role`, `revoked_at`,
  `invite_url`; `PortalUser` gains `tenant_name`

## Email template

`send_invite_email()` + `render_invite_email()` from Phase 3.5
already existed — `invite_link`, `invite_code`, `tenant_name`,
`inviter_name` variables, `en` + `th` templates. Unchanged for this
redesign; the new endpoint reuses them. Dev-mode sender logs to the
backend container stdout rather than calling Resend, so the
verification step `Bob Email` produces:

```
email.dev_console email_to=carol@gx.test
email_subject=Join {inviter_name} on SRAN AI Gateway
```

One send per recipient, dev-mode result `dev_mode`.

## Tests

### New
- `backend/tests/test_portal_invite_link_flow.py` — **17 tests** across
  seven test classes:
  - `TestGetActiveInvite` — null vs live shape
  - `TestCreateInvite` — idempotent, `force` rotates, partial-index
    survives
  - `TestRevokeInvite` — delete clears, idempotent when nothing live
  - `TestOwnerOnlyGates` — member gets 403 on the singular route
  - `TestPublicPreview` — valid / bogus / revoked code responses
  - `TestMultiUseRedemption` — same code redeems twice, revoked
    refuses
  - `TestSendInviteEmail` — no-active-invite 404, dedup + invalid,
    member-can't-send
  - `TestPublicSettingsExposesResendFlag` — the UI gate signal

### Updated
- `test_portal_team_routes.py::TestRevokeInvite::test_owner_revokes_pending_invite`
  — error code `invite_already_used` → `invite_revoked` to match the
  column-split semantics
- `test_tenant_service.py::TestRedeemInvite::test_revoked_invite_rejected`
  — renamed from `test_already_used_invite_rejected` + asserts the
  new multi-use model

### Overall
**118 passing** across the portal subset (17 new + 101 prior).

## Verification — real-endpoint smoke

Ten scripted curl checks against the running dev stack:

| # | Step | Result |
| - | ---- | ------ |
| 1 | Bob (Owner, Globex) — fresh-state create → link returned with role/expiry/URL | ✅ |
| 2 | Bob Copy — endpoint returns `invite_url` that's a real URL | ✅ `http://localhost:3001/invite/{code}` |
| 3 | Bob Regenerate (`force=true`) → new code, old revoked | ✅ new code, old shape gone from GET |
| 4 | Bob Revoke → GET returns `{invite: null}` | ✅ |
| 5 | Bob Email to `carol@gx.test, dan@gx.test` → `sent: 2/2`, dev-console log | ✅ |
| 6 | Bob Email with invalid + duplicate → `sent: 1/3`, per-result reasons returned | ✅ |
| 7 | Charlie (Member, Acme) `GET /portal/team/invite` → `403 owner_required` | ✅ |
| 8 | `GET /invite/{live-code}` → HTML contains "Join Globex Inc" + "Bob Beaumont invited you to join as a member" | ✅ |
| 9 | `GET /invite/definitely-not-real` → HTML contains "Invite unavailable" + "Create your own team" | ✅ |
| 10 | Cross-tenant: Alice preview of Globex code returns wrong-tenant? No — preview is public so anyone can see; what's protected is _mutation_. Alice's `DELETE /portal/team/invites/{globex-code}` returns 404 (tenant-scoped), not 403. | ✅ |

All 10 pass end-to-end.

## Screenshots

**Not captured.** No headless Chromium is available in the run
environment, so this report documents the flows via live HTTP
output rather than image captures (same caveat as the previous two
reports). The dev server at http://localhost:3001 is the canonical
source of truth for visual review; a human reviewer should log in
as Bob / Charlie and Alice to confirm the rendered layout matches
the brief's ASCII mockups.

## Known limitations + decision rationale

- **Admin role is visible-but-disabled.** The backend already
  accepts `role='admin'` in `POST /portal/team/invite` and
  redeem_invite will assign the claim — but the full RBAC tier
  (permission checks, admin-only endpoints) is explicitly deferred
  in `PORTAL_TENANCY_MIGRATION_PLAN.md` §14. Shipping it enabled
  would let an owner mint "admins" who have exactly the same
  permissions as members, which would be a confusing footgun. Keep
  it greyed out until the permission work happens.
- **Multi-use vs one-time invite codes.** Chose multi-use for the
  shareable-link UX: "post this in Slack and everyone joins" is
  the dominant user story and Pattern-2 is explicitly a Slack/
  Discord-style link. Revocation + 7-day expiry + one-live-per-
  tenant bound the blast radius. If a tenant wants one-time codes
  later, the column split supports it: treat `revoked_at` as
  auto-set on first redemption.
- **Email hint removed.** The old schema's `email_hint` column is
  still there for backwards compat with the plural endpoints, but
  Pattern-2 never asks for it on create — the Send Email block
  replaces its purpose with actual delivery.
- **Landing page renders on a single-slide visual panel.** The
  existing VisualPanel component has 3 slides on /signup; /invite
  uses one simpler "Join your team" slide so the visual panel
  doesn't outshine the context banner on the form side.
- **Dev environment only.** `PORTAL_PUBLIC_URL=http://localhost:3001`
  is now wired into docker-compose.yml so invite URLs match the
  portal origin; production deploys must set this to the real
  public host, otherwise the Copy button produces dead URLs.

## Recommendation

Approve. The Team page now reads as the Slack-style shareable-link
invite flow the brief calls for:

- One primary artefact (the link) instead of "paste this code"
- One card per concept: Block 1 is the link; Block 2 is the
  optional email fan-out
- Owner-only surface hidden entirely from members, replaced with
  a compact "ask your owner" card
- Backend contract is cleaner too: one live invite per tenant at
  the DB level, column split between "last-redeemed" and "killed"

Follow-up ticket: proper Thai localisation for the invite-email
template body (today the subject + button copy are translated but
the "You were invited" framing is English-only in the `.txt` file).
Not blocking — same caveat applies to verification/reset emails.
