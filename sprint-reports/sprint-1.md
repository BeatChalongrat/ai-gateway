# Sprint 1 Report — Stop the Bleeding + Foundation

> Branch: `sprint-1-ui-functional` (off `chat-folders`). 24 commits, one per task/batch.
> Baseline verified in `sprint-1-prestate.md`. **Both apps `tsc --noEmit` clean. Deployable.**

## Before-state
All 18 Sprint-1 issues were re-verified against live code in `sprint-1-prestate.md` (Sections A–G) before any edit. Summary: 6 dead controls, 3 silent error-swallows, 2 stub forms, 2 P0 styling bugs, 6 native-dialog sites, 5 fabricated-metric sites, plus Foundation gaps (emoji icons, light-only hex, spinner loading).

## Changes made

### Day 1–2 — P0 functional fixes
| # | File(s) | Change | Commit |
|---|---|---|---|
| 1 | requests | Removed dead "Last 24h" button | `6a023b7` |
| 2 | policies | Detector "Enabled" Toggle → read-only indicator (no backend endpoint) | `3a35a87` |
| 3 | ai-review | Removed dead manual-scan toggle (no backend field; always available) | `3667def` |
| 4 | signup | Dead OAuth buttons → shared env-gated `<SocialButtons/>` | `dece3b5` |
| 5 | team | Removed dead "Change role" menu item | `bdb8d6b` |
| 6 | search-settings | Removed dead `addTestResult` UI | `a39a464` |
| 7 | providers | `loadKeys` now surfaces errors (+Retry) instead of "no keys" | `145e779` |
| 8 | chat-users | Toggle failure → toast (was silent) | `8a5961c` |
| 9 | dashboard | Per-project key counts via `allSettled` + "N+ / counts unavailable" | `ddd4999` |
| 10 | settings | Security/password stub gated behind `NEXT_PUBLIC_PASSWORD_CHANGE_ENABLED` (hidden) | `bd0fa90` |
| 11 | settings | Danger Zone delete gated behind `NEXT_PUBLIC_ACCOUNT_DELETE_ENABLED` (hidden) | `244d910` |
| 12 | forgot/reset-password | Import `login.css` + centered `.auth-root` shell (cold-load styling) | `04fdfc5` |
| 13 | invite | "Sign in instead" CTA → legible theme tokens (was white-on-white) | `9579fc1` |

### Day 3–4 — Foundation
| ID | Change | Commit |
|---|---|---|
| F4 | Native `confirm/prompt/alert` → Modal/ConfirmModal/ConfirmDialog (portal + admin, 6 sites) | `ff35c49`, `0f312cc` |
| F4 | `.eslintrc.json` (both apps) banning confirm/alert/prompt | `5eb328e` |
| F1 | `components/ui/Icon.tsx` + emoji/glyph sweep (5 admin pages) | `20fe101` |
| F2 | Light-only hex → theme tokens (policies, tenants, api-keys, dlp-coverage + portal-users via F1) | `348e0f6` |
| F5 | Portal skeleton presets + swap PageSpinner on dashboard/projects/projects[id]/billing | `37e722b` |

### Day 5 — F6 honest metrics
| Item | Change | Commit |
|---|---|---|
| routing 99.2% | Removed the fabricated success-rate tile | `5adf81b` |
| usage chart | UsageChart now plots **real** per-time-bucket counts (existing CSS bars) | `5adf81b` |
| projects 0-requests | Removed always-0 requests stat from ProjectCard | `5adf81b` |
| projects/[id] | Account-wide stat tiles labelled "Across all your projects" | `5adf81b` |
| dashboard | Removed permanent "Next Reset: —" | `5adf81b` |

