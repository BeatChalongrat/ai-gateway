# Phase 3 — Per-Page Verdicts

> SRAN AI Gateway UI Audit · 45 routes scored on 8 dimensions (A Hierarchy · B Typography · C Spacing · D Color/Contrast · E Component Sophistication · F State Coverage · G Density · H Polish/Motion).
> Verdict bands: **36–40 🟢 Keep As-Is · 28–35 🟡 Minor Polish · 20–27 🟠 Major Refactor · <20 🔴 Rebuild.**
> Calibration: Admin + Portal `(app)` = dashboard/admin standard (Linear/Vercel/Stripe/PlanetScale). Portal `(marketing)` = marketing standard (Vercel/Linear/Resend). Auth = auth-screen standard. Legal = long-form standard. Stub features scored on **visual** quality only (functional completeness is the separate Functional Audit's job).
> Every finding carries `file:line` evidence. "Strengths (Keep As-Is)" = **ห้ามแตะ**.

---

## Score table (all 45)

### Gateway Admin (`:3000`) — avg **32.5/40**
| Route | Traffic | Score | Verdict |
|---|---|---|---|
| `/` | High | 39 | 🟢 Keep As-Is |
| `/tenants` | Med | 37 | 🟢 Keep As-Is |
| `/projects` | Med | 37 | 🟢 Keep As-Is |
| `/audit-log` | Med | 37 | 🟢 Keep As-Is |
| `/routing` | High | 36 | 🟢 Keep As-Is |
| `/api-keys` | Med | 36 | 🟢 Keep As-Is |
| `/requests` | High | 35 | 🟡 Minor Polish |
| `/chat-analytics` | Med | 34 | 🟡 Minor Polish |
| `/search-settings` | Low | 34 | 🟡 Minor Polish |
| `/chat-users` | Med | 34 | 🟡 Minor Polish |
| `/providers` | Med | 33 | 🟡 Minor Polish |
| `/models` | Med | 33 | 🟡 Minor Polish |
| `/login` | Med | 33 | 🟡 Minor Polish |
| `/dlp-coverage` | Low | 33 | 🟡 Minor Polish |
| `/policies` | Med | 32 | 🟡 Minor Polish |
| `/portal-settings` | Low | 31 | 🟡 Minor Polish |
| `/chat-knowledge` | Low | 31 | 🟡 Minor Polish |
| `/portal-users` | Med | 30 | 🟡 Minor Polish |
| `/usage` | Med | 30 | 🟡 Minor Polish |
| `/portal-token-packs` | Low | 30 | 🟡 Minor Polish |
| `/chat-knowledge/[id]` | Low | 30 | 🟡 Minor Polish |
| `/chat-conversations` | Med | 29 | 🟡 Minor Polish |
| `/ai-review` | Low | 29 | 🟡 Minor Polish |
| `/chat-conversations/[id]` | Med | 28 | 🟡 Minor Polish |
| `/alerts/events` | Low | 28 | 🟡 Minor Polish |
| `/dlp-detectors` | Low | 26 | 🟠 Major Refactor |

### Portal (`:3001`) — avg **30.4/40**
| Route | Group | Traffic | Score | Verdict |
|---|---|---|---|---|
| `/` | marketing | High | 38 | 🟢 Keep As-Is |
| `/login` | auth | High | 35 | 🟡 Minor Polish |
| `/signup` | auth | High | 35 | 🟡 Minor Polish |
| `/dashboard` | app | High | 34 | 🟡 Minor Polish |
| `/projects/[id]` | app | High | 34 | 🟡 Minor Polish |
| `/billing` | app | High | 34 | 🟡 Minor Polish |
| `/team` | app | Med | 32 | 🟡 Minor Polish |
| `/pricing` | marketing | Med | 31 | 🟡 Minor Polish |
| `/projects` | app | High | 31 | 🟡 Minor Polish |
| `/settings` | app | Med | 31 | 🟡 Minor Polish |
| `/invite/[code]` | auth | Med | 30 | 🟡 Minor Polish |
| `/usage` | app | Med | 30 | 🟡 Minor Polish |
| `/privacy` | marketing | Low | 29 | 🟡 Minor Polish |
| `/terms` | marketing | Low | 29 | 🟡 Minor Polish |
| `/forgot-password` | auth | Med | 27 | 🟠 Major Refactor |
| `/reset-password` | auth | Med | 27 | 🟠 Major Refactor |
| `/security` | marketing | Med | 26 | 🟠 Major Refactor |
| `/docs` | marketing | Med | 22 | 🟠 Major Refactor |
| `/oauth-complete` | auth | Low | 22 | 🟠 Major Refactor |

**Distribution (45):** 🟢 Keep As-Is **7** · 🟡 Minor Polish **32** · 🟠 Major Refactor **6** · 🔴 Rebuild **0**. Overall avg **31.6/40**.

---

# Gateway Admin — page blocks

### `/` — Platform Overview Dashboard
**Project**: Gateway Admin  **Traffic**: High  **Total Score**: 39/40  **Verdict**: 🟢 Keep As-Is

**Per-dimension**: A 5/5 · B 5/5 · C 5/5 · D 5/5 · E 5/5 · F 4/5 · G 5/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] KPI card is genuinely sophisticated: top accent gradient line, animated count-up, baseline-aligned delta chip, SVG sparkline pinned to card bottom via `marginTop:"auto"` so it never collides with the number — `page.tsx:357-446`. The `useAnimatedNumber` rAF easing (`page.tsx:147-173`) is Linear-grade.
- [F] Every async section carries independent loading/error/empty triples and the empty states give real guidance + a CTA, not "No data": volume-chart empty shows SVG + "Send a request to `/v1/chat/completions`" (`page.tsx:739-769`); top-models empty (`page.tsx:867-896`); DLP per-panel empties (`page.tsx:1620-1623,1652-1655`). Per-section `SectionState` union prevents one slow call pinning the rest (`page.tsx:496-567`).
- [D] Restrained, semantic color: theme tokens everywhere, hardcoded provider brand hex isolated + justified for recharts (`page.tsx:96-124`). Delta chip flips good/bad via `invertDeltaColor` for latency/spend (`page.tsx:278-294,651,666`).
- [H] Full motion system with staggered `fadeInUp`/`fadeInRight`/`growWidth`/`slideInRow` + live-dot pulse + complete `prefers-reduced-motion` kill-switch (`page.tsx:1678-1720`).
- [G] Recent-requests table is dense + crafted: tabular-nums, HTTP-class tiered status pills, row tinting for 4xx/5xx, new-row diffing via `seenRef` (`page.tsx:959-1196`).

**Issues (observable):**
- [F] Volume chart + Top-models use centered "Loading…" text rather than a shaped skeleton, while the DLP tiles right below DO use shaped skeletons (`page.tsx:731-734,864,1307-1316`) — inconsistent loading vocabulary on one page.
- [C/B] Entire page is inline-styled, bypassing the `sran-*` class system; magic px font sizes (10.5/11/13.5/23/28) duplicated across components — visually fine, but a consistency risk vs `routing/page.tsx`.

**Recommendations (no logic change):**
- 🟡 [P1] Swap the volume-chart + top-models "Loading…" text for shaped skeletons (bar block + 5 placeholder rows) to match the DLP-tile treatment.
- 🟢 [P2] Lift repeated font-size/spacing constants into the shared token set so the dashboard tracks the design system like the routing page does.

**Anti-patterns flagged:** spinner/text-only loading where a shaped skeleton would fit (volume chart, top models).

---

### `/requests` — Gateway Request Audit + Detail/Trace
**Project**: Gateway Admin  **Traffic**: High  **Total Score**: 35/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 5/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 5/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Master–detail split with a sticky scrollable detail panel is the right audit-log pattern (`requests/page.tsx:494-548`). The 4-stage "Safe Trace" with per-stage source badges (decrypted / in-memory / placeholder) plus a separately gated "Admin Secure Trace" reveal is genuinely sophisticated, security-aware UI (`requests/page.tsx:601-698`).
- [F] Top-level states are all real components: `SkeletonTable`, `ErrorState`+retry, `EmptyState` (`requests/page.tsx:487-492`); detail panel adds its own select/loading/fail states (`requests/page.tsx:549-552,730`).
- [G] Dense scannable rows: status dot, mono timestamp, ellipsized model, provider, policy pill, right-aligned latency — plus a 48-bucket 24h ok/err sparkline header + total/allowed/masked/blocked chips (`requests/page.tsx:83-133,426-444,503-530`).
- [H] Considered micro-interactions: focus ring + bg shift on search, row hover tint, CSV export with toast (`requests/page.tsx:454-457,331-386`).

**Issues (observable):**
- [F/responsive] Below 1100px the detail panel is fully hidden (`.rq-detail-panel{display:none}`, `requests/page.tsx:78`) — clicking a row on a narrow viewport produces no visible result; request detail is unreachable rather than stacked.
- [Stub] "Last 24h" button has no `onClick` (`requests/page.tsx:399`) — feature นี้เป็น stub, score ตาม visual quality ไม่ใช่ functional completeness (Functional Audit แยกจัดการ).
- [D] Many hardcoded hex status colors (`#059669`,`#d97706`,`#dc2626`,`rgba(59,108,246…)`) alongside the `T`-token system (`requests/page.tsx:118,126,459,568-569`).
- [B] Page loads its own `Outfit` webfont (`requests/page.tsx:13,389`), diverging from the admin font stack.
- [F] In-detail loading is a plain "Loading…" string (`requests/page.tsx:552`) where a field-grid skeleton would read better.
- [E] Secure-trace reason capture uses `window.prompt` (`requests/page.tsx:305`) — unstyled vs the rest of the page.

**Recommendations (no logic change):**
- 🔴 [P0] On <1100px, stack the detail panel below the list (or open as a sheet) instead of `display:none`.
- 🟡 [P1] Replace `window.prompt` for the secure-trace reason with the existing `Modal`.
- 🟡 [P1] Centralize status hex into shared accent tokens (`var(--accent-success/danger/...)`).
- 🟢 [P2] Swap detail-panel "Loading…" for a field-grid skeleton; replace `↗`/`✕` glyphs with inline SVG.

**Anti-patterns flagged:** glyph-as-icon (`↗` empty state, `✕` clear button); text-only loading in detail panel; hardcoded status hex alongside tokens; native `window.prompt`.

---

### `/routing` — Routing Stats + Keyword Rule CRUD
**Project**: Gateway Admin  **Traffic**: High  **Total Score**: 36/40  **Verdict**: 🟢 Keep As-Is

**Per-dimension**: A 4/5 · B 5/5 · C 5/5 · D 5/5 · E 5/5 · F 5/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Best-in-batch state coverage — every branch explicit + shaped: skeleton tiles + skeleton breakdown cards (`routing/page.tsx:256-258,547-575`), custom skeleton rules table (`routing/page.tsx:862-885`), error+retry (`routing/page.tsx:576-580,886-890`), guidance-rich SVG empty states (`routing/page.tsx:581-597,891-907`).
- [E] Full CRUD done well: `DataTable` with search/pagination, validated `Modal` form (per-field errors), `ConfirmModal` delete guard (`routing/page.tsx:695-757,927-1046`).
- [B/D] Most on-design-system page of its batch — consistent `sran-btn/-card/-table/-input/-skeleton` + `var(--font-size-*)`/`var(--color-*)`; per-tile accent palette isolated + intentional (`routing/page.tsx:212-294,759-836`).
- [C] Clean responsive metric-tile + breakdown grid with sensible breakpoints (`routing/page.tsx:1059-1087`); StatTile bottom accent line is a tasteful depth cue (`routing/page.tsx:282-291`).

**Issues (observable):**
- [D/integrity] "Success rate" tile is hardcoded to `"99.2%"` whenever traffic exists (`routing/page.tsx:481-484,538`) — a fabricated metric that undermines trust in the whole stats row.
- [G] Stats section is airy — 28px numbers in 1rem-padded tiles + 3rem empty padding — slightly under-dense for a data admin page.
- [E] `emptyIcon="⌨"` glyph passed to `DataTable` (`routing/page.tsx:917`), largely dead since the empty path uses `IconGitMerge` SVG (`routing/page.tsx:899`).
- [H] Motion limited to a single `spin` keyframe; no reduced-motion guard.

