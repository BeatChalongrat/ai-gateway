# Phase 4 — Cross-Cutting / Systemic Findings

> Patterns observed across ≥3 pages during the per-page audit. These are the findings that a per-page sprint will keep re-encountering — fixing them once at the system level is cheaper than 45 times at the page level. Ordered by impact.

---

## S1. Emoji / glyph used as a UI icon (the #1 recurring issue)

Both apps have a real icon discipline — admin uses hand-rolled inline `<svg>`, portal uses `lucide-react` — yet **emoji and Unicode glyphs leak in as icons on ~18 pages.** Because admin has **no central icon module** (every icon is authored inline at its call site), there is nothing stopping a contributor reaching for `"💎"` instead.

**Admin — emoji (true anti-pattern):** `/portal-users` `💰⏸▶👁👥` (`portal-users/page.tsx:344,474-481,681-687`) · `/audit-log` `🛡` empty icon (`audit-log/page.tsx:1217`, despite a shield SVG existing at `:139-141`) · `/portal-token-packs` `💎` (`:223`) · `/search-settings` `✓`/`✗` in toasts (`:226,228`) · `/chat-conversations/[id]` `⚠️` DLP banner (`[id]/page.tsx:115`) · `/providers` `⬡`/`🔍` empty states (`:674,678`).

**Admin — glyph-as-icon (lower severity, same root cause):** `/models` `◇` (`:331`) · `/policies` `✓`/`◆` (`:175,270,410`) · `/routing` `⌨` emptyIcon (`:917`) · `/requests` `↗`/`✕` (`:`) · `/alerts/events` `emptyIcon="!"` (`:109`) · `/ai-review` `&#9881;`/`▲▼` (`:490`) · `/portal-token-packs` `★` (`:247`) · `/search-settings` `⋮` kebab (`:348`) · `/tenants`,`/projects`,`/api-keys` clear-search `✕` / `▣▢⚷` empty glyphs.

**Portal — glyph-as-icon:** landing `→`/`✓` (`Hero.tsx:656,682-690`, `CTASection.tsx:42`) · `/pricing` `✓` feature ticks (`marketing.css:1750-1754`) · `/billing` `×` dismiss (`billing/page.tsx:151-164`) · `/dashboard` mixes lucide arrows with literal `→` (`:168,434`).

**Impact:** inconsistent weight/baseline/color vs the real icon set; emoji render differently per-OS; several are on the default **dark** admin theme where colored emoji clash. **Fix:** see Foundation F1 (extract an admin icon module + a one-pass glyph→SVG sweep).

---

## S2. Hardcoded hex instead of semantic theme tokens → dark-theme contrast risk

The single biggest *correctness-adjacent* visual risk, because **admin defaults to dark** and many pages were clearly tuned in light. Pages scatter light-only literals (`#f3f4f6`, `#f8f9fa`, `#92400e`, `#f0f2f5`, `#111827`, `#1e3a5f`, `rgb(74,222,128)`, `rgba(248,113,113,…)`) where a `--border-default`/`--text-*`/`--accent-*`/`--color-status-*` token exists.

Seen on: `/policies` (`:330,434,573`) · `/tenants` (`:290`) · `/api-keys` (`:354`) · `/portal-users` (`:96,500,503`) · `/dlp-coverage` (`:36-64,222,294,310`) · `/chat-analytics` axis ticks (`:279,283`) · `/dlp-detectors` (`:30-32,124`) · `/chat-knowledge` StatusPill (`:374-380`) · `/chat-knowledge/[id]` danger literals (`:339-340,377-381,406`) · `/chat-users` avatar/pill (`:198,296-297`) · `/portal-settings` button `#0f172a` (`:567,645`). Portal dashboard hero muted text `rgba(255,255,255,0.5)` is borderline on the navy gradient (`:297-305`).

**Fix:** Foundation F2 — a token-sweep pass replacing literals with the existing semantic tokens; both globals.css files already define a complete dual-theme set, so this is mechanical, not a redesign.

---

## S3. Native browser dialogs (`window.confirm` / `prompt` / `alert`) amid a custom dialog system

Both apps ship polished `Modal`/`ConfirmModal`/`ConfirmDialog`/`Toast` components, yet several pages still reach for native browser dialogs for exactly the flows those components exist for — jarring, off-brand, theme-blind, and often blocking.

Admin: `/dlp-detectors` `prompt`+`confirm`+`alert` (`:69,78,83-85,90`) · `/requests` `window.prompt` for secure-trace reason (`:305`) · `/chat-knowledge/[id]` `window.confirm` ×2 (`:72,100`).
Portal: `/projects` `alert()` for rename/delete errors (`:119,133`) · `/projects/[id]` `alert()` for revoke (`:173`) · `/settings` `confirm()` for avatar removal (`:146`).

**Fix:** Foundation F4 — replace every native dialog with the existing shared components; lint-ban `window.confirm/prompt/alert` going forward.

