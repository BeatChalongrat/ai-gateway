# Phase 5 — Prioritized Roadmap

> Effort key: **S** = 1–2 hr · **M** = 4–8 hr · **L** = 2–3 days. Sprint = 2 weeks solo.
> Hard rule (carried from the Constitution): **visual only** — no business logic, API, routing, or feature add/remove. Where a fix brushes logic (the "honest metrics" items), it is flagged **DECISION** and must not be applied silently.
> Do **Foundation** before per-page Tiers — it removes the most-repeated issues at the source so per-page work shrinks.

---

## Foundation work (do BEFORE per-page changes)

| ID | Item | What | Effort | Unblocks |
|---|---|---|---|---|
| **F1** | Admin icon module + glyph sweep | Create `admin-ui/components/ui/icons.tsx` (or a typed icon map) collecting the inline SVGs already scattered through the app; replace every emoji/glyph-as-icon (S1) with it. | **M** | every admin icon issue (~14 pages) |
| **F2** | Theme-token sweep | Replace hardcoded light-only hex (`#f3f4f6`, `#92400e`, `#f0f2f5`, `#1e3a5f`, danger `rgb()`s…) with the existing semantic tokens. Mechanical; tokens already exist in both globals.css. | **M** | dark-theme correctness (S2), ~11 pages |
| **F3** | Token-namespace consolidation (admin) | Pick `--bg/--text/--accent/--border-*` as canonical; alias legacy `--color-*` to them (or vice-versa) in `admin-ui/app/globals.css`; note the cross-app divergence as intentional-for-now. | **S** | F2, removes future drift (S6) |
| **F4** | Dialog replacement + lint ban | Swap every `window.confirm/prompt/alert` for the existing `Modal`/`ConfirmModal`/`ConfirmDialog`/`Toast`; add an ESLint `no-restricted-globals` rule. | **M** | S3 (3 admin + 3 portal pages) |
| **F5** | Portal skeleton presets | Build `HeroSkeleton`, `StatCardRowSkeleton`, `TableSkeleton`, `CardGridSkeleton` in portal using the existing `.skeleton` shimmer; ready to drop into each `(app)` page. | **M** | S4 (7 portal pages) |
| **F6** | Honest-metrics pass | routing 99.2% · usage fake chart · projects 0-requests · dashboard "Next Reset" · models metadata. **DECISION per item:** wire to real data / label "Preview" / hide. | **S** (UI) + decision | S5 trust issue |
| **F7** | P0 bug-fix bundle | Import `login.css` + `.auth-root` wrapper on `/forgot-password` + `/reset-password`; fix `/invite/[code]` white-on-white CTA; (see Tier 1). | **S** | unblocks cold-load styling |

**Foundation total ≈ 1 sprint** (F1+F2+F4+F5 are the bulk; F3+F6+F7 small).

---

## Tier 1 — Critical (do first): high user impact / broken-looking / public-facing

| # | Page(s) | Why now | Work | Effort | Acceptance criteria |
|---|---|---|---|---|---|
| 1 | **Portal `/forgot-password` + `/reset-password`** (27, 27) | Auth recovery renders **unstyled on cold/email-link load** (S10/F7) — every password reset hits this. | Import `login.css`, wrap in `.auth-root`; ideally reuse `AuthSplitLayout`+`VisualPanel`. | M | Direct-load + refresh of both routes is fully styled, brand-consistent split layout; reduced-motion respected. |
| 2 | **Portal `/invite/[code]`** invalid branch (30) | "Sign in instead" CTA is **white-on-white / invisible** (S10). | Token-color the secondary CTA; red-tint the error eyebrow. | S | Invalid-invite CTA passes WCAG AA on the white form side; error eyebrow legible. |
| 3 | **Portal `/security`** (26) | Public, flagship trust page for a security product, but "compliance badges" are **plain bold text** (S12). | Add a real SOC2/GDPR/ISO/HIPAA badge grid; convert detector categories to icon-led cards; `.reveal`. | M | Badge grid present; detector cards match landing vocabulary; sections animate in. |
| 4 | **Portal `/docs`** (22) | Public dev surface reads as a **"coming soon" placeholder** (lowest score). | Copy-to-clipboard + language tabs on the code block; a minimal real reference (base URL/auth/one endpoint); `.reveal`. | M | Code block has copy + ≥2 language tabs; page has standalone reference value. |
| 5 | **Admin `/dlp-detectors`** (26) | Lowest admin score; **native `prompt/confirm/alert`** + text-only loading on a security-config surface. | Apply F4 (dialogs) + F5-equivalent skeleton/EmptyState; status pills; destructive Delete treatment. | M | No native dialogs; skeleton + EmptyState; status as pills; Delete visually separated. |
| 6 | **Portal `(app)` spinner→skeleton** on high-traffic pages: `/dashboard`, `/projects`, `/projects/[id]`, `/billing` (34/31/34/34) | First paint of the most-visited authed pages is a bare spinner (S4). | Drop F5 presets in; replace `alert()` error channels (F4). | M | Each page shows a content-shaped skeleton; no `alert()` for mutation errors. |
| 7 | **Admin `/requests` narrow-viewport** (35) | Below 1100px the detail panel is `display:none` → **row click does nothing** (S11). | Stack detail below list (or open as a sheet) under 1100px. | S | Selecting a row on a narrow viewport reveals the detail. |

**Tier 1 ≈ 1 sprint** (much of it consumes Foundation outputs).

---

## Tier 2 — High: medium-traffic polish + systemic cleanups

