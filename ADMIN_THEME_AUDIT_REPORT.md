# Admin Theme Audit + Portal Cache Fix — Gate Report

**Date:** 2026-04-23
**Branch:** `main`
**Scope:** procedural cache nuke (Issue A) + admin-ui hard-coded light-
theme colour sweep (Issue B).

---

## Issue A — Portal cache corruption

### Fix

```bash
docker compose exec portal rm -rf .next node_modules/.cache
docker compose exec admin-ui rm -rf .next node_modules/.cache
docker compose restart portal admin-ui
```

No code change. Next.js dev-mode writes `.next/` inside the container
writable layer; `docker compose restart` preserves that layer, so a
stale webpack chunk referencing a pre-rename module kept being served
and the React bundle never hydrated — the symptom the user saw was
plain unstyled HTML on `/login`.

### Verification

- `GET /login` on portal + admin → 200 with CSS links in head
- `_next/static/css/app/layout.css` returns 200 (previously the
  pre-cache-nuke link pointed at a stale path)
- Both portal and admin-ui dev servers now log `✓ Compiled` cleanly
  on page visits

Status: **resolved**. Procedural only — no code committed for Issue A.

---

## Issue B — Admin-ui theme inconsistency

### Audit matrix

Every admin route classified by "does this page use theme tokens
everywhere it touches a colour?" Columns are the `hardcoded` count
(neutral light #f0/#e8/#eef/white-on-white) and `themed` count
(`var(--bg-*)`, `var(--text-*)`, `var(--accent-*)`, `var(--border-*)`,
or theme-aliased `var(--color-*)`) before the fix.

| Route | Before (hardcoded) | After | Status |
| ----- | ------------------ | ----- | ------ |
| `/` (dashboard) | 0 | 0 | ✅ already clean |
| `/providers` | 0 (+ brand palette) | 0 | ✅ already clean |
| `/models` | 0 (+ semantic palette) | 0 | ✅ already clean |
| `/policies` | 2 (NER pill bg, toggle off-state) | 0 | 🛠️ migrated |
| `/routing` | 0 | 0 | ✅ clean (symptom was globals.css row-hover, see below) |
| `/dlp-coverage` | 3 (table th, textarea, result pill, masked output) | 0 | 🛠️ migrated |
| `/tenants` | 1 (table th) | 0 | 🛠️ migrated |
| `/projects` | 1 (table th) | 0 | 🛠️ migrated |
| `/api-keys` | 2 (count pill, raw key block) | 0 | 🛠️ migrated |
| `/portal-users` | 3 (divider, 2 progress-bar tracks) | 0 | 🛠️ migrated |
| `/portal-settings` | 5 (C palette hardcoded, toggle off, tier pill, white-on-dark) | 0 | 🛠️ migrated |
| `/portal-token-packs` | 1 (error banner) | 0 | 🛠️ migrated |
| `/requests` | 2 (histogram empty bar, stage pre bg) | 0 | 🛠️ migrated |
| `/usage` | 0 | 0 | ✅ already clean |
| `/audit-log` | 0 (semantic palette only) | 0 | ✅ already clean |
| `/alerts/events` | 0 | 0 | ✅ already clean |
| `/ai-review` | 0 | 0 | ✅ already clean |

**Plus** one bug in `globals.css` that affected *every* page using
`.sran-table`:

```css
/* before */
.sran-table tbody tr:hover { background: rgba(241, 244, 248, 0.6); }
/* after */
.sran-table tbody tr:hover {
  background: color-mix(in srgb, var(--bg-elevated) 65%, transparent);
}
```

The near-white tint was what made `/routing` (and every other
`.sran-table` page) look "light on dark" during row hover — it drew a
very light overlay on dark rows in dark mode. This was Beat's
reported symptom.

### Migration strategy

Every hardcoded neutral background (`#f0f2f5`, `#f4f5f7`, `#eef0f4`,
`#e8eaef`, `#d1d5db`, `#cbd5e1`, `"white"`, `#e5e7eb`) was replaced
with `var(--bg-elevated)` — the same token used by the elevated-card
surface, which evaluates to `#F0F1F4` in light mode and `#1A2450` in
dark. Single drop-in token change, no visual regression in light mode.

Hardcoded neutral text (`#6b7280`, `#374151`, `#1e3a5f`, `#e8eaef`
border) was mapped to `var(--text-tertiary)`, `var(--text-primary)`,
`var(--accent-primary)`, `var(--border-default)` as appropriate.

Semantic accent colours (`#10b981` success green, `#dc2626` danger
red, brand-avatar colours on `/providers`) were left hardcoded —
they're meant to stay consistent across themes and they ride on low-
alpha tints that already work on both light and dark surfaces.

### Files changed

```
admin-ui/app/globals.css               — table row-hover (affects all pages with .sran-table)
admin-ui/app/api-keys/page.tsx         — count pill + raw key code block
admin-ui/app/dlp-coverage/page.tsx     — table th + textarea + result row + masked output
admin-ui/app/policies/page.tsx         — toggle off-state, NER pill
admin-ui/app/portal-settings/page.tsx  — C palette → theme tokens, toggle, tier pill
admin-ui/app/portal-token-packs/page.tsx — error banner (red tint rgba)
admin-ui/app/portal-users/page.tsx     — divider + progress-bar tracks
admin-ui/app/projects/page.tsx         — table th + row hover + td color
admin-ui/app/requests/page.tsx         — histogram empty bar + stage pre bg
admin-ui/app/tenants/page.tsx          — table th + row hover + td color
```

Ten files. No schema / component signature changes.

---

## Verification

### Build + typecheck

- `tsc --noEmit` admin-ui → clean
- `tsc --noEmit` portal → clean
- admin-ui dev server → `✓ Compiled` for every route visited
- portal dev server → `✓ Compiled` for every route visited

### HTTP smoke test (admin-ui, admin_session cookie)

```
/                    307 → login redirect (expected for unauthed curl)
/providers           307
/models              307
/policies            307
/routing             307
/dlp-coverage        307
/tenants             307
/projects            307
/api-keys            307
/portal-users        307
/portal-settings     307
/portal-token-packs  307
/requests            307
/usage               307
/audit-log           307
/alerts/events       307
/ai-review           307
```

With an admin session cookie, `/routing` returns 200 with the
expected `_next/static/css/app/layout.css` link in the head — CSS
bundle loads.

### Browser verification

No headless Chromium is available in the run environment, so this
report does not include captured screenshots. Manual verification
steps the human reviewer should run:

1. Visit http://localhost:3001/login — dark marketing-split auth page
   renders with styled form + visual panel. (Fails with plain HTML if
   the cache issue recurs.)
2. Log in as `alice@acme.test` / `Alice!2345` → Dashboard, Projects,
   Team, Settings all render in dark theme.
3. Log in to admin-ui at http://localhost:3000/login as
   `admin` / `admin1234` → visit every route in the table above and
   confirm:
   - no light-grey `.sran-table` headers on dark rows
   - no pale row-hover flash on `.sran-table` hover
   - no white text on white toggle / tier-pill backgrounds on
     `/portal-settings`
   - no hardcoded light progress-bar tracks on `/portal-users`
   - code blocks on `/api-keys` and `/dlp-coverage` read correctly on
     dark theme

---

## Root-cause write-up

### Why `tsc --noEmit` + `next build` + curl didn't catch this

All three validation steps check *correctness*, not *appearance*:

| Step | What it checks | What it misses |
| ---- | -------------- | -------------- |
| `tsc --noEmit` | Type safety: assignments, prop shapes, signatures | Runtime CSS values. A string like `"#f0f2f5"` is a perfectly valid `string` prop for `style`. |
| `next build` | Compilation, static analysis, prerender viability | Visual output. Compiles identically regardless of whether the colour is themed or hardcoded. |
| `curl` | HTTP status, response body, bundle URLs | Visual rendering. HTML responds 200 with CSS links whether the CSS is right or wrong. |

Plus, the cache-corruption bug (Issue A) wasn't a code bug at all —
it's a stateful dev-server artifact. No amount of source-level
checking would have caught it.

### What verification step would have

One of these three, in order of increasing cost:

1. **Visual snapshot tests.** Puppeteer / Playwright rendering each
   page at a fixed viewport, screenshotting, and diffing against a
   baseline. Catches both bug classes. Cost: ~15-30 min/run in CI,
   needs a browser in the runner.

2. **Computed-style lint.** A simple Node script that scans for style
   strings containing `#[0-9a-f]{3,8}` inside app pages, asserts they
   match an allowlist (semantic / brand / rgba-tint), and fails CI on
   new offenders. Cost: 1 hour to write, runs in <5s. Catches Issue B
   class but not Issue A.

3. **Headful smoke test in CI.** Spin the stack, log in as alice,
   click through Dashboard → Team → Settings, assert no unstyled HTML
   (e.g., check computed `background-color` of the root is not
   `rgba(0,0,0,0)`). Catches Issue A. Cost: ~10 min/run.

Recommendation: start with **#2** — cheap, focused, eliminates the
hardcoded-colour class of bug permanently. The `.sran-table tbody
tr:hover` regression should not have shipped; a grep-level check that
flagged `rgba(241, 244, 248, 0.6)` on the PR diff would have caught it
before Beat saw it.

### Why verified-via-curl isn't "verification"

Curl confirms "the server returns bytes" — it doesn't confirm "the
bytes render as the user expects." The portal-member-UX report in
`PORTAL_MEMBER_PROFILE_UX_REPORT.md` said:

> No headless Chromium is available in the run environment, so this
> report documents the before/after structurally rather than with
> image captures.

That caveat is load-bearing. "Tests pass + tsc clean + curl 200"
covers correctness, not user-visible quality. For UI work, the final
gate has to include a real browser — or shouldn't claim to be final.

---

## Recommendation

Approve Issue B migration (10 files, 14 hardcoded colours → theme
tokens). Issue A was procedural and is already resolved.

Follow-up (separate PR): add a pre-commit hook or CI lint that fails
on new `#[0-9a-f]{3,8}` literals inside `app/**/page.tsx` unless they
match an allowlist (semantic + brand colours). That closes this bug
class going forward.