---

## S4. Spinner-only loading where a content-shaped skeleton belongs

Admin already has the better habit (most pages use `SkeletonTable`/shaped skeletons). **Portal `(app)` is the offender — almost every authenticated page uses a generic centered `PageSpinner`** for a layout whose shape is fully predictable.

Portal: `/dashboard` (`:81`), `/projects` (`:168`), `/projects/[id]` (`:209`), `/usage` (`:131`), `/team` (`:153`), `/billing` (`:111`), `/settings` (`:174`) — all `PageSpinner`. Plus text-only loaders: portal `InviteSection` "Loading invite link…" (`:306-322`), `ProjectMembersSection` "Loading members…" (`:216-225`).
Admin stragglers: `/` volume chart + top-models "Loading…" (`page.tsx:731-734,864`), `/dlp-detectors` (`:126`), `/ai-review` (`:674`), `/search-settings` (`:313-315`), `/usage` system table, `/requests` detail panel (`:552`).

**Fix:** Foundation F5 — build 3–4 skeleton **preset** components in portal (hero, stat-card-row, table, card-grid) mirroring the existing `.skeleton` shimmer, then swap `PageSpinner` per page.

---

## S5. Fabricated / placeholder data shown as if it were real (TRUST issue — needs product sign-off)

Spans both apps and is the most damaging class of finding for a product that sells **observability + security**. These are display-layer issues but a few brush against logic, so flag for a product decision rather than a silent UI swap.

- Admin `/routing`: "Success rate" tile hardcoded to **`"99.2%"`** whenever traffic exists (`routing/page.tsx:481-484,538`).
- Portal `/usage`: "Requests over time" chart is a **deterministic fake** — bar heights unrelated to data, comment says "Placeholder until a chart library is wired up" (`UsageChart.tsx:19-21,100`) — on the page literally named Usage.
- Portal `/projects` + `/projects/[id]`: per-project request/token stats are **always 0 / account-wide totals mislabeled as per-project** (`projects/page.tsx:60-61`, `[id]/page.tsx:108-112`).
- Portal `/dashboard`: "Next Reset" HeroStat permanently `"—"` (`:318`).
- Admin `/models`: capability/context metadata covers only ~16 legacy models; everything else shows `—` (`models/page.tsx:32-55`).

**Fix:** Foundation F6 — an "honest metrics" pass: wire to real data, label "Preview", or hide. **Decision needed from Beat** on each (wire vs hide vs label).

---

## S6. Two parallel design systems that have drifted (cross-app divergence)

The two apps share a brand but are built on divergent stacks, and each carries internal drift too. Not a bug, but it doubles maintenance and guarantees the two surfaces feel subtly different.

| Axis | Admin | Portal |
|---|---|---|
| Tailwind | **v4** (CSS `@theme`) | **v3** (`tailwind.config.ts`) |
| Icons | inline `<svg>` (no module) | `lucide-react` |
| Styling idiom | `sran-*` CSS classes **+ heavy inline styles** | utilities + components + CSS-var motion |
| Default theme | dark | light |
| Tokens | duplicated `--bg/--text/--accent` block | duplicated near-identical block |
| Buttons | `.sran-btn-*` | `.btn-premium` / `<Button>` |
| Skeleton | `.sran-skeleton` | `.skeleton` |
| Toast | `.sran-toast` | `<Toast>` |

**Internal drift, admin:** a **token-namespace split** — legacy `--color-*` (used by `/chat-users`, `/chat-analytics`) vs the newer `--bg-*/--text-*/--accent-*` (used by `/portal-settings`, most pages). Both resolve, but it signals two token generations. Plus **page-local `Outfit` font imports** on `/policies` (`:4,12`) and `/requests` (`:13`) that diverge from the app font stack. Plus several large pages (`/`, `/requests`, `/login`) built almost entirely from inline styles, bypassing `sran-*`.

**Fix:** Foundation F3 — pick one token namespace for admin and alias the other; document the cross-app divergence as intentional-for-now (full unification is a separate, larger track, not this sprint).

---

## S7. Missing "filtered-empty" and weak empty states

Many list pages handle *no-data-at-all* but render a **blank table/grid when a filter matches nothing** — the user can't tell if it's loading, broken, or genuinely empty. `/api-keys` and `/chat-knowledge` do this right (separate "no match" + clear-filters) and are the reference pattern.

Missing filtered-empty: admin `/tenants` (`:340-431`), `/projects` (`:323-329`). Bare-text empties (not the shared component): admin `/usage` "No scan runs yet." (`:259-261`), `/dlp-detectors` "No detectors." (`:128`); portal `/team` "No members match these filters." (`:269-276`).

**Fix:** small, per-page (Tier 3), but call out the `/api-keys` pattern as the house standard so it's applied consistently.

---

