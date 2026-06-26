# Sprint 3 Report — Tier 2 Systemic Sweeps

> Branch: `sprint-3-ui` (off `sprint-2-ui`). 7 task commits + this report. **Both apps `tsc --noEmit` clean. Deployable.**

## Changes made

### Day 1–2 — F1 / F2 medium-traffic admin
- **F1 (icon apply):** the emoji/glyph→icon goal is already at **verify=0** on all 9 listed pages (achieved in Sprint 1 F1 + Sprint 2 sweep — re-verified at sprint start). No remaining emoji work. **Decision:** did NOT do a blanket migration of the remaining *bespoke* inline SVGs (logos, donuts, sparklines, per-site search magnifiers with their own stroke/color) — that's regression-risky (Icon.tsx is stroke 1.8 vs many inline 2) for no user-visible gain. Documented; the inline SVGs render correctly and consistently.
- **F2 (token sweep)** `91…c1`: light-only color literals → semantic tokens for dark-theme correctness in `chat-knowledge`, `chat-knowledge/[id]`, `chat-users`, `portal-settings`, plus `components/dlp/CustomDetectorForm.tsx` and `AuditLogTimeline.tsx`. Brand gradients (avatar monogram) and multi-color provider/category palettes intentionally kept.
- **chat-analytics recharts axis** (was a backlog item): **RESOLVED** — resolve `--text-muted` via `getComputedStyle` in a `useEffect` and pass it to the axis tick `fill` (a CSS `var()` can't resolve in an SVG `fill` attribute, which is why the hardcoded rgba existed).

### Day 3 — motion + reduced-motion `…c2`
- `/models` `.mr-icon-pulse/rock/breathe` and `/policies`' global `.dlp-icon-pulse/swing/glow` → **one-shot on mount** (removed `infinite`).
- Added a global `@media (prefers-reduced-motion: reduce)` block to `admin-ui/app/globals.css` (portal already shipped one). **This also satisfies the routing-spin guard** — the instruction's `routing:1070-1073` is actually the responsive-grid media query; the refresh spin is now covered globally.

### Day 4 — portal medium-traffic
- **/pricing** `…c3`: per-1M effective rate under each price ($10 / $8 / $6 per 1M); the featured badge text → **"BEST VALUE"** (also dropped its stray `★`); `.reveal` staged entrance on the three cards; FAQ converted to `<details>` accordions (`.faq-item`).
- **/oauth-complete** `…c4`: brand `<Logo>` above the spinner (loading + error + Suspense fallback); error state gets an `AlertCircle` icon tile; replaced `text-gray-500`/`text-brand-*`/`bg-bg-page` with theme tokens.

### Day 5 — portal app
- **/usage** `…c5`: truthful counts — footer was "X of {full backend total}" though only ~200 rows are loaded → "X of N recent requests · all-time"; table subtitle "last 100" → "Your most recent requests".
- **/team** `…c6`: RoleSelector — removed the disabled "Admin" ("Coming soon") option; added Esc + click-outside close (was mouse-leave only).
- **/settings** `…c7`: linked accounts now render only the **actually-linked** provider (read-only); the section is hidden for email/password accounts instead of showing non-functional "Coming soon" connect rows.

## After-state (verified)
- admin `globals.css` contains the `prefers-reduced-motion` block; no `infinite` left on the models/dlp icon animations.
- `/oauth-complete` has zero `text-gray-500`.
- `/pricing` shows "BEST VALUE" + 5 `.faq-item` accordions + per-1M rates.
- `npx tsc --noEmit` clean for **both** apps.

## Decisions taken during execution
1. **F1 = verify, not churn** (see Day 1–2) — emoji→icon already complete; skipped a risky bespoke-SVG migration.
2. **"BEST VALUE" on Pro** per instruction, even though per-1M Business is cheapest ($6/M) — it reads as the recommended-tier label; the honest per-1M rates sit right beside it so nothing is misleading.
3. **routing spin guard** delivered via the global reduced-motion block (line ref in the plan pointed at the responsive grid).
4. **team RoleSelector** now has a single live option (Member) after dropping the Admin stub — minimal but per instruction; re-add Admin when the backend supports it.
5. **settings linked accounts** hidden for non-OAuth accounts (rather than a static placeholder list) — cleanest honest state given no connect/disconnect backend.
6. **dead `.sv-*` CSS** (Beat: "do if convenient") — **left in backlog**; it wasn't on the critical path and removing it cleanly needs a careful check of signup.css usage. Not done this sprint.

## Hard-rules compliance
- Keep-As-Is admin pages touched only by allowed Foundation sweeps (F2 tokens / motion); no logic/API/routing changes; no new deps; SRAN Chat untouched. Commit per task; tsc clean throughout.

## Backlog updates
- **RESOLVED:** chat-analytics recharts axis colors (getComputedStyle).
- **Still open:** dead `.sv-divider/.sv-socials/.sv-social` CSS; ESLint toolchain install; admin token-namespace drift (`--color-*` vs `--bg/--text`); `?reset=success` flash unused; routing `IconCircleCheck` unused.

## Sprint health
- **Deployable:** yes. **Tests:** `tsc --noEmit` clean both apps. **Hard rules violated:** none.

## ✋ STOP — awaiting Beat's "go sprint 4".