**Recommendations (no logic change):**
- 🔴 [P0] Either wire "Success rate" to a real value or render `—`/hide the tile — do not display a hardcoded `99.2%` (`routing/page.tsx:483`).
- 🟡 [P1] Drop the `⌨` `emptyIcon`; rely on the SVG empty state.
- 🟢 [P2] Tighten stat-tile padding; add a subtle reduced-motion-guarded entry animation.

**Anti-patterns flagged:** glyph-as-icon (`⌨` emptyIcon); hardcoded/fabricated metric (success rate).

---

### `/providers` — LLM Providers & API Keys
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 33/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Logo system with brand-color gradient fallback tile on `<img>` onError — `providers/page.tsx:76-124`.
- [F] Strong state coverage: skeleton cards, error+retry, two distinct empty states (no providers vs no match), per-row test/add busy states — `providers/page.tsx:666-679,259-261,336`.
- [E] Segmented filter tabs with live per-bucket counts + elevated active pill (not `bg-gray-100`) — `providers/page.tsx:604-663`.
- [D] Env-key override renders a real warning callout via `color-mix` warning token — `providers/page.tsx:272-287`.
- [G] Compact 3-row card (logo+status dot, key badge+model count, model chips with `+N` overflow) — `providers/page.tsx:389-517`.

**Issues (observable):**
- [F] Per-provider key/status fetched in an unbatched fire-and-forget `forEach` with no per-card placeholder → badges pop in async after grid render — `providers/page.tsx:533-543`.
- [H] Empty-state icons are emojis (`"⬡"`,`"🔍"`) passed to `EmptyState` — emoji-as-UI-icon in an SVG system — `providers/page.tsx:674,678`.
- [C] `KeysPanel` "Loading keys..." is text-only where a small skeleton row fits — `providers/page.tsx:232-235`.
- [B] Card relies on hardcoded px/rem font sizes inline rather than `--font-size-*` tokens — `providers/page.tsx:396,421,436,479`.
- [E] Hover lift via imperative inline `onMouseEnter/Leave` (no keyboard/focus parity) — `providers/page.tsx:378-387`.

**Recommendations (no logic change):**
- 🔴 [P0] Replace `EmptyState` emoji icons with the inline-SVG set — `providers/page.tsx:674,678`.
- 🟡 [P1] Add a skeleton for the key-status badge while it resolves — `providers/page.tsx:453-469`.
- 🟡 [P1] Swap `KeysPanel` "Loading keys..." for a 2-row skeleton — `providers/page.tsx:232-235`.
- 🟢 [P2] Move card hover/lift to a CSS class with `:focus-visible` parity — `providers/page.tsx:378-387`.

**Anti-patterns flagged:** emoji as UI icons (empty states); text-only loading where skeleton fits (KeysPanel).

---

### `/models` — Model Registry & Capabilities
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 33/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 5/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] `ContextBar` proportional fill (ctxK/256), color-coded `CapTag`/`TierBadge`, brand-tinted `ProviderChip` with count — `models/page.tsx:141-208`.
- [G] Dense yet readable: stat cards + provider chip cloud + search + streaming toggle + list/grid switch + pagination in one toolbar — `models/page.tsx:334-447`.
- [F] Full state matrix: skeleton table, error+retry, empty registry, dedicated "no match + Clear filters" branch — `models/page.tsx:326-461`.
- [A] Sticky uppercase headers; 36px logo + mono model name primary; provider dot+name secondary — `models/page.tsx:116-122,482-501`.
- [D] Tier/capability palettes are intentional semantic sets (budget=green, premium=amber) — `models/page.tsx:95-110`.

**Issues (observable):**
- [F] Capability/context metadata is a hardcoded client-side `META` map covering ~16 legacy models; newer registered models fall to `—` (`models/page.tsx:32-55,505-519`). Visual `—` fallback is fine; coverage gap is observable.
- [H] Per-row staggered fade (`0.3 + i*0.03s`) plus three infinite icon animations (pulse/rock/breathe) is excessive perpetual motion for a data table — `models/page.tsx:131-136,480,313-315`.
- [D] Toolbar borders use hardcoded `#f1f5f9`/`#94a3b8` instead of theme tokens → won't track dark theme — `models/page.tsx:338,344`.
- [B] Empty-state icon `"◇"` is a glyph, not SVG — `models/page.tsx:331`.
- [E] Grid-card hover via inline `onMouseEnter/Leave`; no focus parity — `models/page.tsx:570-571`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace hardcoded hex (`#f1f5f9`,`#94a3b8`) with `--border-subtle`/`--text-muted` — `models/page.tsx:338,344`.
- 🟡 [P1] Tone down perpetual icon animations to one-shot on mount; cap row stagger — `models/page.tsx:131-136`.
- 🟢 [P2] Use an SVG for the empty-registry icon — `models/page.tsx:331`.
- 🟢 [P2] Tooltip on `—` cells so the metadata gap reads intentional — `models/page.tsx:508,518`.

**Anti-patterns flagged:** glyph (not SVG) empty-state icon; excessive perpetual motion (3 infinite icon animations + per-row stagger).

---

### `/policies` — DLP Policies (Profiles / Detectors / NER)
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 32/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Sophisticated controls: custom 40×22 `Toggle`, `ActionSel` styled select, semantic `CatPill`/`SevBadge`, inline `SaveDot` optimistic-save indicator per row — `policies/page.tsx:128-177`.
- [F] Optimistic save with rollback-on-error in `ProfCard.onChange` — `policies/page.tsx:284-296`.
- [A] Three-tab card (Profiles/Detectors/NER) with underline active tab + stat row — `policies/page.tsx:236-256,220-227`.
- [D] NER status indicator maps four states to distinct dot colors + descriptive text + glow only when ready — `policies/page.tsx:528-566`.
- [E] Outbound-inheritance rendered honestly with "(inherited)" affordance + contract tooltip — `policies/page.tsx:298-390`.

**Issues (observable):**
- [B] Loads a third webfont (`Outfit` via next/font) just for this page, diverging from sibling pages — `policies/page.tsx:4,12,206`.
- [D] Hardcoded light-theme hex (`borderBottom:#f3f4f6`, info callout `#92400e`) won't adapt to the dark default — `policies/page.tsx:330,434,573`.
- [F] `SaveDot` uses `"✓ saved"`; empty states use `"◆"` glyph — `policies/page.tsx:175,270,410`.
- [H] Perpetual `dlp-icon-pulse/swing/glow` infinite stat-icon animations + per-row fade — `policies/page.tsx:223-225,349`.
- [G] Detector "Default Action" select edits the shared default-profile category action, so toggling one detector silently changes every detector in that category, with no grouping cue — `policies/page.tsx:412-425,480`.

**Recommendations (no logic change):**
- 🔴 [P0] Replace hardcoded light hex (`#f3f4f6`,`#92400e`) with theme tokens for dark-theme legibility — `policies/page.tsx:330,434,573`.
- 🟡 [P1] Drop the page-local `Outfit` font; inherit the admin stack — `policies/page.tsx:4,12,206`.
- 🟡 [P1] Add an "applies to category" caption on the Default Action column — `policies/page.tsx:464,480`.
- 🟢 [P2] Swap glyph icons (`✓`,`◆`) for inline SVG — `policies/page.tsx:175,270,410`.

**Anti-patterns flagged:** glyph (not SVG) icons; hardcoded light-theme hex (dark-theme contrast risk); page-local font divergence; perpetual icon animation.

---

### `/tenants` — Tenant Management
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 37/40  **Verdict**: 🟢 Keep As-Is

**Per-dimension**: A 5/5 · B 5/5 · C 4/5 · D 5/5 · E 5/5 · F 4/5 · G 4/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A] Clear three-tier hierarchy: 30px/800 title + subtitle, 4-up stat row, primary table card — `tenants/page.tsx:255-280`.
- [E] Animated stat cards with per-icon motion (pulse/glow/swing) + tinted icon chips — `tenants/page.tsx:79-99,272-275`.
- [D] Semantic status system (color+bg+animated dot, glow only on active) + region-aware pills — `tenants/page.tsx:47-51,363-376,37-43`.
- [B] Real type scale: Outfit display + JetBrains Mono for values/slug/dates, uppercase 10.5px th — `tenants/page.tsx:68,94,356,379`.
- [F] Distinct loading/error/empty-with-CTA states — `tenants/page.tsx:281-286`.
- [H] Staggered row/card fade-up + hover-reveal action buttons — `tenants/page.tsx:73-74,401-413`.

**Issues (observable):**
- [F] No filtered-empty state: zero search results render a blank `<tbody>` (contrast api-keys) — `tenants/page.tsx:340-431`.
- [C] Toolbar bottom border hardcodes `#f3f4f6` instead of a token → won't track dark theme — `tenants/page.tsx:290`.
- [G] Table only 5 columns; project/key counts per tenant would raise density without crowding.

**Recommendations (no logic change):**
- 🟡 [P1] Add a filtered-empty row mirroring api-keys' "No keys match…" + clear-filters.
- 🟢 [P2] Swap hardcoded `#f3f4f6` toolbar border for `var(--border-default)`.
- 🟢 [P2] Replace clear-search `✕` glyph with the inline-SVG X.

**Anti-patterns flagged:** none (inline styles + hand-rolled SVG are the intentional system; `▣` empty icon is a glyph, not emoji).

---

### `/projects` — Project Management
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 37/40  **Verdict**: 🟢 Keep As-Is

**Per-dimension**: A 5/5 · B 5/5 · C 4/5 · D 5/5 · E 5/5 · F 4/5 · G 4/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Info bar with animated SVG donut (active/total) + stat trio + overlapping tenant-avatar stack with hover lift — `projects/page.tsx:184-230`.
- [E] Dual grid/list view toggle with selected-segment shadow — `projects/page.tsx:291-308`.
- [A] Card with colored top-border accent + avatar + status pill + meta footer — `projects/page.tsx:330-385`.
- [H] Card hover lift + gradient-masked hover action tray that correctly uses `var(--bg-surface)` for theme safety — `projects/page.tsx:45-51,388-397`.
- [D] Policy pills differentiate strict (amber) vs default (accent); archived/active fully styled in both views — `projects/page.tsx:328,346-365,460-469`.
- [F] Loading/error/empty-with-CTA + a real "show archived" toggle — `projects/page.tsx:313-320,270-289`.

**Issues (observable):**
- [F] No zero-match state — filtering to empty leaves a blank grid/table — `projects/page.tsx:323-329`.
- [G] `activeCount = items.length` so the donut is always 100% / Active always equals Total — a visually redundant metric — `projects/page.tsx:108,159`.
- [C] Grid `minmax(300px,1fr)` can leave a single stretched card on mid widths.

**Recommendations (no logic change):**
- 🟡 [P1] Add a filtered-empty placeholder when `filtered.length === 0` but `items.length > 0`.
- 🟢 [P2] Reconsider the always-100% donut/Active stat — it conveys no information as written.

**Anti-patterns flagged:** none (`▢` empty icon is a glyph; the hover-tray gradient is theme-token masking, not a default gradient button).

---

### `/api-keys` — API Key Management
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 36/40  **Verdict**: 🟢 Keep As-Is

**Per-dimension**: A 5/5 · B 4/5 · C 4/5 · D 5/5 · E 5/5 · F 5/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Collapsible per-project groups with colored avatar, mono count chip, rotating chevron — `api-keys/page.tsx:206-228`.
- [F] Best state coverage of its batch: loading, error, empty-with-CTA, AND explicit "No keys match the current filters" + clear-filters — `api-keys/page.tsx:185-197`.
- [E] Secure "key created" reveal modal: warning banner, mono key block with `userSelect:all`, copy — `api-keys/page.tsx:351-363`.
- [D] Status conveyed redundantly via dot + colored badge (active/revoked) — `api-keys/page.tsx:246-277`.
- [H] Hover-reveal row actions + per-row hover tint keep the dense list calm — `api-keys/page.tsx:36-39,283-294`.

**Issues (observable):**
- [D] Raw-key warning banner hardcodes `#92400e` (light-only) → low contrast on dark surface — `api-keys/page.tsx:354`.
- [B] Slightly flatter type scale than tenants/projects; no display-mono stat row.
- [G] Copy button copies `k.key_prefix` (masked prefix, not a full key) — yields a non-useful value.