## S8. Perpetual motion overload + inconsistent reduced-motion guards

Portal has a disciplined global `prefers-reduced-motion` kill-switch (`portal/app/globals.css:555-564`) and the admin dashboard + dlp pages have their own. But:
- **Too much perpetual motion** on data tables: `/models` runs 3 infinite icon animations (pulse/rock/breathe) + per-row stagger (`:131-136`); `/policies` runs `dlp-icon-pulse/swing/glow` infinitely (`:223-225`). Distracting on dense data.
- **Inconsistent guards:** `/routing` has no reduced-motion guard on its spin (`:1070-1073`); several admin pages animate without one.

**Fix:** Tier 2 — convert perpetual table-icon animations to one-shot-on-mount; ensure every admin page inherits a reduced-motion guard (add one global block to admin globals.css like portal has).

---

## S9. Component duplication / inline-style proliferation

- `/chat-knowledge/[id]` **re-implements `StatCard`** locally (`:480-505`) instead of `components/ui/StatCard`.
- `/usage` (admin) hand-rolls a `<table>` instead of `.sran-table` (`:264-293`).
- `/chat-conversations` duplicates `.sran-input` styling in a local `inputStyle` object (`:104,260-269`).
- `/ai-review` repeats identical `<td>` inline border/padding dozens of times (`:657-694`).
- Portal `ProjectCard`, dashboard, `[id]` set one-off inline font sizes (11/12/13/14…) rather than a shared scale.

**Fix:** opportunistic during per-page work; reuse the shared primitives that already exist.

---

## S10. Accessibility gaps (targeted, not systemic-bad)

The baseline is good (ARIA roles on menus/toggles, `role="alert"/"status"`, reduced-motion, focus rings in globals.css). Specific failures:
- **Contrast fail:** portal `/invite/[code]` "Sign in instead" CTA is **white-on-white** (`:122-137`) — effectively invisible (P0).
- **Focus not keyboard-mirrored:** admin `/providers` + `/models` card hover via inline `onMouseEnter/Leave` with no `:focus-visible` parity (`:378-387`, `:570-571`); admin `/login` inputs use inline focus handlers, no `:focus-visible` ring + no `aria-invalid`/`required` (`:128-151`).
- **Low-contrast secondary text:** portal `/oauth-complete` uses `text-gray-500` as the only secondary treatment (`:55,123`); dashboard hero muted text borderline (`:297-305`).
- **Tiny text floor:** admin `/chat-conversations/[id]` + `/ai-review` drop to 0.6–0.65rem / 10–11px (`[id]/page.tsx:209`; `ai-review:561-588`).

---

## S11. Responsive gaps

Mostly handled (admin sidebar icon-rail/drawer; tables guarded with `minWidth` + horizontal scroll; portal mobile drawer). Specific breaks:
- Admin `/requests`: detail panel is `display:none` below 1100px (`:78`) — **row selection produces no visible result** on narrow viewports (P0-ish for that page).
- Wide multi-column tables (`/chat-conversations` `minWidth:920`, `/chat-knowledge` `minWidth:920`) force horizontal scroll — acceptable but worth a column-priority pass eventually.

---

## S12. Brand identity — assessment

**Current personality:** enterprise security / developer-infrastructure — deep royal-navy sidebar (signature, both themes both apps), restrained accent (indigo `#6366F1`), mono numerics, "cybersecurity" sidebar scanline texture, shield/lock iconography. This is **coherent and on-target** for an AI gateway sold on DLP + observability — closest to Vercel/PlanetScale/Datadog, deliberately *not* consumer-playful.

**Where the brand is strongest (protect):** admin `/` dashboard, `/audit-log`, `/tenants`, `/projects`; portal landing `/`. These define the house style — use them as the north star for everything else.

**Where the brand thins out:** the marketing `/security` and `/docs` pages (prose-only, no visual system) under-sell exactly the capabilities the brand is built on; the portal `(app)` spinner-loading + fake-chart moments read as unfinished against the polished landing. The auth recovery pages (`/forgot-password`, `/reset-password`) drop the brand entirely (unstyled).

---

## Systemic → Foundation mapping (feeds Phase 5)

| Systemic finding | Foundation item | Blocks |
|---|---|---|
| S1 emoji/glyph icons | **F1** admin icon module + glyph sweep | per-page icon fixes |
| S2 hardcoded hex | **F2** token sweep | dark-theme correctness everywhere |
| S6 token namespace split | **F3** token consolidation | F2, all admin pages |
| S3 native dialogs | **F4** dialog replacement + lint ban | dlp-detectors, several portal pages |
| S4 spinner loading | **F5** portal skeleton presets | all portal (app) pages |
| S5 fake metrics | **F6** honest-metrics pass (needs decision) | routing, usage, projects, dashboard |
| S10 invite contrast / auth CSS | **F7** P0 bug-fix bundle | forgot/reset/invite |