## After-state (verified)
- `grep window.(confirm|prompt|alert)` in admin-ui/app + portal/app → **0**.
- `grep` emoji (💰⏸▶👁👥🛡💎⚠️★) in admin-ui/app → **0**.
- `routing` "99.2%" / dashboard "Next Reset" → only removal-comments remain. ("Last 24h" still appears once = audit-log's legitimate period-chip label, not the removed button.)
- forgot-password + reset-password import `login.css`. ✓
- Skeletons live on dashboard/projects/projects[id]/billing; PageSpinner remains on usage/team/settings (Sprint 2 batch, by plan). ✓
- `npx tsc --noEmit` → clean for **both** admin-ui and portal.

## Decisions taken during execution (documented defaults / deviations)
1. **policies Toggle (#2):** backend exposes no per-detector enable endpoint — `enabled` derives from `settings.dlp_disabled_detectors` (env). Per "no endpoint → read-only", converted to a read-only status indicator + tooltip.
2. **ai-review toggle (#3):** backend has no `manual_scan_enabled`; manual scan is always available via `POST /ai-review/scan`. Per "backend doesn't accept → remove", removed the toggle (kept the real scope/limit config, now always shown).
3. **signup OAuth (#4):** used Beat's option (b) — reused existing `SocialButtons` (env-gated) instead of deleting.
4. **F6 routing tile:** `StatTile` has no caption slot, so a "Coming soon" string in the big-number position reads poorly — **removed the tile** rather than show "Coming soon" there.
5. **F6 usage chart:** recharts is **not** a portal dependency (only admin-ui has it); the locked "use recharts" assumed otherwise. Avoided a new dep by wiring **real data into the existing CSS bars**.
6. **settings stubs (#10/#11):** gated both the Security card and the Danger Zone behind env flags (hidden by default) rather than only removing the delete button — the Danger Zone's sole content was that disabled button, so a button-only removal would leave an actionless card. Consistent with the `SocialButtons` env-gate pattern; clean re-enable path.
7. **forgot/reset shell (#12):** `.auth-root` alone doesn't center (`.auth-form-card` is centered by the split's form-side on login/signup, which these pages lack), so added a flex-centering wrapper.
8. **F4 ESLint (dormant):** neither app has an ESLint toolchain installed (no `eslint` binary, no TS parser). The `.eslintrc.json` files were created per Beat's explicit instruction but are **dormant** — `next build` skips lint when eslint isn't installed. They activate once a toolchain is added. Also: `no-restricted-globals` catches bare `confirm()/alert()/prompt()` but **not** `window.confirm` member calls (kept minimal per Beat). → backlog.
9. **F2 chat-analytics excluded:** its axis-tick colors are passed to recharts as SVG `fill` **attributes**, which don't resolve CSS `var()`. A blind token swap would break them. Deferred → backlog.
10. **invite eyebrow (#13 scope):** fixed the CTA (the task); the red-text-on-gradient-pill eyebrow is a separate P2 → backlog.

## Hard-rules compliance
- Keep-As-Is pages touched **only** by allowed Foundation sweeps / the locked F6 decision: `/routing` (F6 success-rate, explicitly locked), `/tenants` + `/api-keys` (F2 token sweep), `/audit-log` (F1 icon). Admin `/` and admin `/projects` untouched. Portal landing `/` untouched.
- No business-logic/API/routing changes except the locked F6 items and P0 dead-control removals.
- SRAN Chat (`apps/web-chat/`) untouched.
- No new runtime dependencies added.

## New findings → backlog
Logged to `sprint-reports/backlog.md` (8 items): dead `.sv-*` CSS after signup OAuth swap; invite error-eyebrow contrast; chat-analytics recharts axis colors; portal SocialButtons 💡 emoji; ESLint toolchain install (to activate the ban + add `no-restricted-properties` for `window.*`); unused `IconCircleCheck` in routing; admin token-namespace drift; `?reset=success` flash unused on /login.

## Sprint health
- **Deployable:** yes.
- **Tests pass:** `tsc --noEmit` clean for both apps. (No unit-test suite for these apps; no dev server run per audit constraints.)
- **Hard rules violated:** none.

## ✋ STOP — awaiting Beat's "go sprint 2".