**Recommendations (no logic change):**
- 🟡 [P1] Move warning-banner text to a theme token (`var(--accent-warning)`) for dark-theme legibility.
- 🟢 [P2] Hide the row copy button or label it "copy prefix".

**Anti-patterns flagged:** none (`⚷` empty icon is a glyph).

---

### `/portal-users` — Portal User Roster
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 30/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 3/5 · E 4/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [G] Densest, most informative table: User, Plan, inline credit bar+balance, usage, status, created, actions — `portal-users/page.tsx:349-422`.
- [E] Credit bar with threshold color logic (green/amber/red) reused in row + detail drawer — `portal-users/page.tsx:63-74,393-400,463-469`.
- [F] Strong coverage: skeleton, error, empty, lazy detail-modal, optimistic suspend/activate with rollback — `portal-users/page.tsx:336-346,203-221,486-488`.
- [D] Tab active-state contrast deliberately reasoned (fixed navy fill → fixed white text, documented) — `portal-users/page.tsx:284-293`.
- [A] Uses shared `sran-page-header`/`sran-card`/`StatCard` cleanly with a 4-up stat row — `portal-users/page.tsx:227-242`.

**Issues (observable):**
- [D/E/H] Emoji used as UI icons throughout — `💰 Top-up`,`⏸ Suspend`,`▶ Activate` (`:474-481`), kebab items `💰/⏸/▶/👁` (`:681-687`), `👥` empty-state (`:344`). Breaks the SVG icon system.
- [D] Hardcoded `#f0f2f5` transaction-row border + `#111827` negative-amount text are light-only — `portal-users/page.tsx:500,503`.
- [D] `PlanChip` paid color hardcodes navy `#1e3a5f` → near-invisible on dark — `portal-users/page.tsx:96`.
- [H] Less motion than siblings: no row stagger, no animated dot.
- [E] StatCard `trend="active"` shows "● live" on the static Total Users count — semantically odd — `portal-users/page.tsx:238`.

**Recommendations (no logic change):**
- 🔴 [P0] Replace all emoji (`💰⏸▶👁👥`) with the inline-SVG set for consistency + dark-theme rendering.
- 🟡 [P1] Convert hardcoded `#f0f2f5`,`#111827`,`#1e3a5f` to semantic tokens for dark mode.
- 🟢 [P2] Drop the "● live" trend on the static Total Users stat.

**Anti-patterns flagged:** emoji as UI icons/empty-state icon (`:344,474-481,681-687`); light-only `#f0f2f5` border + hardcoded hex as only secondary treatment in the transactions table (`:500-503`).

---

### `/usage` — Usage & Cost
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 30/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 3/5 · F 4/5 · G 3/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Full state matrix per tab: error→skeleton→empty→data, shape-matched skeleton count (3 user, 4 system) — `usage/page.tsx:204-247`.
- [A] Tab bar uses a real underline-accent active treatment (2px info border + weight shift), not `bg-gray-100` — `usage/page.tsx:158-162`.
- [B] Smart cost formatter switches precision by magnitude so micro-costs don't render as `$0.0000` — `usage/page.tsx:55-59`.
- [D] StatCard accent stripe + semantic Badge variants for trigger/status — `usage/page.tsx:279,288`.

**Issues (observable):**
- [G] System scan-runs table is dense (9 cols at xs) but every cell repeats an identical inline `borderBottom`; no zebra/hover — hard to scan — `usage/page.tsx:275-289`.
- [E] Inline-styled `<table>` hand-built instead of reusing `.sran-table` → inconsistent with chat-conversations — `usage/page.tsx:264-293`.
- [G] User tab shows only 3 KPI cards and no time-series/breakdown — very low density for a "Usage & Cost" landing — `usage/page.tsx:214-230`.
- [H] No row hover and no tab-switch motion; static — `usage/page.tsx:273-291`.
- [D] Empty state is literal "No scan runs yet." raw text, not the shared `EmptyState` — `usage/page.tsx:259-261`.

**Recommendations (no logic change):**
- 🟡 [P1] Swap the hand-rolled system table for `.sran-table` (zebra + hover); use `<EmptyState>` instead of literal text.
- 🟢 [P2] Add a per-model/per-project mini breakdown to the sparse User tab.
- 🟢 [P2] Add hover background on table rows.

**Anti-patterns flagged:** literal "No data"-style empty text (`usage/page.tsx:259-261`).

---

### `/audit-log` — Admin Actions
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 37/40  **Verdict**: 🟢 Keep As-Is

**Per-dimension**: A 5/5 · B 5/5 · C 5/5 · D 5/5 · E 5/5 · F 5/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Genuinely sophisticated: severity stripe column + category badges with hand-SVG icons + 24h stacked sparkline + click-to-filter linked entities + collapsible advanced form + day grouping — Datadog-tier — `audit-log/page.tsx:293-375,1375-1424`.
- [D] Single source-of-truth `SEVERITY` palette keeps pill, row stripe, sparkline, KPI accents in sync — `audit-log/page.tsx:60-77`.
- [F] Complete coverage incl. stale-response guard (`reqRef`), distinct "no records yet" vs "no match" copy, truncation warning when `total > FETCH_LIMIT` — `audit-log/page.tsx:611-634,1153-1157,1211-1218`.
- [B] Disciplined uppercase tracked micro-labels + tabular-nums KPIs + mono IDs/timestamps — `audit-log/page.tsx:264-282,1742-1761`.
- [A] Syntax-highlighted JSON expansion + copy-to-clipboard — `audit-log/page.tsx:501-528,1488-1571`.
- [G] Quick period + category chips translated to concrete backend params — `audit-log/page.tsx:581-607`.

**Issues (observable):**
- [D] `EmptyState icon="🛡"` injects an emoji where a shield SVG already exists at `audit-log/page.tsx:139-141` — `audit-log/page.tsx:1217`.
- [H] Most interactions are instant; row-expand + JSON panel appear without motion — `audit-log/page.tsx:1426-1443`.
- [G] KPI grid + sparkline + chips + active-filter row + advanced form make a tall pre-table header — `audit-log/page.tsx:869-1136`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace `icon="🛡"` with the existing inline shield SVG — `audit-log/page.tsx:1217,139-141`.
- 🟢 [P2] Add a small height/opacity transition on row-expand.

**Anti-patterns flagged:** emoji as UI icon in empty state (`audit-log/page.tsx:1217`).

---

### `/chat-analytics` — Chat Analytics
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 34/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 5/5 · E 5/5 · F 5/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] recharts LineChart + vertical BarChart with per-provider brand-color cells (`colorForModel`) + themed tooltips — `chat-analytics/page.tsx:66-90,359-363`.
- [F] Best-in-set loading: purpose-built `SkeletonStat` + animated bar `SkeletonChart` instead of a spinner, plus per-widget empties — `chat-analytics/page.tsx:27-48,265,322`.
- [D] Semantic CSS-var tooltips + low-alpha grid strokes; `compactNumber` + tabular-nums — `chat-analytics/page.tsx:286-293,438-443`.
- [A] Asymmetric 2fr/1fr chart grid (time-series wide, breakdown narrow) — `chat-analytics/page.tsx:233`.
- [F] "Recent Conversations" links through with a "View all →" — `chat-analytics/page.tsx:391-399`.

**Issues (observable):**
- [D] Mixes two token namespaces: `--color-surface`/`--color-border` here vs `--color-border-default` elsewhere — risk of unresolved vars — `chat-analytics/page.tsx:163-166,288-290`.
- [B] Chart axis ticks hardcode `rgba(180,200,230,0.7)` (light-on-dark assumption) rather than a token → may wash out in light theme — `chat-analytics/page.tsx:279,283`.
- [H] "View all →" uses a glyph arrow + inline `--color-sran-500` fallback; no hover/transition — `chat-analytics/page.tsx:391-399`.
- [G] Section headings uniform `0.85rem` across all three cards — flat heading hierarchy — `chat-analytics/page.tsx:249,310,381`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace hardcoded `rgba(180,200,230,…)` axis colors with a theme token.
- 🟡 [P1] Normalize `--color-surface`/`--color-border` to the page-standard `*-default` names.
- 🟢 [P2] Add hover underline/transition to "View all →".

**Anti-patterns flagged:** none (flat headings are minor).

---

### `/chat-conversations` — Conversation Logs
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 29/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 3/5 · B 4/5 · C 4/5 · D 4/5 · E 3/5 · F 5/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Clean coverage: error / skeleton (`SkeletonTable rows=10 cols=8`) / empty / data, plus draft-vs-applied filter separation (no per-keystroke re-fetch) — `chat-conversations/page.tsx:34-37,161-173`.
- [E] Reuses shared `.sran-table` + `Pagination` + `EmptyState` — `chat-conversations/page.tsx:176,227`.
- [B] Tabular-nums on Msgs/Tokens + uppercase tracked field micro-labels — `chat-conversations/page.tsx:207-211,246-251`.
- [C] Responsive 1/2/5 filter grid with `align-items:end` so buttons line up with inputs — `chat-conversations/page.tsx:88-92`.

**Issues (observable):**
- [D] Files/Shared columns render `"✓"` vs `"—"` plain glyphs with no color — boolean state carries no visual weight — `chat-conversations/page.tsx:213-214`.
- [A] Filter card + flat table, no KPI/summary row — weak top hierarchy vs sibling analytics page — `chat-conversations/page.tsx:76-158`.
- [E] Filter inputs use a local `inputStyle` object AND `.sran-input` simultaneously — duplicated declarations — `chat-conversations/page.tsx:104,260-269`.
- [H] No row hover/active treatment — `chat-conversations/page.tsx:191-222`.
- [G] 9-column table with `minWidth:920` forces horizontal scroll on narrow viewports — `chat-conversations/page.tsx:176`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace plain `✓`/`—` in Files/Shared with small colored badges/icons.
- 🟢 [P2] Drop the local `inputStyle`; rely on `.sran-input`.
- 🟢 [P2] Add a compact KPI/summary strip above the table.

**Anti-patterns flagged:** none egregious (boolean-as-plain-glyph + redundant input styling are quality issues).

---

### `/chat-conversations/[id]` — Conversation Detail
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 28/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 3/5 · C 4/5 · D 4/5 · E 3/5 · F 4/5 · G 3/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [D] Role-coded message tiles (user=indigo, assistant=green, fallback=slate) via `roleColor` — `[id]/page.tsx:28-49,192-205`.
- [A] Strong structure: title/subtitle + Back → DLP banner → summary grid → messages → conditional attachments — `[id]/page.tsx:80-306`.
- [F] Handles loading (skeleton), error (retry), empty-files (section hidden), zero-token (metadata hidden) — `[id]/page.tsx:122-127,239,257`.
- [D] Persistent DLP/masked-content status banner with `role="status"` — `[id]/page.tsx:97-120`.
- [G] Per-message token footer renders only when tokens exist, avoiding `0 in · 0 out` noise — `[id]/page.tsx:239-249`.

**Issues (observable):**
- [D] DLP banner uses a literal `⚠️` emoji as its icon — `[id]/page.tsx:115`.
- [B] Back-to-back tiles are all identical `border 1px + radius 8 + padding` containers — the "undifferentiated border box" pattern; only the role pill distinguishes them — `[id]/page.tsx:176-181`.
- [B] Heavy reliance on `0.6rem`–0.65rem micro-text for labels/timestamps/footers — `[id]/page.tsx:209,217,242,318`.
- [E] Message body in a `<pre>` (plain preformatted) — no markdown, so assistant code blocks render raw — `[id]/page.tsx:226-238`.
- [H] No hover/anchor affordance, no scroll-to/indexing in a long transcript — `[id]/page.tsx:171-252`.

**Recommendations (no logic change):**
- 🔴 [P0] Replace the `⚠️` emoji with an inline SVG warning icon — `[id]/page.tsx:115`.
- 🟡 [P1] Differentiate user vs assistant tiles beyond the pill (tinted bg / left accent matching `roleColor`) — `[id]/page.tsx:176-181`.
- 🟢 [P2] Bump the `0.6rem` token/label text up one step.

**Anti-patterns flagged:** emoji as UI icon (`[id]/page.tsx:115`); undifferentiated `border + rounded + padding` box repeated per message tile (`[id]/page.tsx:176-181`).

---