| # | Scope | Work | Effort |
|---|---|---|---|
| 8 | **Admin token-hex sweep apply** (F2 consumers): `/portal-users`, `/policies`, `/dlp-coverage`, `/chat-analytics`, `/api-keys`, `/tenants`, `/chat-knowledge`, `/chat-knowledge/[id]` | Apply F2/F3 tokens per page; verify in dark theme. | M |
| 9 | **Admin emoji→icon apply** (F1 consumers): `/portal-users`, `/audit-log`, `/portal-token-packs`, `/search-settings`, `/providers`, `/chat-conversations/[id]` | Swap emoji/glyphs for the F1 icon module. | M |
| 10 | **Perpetual-motion + reduced-motion** (S8): `/models`, `/policies` one-shot icon anims; add global reduced-motion block to admin globals.css; guard `/routing`. | Convert infinite anims → mount-once; one CSS block. | S |
| 11 | **Portal `/pricing`** (31) | Per-1M effective-rate / "best value" callout; `.reveal` on cards; group FAQ. | M |
| 12 | **Portal `/oauth-complete`** (22) | Brand mark above spinner + error icon; token secondary text. | S |
| 13 | **Portal `/usage` + `/team` + `/settings`** spinner→skeleton + empty-state polish (F5/F4 consumers) | Skeletons; `/team` shared EmptyState; `/settings` ConfirmDialog for avatar removal. | M |
| 14 | **Admin loading-vocabulary consistency** (S4 stragglers): `/` volume-chart + top-models skeletons; `/ai-review`, `/search-settings`, `/usage` skeletons. | Shaped skeletons. | M |

**Tier 2 ≈ 1 sprint.**

---

## Tier 3 — Medium polish: low-traffic + minor refinements

| # | Scope | Work | Effort |
|---|---|---|---|
| 15 | Admin `/chat-conversations` (29) | colored boolean badges for Files/Shared; drop duplicated `inputStyle`; optional KPI strip. | S |
| 16 | Admin `/chat-conversations/[id]` (28) | tinted user/assistant tiles; bump 0.6rem micro-text; markdown render is **logic — defer to functional track**. | S |
| 17 | Admin `/alerts/events` (28) | severity/delivery filter pills; bell SVG empty icon. | S |
| 18 | Admin `/ai-review` (29) | extract repeated `<td>` style to a class; skeletons; gear/chevron SVG. | S |
| 19 | Admin `/usage` (30) | `.sran-table` + shared EmptyState; User-tab density. | S |
| 20 | Admin `/portal-settings` (31), `/portal-token-packs` (30), `/chat-knowledge` (31) | token buttons; `💎`/`★`→SVG; stat strip. | S |
| 21 | Admin `/login` (33) | flatten gradient button or document as auth-only; `:focus-visible` + `aria-invalid`. | S |
| 22 | Admin `/providers` `/models` (33/33) | key-status skeleton; CSS hover with focus parity; toolbar tokens; tone motion. | M |
| 23 | Marketing `/privacy` `/terms` (29/29) | anchor TOC; demote "Last updated" caption; clause-group dividers. | S |
| 24 | Portal `/signup` (35) | uncheck TOS default (**DECISION — consent**); align CTA with login; live match indicator. | S |
| 25 | Portal `/login` (35) | active style for enabled OAuth buttons; flesh Suspense skeleton; larger panel headline. | S |
| 26 | Admin `/chat-users` (34), `/dlp-coverage` (33), `/search-settings` (34) | toggle vocabulary unify; focus-ring color; remaining token/skeleton nits. | S |

**Tier 3 ≈ 0.5–1 sprint** (many S items, batchable).

---

## Tier 4 — Skip (Keep As-Is — ห้ามแตะ)

These are at/above the Keep threshold (36–40) or are the brand north-star; **do not refactor**, only let Foundation token/icon sweeps touch them incidentally:

- Admin **`/`** (39) — the reference dashboard.
- Admin **`/audit-log`** (37), **`/tenants`** (37), **`/projects`** (37), **`/routing`** (36 — *except* the F6 success-rate decision), **`/api-keys`** (36).
- Portal **`/`** landing (38).

Their per-page "Issues" are P1/P2 nits already captured; they get swept by Foundation, not bespoke work.

---

## Suggested sequencing (4 sprints)

1. **Sprint 1 — Foundation** (F1–F7). Ship the icon module, token sweep, dialog replacement, skeleton presets, P0 bug bundle; get the F6 honest-metrics decision from Beat.
2. **Sprint 2 — Tier 1.** Auth recovery, invite, `/security`, `/docs`, `/dlp-detectors`, portal high-traffic skeletons, requests responsive.
3. **Sprint 3 — Tier 2.** Apply F1/F2 sweeps across med-traffic pages; motion cleanup; pricing/oauth/usage/team/settings.
4. **Sprint 4 — Tier 3.** Low-traffic polish batch + marketing legal + auth nits.

Net: **~4 sprints (8 weeks solo)** to move the whole estate from avg **31.6** into solid Minor-Polish/Keep territory, with no Rebuilds required.

---

## Decisions needed from Beat before/early in Sprint 1

1. **F6 honest-metrics** — for each of {routing 99.2%, usage fake chart, projects 0-requests, projects/[id] account-wide stats, dashboard "Next Reset"}: **wire to real data / label "Preview" / hide?** (Wiring = backend work, out of this visual track.)
2. **Cross-app unification scope** — keep admin (Tailwind v4 / inline-svg) and portal (v3 / lucide) as **two deliberately-separate systems** (this audit's assumption), or open a separate track to unify? Recommend keep-separate for now.
3. **`/signup` TOS default** — unchecking it is a consent/legal call, not purely visual; confirm before changing.
4. **Markdown rendering in admin `/chat-conversations/[id]`** — that's functional, deferred to the Functional Audit; confirm it stays out of the visual track.