### `/login` — Admin Sign In
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 33/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 5/5 · E 4/5 · F 3/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [D] Cohesive deep-navy brand gradient + grid texture + radial glow — a premium auth backdrop, not flat fill — `login/page.tsx:71,80-93`.
- [E] Glass-morphism form card (`backdropFilter: blur(8px)` + translucent fill + hairline border) — `login/page.tsx:116-124`.
- [B] Uppercase tracked micro-labels (0.72rem/600/0.08em) give crisp dashboard rhythm — `login/page.tsx:58-66`.
- [A] Logo → title → "Admin Console" eyebrow → card → version footer, 420px cap — `login/page.tsx:95,107-113`.
- [F] Distinct loading (button disabled + "Authenticating…" + dimmed) + styled inline error banner with 429-specific copy — `login/page.tsx:31-32,154-166,174,187`.

**Issues (observable):**
- [D] Sign-In button is a literal default two-stop `linear-gradient(135deg,#2d5bc4→#1e3d8f)` — the gradient-button pattern (brand-toned) — `login/page.tsx:174`.
- [F] Inputs have focus handlers but no visible validation, no `required`/`aria-invalid`; error clears only on submit — `login/page.tsx:128-137`.
- [H] Focus styling is imperative inline `onFocus/onBlur` border swap with no `:focus-visible` ring — `login/page.tsx:135-136`.
- [B/C] Entire screen hardcoded inline styles + magic px/rem rather than the `sran-*`/token system (auth exception noted, but maintenance debt) — `login/page.tsx:44-66`.

**Recommendations (no logic change):**
- 🟡 [P1] Flatten the submit button to a solid brand fill (or document as the auth-only brand CTA) — `login/page.tsx:174`.
- 🟢 [P2] Add a `:focus-visible` ring + `aria-invalid`/`required` on inputs — `login/page.tsx:128-151`.
- 🟢 [P2] Route colors through theme tokens.

**Anti-patterns flagged:** default 2-stop gradient button (brand-toned).

---

### `/dlp-detectors` — Custom DLP Detectors
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 26/40  **Verdict**: 🟠 Major Refactor

**Per-dimension**: A 4/5 · B 3/5 · C 3/5 · D 3/5 · E 3/5 · F 2/5 · G 4/5 · H 2/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A] Master-list + slide-in detail/create panel (460px) is a sound two-pane CRUD layout — `dlp-detectors/page.tsx:121-199`.
- [E] The `AuditLogTimeline` is the standout — colored rail dots per action, status `before → after`, red/green diff for pattern changes — `AuditLogTimeline.tsx:53-87`.
- [G] Dense scannable table with context-aware lifecycle buttons that hide the current state's verb — `dlp-detectors/page.tsx:152-162`.
- [E] Conditional form fields by detection type + inline test-case runner with pass/fail coloring — `CustomDetectorForm.tsx:154-200`.

**Issues (observable):**
- [F] Loading is a bare "Loading…" where a skeleton table fits; empty state is literal "No detectors." — imports none of the shared `Skeleton`/`EmptyState` primitives — `dlp-detectors/page.tsx:126,128`.
- [H] Mutations use `window.prompt`/`window.confirm`/`alert` for disable/delete reasons + errors — native dialogs are a hard polish failure for an admin console — `dlp-detectors/page.tsx:69,78,83-85,90`.
- [E] Action column is four undifferentiated transparent `miniBtn`s incl. a Delete that only differs by red text — easy mis-click — `dlp-detectors/page.tsx:151-163,211-214`.
- [B] Status is colored bold text only (`STATUS_COLOR`), not a pill/badge — `dlp-detectors/page.tsx:144-148`.
- [D] Status colors are hardcoded hex (`#059669`,`#dc2626`) that don't adapt to theme; errors render as raw `#dc2626` text — `dlp-detectors/page.tsx:30-32,124`.
- [C] All-inline styles with ad-hoc px gaps; no `sran-card`/`sran-table` — spacing diverges — `dlp-detectors/page.tsx:205-214`.

**Recommendations (no logic change):**
- 🔴 [P0] Replace `window.prompt/confirm/alert` with the existing `Modal`/`ConfirmModal` + `Toast` (already used by search-settings) — `dlp-detectors/page.tsx:69,83,90`.
- 🟡 [P1] Swap "Loading…"/"No detectors." for `SkeletonTable` + `EmptyState` — `dlp-detectors/page.tsx:126-128`.
- 🟡 [P1] Render status as a token-colored pill — `dlp-detectors/page.tsx:144-148`.
- 🟢 [P2] Give Delete a destructive button treatment, grouped apart from lifecycle actions — `dlp-detectors/page.tsx:162`.

**Anti-patterns flagged:** spinner/text-only loading where skeleton fits; literal "No detectors." empty state; native `prompt/confirm/alert`; raw `#dc2626` text as only error treatment.

---

### `/dlp-coverage` — Detector Catalog & Quick Test
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 33/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 5/5 · B 4/5 · C 4/5 · D 3/5 · E 5/5 · F 4/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Best-composed page of its batch: summary bar with mono big-number total + per-category pop-in counters, filter chips with colored dots, category-colored top-border cards, live masked Quick Test result panel — `dlp-coverage/page.tsx:206-231,279-335,429-471`.
- [A] Strong hierarchy — 30px tracked H1, summary band, toolbar, card grid, Known Gaps, Quick Test — `dlp-coverage/page.tsx:188-189,278-279,337-338,380-387`.
- [H] Tasteful staggered entrances (`dcFadeUp`/`dcPop`) + hover elevation — purposeful motion — `dlp-coverage/page.tsx:78-91,217-218,290-291`.
- [F] Full coverage: loading/error (+Retry), test loading/error/empty, no-match result — `dlp-coverage/page.tsx:155-179,414,424-437`.
- [B] Dual-typeface system — Outfit for UI, mono for numerics/detector names/matched text — `dlp-coverage/page.tsx:9,210,318,449`.
- [G] Search across name/category/severity/method + chips + live count — `dlp-coverage/page.tsx:131-151,238-275`.

**Issues (observable):**
- [D] Hardcoded light-mode hex leaks into the default DARK theme: card separators `#f3f4f6`/`#f8f9fa`, category-counter hover `background:#f3f4f6` (near-white flash on dark) — `dlp-coverage/page.tsx:222,294,310`.
- [D] Severity/method/priority/category palettes are fixed hex maps ignoring tokens — tuned for light, approximate on dark — `dlp-coverage/page.tsx:36-64`.
- [H] Negative-margin bleed trick (`margin:-1.75rem -2.25rem -2.5rem`) repeated in all three branches — fragile — `dlp-coverage/page.tsx:157,169,182`.
- [C] Focus-ring box-shadow uses hardcoded indigo `rgba(99,102,241,…)` not matching `T.accent` — `dlp-coverage/page.tsx:240`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace `#f3f4f6`/`#f8f9fa` borders + `#f3f4f6` hover fill with tokens so dark theme doesn't flash near-white — `dlp-coverage/page.tsx:222,294,310`.
- 🟢 [P2] Align search focus-ring with `T.accent` — `dlp-coverage/page.tsx:240`.
- 🟢 [P2] Derive severity/category colors from semantic tokens for true dual-theme contrast — `dlp-coverage/page.tsx:36-64`.

**Anti-patterns flagged:** hardcoded light-only hex backgrounds/borders in a default-dark theme (minor).

---

### `/search-settings` — Web Search Providers
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 34/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Richest interaction surface: tenant selector, health-summary band, config card, ordered provider cards with priority badges, custom `Toggle`, kebab overflow (Edit/Test/Move/Delete) + proper `Modal`/`ConfirmModal`/`Toast` — fully realized CRUD — `search-settings/page.tsx:264-373,376-439`.
- [F] Deep coverage: per-action saving/adding/deleting/testing flags, health dot states, loading + empty-providers, inline failure-reason — `search-settings/page.tsx:35-43,312-315,332-343`.
- [E] Kebab dims Move-up/down at list ends + color-codes Delete destructive — `search-settings/page.tsx:356-361`.
- [D] Health uses a consistent colored-dot vocabulary shared between summary counts + rows — `search-settings/page.tsx:275-278,330`.
- [B/G] Rows pack priority badge + name + mono masked key + uptime/latency/queries + failure reason — `search-settings/page.tsx:323-344`.
- [C] Consistent `card()` helper + `sran-input`/`sran-btn` — `search-settings/page.tsx:444-445`.

**Issues (observable):**
- [D] Emoji as status icons in toasts: `✓`/`✗` prefix test-result messages — `search-settings/page.tsx:226,228`.
- [E] Kebab trigger is a literal `⋮` glyph rather than an inline SVG — `search-settings/page.tsx:348`.
- [D] Health colors hardcoded hex (`#5DCAA5`/`#EF9F27`/`#E24B4A`) not tokens; priority badge bg raw `#3b6cf6`/`#6b7280` — `search-settings/page.tsx:36-42,324`.
- [F] Providers loading is text "Loading…" where a card skeleton fits — `search-settings/page.tsx:313-315`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace `✓`/`✗` emoji in toasts with the toast's own success/error variant styling — `search-settings/page.tsx:226-228`.
- 🟢 [P2] Swap the `⋮` glyph for an inline-SVG kebab — `search-settings/page.tsx:348`.
- 🟢 [P2] Route health/priority colors through tokens; use a skeleton for providers loading — `search-settings/page.tsx:36-42,313`.

**Anti-patterns flagged:** emoji (`✓`/`✗`) as status icons in toasts; `⋮` glyph instead of SVG; text-only loading.

---

### `/chat-users` — Chat User Roster
**Project**: Gateway Admin  **Traffic**: Med  **Total Score**: 34/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 4/5 · F 5/5 · G 5/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Exemplary coverage via shared primitives: `SkeletonTable`, `ErrorState`+retry, `EmptyState` no-match, optimistic in-place toggle that refetches on failure — `chat-users/page.tsx:152-164,72-87`.
- [G] Tight pro table — avatar-initial + name/email stack, role, right-aligned tabular-nums numerics, compacted counts (`1.2K`/`3.4M`), relative last-active, status pill, action — horizontal-scroll guard — `chat-users/page.tsx:166-272,19-34,239-247`.
- [E] Gradient avatar monogram + `StatusPill` (token'd green/red translucent) — `chat-users/page.tsx:191-208,295-313`.
- [A] Uses shared `PageHeader` + `sran-card`/`sran-table`/`sran-btn` — fully on-system, unlike the DLP pages — `chat-users/page.tsx:97-100,167`.
- [B] `tabular-nums` on all metric columns — `chat-users/page.tsx:239,242,245`.
- [F] Debounced-by-submit search with conditional Clear — `chat-users/page.tsx:90-93,115-149`.

**Issues (observable):**
- [E] Per-row toggle is a generic button "Disable/Enable/…", lower-affordance than the animated `Toggle` used on search-settings for the same concept — inconsistent control vocabulary — `chat-users/page.tsx:254-267`.
- [D] Uses `var(--color-*)` while siblings use `--text-*/--bg-*/--border-default` — token-namespace inconsistency — `chat-users/page.tsx:123-127,213,248`.
- [D] Avatar gradient + StatusPill colors hardcoded hex/rgba rather than tokens — `chat-users/page.tsx:198,296-297`.
- [H] Toggling shows "…" text, no spinner/transition — `chat-users/page.tsx:262-263`.

**Recommendations (no logic change):**
- 🟢 [P2] Consider the animated `Toggle` for active/disable to unify the affordance — `chat-users/page.tsx:254-267`.
- 🟢 [P2] Normalize on one token namespace — `chat-users/page.tsx:123-127`.
- 🟢 [P2] Move avatar gradient + pill colors to tokens — `chat-users/page.tsx:198,296-297`.

**Anti-patterns flagged:** none (EmptyState uses "No users match"; loading is a real skeleton).

---

### `/portal-settings` — Portal Settings
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 31/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 4/5 · F 4/5 · G 3/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] Hand-built `Toggle` (role="switch", aria-checked, wait-cursor while saving) + 3-segment tier control — `portal-settings/page.tsx:379-421,555-581`.
- [F] Excellent coverage: skeleton, ErrorState+retry, optimistic toggle with rollback, dirty-state Save with "Unsaved changes" pulse dot — `portal-settings/page.tsx:248-251,311-325,654-662`.
- [H] Real motion: `psFadeUp`/`psFadeIn`/`psPin`, hover lift on metric cards, tab content re-animates on `key={tab}` — `portal-settings/page.tsx:89-108,253`.
- [B] Tabular-nums + tight letter-spacing on stat values — `portal-settings/page.tsx:133-137`.

**Issues (observable):**
- [D] Tier-selected + Save buttons hardcode `var(--bg-surface-dark,#0f172a)` with white text → weak figure/ground on the dark theme's dark surface — `portal-settings/page.tsx:567,645`.
- [G] Only 3-4 settings across 3 tabs at a 1000px cap leaves the card very airy — `portal-settings/page.tsx:169`.
- [B] Token-namespace split: `--bg-surface`/`--accent-primary` here vs `--color-*` on peers.
- [F] Security tab is a "Coming soon" stub — feature นี้เป็น stub, score ตาม visual quality ไม่ใช่ functional completeness (Functional Audit แยกจัดการ); the dashed-border empty state is well executed — `portal-settings/page.tsx:672-694`.

**Recommendations (no logic change):**
- 🟡 [P1] Swap the `#0f172a` literal for a semantic `--accent-primary`/`--bg-inverse` token so the primary button keeps contrast in both themes.
- 🟢 [P2] Two-column layout inside the Trial tab to reduce airiness.

**Anti-patterns flagged:** none.

---

### `/portal-token-packs` — Token Packs
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 30/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 3/5 · C 4/5 · D 4/5 · E 4/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Full CRUD state machine: skeleton, ErrorState, empty, inline form-validation banner, saving label, toast, soft-delete ConfirmModal — `portal-token-packs/page.tsx:216-225,302-306,392-400`.
- [E] Live token-format preview under the input (`= 50K tokens`) + computed price-range stat — `portal-token-packs/page.tsx:329-333,71-82`.
- [G] Well-differentiated table: tabular-nums tokens, ellipsised description, centered status badge, right-aligned actions — `portal-token-packs/page.tsx:251-276`.
- [D] Status uses semantic `sran-badge-success`/`-neutral` not raw color — `portal-token-packs/page.tsx:259-261`.

**Issues (observable):**
- [E] `EmptyState icon="💎"` uses an emoji — inconsistent with the page's hand-rolled SVG `+` at `:200-202` — `portal-token-packs/page.tsx:223`.
- [B] Flat ~12-14px inline font scale with little weight contrast between header/body.
- [H] No motion (no row hover, no modal transition referenced).
- [D] "Most Popular" renders a `★` glyph as an icon — `portal-token-packs/page.tsx:247`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace `💎` empty-state emoji with an inline SVG.
- 🟢 [P2] Replace the `★` char with a small inline star SVG inside the Popular badge.

**Anti-patterns flagged:** emoji as UI icon (`💎` empty state `:223`); glyph-as-icon (`★` `:247`).

---

### `/alerts/events` — Alert Events
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 28/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 3/5 · C 3/5 · D 4/5 · E 4/5 · F 4/5 · G 3/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Inherits the shared `DataTable` state matrix — skeleton/error/empty/search + retry — `alerts/events/page.tsx:103-115`, `DataTable.tsx:76-78`.
- [E] Detail modal is well built: 2-column `sran-detail-field` grid with full-width spans for Webhook/Message + `pre-wrap` — `alerts/events/page.tsx:127-179`.
- [D] Dual semantic badge mappers (severity → error/warning/info; delivery → success/failed/pending/log_only) — `alerts/events/page.tsx:33-50`.
- [B] Code-styled `alert_type`/IDs vs prose message — `alerts/events/page.tsx:69,83`.

**Issues (observable):**
- [G] Six columns, no stat strip, no severity/delivery filter chips — only free-text client search over a `limit=50` slice — thin for a log console — `alerts/events/page.tsx:88`.
- [E] `emptyIcon="!"` passes a bare punctuation char as the empty icon — `alerts/events/page.tsx:109`.
- [C/B] Single small font band across the whole table; rows read uniformly flat.
- [H] No row-level motion or selected-row affordance beyond pointer cursor.

**Recommendations (no logic change):**
- 🟡 [P1] Add severity/delivery filter pills (client-side over loaded items).
- 🟢 [P2] Replace `emptyIcon="!"` with a bell/alert inline SVG.

**Anti-patterns flagged:** literal-char-as-icon (`emptyIcon="!"` `:109`).

---

### `/ai-review` — AI Sensitive Data Reviewer
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 29/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 3/5 · C 3/5 · D 4/5 · E 4/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] The `KeyPicker` is the most sophisticated component here — tenant-grouped checkbox list, per-tenant select-all/clear, search, pill summary with "+N more" + inline remove — `ai-review/page.tsx:106-244`.
- [A] Collapsible settings header doubling as a `StatusStrip` (Enabled / provider·model / Key set / Realtime) — `ai-review/page.tsx:250-265,484-492`.
- [F] Deep coverage: enable gate, provider save, async Test-connection with latency/model, "Saved ✓" flash, scanning state, per-finding actions, scan-history footer — `ai-review/page.tsx:544-548,611-622,671`.
- [D] Consistent status-token badges (trigger, status, risk) with bg-tint pills — `ai-review/page.tsx:564,658,685-688`.

**Issues (observable):**
- [H/E] Gear `&#9881;` + `▲`/`▼` text arrows as the Settings button icon — glyphs standing in for icons — `ai-review/page.tsx:490`.
- [C] Every `<td>` repeats identical `padding:0.5rem 0.75rem; borderBottom:1px solid …` inline — undifferentiated, hard to retune — `ai-review/page.tsx:657-665,683-694`.
- [F] Findings/history use text `Loading…` (`ai-review/page.tsx:674`) where a SkeletonTable fits.
- [B] Drops to 10-11px fonts in the scan-mode cards — below density-floor — `ai-review/page.tsx:561-588`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace `&#9881;`/`▲▼` glyphs with inline SVG gear + chevron.
- 🟡 [P1] Swap `Loading…` text for `SkeletonTable` in findings/history.
- 🟢 [P2] Extract the repeated `<td>` inline style into a `sran-table`-style class.

**Anti-patterns flagged:** glyph-as-icon (`&#9881;` + `▲▼` `:490`); undifferentiated repeated inline cell borders; text-only loading where skeleton fits.

---

### `/chat-knowledge` — Knowledge Bases (list)
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 31/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 4/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Two genuinely differentiated empty states — filtered-empty invites "Clear filters", true-empty explains KB provenance — `chat-knowledge/page.tsx:148-168`; plus skeleton + ErrorState.
- [E] Purpose-built `KbEmptyState` with a 56px Lucide book-open SVG (deliberately bigger than the shared chip), documented rationale — `chat-knowledge/page.tsx:272-334`.
- [B] `Field` labels uppercase + letter-spacing; table mixes tabular-nums numerics with muted timestamps — `chat-knowledge/page.tsx:344-358,216-231`.
- [G] Filter card → table → pagination, clean dense list with horizontal-scroll guard (`minWidth:920`) — `chat-knowledge/page.tsx:171-258`.

**Issues (observable):**
- [D] `StatusPill` hardcodes `rgb(74,222,128)`/`rgba(34,197,94,…)` instead of semantic tokens → won't track theme — `chat-knowledge/page.tsx:374-380`.
- [G] No summary stat strip (total KBs / docs / chunks) above the table.
- [H] No row hover affordance or motion; static links.

**Recommendations (no logic change):**
- 🟢 [P2] Move `StatusPill` colors onto `--color-status-success*` tokens.
- 🟢 [P2] Add a small stat strip (Total / Active / Documents).

**Anti-patterns flagged:** none (hardcoded pill rgb is a minor token-drift).

---

### `/chat-knowledge/[id]` — Knowledge Base Detail
**Project**: Gateway Admin  **Traffic**: Low  **Total Score**: 30/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 4/5 · F 4/5 · G 4/5 · H 2/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A] Clear top-down hierarchy: header+back → action banner → 5-card stat strip → metadata DL → documents → sample chunks → danger zone — `chat-knowledge/[id]/page.tsx:147-393`.
- [E] `DocStatusPill` has a 4-state palette (pending/processing/indexed/failed) with per-status bg/fg/border; doc rows surface truncated `error_message` with `title` tooltip — `chat-knowledge/[id]/page.tsx:436-478,403-418`.
- [D/A] Danger-zone card visually quarantined with red-tinted border/bg + destructive delete button — `chat-knowledge/[id]/page.tsx:335-384`.
- [F] Loading skeleton, error, empty docs/chunks, reindex/delete in-flight labels, reusable action-message banner (`reindexed/total/failed`) — `chat-knowledge/[id]/page.tsx:120-143,164-178,69-95`.

**Issues (observable):**
- [H] Destructive actions use native `window.confirm()` (`chat-knowledge/[id]/page.tsx:72,100`) — jarring, inconsistent with the `ConfirmModal` used on portal-token-packs; reindex/delete show text-only "…Reindexing…" with no spinner.
- [E] Local re-implementation of `StatCard` (`chat-knowledge/[id]/page.tsx:480-505`) duplicates the shared `components/ui/StatCard`.
- [D] Hardcoded `rgb(252,165,165)`/`rgba(248,113,113,…)` danger literals instead of `--color-status-error*` — `chat-knowledge/[id]/page.tsx:339-340,377-381,406`.
- [F] Sample-chunks empty is a plain "No chunks yet…" line — `chat-knowledge/[id]/page.tsx:295`.

**Recommendations (no logic change):**
- 🔴 [P0] Replace both `window.confirm()` calls with the existing `ConfirmModal` — `chat-knowledge/[id]/page.tsx:72,100`.
- 🟡 [P1] Add a spinner to Reindex/Delete during in-flight; reuse shared `StatCard`.
- 🟢 [P2] Move danger rgb literals onto `--color-status-error*` tokens.

**Anti-patterns flagged:** native `window.confirm` for destructive actions; component duplication (local `StatCard`).

---

# Portal — Marketing

### `/` — Landing
**Project**: Portal  **Group**: marketing  **Traffic**: High  **Total Score**: 38/40  **Verdict**: 🟢 Keep As-Is

**Per-dimension**: A 5/5 · B 5/5 · C 5/5 · D 5/5 · E 5/5 · F 5/5 · G 4/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] The `FrostCard` system — cursor-driven 3D tilt, layered inset/elevation shadows, corner brackets, scan-line, cyber-sweeps, hover glare — is Linear/Vercel-tier component craft — `(marketing)/_components/Hero.tsx:7-95`.
- [E] The 3-stage gateway diagram (`YourAppCard` mini-browser w/ diff snippet + lang chips, `GatewayCard` animated shield w/ pulsing aura/orbit rings, `ModelIntelCard` brain + live-status ping) tells the product story visually — `(marketing)/_components/Hero.tsx:143-519`.
- [E/H] `SocialProof` 3D parallax plane: 14 provider tiles on a tilting perspective stage with ground shadows, three depth tiers, idle float + a static grayscale fallback under 768px — `(marketing)/_components/SocialProof.tsx:35-176`, `(marketing)/marketing.css:982-1114`.
- [B] Dramatic display scale with gradient text wipe + tight tracking: `clamp(40px,6vw,64px)`, `letter-spacing:-0.035em` — `(marketing)/_components/Hero.tsx:599-625`.
- [F] Exhaustive + accessible states: hover tilt, IntersectionObserver scroll-reveal, `:focus-within` flip on feature cards, `:has()` focus-blur on steps, full `prefers-reduced-motion` suppression — `(marketing)/marketing.css:1369-1534`, `(marketing)/_components/Features.tsx:21`.
- [C] Generous intentional rhythm — hero `paddingTop:72/bottom:96`, `marginBottom:72` before the diagram; sections at 100/120px — `(marketing)/_components/Hero.tsx:565-568`, `(marketing)/marketing.css:727,1160`.

**Issues (observable):**
- [B] Hero CTA / trust row / CTA section use glyphs (`→`,`✓`) as inline UI rather than SVG, inconsistent with the SVG-everywhere card discipline — `(marketing)/_components/Hero.tsx:656,682-690`, `(marketing)/_components/CTASection.tsx:42`.
- [G] `GatewayCard` h2 is `whitespace-nowrap` for "Route · Protect · Track" — risks horizontal overflow on the narrowest column — `(marketing)/_components/Hero.tsx:380`.
- [E] `FlowDiagram.tsx` (full animated flow + count-up) is no longer rendered — ~146 lines of polished dead component remain — `(marketing)/_components/FlowDiagram.tsx:31-146`.
- [B/C] Hero is built almost entirely from inline `style={{}}` objects that duplicate the now-orphaned `.hero*` CSS (`marketing.css:328-446`) — maintainability smell.

**Recommendations (no logic change):**
- 🟢 [P2] Swap the `→`/`✓` glyphs for the inline-SVG treatment used elsewhere.
- 🟢 [P2] Replace `whitespace-nowrap` on the gateway h2 with a wrap-safe/`text-balance` treatment.
- 🟢 [P2] Delete unused `FlowDiagram.tsx` + orphaned `.hero*`/`.flow*` CSS, or re-document why they're retained.

**Anti-patterns flagged:** minor — glyph (`✓`/`→`) as UI icon in hero/CTA. No emoji section markers, no lazy default-gradient buttons, no nested-card overload, no edge-to-edge text.

---

### `/pricing` — Token Packs + FAQ
**Project**: Portal  **Group**: marketing  **Traffic**: Med  **Total Score**: 31/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 4/5 · F 4/5 · G 3/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E/D] Featured tier well-differentiated: 2px violet border + gradient "★ MOST POPULAR" pill, and only Pro uses the dark `primary` CTA (Starter/Business `secondary`) — clear conversion hierarchy — `(marketing)/pricing/page.tsx:41-58`, `(marketing)/marketing.css:1662-1682,1767-1780`.
- [B] Strong price scale: `pricing-tokens-num` 56px vs 24px unit + 44px price figure — `(marketing)/marketing.css:1697-1733`.
- [C] Pricing grid breaks out of the 760px prose column to the full 1160px container so three cards breathe; collapses to one column under 900px — `(marketing)/marketing.css:1629-1643`.
- [F] Cards lift on hover with shadow; CTAs have distinct hover states — `(marketing)/marketing.css:1658-1661,1771-1780`.

**Issues (observable):**
- [G] All three tiers list near-identical feature bullets — the only differentiator is support level, so two-thirds of each card is filler — `(marketing)/pricing/page.tsx:33-37,52-56,71-75`.
- [H] Cards aren't `.reveal`-wrapped → no staged entrance vs the rest of the marketing surface — `(marketing)/pricing/page.tsx:22-77`.
- [B] FAQ is a flat run of `h3`+`p` with no accordion/grouping — one long column — `(marketing)/pricing/page.tsx:92-121`.
- [D] Feature ticks rely on the `✓` glyph via `::before` — same glyph-as-icon pattern — `(marketing)/marketing.css:1750-1754`.

**Recommendations (no logic change):**
- 🟡 [P1] Differentiate tiers visually (per-1M effective rate / "best value" callout) so the price ladder communicates value.
- 🟢 [P2] Add `reveal`/`reveal-delay-*` to the pricing cards for staged entrance.
- 🟢 [P2] Group the FAQ visually (cards or dividers).

**Anti-patterns flagged:** minor — `✓` glyph as feature icon.

---

### `/security` — DLP + Compliance
**Project**: Portal  **Group**: marketing  **Traffic**: Med  **Total Score**: 26/40  **Verdict**: 🟠 Major Refactor

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 3/5 · E 2/5 · F 2/5 · G 4/5 · H 2/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [B/C] Long-form typography well-tuned: 760px measure, 1.7 line-height, 28px `h2` with `-0.02em` tracking + 48px top rhythm, 18px `h3` — `(marketing)/marketing.css:1571-1597`.
- [A] Clear sectioning (Data protection → Compliance → Data handling → Vuln reporting → Whitepaper) with bolded lead-ins — `(marketing)/security/page.tsx:17-106`.
- [G] Genuinely informative + dense without feeling cramped — detector categories, frameworks, in-transit/at-rest/retention/access all covered — `(marketing)/security/page.tsx:23-92`.

**Issues (observable):**
- [E] The page the brief calls "DLP + compliance badges" has **no badges, cards, or visual components at all** — SOC 2 / GDPR / ISO 27001 / HIPAA are plain `<strong>` text in a `<ul>`, where Vercel/Linear show a badge/logo grid — `(marketing)/security/page.tsx:52-69`.
- [H/F] Zero motion or interactive state in the body — not `.reveal`-wrapped — on a page inside an animation-rich system — `(marketing)/security/page.tsx:17-106`.
- [D] Almost monochrome — body is `--text-2`, only accents are the violet eyebrow + underlined links; no severity/category color coding for the detector list — `(marketing)/marketing.css:1591,1621-1626`.
- [E] Five detector categories + four data-handling topics are prose lists where icon-led cards would raise sophistication at no logic cost — `(marketing)/security/page.tsx:23-92`.

**Recommendations (no logic change):**
- 🔴 [P0] Add an actual compliance badge grid (SOC 2 / GDPR / ISO 27001 / HIPAA) — the page's headline asset is currently invisible — `(marketing)/security/page.tsx:52-69`.
- 🟡 [P1] Convert detector categories + data-handling topics into icon-led cards (reuse the landing `feat`/`FrostCard` vocabulary).
- 🟢 [P2] Wrap content sections in `.reveal` for the scroll-reveal system.

**Anti-patterns flagged:** undifferentiated prose-only container on a flagship marketing page; secondary text leans entirely on a single `--text-2` treatment; "compliance badges" rendered as plain bold text.

---

### `/docs` — Developer Quickstart
**Project**: Portal  **Group**: marketing  **Traffic**: Med  **Total Score**: 22/40  **Verdict**: 🟠 Major Refactor

**Per-dimension**: A 3/5 · B 4/5 · C 3/5 · D 3/5 · E 2/5 · F 2/5 · G 2/5 · H 2/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [B/D] The code block is the page's one real moment of polish: dark navy `pre` (`#0F1424`) with light text, 10px radius, mono 13px/1.6 — `(marketing)/marketing.css:1606-1620`.
- [B] Quickstart copy is sharp ("Change your base URL, everything else stays the same") with a correct OpenAI-compatible snippet — `(marketing)/docs/page.tsx:17-34`.
- [A] PageShell hero gives a consistent eyebrow/title/subtitle entry — `(marketing)/docs/page.tsx:11-16`.

**Issues (observable):**
- [G/E] The page is essentially a placeholder — title literally "Documentation is on the way." with one code block + two bullet lists; very low density, no component sophistication — `(marketing)/docs/page.tsx:14-48`.
- [F/H] No interactive state beyond link hover — no copy-to-clipboard on the code block (baseline for a dev quickstart), no reveal animation — `(marketing)/docs/page.tsx:22-34`.
- [E] Single code sample, no language tabs / no syntax highlighting (plain `<code>` string); "What's coming" is a promise list — `(marketing)/docs/page.tsx:36-42`.
- [C] Only one short section above a large empty `page-body` — thin on desktop.

**Recommendations (no logic change):**
- 🟡 [P1] Add copy-to-clipboard + a language tab strip (Python/TS/Go/Ruby — already promised) to the quickstart block.
- 🟡 [P1] Replace the "coming soon" framing with a minimal real reference (base URL, auth header, one endpoint).
- 🟢 [P2] Wrap sections in `.reveal`; icon-led card layout for the "What's coming" list.

**Anti-patterns flagged:** placeholder "coming soon" page shipped as a live marketing surface; very low density; no copy/highlight affordance on a developer code sample.

---

### `/privacy` — Privacy Policy
**Project**: Portal  **Group**: marketing  **Traffic**: Low  **Total Score**: 29/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 3/5 · F 3/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [B] Long-form measure correct: `.page-content` 760px, 16px/1.7 — proper reading column — `(marketing)/marketing.css:1571-1576`.
- [A] Real heading hierarchy h2 28px / h3 18px with distinct margins + mono uppercase eyebrow — Vercel/Resend-tier legal scaffolding — `(marketing)/marketing.css:1578-1591`, `PageShell.tsx:13-21`.
- [D] Links use violet with `text-underline-offset:3px` + soft underline that darkens on hover — `(marketing)/marketing.css:1621-1627`.
- [G] Plain-language copy, well-chunked, `<strong>` emphasis on the load-bearing "we do NOT train / sell / share" lines — `(marketing)/privacy/page.tsx:66-70`.

**Issues (observable):**
- [E] No table-of-contents / anchor links for a 14-section legal doc — reader must scroll-hunt — `(marketing)/privacy/page.tsx:23-129`.
- [A] "Last updated: April 2026" rendered through `subtitle` → styled as an 18px `.page-subtitle`, too heavy for a metadata date — `(marketing)/marketing.css:1562-1566`.
- [F] No interactive states (static content) — dimension effectively N/A.

**Recommendations (no logic change):**
- 🟢 [P2] Add a sticky/inline TOC of the h2s.
- 🟢 [P2] Demote "Last updated" to a small muted caption.

**Anti-patterns flagged:** none.

---

### `/terms` — Terms of Service
**Project**: Portal  **Group**: marketing  **Traffic**: Low  **Total Score**: 29/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 3/5 · F 3/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [B] Same disciplined 760px measure + 16/1.7 rhythm as privacy — consistent long-form typography — `(marketing)/marketing.css:1571-1576`.
- [G] Genuinely readable plain-English clauses with sensible list grouping for account/payment/acceptable-use — `(marketing)/terms/page.tsx:30-90`.
- [D] Inline mailto links inherit the accessible violet underline — `(marketing)/terms/page.tsx:142`, `(marketing)/marketing.css:1621-1627`.

**Issues (observable):**
- [E] Longest legal doc (12 h2 sections), still no TOC/anchors — `(marketing)/terms/page.tsx:23-143`.
- [C] Uniform 48px h2 margins; no visual grouping between clause clusters — long unbroken scroll — `(marketing)/marketing.css:1582`.
- [F] Static, no form states — dimension N/A.

**Recommendations (no logic change):**
- 🟢 [P2] Add anchor TOC; subtle dividers between major clause groups.
- 🟢 [P2] Same "Last updated" demotion as privacy.

**Anti-patterns flagged:** none.

---

# Portal — Auth

### `/login` — Sign in (split + carousel)
**Project**: Portal  **Group**: auth  **Traffic**: High  **Total Score**: 35/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 5/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 4/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E/H] Sophisticated brand panel: 3-slide crossfade carousel with auto-advance, tab-role dot nav, live pulse-dot stat, glass stats card, grid-mask + floating glow — `VisualPanel.tsx:42-93`, `login.css:21-65`, `login/page.tsx:14-69`.
- [F] Solid form states: `role="alert"` error block, loading label swap ("Signing in…"), disabled submit, password show/hide with proper aria-label — `LoginForm.tsx:122-178`.
- [H] Reduced-motion genuinely honored — carousel freezes on slide 1, `.auth-root`-scoped media query kills all anim — `VisualPanel.tsx:27-31`, `login.css:572-580`.
- [D] Primary CTA is solid near-black (`var(--text)`) with a tasteful shimmer sweep — NOT a lazy gradient — `login.css:431-471`.
- [E] Open-redirect-safe `?redirect=` allowlist — `LoginForm.tsx:24-43`.

**Issues (observable):**
- [F] When `OAUTH_ENABLED` is true the Google/GitHub buttons are clickable but `.af-social` styles them `cursor:not-allowed; opacity:0.7` with a "soon" ::after badge — functional buttons that look disabled — `login.css:500-531` vs `LoginForm.tsx:188-214`.
- [E] Suspense skeleton renders only eyebrow + title (no form fields), so the "visually indistinguishable" claim is false — brief flash of a near-empty card — `login/page.tsx:90-97`.
- [B] `.av-headline` at 22px is small for a full-height brand panel headline — reads like a subtitle — `login.css:168-175`.
- [F] Only a single generic server-error string; no inline per-field validation.

**Recommendations (no logic change):**
- 🟡 [P1] Give enabled OAuth buttons their own active style (drop `not-allowed`/opacity/"soon") so they don't read as broken when the flag is on.
- 🟢 [P2] Flesh out the Suspense skeleton with field placeholders, or simplify to a centered spinner.
- 🟢 [P2] Bump `.av-headline` toward ~28-32px.

**Anti-patterns flagged:** none (gradient CTA avoided; spinner replaced by labeled button).

---

### `/signup` — Create account
**Project**: Portal  **Group**: auth  **Traffic**: High  **Total Score**: 35/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 5/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 4/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] 3-bar password strength meter with `aria-live="polite"` + color-coded Weak/Okay/Strong — `SignupForm.tsx:184-191`, `signup.css:101-131`.
- [F] Strong client validation chain (length ≥8, match, TOS) with `noValidate` + scoped errors + loading/disabled — `SignupForm.tsx:62-99`.
- [E] Gradient eyebrow pill + progressive-disclosure invite-code toggle keep the dense form approachable — `signup.css:24-34`, `SignupForm.tsx:218-249`.
- [H] Shares the reduced-motion-safe carousel + entrance animations with login — `signup/page.tsx:7-10` (reuses `VisualPanel`).

**Issues (observable):**
- [F] TOS checkbox defaults to `checked` (`useState(true)`) — a pre-ticked consent box is a recognized consent anti-pattern (GDPR/UX), beside live Terms/Privacy links — `SignupForm.tsx:50,251-268`.
- [D/E] Primary CTA is a violet→cyan gradient with glow (`signup.css:176-189`) while login's CTA is solid black — the two highest-traffic auth CTAs are inconsistent.
- [E] Confirm-password has no inline match indicator; mismatch only surfaces on submit — `SignupForm.tsx:69`.
- [F] No success state — submit redirects straight to /dashboard.

**Recommendations (no logic change):**
- 🟡 [P1] Default the TOS checkbox to unchecked so consent is explicit.
- 🟢 [P2] Align signup/login CTA styling (pick gradient or solid for both).
- 🟢 [P2] Add a live "passwords match" check on the confirm field.

**Anti-patterns flagged:** pre-checked consent checkbox (`SignupForm.tsx:50`); gradient primary CTA (borderline — intentional brand gradient, but inconsistent with login).

---

### `/forgot-password` — Reset request
**Project**: Portal  **Group**: auth  **Traffic**: Med  **Total Score**: 27/40  **Verdict**: 🟠 Major Refactor

**Per-dimension**: A 3/5 · B 4/5 · C 3/5 · D 3/5 · E 3/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Excellent coverage for its size: error (`role="alert"`), loading label swap, disabled submit, real success view with a constant "if an account exists" message (no user-existence oracle) — `forgot-password/page.tsx:51-72,24-49`.
- [F] Success copy is specific — 1-hour expiry + spam-folder/retry guidance, not a bare "Done" — `forgot-password/page.tsx:60-68`.
- [B] Inherits the clean `af-*` form typography when styled — `login.css:337-412`.

**Issues (observable):**
- [D/A] **Page never imports `login.css`** (only login/signup/invite do), yet it relies on `.auth-form-card`, `.af-*`, `.auth-back-home`. The `(auth)/layout.tsx` is a bare pass-through (`layout.tsx:5-7`), so on a direct visit/refresh the route's CSS chunk lacks these rules → page renders largely unstyled — `forgot-password/page.tsx:13-16` (no CSS import).
- [A/E] Even when styled, no visual/brand split panel — just a floating card, a sharp drop-off from login/signup.
- [F] Relies on HTML `required` only; no inline email-format feedback — `forgot-password/page.tsx:98-107`.

**Recommendations (no logic change):**
- 🔴 [P0] Import `../login/login.css` (as login/signup do) and wrap in `.auth-root`/`.auth-split` so direct loads are styled.
- 🟡 [P1] Reuse `AuthSplitLayout` + `VisualPanel` so recovery pages carry the same brand impression.

**Anti-patterns flagged:** uses `.auth-form-card`/`af-*` without importing the stylesheet that defines them → unstyled on cold load.

---

### `/reset-password` — Choose new password
**Project**: Portal  **Group**: auth  **Traffic**: Med  **Total Score**: 27/40  **Verdict**: 🟠 Major Refactor

**Per-dimension**: A 3/5 · B 4/5 · C 3/5 · D 3/5 · E 3/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Best client validation of the auth set: length ≥8, requires letter+number, match, distinct missing-token error state, token-gated disabled submit — `reset-password/page.tsx:42-59,94-100,149-153`.
- [F] Show/hide toggle drives BOTH password + confirm fields with correct aria-labels — `reset-password/page.tsx:126-131,140`.
- [B] Inherits clean `af-*` typography/spacing when styled.

**Issues (observable):**
- [D/A] **Same missing-`login.css` import** as forgot-password (`reset-password/page.tsx:12-16`). This page is ALWAYS entered cold via an email link, so the unstyled render is guaranteed, not edge-case.
- [F] On success it `router.push("/login?reset=success")`, but `LoginForm` only reads `?redirect` and ignores `?reset` (`LoginForm.tsx:67,94`) — the success confirmation is silently dropped.
- [A/E] No brand/visual panel — same floating-card downgrade.

**Recommendations (no logic change):**
- 🔴 [P0] Import `login.css` + wrap in `.auth-root` so the email-link landing is styled.
- 🟡 [P1] Surface the `?reset=success` flash on `/login` (or show an in-page success state before redirect).

**Anti-patterns flagged:** classes used without their stylesheet imported → guaranteed unstyled cold load; orphaned `?reset=success` success signal.

---

### `/invite/[code]` — Join your team
**Project**: Portal  **Group**: auth  **Traffic**: Med  **Total Score**: 30/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 2/5 · E 4/5 · F 4/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A/H] Correctly imports both `login.css` + `signup.css` and reuses the full split layout + carousel, keeping brand continuity with /signup — `invite/[code]/page.tsx:20-24,146-160`.
- [F] Graceful valid/invalid branching server-side: invalid code degrades to a recovery card with "create your own team" + "sign in" escape hatches rather than a 404 — `invite/[code]/page.tsx:82-144`.
- [E] Valid path injects a contextual "Join {tenant} as {role}" banner via `inviteContext`, with locked invite-code field — `SignupForm.tsx:108-132,57`.

**Issues (observable):**
- [D] **Contrast failure on the invalid branch:** the "Sign in instead" link is inline-styled `color: rgba(255,255,255,0.82)` with a white border, rendered inside `.sv-form-side` whose background is `#fff` — white-on-white, effectively invisible — `invite/[code]/page.tsx:122-137` vs `signup.css:8-16`.
- [D] Error eyebrow overrides only the text to `#F87171` while keeping the `.sv-form-eyebrow` violet→cyan gradient pill background — red text on a colored gradient pill — `invite/[code]/page.tsx:100-104`, `signup.css:24-34`.
- [E] Invalid branch built from a wall of inline styles instead of classes — off-system — `invite/[code]/page.tsx:113-138`.

**Recommendations (no logic change):**
- 🔴 [P0] Fix the "Sign in instead" secondary button to a dark/legible token on the white form side (currently illegible).
- 🟢 [P2] Replace the inline-styled invalid-state buttons with existing `af-*`/`sv-*` secondary classes.
- 🟢 [P2] Give the error eyebrow a red-tint background (not the brand gradient).

**Anti-patterns flagged:** white-on-white illegible secondary CTA; heavy inline styling bypassing the design system.

---

### `/oauth-complete` — OAuth handoff (loading/redirect)
**Project**: Portal  **Group**: auth  **Traffic**: Low  **Total Score**: 22/40  **Verdict**: 🟠 Major Refactor

**Per-dimension**: A 3/5 · B 3/5 · C 3/5 · D 3/5 · E 2/5 · F 3/5 · G 3/5 · H 2/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Both transient states exist + handled: spinner+"Signing you in…" loading and a real error fallback with "Back to sign in", plus Suspense fallback — `oauth-complete/page.tsx:53-60,106-120`.
- [F] Spinner is accessible (`role="status"`, aria-label) — `Spinner.tsx:16-18`.
- [E] Reuses the same `safeRedirectTarget` allowlist as login — `oauth-complete/page.tsx:25-41`.

**Issues (observable):**
- [E/H] Spinner-only loading with no brand mark/logo or skeleton on a full `min-h-screen` — generic + off-brand vs the rest of auth — `oauth-complete/page.tsx:122-127`.
- [D] `text-gray-500` is the sole secondary treatment for the loading copy — `oauth-complete/page.tsx:55,123`.
- [B] Single small `text-sm` for all body text in both states — flat hierarchy — `oauth-complete/page.tsx:58,110`.

**Recommendations (no logic change):**
- 🟢 [P2] Add the SRAN logo/mark above the spinner (+ an error icon for the failure state) to match auth branding.
- 🟢 [P2] Swap `text-gray-500` for the portal's secondary text token.

**Anti-patterns flagged:** spinner-only loading where a branded state fits; `text-gray-500` as the only secondary treatment.

---

# Portal — App

### `/dashboard` — Dashboard
**Project**: Portal  **Group**: app  **Traffic**: High  **Total Score**: 34/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 5/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 3/5 · G 4/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A] Clear top-down narrative: avatar header → credit hero → quickstart → chat launch → stats → recent activity, each `space-y-5` — `dashboard/page.tsx:92-191`.
- [E] Genuinely sophisticated hero: animated count-up balance (`useCountUp`, line 216), SVG donut with `heroDonutGrad` gradient + animated stroke-dashoffset via CSS vars (`dashboard/page.tsx:368-385`), drifting grid overlay + breathing radial glow (`231-258`).
- [H] Motion used well: `hero-grid`/`hero-glow`/`hero-circle`, `card-hover`, `btn-premium` on Upgrade — `dashboard/page.tsx:438`.
- [B] Disciplined numerics: `tabular-nums` on the 48px balance, uppercase tracked labels, `font-heading` — `dashboard/page.tsx:285-296`.
- [F] Recent-activity empty state is real (icon tile + "Send your first request…"), not "No data" (`dashboard/page.tsx:871-899`); low-credit warning banner with View-plans CTA (`137-171`).
- [E] StatPanel top accent hairline gradient per-metric — `dashboard/page.tsx:749-759`.

**Issues (observable):**
- [F] Page-level loading is spinner-only (`PageSpinner`, line 81) while the content shape is highly predictable (donut hero + 3-col grids) — a shaped skeleton fits far better — `Spinner.tsx:22-28`.
- [F] Error path is all-or-nothing (no partial render / retry) — a failed `/portal/me` hides the whole page — `dashboard/page.tsx:122-135`.
- [D] Hero secondary text at `rgba(255,255,255,0.5)`–`0.55` is borderline on the navy gradient — `dashboard/page.tsx:297-305,471`.
- [B/G] "Next Reset" HeroStat hardcoded to "—" — a permanent dead stat reads as unfinished — `dashboard/page.tsx:318`.
- [H] Mixed arrow treatment: lucide `ArrowRight`/`ArrowUpRight` in some links but literal "→" glyph in others ("View transactions →", "View plans →", lines 168,434).
- [E] Heavy inline-style objects throughout instead of token utilities.

**Recommendations (no logic change):**
- 🟡 [P1] Replace `PageSpinner` with a skeleton (hero block + 3 stat cards + table rows) using the existing shimmer.
- 🟢 [P2] Wire "Next Reset" to real data or drop the tile.
- 🟢 [P2] Standardize on lucide arrows; remove literal "→".
- 🟢 [P2] Bump hero muted text to ~`rgba(255,255,255,0.65)`.

**Anti-patterns flagged:** spinner-only loading where a content-shaped skeleton fits (`dashboard/page.tsx:81`).

---

### `/projects` — Projects
**Project**: Portal  **Group**: app  **Traffic**: High  **Total Score**: 31/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 4/5 · F 3/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [F] Role-branched empty state is excellent: members get "Ask your team admin…" vs owners get "Create your first project…" + "View docs", both with a primary CTA — `projects/page.tsx:174-189`.
- [E] `ProjectCard` is a real component: gradient surface, `card-hover`, kebab with click-outside + keyboard (`Enter`/`Space`) + `stopPropagation` so menu/CTA don't trigger card nav — `ProjectCard.tsx:37-58,103-117,202-205`.
- [E] Create/Rename/Delete modals all wire loading/disabled + guard close while submitting — `projects/page.tsx:209-319`.
- [D] Destructive delete copy explicit ("All API keys … revoked immediately. This cannot be undone.") + danger button — `projects/page.tsx:305-318`.
- [A/C] Clean `PageHeader` + responsive `grid-cols-1 md:2 lg:3 gap-4` — `projects/page.tsx:191`.

**Issues (observable):**
- [F] Rename + Delete failures fall back to native `alert()` (`projects/page.tsx:119,133`) — jarring vs the in-modal `submitError` banner used by Create (`235-248`).
- [F] Loading is spinner-only (`PageSpinner`, line 168) for a known-shape card grid.
- [G] `requests` stat is always 0 (no per-project count yet, noted at `projects/page.tsx:60-61`); every project shows "0 requests" (`ProjectCard.tsx:188-199`) — looks broken.
- [B] Many one-off inline font sizes (11/13/14/11.5) — `ProjectCard.tsx:80-98`.

**Recommendations (no logic change):**
- 🟡 [P1] Surface rename/delete errors in-modal (reuse the error banner) instead of `alert()`.
- 🟡 [P1] Swap grid `PageSpinner` for 3–6 skeleton cards.
- 🟢 [P2] Hide the requests stat (or show "—") until per-project counts exist.

**Anti-patterns flagged:** native `alert()` for mutation errors (`projects/page.tsx:119,133`); spinner-only loading where skeleton cards fit (`projects/page.tsx:168`).

---

### `/projects/[id]` — Project Detail (Keys + Members)
**Project**: Portal  **Group**: app  **Traffic**: High  **Total Score**: 34/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 4/5 · H 5/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] One-time key-reveal modal is the highlight: warning banner, mono code block, copy with `Copied`/`Check` feedback (resets after 2s), reused for both create + rotate — `[id]/page.tsx:458-525,201-207,188-190`.
- [E/H] `ProjectMembersSection` `RowMenu` is advanced: direction-aware open (flips up when `spaceBelow < 180`), in/out animations, Escape-to-close, focus-restore, autofocus first item, full ARIA `menu`/`menuitem` — `ProjectMembersSection.tsx:468-516,553-572`.
- [E] Add-member modal: search-filtered roster excluding existing members, selectable rows + `Check`, role radio-card group with hints — `ProjectMembersSection.tsx:721-984`.
- [F] Strong coverage: full-page error keeps `BackLink` (`[id]/page.tsx:211-229`); rich empty-keys state + CTA (`288-317`); members has own loading/empty/error; mutations via `useToast`.
- [D] Status palettes for keys (active/revoked/neutral) + role badges with icons — `[id]/page.tsx:674-695`, `ProjectMembersSection.tsx:400-444`.
- [A] Logical sections: back → titled header w/ icon → 3 stat tiles → Keys card → Members card — `[id]/page.tsx:231-410`.

**Issues (observable):**
- [F] Revoke failure uses native `alert()` (`[id]/page.tsx:173`) while create/rotate use in-modal banners (`433-446,577-590`) and members use toasts — three different error channels on one page.
- [F] Top-level loading is spinner-only (`PageSpinner`, line 209); members loading is plain "Loading members…" text (`ProjectMembersSection.tsx:216-225`), members error is bare danger text with no container (`227-229`).
- [G] Stats row counts are all-user totals, not per-project (noted at `[id]/page.tsx:108-112`) — "Total Requests"/"Tokens Used" will mislead on a project page.
- [B] Same proliferation of inline magic font sizes (11/11.5/12/13/16/24).

**Recommendations (no logic change):**
- 🟡 [P1] Replace the revoke `alert()` with the existing toast or an in-modal banner for a single error idiom.
- 🟡 [P1] Swap `PageSpinner` for a content-shaped skeleton; give members loading/error the same icon+container treatment as the keys empty state.
- 🟢 [P2] Label the stat tiles account-wide (or show "—") until per-project usage filtering lands.

**Anti-patterns flagged:** native `alert()` for revoke error (`[id]/page.tsx:173`); spinner-only page loading where a skeleton fits (`[id]/page.tsx:209`); members error as bare `text-danger` line with no container (`ProjectMembersSection.tsx:227-229`).

---

### `/usage` — Usage
**Project**: Portal  **Group**: app  **Traffic**: Med  **Total Score**: 30/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 3/5 · F 3/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A] Clear top-down flow: header + segmented range toggle, 3 stat cards, chart, top-models/per-project split, log table — `usage/page.tsx:135-324`.
- [E] StatCards use tone palette + count-up + tabular-nums — `_components/StatCard.tsx:57-151`.
- [D] Status bucketing maps raw backend strings into a 3-color token palette (success/error/rate_limited) — `RequestLogTable.tsx:19-41`.
- [F] Real empties everywhere: per-panel ("No requests yet — run your first call…") `usage/page.tsx:243-251`, table EmptyState with CTA `RequestLogTable.tsx:77-83`, chart "Awaiting data" overlay `UsageChart.tsx:73-97`.
- [H] chip-bounce toggle, bar-grow stagger, empty-float, row-hover — `usage/page.tsx:154`, `UsageChart.tsx:105-110`.

**Issues (observable):**
- [E] The "Requests over time" chart is a FAKE placeholder — deterministic bar heights unrelated to data, comment "Placeholder until a chart library is wired up" — `UsageChart.tsx:19-21,100`. On a page named Usage this is the headline visual carrying no real signal.
- [F] Loading is spinner-only (`PageSpinner`) where a skeleton matching stat-cards + table fits — `usage/page.tsx:131`, `Spinner.tsx:22-28`.
- [G] Top-models / per-project panels are plain text rows with no bar/sparkline to encode magnitude — `usage/page.tsx:253-318`.

**Recommendations (no logic change):**
- 🔴 [P0] Replace the placeholder bar chart with a real series, or relabel it honestly ("Preview") so it doesn't imply real data — current state misleads on the primary page.
- 🟡 [P1] Swap PageSpinner for a stat-cards + table skeleton.
- 🟢 [P2] Add tiny inline magnitude bars to the top-models/per-project lists.

**Anti-patterns flagged:** spinner-only loading where skeleton fits; placeholder chart presented as live data.

---

### `/team` — Team
**Project**: Portal  **Group**: app  **Traffic**: Med  **Total Score**: 32/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 3/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [E] RowMenu kebab is sophisticated: smart up/down viewport flip, exit-animation lifecycle, focus trap + return, full ARIA menu roles — `team/page.tsx:636-731,682-723`.
- [E] Email invite chip-input: paste-splitting, per-chip valid/invalid status, backspace-to-delete, regenerate/revoke confirm dialogs — `InviteSection.tsx:218-237,678-740`.
- [D] Role + status badges tokenized with icon + color (Crown owner, dot Active, Suspended/Pending) — `team/page.tsx:470-571`.
- [A] Owner vs member fully role-aware — InviteSection for owners, MemberOnlyInviteCard for members — `team/page.tsx:179-197`.
- [H] menu-anim in/out, row-stagger with per-row delay, chip-bounce filter chips — `team/page.tsx:369-373,604`.
- [F] Suspended rows visually dimmed (opacity 0.72) + greyed name — `team/page.tsx:372,390-392`.

**Issues (observable):**
- [F] Page loading is spinner-only (`team/page.tsx:153`); the invite block's loading is a literal "Loading invite link…" string rather than a skeleton — `InviteSection.tsx:306-322`.
- [F] Table empty state is bare-text "No members match these filters." with no icon/reset-filter affordance — `team/page.tsx:269-276`.
- [G] "Change role" menu item is a disabled "Coming soon" stub, so the kebab often offers only one live action — `team/page.tsx:770-775`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace page + invite spinner/text loaders with a roster skeleton (avatar circle + 2 text lines per row).
- 🟢 [P2] Upgrade the "no match" row to the shared EmptyState with "Clear filters".

**Anti-patterns flagged:** spinner-only loading; plain-text empty state in members table.

---

### `/billing` — Billing & Credits
**Project**: Portal  **Group**: app  **Traffic**: High  **Total Score**: 34/40  **Verdict**: 🟡 Minor Polish

**Per-dimension**: A 5/5 · B 4/5 · C 4/5 · D 4/5 · E 5/5 · F 4/5 · G 4/5 · H 4/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A] Strong hierarchy: balance hero → token-pack grid → transaction history — `billing/page.tsx:168-495`.
- [E] Balance count-up (`useCountUp`, 1000ms) with tabular-nums + 42px heading — `billing/page.tsx:109,220-233`.
- [E] "Most Popular" pack gets a distinct state — 2px accent border, scale-[1.02], gradient ribbon badge with Star — `billing/page.tsx:281-311`.
- [D] Hero reuses the dashboard gradient + grid + radial glow, not a flat panel (`billing/page.tsx:169-206`); TxTypeChip maps each transaction type to its own icon+color token (`billing/page.tsx:594-657`).
- [E/D] Purchase CTA uses navy `--bg-sidebar` (brand), not a lazy `from-x-500 to-y-500` gradient — `billing/page.tsx:397-401`.
- [F] Full state set: error banner, dismissible purchase success banner, EmptyState for empty transactions, modal confirm — `billing/page.tsx:123-166,435-440,497-534`.

**Issues (observable):**
- [D/E] The purchase success/notice banner uses a literal "×" character as the dismiss control instead of a lucide `X` — `billing/page.tsx:151-164`.
- [F] Loading is spinner-only where a skeleton (hero block + 3 pack cards) would match the shape — `billing/page.tsx:111`.
- [B] Price has no per-token or "/month" framing, leaving value slightly ambiguous — `billing/page.tsx:333-373`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace the "×" dismiss glyph with a lucide `X`.
- 🟡 [P1] Swap PageSpinner for a hero + pack-card skeleton.
- 🟢 [P2] Add a tiny "$X per 1M tokens" helper under each price.

**Anti-patterns flagged:** literal "×" used as a UI close icon; spinner-only loading where skeleton fits.

---

### `/settings` — Settings
**Project**: Portal  **Group**: app  **Traffic**: Med  **Total Score**: 31/40  **Verdict**: 🟡 Minor Polish

> Note: change-password (Security card) and delete-account (Danger Zone) are DISABLED STUBS — feature นี้เป็น stub, score ตาม visual quality ไม่ใช่ functional completeness (Functional Audit แยกจัดการ).

**Per-dimension**: A 4/5 · B 4/5 · C 4/5 · D 4/5 · E 4/5 · F 4/5 · G 4/5 · H 3/5

**Strengths (Keep As-Is — ห้ามแตะ):**
- [A] Well-segmented cards: Account, Profile, Preferences, Security, Linked accounts, Danger Zone — each a labeled `font-heading` section — `settings/page.tsx:207-716`.
- [E] Avatar flow is real: type/size validation, FileReader → crop modal → webp upload, in-place uploading spinner overlay — `settings/page.tsx:87-142,299-306`.
- [E] Linked accounts use authentic multi-path brand SVGs (Google 4-color, GitHub) not emoji/letter glyphs — `settings/page.tsx:839-856`.
- [D] Danger Zone visually differentiated (rose border + tinted inner panel + AlertTriangle); stubs read clearly via "Coming soon" pills + opacity-60 + pointer-events-none — `settings/page.tsx:639-715,517-535,540-596`.
- [F] Inline success/error messaging on profile + avatar plus toasts, dirty-state-gated Save — `settings/page.tsx:180-182,404-434`.
- [B] User ID shown truncated mono with copy + copied confirmation — `settings/page.tsx:218-251`.

**Issues (observable):**
- [F] Avatar removal uses the native browser `confirm()` (`settings/page.tsx:146`), inconsistent with the polished ConfirmDialog/Modal used on Team/Billing.
- [F] Loading is spinner-only where a card-stack skeleton fits — `settings/page.tsx:174`.
- [E] Preference toggles are decorative-only (`aria-hidden`, no input) inside a pointer-events-none block (`settings/page.tsx:517,892-916`) — visually a switch but non-interactive; acceptable as "coming soon" but reads as a real control.
- [H] Lower motion density than other pages — mostly static cards — `settings/page.tsx:304,899`.

**Recommendations (no logic change):**
- 🟡 [P1] Replace native `confirm()` for avatar removal with the shared ConfirmDialog.
- 🟡 [P1] Swap PageSpinner for a card-stack skeleton.
- 🟢 [P2] Add a page-enter/row-stagger touch to the cards to match Billing/Team.

**Anti-patterns flagged:** native `confirm()` amid a custom dialog system; spinner-only loading where skeleton fits.
